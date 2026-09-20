"""Positive SFT with train/inference-consistent Qwen rendering."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from pathlib import Path
from typing import Any

from harness_zero.tools import execute_tool_schema
from harness_zero.chatfmt import create_qwen35_renderer, to_renderer_messages
from deepagents_harbor.message_content import assistant_text


def learning_rate_at_step(
    *, step: int, total_steps: int, warmup_steps: int, peak: float, final: float
) -> float:
    if step <= warmup_steps:
        return peak * step / warmup_steps
    decay_steps = max(1, total_steps - warmup_steps)
    fraction = min(1.0, (step - warmup_steps) / decay_steps)
    return peak + (final - peak) * fraction


def prepare_datums(
    *, rows: list[dict[str, Any]], renderer: Any, max_length: int
) -> tuple[list[Any], dict[str, Any]]:
    import tinker
    import torch
    from tinker_cookbook.renderers import TrainOnWhat
    from tinker_cookbook.supervised.data import conversation_to_datum

    datums = []
    total_tokens = 0
    trainable_tokens = 0
    assistant_messages = 0
    reasoning_messages = 0
    masked_reasoning_tokens = 0
    masked_assistant_messages = 0
    skipped_too_long = 0
    rendered_hash = hashlib.sha256()
    loss_mask_hash = hashlib.sha256()
    if not renderer.has_extension_property:
        raise ValueError("SFT requires Qwen rendering with historical thinking preserved")
    for index, row in enumerate(rows):
        if row.get("training_eligible") is False:
            raise ValueError(f"datum {index} is a diagnostic repair; recollect its missing model prefixes")
        source_messages = row["messages"]
        masked_turns = set(row.get("masked_reasoning_turns") or [])
        masked_assistants = set(row.get("masked_assistant_turns") or [])
        turn_count = sum(m.get("role") == "assistant" for m in source_messages)
        if any(not isinstance(i, int) or not 0 <= i < turn_count for i in masked_turns):
            raise ValueError(f"datum {index} has invalid masked_reasoning_turns")
        if any(type(i) is not int or not 0 <= i < turn_count for i in masked_assistants):
            raise ValueError(f"datum {index} has invalid masked_assistant_turns")
        if masked_assistants & masked_turns:
            raise ValueError(f"datum {index} overlaps whole-message and reasoning masks")
        masked_assistant_messages += len(masked_assistants)
        assistant_messages += sum(
            message.get("role") == "assistant" for message in source_messages
        )
        reasoning_messages += sum(
            message.get("role") == "assistant"
            and isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") == "thinking"
                for part in message["content"]
            )
            for message in source_messages
        )
        messages = to_renderer_messages(
            renderer,
            source_messages,
            [{"type": "function", "function": execute_tool_schema()}],
        )
        if masked_assistants:
            assistant_index = 0
            for message in messages:
                message['trainable'] = False
                if message['role'] == 'assistant':
                    message['trainable'] = assistant_index not in masked_assistants
                    assistant_index += 1
        datum = conversation_to_datum(
            messages,
            renderer,
            max_length=None,
            train_on_what=TrainOnWhat.CUSTOMIZED if masked_assistants else TrainOnWhat.ALL_ASSISTANT_MESSAGES,
        )
        length = datum.model_input.length
        if length > max_length:
            skipped_too_long += 1
            continue
        weights = datum.loss_fn_inputs["weights"].tolist()
        if len(weights) != length:
            raise ValueError(f"datum {index} token and loss-mask lengths differ")
        if masked_turns:
            targets = datum.loss_fn_inputs["target_tokens"].tolist()
            assistant_index = 0
            for position, message in enumerate(messages):
                if message["role"] != "assistant":
                    continue
                source_index = assistant_index
                assistant_index += 1
                if source_index not in masked_turns:
                    continue
                thinking, _ = assistant_text(message)
                if not thinking.strip():
                    raise ValueError(
                        f"datum {index} marks assistant {source_index} reasoning "
                        "for masking, but it has no reasoning"
                    )
                # Ask the renderer for this message's target span. Searching
                # only inside that span cannot match user/tool/history text.
                scoped = conversation_to_datum(
                    [{**m, "trainable": i == position} for i, m in enumerate(messages)],
                    renderer,
                    max_length=None,
                    train_on_what=TrainOnWhat.CUSTOMIZED,
                )
                if scoped.loss_fn_inputs["target_tokens"].tolist() != targets:
                    raise ValueError("customized rendering changed the target tokens")
                scope = scoped.loss_fn_inputs["weights"].tolist()
                needle = renderer.tokenizer.encode(thinking.strip(), add_special_tokens=False)
                start = next((
                    i for i in range(len(targets) - len(needle) + 1)
                    if scope[i] > 0
                    and targets[i:i + len(needle)] == needle
                    and all(w > 0 for w in scope[i:i + len(needle)])
                ), None)
                if start is None:
                    raise ValueError(
                        f"datum {index} assistant {source_index} reasoning "
                        "was not found inside its rendered target span"
                    )
                end = start + len(needle)
                masked_reasoning_tokens += sum(w > 0 for w in weights[start:end])
                weights[start:end] = [0.0] * len(needle)
            positive = sum(float(weight) > 0 for weight in weights)
            if positive == 0:
                raise ValueError(f"datum {index} has no trainable tokens after masking")
            weights = [1.0 / positive if float(weight) > 0 else 0.0 for weight in weights]
            datum.loss_fn_inputs["weights"] = tinker.TensorData.from_torch(
                torch.tensor(weights)
            )
        positive = sum(float(weight) > 0 for weight in weights)
        if positive == 0:
            raise ValueError(f"datum {index} has no trainable completion tokens")
        datums.append(datum)
        total_tokens += length
        trainable_tokens += positive
        rendered_hash.update(length.to_bytes(8, "big"))
        for token in datum.model_input.to_ints():
            rendered_hash.update(int(token).to_bytes(4, "big"))
        loss_mask_hash.update(length.to_bytes(8, "big"))
        loss_mask_hash.update(bytes(float(weight) > 0 for weight in weights))
    if not datums:
        raise ValueError(
            f"all {len(rows)} SFT sessions exceed the {max_length}-token limit"
        )
    return datums, {
        "examples": len(datums),
        "source_sessions": len(rows),
        "skipped_too_long": skipped_too_long,
        "tokens": total_tokens,
        "trainable_tokens": trainable_tokens,
        "assistant_messages": assistant_messages,
        "reasoning_messages_in_source": reasoning_messages,
        "masked_reasoning_tokens": masked_reasoning_tokens,
        "masked_assistant_messages": masked_assistant_messages,
        "max_tokens": max(d.model_input.length for d in datums),
        "rendered_tokens_sha256": rendered_hash.hexdigest(),
        "loss_mask_sha256": loss_mask_hash.hexdigest(),
    }


def train_positive_sft(
    *,
    data_path: Path,
    base_model: str,
    renderer_name: str,
    rank: int,
    peak_learning_rate: float,
    final_learning_rate: float,
    warmup_ratio: float,
    epochs: int,
    batch_size: int,
    max_length: int,
    seed: int,
    run_name: str,
    output_dir: Path,
    checkpoint_epochs: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0),
    resume_from: str | None = None,
) -> dict[str, Any]:
    import tinker
    from tinker_cookbook.image_processing_utils import get_image_processor

    if renderer_name != "qwen3_5":
        raise ValueError(f"unsupported renderer {renderer_name}; use qwen3_5")

    rows = [
        json.loads(line)
        for line in data_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("SFT dataset is empty")
    if (
        not base_model
        or not renderer_name
        or rank <= 0
        or epochs <= 0
        or batch_size <= 0
        or max_length <= 0
    ):
        raise ValueError(
            "base model, renderer, rank, epochs, batch size, and max length must be valid"
        )
    if not 0 <= warmup_ratio < 1:
        raise ValueError("warmup_ratio must be in [0, 1)")
    if not 0 < final_learning_rate <= peak_learning_rate:
        raise ValueError("learning rates must satisfy 0 < final <= peak")
    if any(point <= 0 or point > epochs for point in checkpoint_epochs):
        raise ValueError("checkpoint epochs must fall inside the training run")

    api_key = os.environ.get("TM_API_KEY")
    if not api_key:
        raise ValueError("Tinker training requires TM_API_KEY")
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.jsonl"
    service = tinker.ServiceClient(api_key=api_key)
    if resume_from:
        client = service.create_training_client_from_state(resume_from)
    else:
        client = service.create_lora_training_client(
            base_model=base_model,
            rank=rank,
            seed=seed,
        )
    tokenizer = client.get_tokenizer()
    try:
        image_processor = get_image_processor(base_model)
    except OSError:
        image_processor = None  # text-only models ship no preprocessor config
    renderer = create_qwen35_renderer(tokenizer, image_processor=image_processor)
    datums, data_stats = prepare_datums(
        rows=rows,
        renderer=renderer,
        max_length=max_length,
    )

    batches_per_epoch = math.ceil(len(datums) / batch_size)
    total_steps = epochs * batches_per_epoch
    warmup_steps = max(1, math.ceil(total_steps * warmup_ratio))
    checkpoints: list[dict[str, Any]] = []
    saved_points: set[float] = set()
    step = 0

    with metrics_path.open("w", encoding="utf-8") as metrics_file:
        for epoch_index in range(epochs):
            order = list(range(len(datums)))
            random.Random(seed + epoch_index).shuffle(order)
            for offset in range(0, len(order), batch_size):
                step += 1
                batch = [datums[index] for index in order[offset : offset + batch_size]]
                progress = epoch_index + min(offset + len(batch), len(datums)) / len(datums)
                learning_rate = learning_rate_at_step(
                    step=step,
                    total_steps=total_steps,
                    warmup_steps=warmup_steps,
                    peak=peak_learning_rate,
                    final=final_learning_rate,
                )
                forward = client.forward_backward(batch, loss_fn="cross_entropy").result()
                client.optim_step(tinker.AdamParams(learning_rate=learning_rate)).result()
                metric = {
                    "step": step,
                    "epoch": progress,
                    "learning_rate": learning_rate,
                    "batch_examples": len(batch),
                    "batch_tokens": sum(d.model_input.length for d in batch),
                    "mean_datum_nll": float(forward.metrics["loss:sum"]) / len(batch),
                    "tinker": forward.metrics,
                }
                metrics_file.write(json.dumps(metric, ensure_ascii=False) + "\n")
                metrics_file.flush()

                for point in checkpoint_epochs:
                    if point not in saved_points and progress >= point:
                        label = str(point).replace(".", "p")
                        state = client.save_state(
                            name=f"{run_name}-epoch-{label}", ttl_seconds=None
                        ).result()
                        sampler = client.save_weights_for_sampler(
                            name=f"{run_name}-epoch-{label}-sampler", ttl_seconds=None
                        ).result()
                        checkpoint = {
                            "target_epoch": point,
                            "effective_epoch": progress,
                            "step": step,
                            "state_path": state.path,
                            "sampler_path": sampler.path,
                        }
                        checkpoints.append(checkpoint)
                        saved_points.add(point)
                        (output_dir / "checkpoints.json").write_text(
                            json.dumps(checkpoints, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )

    result = {
        **data_stats,
        "base_model": base_model,
        "renderer": renderer_name,
        "train_on": "assistant_messages_except_explicit_masks" if data_stats["masked_assistant_messages"] else "all_assistant_messages",
        "loss_reduction": "mean_per_datum",
        "history_thinking": "preserve",
        "execute_tool_schema": execute_tool_schema(),
        "steps": step,
        "epochs": epochs,
        "metrics_path": str(metrics_path),
        "checkpoints": checkpoints,
    }
    (output_dir / "training_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
