from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from harness_zero.chat_model import ReasoningChatOpenAI
from harness_zero.chatfmt import create_qwen35_renderer, lc_to_openai, to_renderer_messages
from deepagents_harbor.message_content import assistant_text, normalize_assistant, split_think
from harness_zero.review import AssistantResponse, ExecuteCall
from harness_zero.dataset_utils import conversation_messages
from harness_zero.tools import execute_tool_schema

tinker = pytest.importorskip("tinker", reason="training tests need: uv sync --group tinker")

from harness_zero.train import prepare_datums


@pytest.fixture(scope="module")
def qwen_renderer():
    from transformers import AutoTokenizer

    try:
        tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-9B", local_files_only=True)
    except OSError:
        pytest.skip("Qwen tokenizer is not cached locally; no downloads during tests")
    return create_qwen35_renderer(tokenizer)


@pytest.mark.parametrize("message", [
    {"content": "<think>check</think>answer", "reasoning_content": "check"},
    {"content": "<think>check</think>answer", "additional_kwargs": {"reasoning_content": "check"}},
    {"content": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "check"}]},
                 {"type": "text", "text": "answer"}]},
    {"content": [{"type": "text", "text": "answer"}], "reasoning_content": "check"},
    {"content": "answer", "reasoning": "check"},
])
def test_normalization_is_shared_and_does_not_duplicate(message):
    assert assistant_text(message) == ("check", "answer")
    converted = lc_to_openai({"type": "ai", **message})
    assert converted["content"] == [
        {"type": "thinking", "thinking": "check"}, {"type": "text", "text": "answer"}
    ]
    assert assistant_text(converted) == ("check", "answer")


def test_prefill_is_explicit_and_truncation_is_not_an_answer():
    raw = "check\n</think>\n\nanswer"
    assert split_think(raw) == ("", raw)
    assert split_think(raw, prefilled=True) == ("check", "answer")
    assert split_think("unfinished", prefilled=True) == ("unfinished", "")
    assert split_think("<think>unfinished") == ("unfinished", "")
    assert assistant_text({"reasoning_content": "check", "content": "answer"}, prefilled=True) == ("check", "answer")


def test_sdk_round_trip_and_sft_prefix_match(tmp_path, qwen_renderer):
    requests = []
    final = {"role": "assistant", "content": "Done.",
             "reasoning_content": "The evidence is sufficient."}

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test", "model": "explicit-student", "object": "chat.completion",
            "choices": [{"index": 0, "message": final, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        })

    model = ReasoningChatOpenAI(
        model="explicit-student", api_key="dummy", base_url="https://test.invalid/v1",
        use_responses_api=False, http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        request_log_path=tmp_path / "requests.jsonl",
    )
    system = SystemMessage(content="Solve with bash.")
    replacement = AssistantResponse(reasoning="Inspect the evidence.", tool_call=ExecuteCall(command="ls"))
    history = [HumanMessage(content="task"), replacement.to_message(candidate_id="r"),
               ToolMessage(content="file.txt", tool_call_id="review_r")]
    tools = [{"type": "function", "function": execute_tool_schema()}]
    result = model.bind_tools(tools).invoke([system, *history])
    assert result.content == "Done."
    assert result.additional_kwargs["reasoning_content"] == "The evidence is sufficient."
    assert requests[0]["messages"][2]["reasoning_content"] == "Inspect the evidence."
    assert json.loads((tmp_path / "requests.jsonl").read_text()) == requests[0]
    event = {
        "candidate_id": "last", "system_message": system.model_dump(),
        "context": [m.model_dump() for m in history],
        "accepted": AssistantResponse.from_message(result).model_dump(),
    }
    row = {"messages": conversation_messages(event)}
    actual_prefix = to_renderer_messages(qwen_renderer, requests[0]["messages"], tools)
    dataset_prefix = to_renderer_messages(qwen_renderer, row["messages"][:-1], tools)
    assert actual_prefix == dataset_prefix
    generation = qwen_renderer.build_generation_prompt(actual_prefix).to_ints()
    datums, _ = prepare_datums(rows=[row], renderer=qwen_renderer, max_length=10000)
    assert datums[0].model_input.to_ints()[:len(generation)] == generation


def test_prefilled_sdk_result_preserves_raw_and_finish_reason():
    model = ReasoningChatOpenAI(model="explicit", api_key="dummy", reasoning_prefill="think", use_responses_api=False)
    raw = "check\n</think>\n\nanswer"
    result = model._create_chat_result({"choices": [{"message": {"role": "assistant", "content": raw}, "finish_reason": "length"}]})
    message = result.generations[0].message
    assert message.content == "answer"
    assert message.additional_kwargs["reasoning_content"] == "check"
    assert message.response_metadata["raw_content"] == raw
    assert message.response_metadata["reasoning_prefill"] == "think"
    assert result.generations[0].generation_info["finish_reason"] == "length"


def test_images_tools_and_final_reasoning_survive_payload():
    model = ReasoningChatOpenAI(model="explicit", api_key="dummy", use_responses_api=False)
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
    payload = model._get_request_payload([
        HumanMessage(content=[{"type": "text", "text": "inspect"}, image]),
        AIMessage(content="Done.", additional_kwargs={"reasoning_content": "checked"}),
    ])
    assert payload["messages"][0]["content"][1] == image
    assert payload["messages"][1]["reasoning_content"] == "checked"
    assert payload["messages"][1]["content"] == "Done."


@pytest.mark.parametrize("thinking", ["The proposed command is malformed.", "检查证据，再执行。", "x"])
def test_reasoning_mask_targets_only_selected_turn(qwen_renderer, thinking):
    renderer = qwen_renderer
    assistant = {"role": "assistant", "content": [
        {"type": "thinking", "thinking": thinking},
        {"type": "text", "text": thinking + " Visible answer."},
    ]}
    messages = [{"role": "user", "content": thinking}, assistant,
                {"role": "tool", "content": thinking, "tool_call_id": "t"}, assistant]
    original, _ = prepare_datums(rows=[{"messages": messages}], renderer=renderer, max_length=10000)
    masked, stats = prepare_datums(rows=[{"messages": messages, "masked_reasoning_turns": [1]}], renderer=renderer, max_length=10000)
    before = original[-1].loss_fn_inputs["weights"].tolist()
    after = masked[-1].loss_fn_inputs["weights"].tolist()
    targets = masked[-1].loss_fn_inputs["target_tokens"].tolist()
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a > 0 and b == 0]
    needle = renderer.tokenizer.encode(thinking, add_special_tokens=False)
    assert [targets[i] for i in changed] == needle
    assert changed == list(range(changed[0], changed[0] + len(needle)))
    assert stats["masked_reasoning_tokens"] == len(needle)
    assert after[changed[-1] + 1] > 0  # newline after reasoning still trained
    assert all(a == 0 or b > 0 for i, (a, b) in enumerate(zip(before, after)) if i not in changed)
    assert sum(targets[i:i + len(needle)] == needle for i in range(changed[0])) >= 3


def test_bad_legacy_tags_and_invalid_masks_fail_before_training(qwen_renderer):
    row = {"messages": [{"role": "user", "content": "task"},
                        {"role": "assistant", "content": "check</think>answer"}]}
    with pytest.raises(ValueError, match="explicit reasoning-prefill"):
        prepare_datums(rows=[row], renderer=qwen_renderer, max_length=10000)
    row["messages"][-1]["content"] = "answer"
    with pytest.raises(ValueError, match="invalid masked_reasoning_turns"):
        prepare_datums(rows=[{**row, "masked_reasoning_turns": [4]}], renderer=qwen_renderer, max_length=10000)
    with pytest.raises(ValueError, match="diagnostic repair"):
        prepare_datums(rows=[{**row, "training_eligible": False}], renderer=qwen_renderer, max_length=10000)


def test_prefilled_assistants_are_context_only_and_real_reasoning_mask_has_no_offset_error(qwen_renderer):
    from tinker_cookbook.renderers import TrainOnWhat
    from tinker_cookbook.supervised.data import conversation_to_datum
    messages=[{'role':'user','content':'Example'}]
    for i in range(10):
        messages.extend([{'role':'assistant','content':[{'type':'thinking','thinking':'Repeated thinking'},
                           {'type':'text','text':f'Example {i}'}]}, {'role':'user','content':'Next'}])
    messages.extend([{'role':'user','content':'Actual task'},
        {'role':'assistant','content':[{'type':'thinking','thinking':'The proposed action needs correction.'},
                                     {'type':'text','text':'Visible final answer.'}]}])
    row={'messages':messages,'masked_assistant_turns':list(range(10)),'masked_reasoning_turns':[10]}
    original,_=prepare_datums(rows=[{'messages':messages}],renderer=qwen_renderer,max_length=10000)
    result,stats=prepare_datums(rows=[row],renderer=qwen_renderer,max_length=10000)
    assert result[0].model_input.to_ints()==original[0].model_input.to_ints()
    weights=result[0].loss_fn_inputs['weights'].tolist()
    rendered=to_renderer_messages(qwen_renderer,messages,[{'type':'function','function':execute_tool_schema()}])
    positions=[i for i,m in enumerate(rendered) if m['role']=='assistant']
    for position in positions[:10]:
        scoped=conversation_to_datum([{**m,'trainable':i==position} for i,m in enumerate(rendered)],
                                    qwen_renderer,None,train_on_what=TrainOnWhat.CUSTOMIZED)
        assert all(w==0 for w,s in zip(weights,scoped.loss_fn_inputs['weights'].tolist()) if s>0)
    tokens=result[0].loss_fn_inputs['target_tokens'].tolist()
    needle=qwen_renderer.tokenizer.encode('Visible final answer.',add_special_tokens=False)
    start=next(i for i in range(len(tokens)) if tokens[i:i+len(needle)]==needle)
    assert all(w>0 for w in weights[start:start+len(needle)])
    assert stats['masked_assistant_messages']==10 and stats['masked_reasoning_tokens']>0
    assert abs(sum(weights)-1)<1e-5
    for bad in [[11],[-1],[True]]:
        with pytest.raises(ValueError,match='invalid masked_assistant_turns'):
            prepare_datums(rows=[{**row,'masked_assistant_turns':bad}],renderer=qwen_renderer,max_length=10000)


def test_sandbox_normalization_keeps_tool_calls():
    message = {"role": "assistant", "content": "check</think>", "tool_calls": [{"id": "t"}]}
    normalized = normalize_assistant(message, prefilled=True)
    assert normalized["reasoning_content"] == "check"
    assert normalized["content"] is None
    assert normalized["tool_calls"] == message["tool_calls"]


def test_training_and_planning_import_without_rollout_runtime():
    import subprocess
    import sys

    code = """
import sys
import harness_zero.cli, harness_zero.train
for module in ('harness_zero.student', 'harness_zero.harness', 'harness_zero.teacher', 'deepagents', 'harbor', 'torch', 'tinker'):
    assert module not in sys.modules, module
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_framework_does_not_import_application():
    import ast
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "deepagents_harbor"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("harness_zero"), path


def test_async_sdk_retains_reasoning():
    import asyncio

    seen = []
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test", "model": "explicit", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "reasoning_content": "checked", "content": "Done."},
                         "finish_reason": "stop"}],
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ReasoningChatOpenAI(model="explicit", api_key="dummy", base_url="https://test.invalid/v1",
                                        http_async_client=client, use_responses_api=False)
            result = await model.ainvoke([HumanMessage(content="task")])
            assert result.additional_kwargs["reasoning_content"] == "checked"
            assert result.content == "Done."
    asyncio.run(run())
    assert seen[0].get("stream") is not True


def test_prefill_plan_wires_main_and_sandbox_clients(tmp_path):
    from harness_zero.harness import HarnessZeroMinisweAgent
    from harness_zero.rollout import build_rollout_plan

    plan = build_rollout_plan(
        repo_root=tmp_path, dataset=tmp_path, components_dir=tmp_path,
        teacher_middleware_factory=None, tasks=["task"], attempts=1, concurrency=1,
        student_model="openai:explicit", student_base_url="http://localhost:1234/v1",
        student_reasoning_effort="high", student_reasoning_enabled=None,
        student_reasoning_prefill="think", teacher_provider="passthrough",
        teacher_model="none", teacher_reasoning_effort="none", job_name="test", output_dir=tmp_path,
    )
    assert 'student_reasoning_prefill="think"' in plan["argv"]
    assert plan["student_reasoning_prefill"] == "think"
    agent = HarnessZeroMinisweAgent(logs_dir=tmp_path, model_name="openai:explicit",
                            model_kwargs={"base_url": "http://localhost:1234/v1", "api_key": "dummy", "reasoning_effort": "high"},
                            student_reasoning_prefill="think", teacher_model="none", teacher_provider="passthrough",
                            components_dir=str(tmp_path))
    assert agent._build_model().reasoning_prefill == "think"
    assert "--reasoning-prefill think" in agent._agent_wrapper()


def test_unknown_teacher_provider_never_falls_back():
    from harness_zero.teacher_models import build_teacher_model

    with pytest.raises(ValueError, match="unsupported teacher provider"):
        build_teacher_model(provider="typo", model="explicit", reasoning_effort="high")


def test_missing_prefill_setting_does_not_silently_collect_bad_tags():
    model = ReasoningChatOpenAI(model="explicit", api_key="dummy", use_responses_api=False)
    with pytest.raises(ValueError, match="student-reasoning-prefill think"):
        model._create_chat_result({"choices": [{"message": {"role": "assistant", "content": "check</think>answer"}}]})
