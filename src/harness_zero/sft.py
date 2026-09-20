"""Build positive-only SFT conversations from successful reviewed trials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from harness_zero.dataset_utils import assert_student_only, conversation_messages, reasoning_leaks_review, trial_result


def build_sft_file(
    trial_dirs: Iterable[Path],
    output_path: Path,
    *,
    reward_threshold: float = 1.0,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    successful_trials = 0
    skipped_trials = 0
    leaked_trials = 0
    masked_reasoning_turns = 0
    for trial_dir in trial_dirs:
        events_path = trial_dir / "trial" / "trajectory.jsonl"
        reward, exception = trial_result(trial_dir)
        if reward is None or not events_path.is_file():
            skipped_trials += 1
            continue
        if reward < reward_threshold or exception is not None:
            skipped_trials += 1
            continue
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not events:
            skipped_trials += 1
            continue
        successful_trials += 1
        masked_turns = [
            index
            for index, item in enumerate(events)
            if item.get("review", {}).get("decision") == "REPLACE"
            and reasoning_leaks_review(item.get("accepted") or {})
        ]
        if masked_turns:
            leaked_trials += 1
            masked_reasoning_turns += len(masked_turns)
        event = events[-1]
        messages = conversation_messages(event)
        assert_student_only(messages)
        rows.append(
            {
                "task_id": event["task_id"],
                "trial": event["trial"],
                "turns": len(events),
                "masked_reasoning_turns": masked_turns,
                "replacements": sum(
                    item["review"]["decision"] == "REPLACE" for item in events
                ),
                "kind": "session",
                "messages": messages,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    stats = {
        "successful_trials": successful_trials,
        "skipped_trials": skipped_trials,
        "leaked_trials": leaked_trials,
        "masked_reasoning_turns": masked_reasoning_turns,
        "examples": len(rows),
    }
    return stats


def discover_trial_stores(root: Path) -> list[Path]:
    stores = {path.parent.parent for path in root.glob("**/trial/trajectory.jsonl")}
    return sorted(stores)
