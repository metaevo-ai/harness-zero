"""Only finite, successful verifier rewards may supervise the student."""

import json
from pathlib import Path

import pytest

from harness_zero.sft import build_sft_file


def write_trial(root: Path, reward: float, layout: str) -> Path:
    if layout == "local":
        store = root
        result = {"reward": reward}
        result_path = store / "trial" / "result.json"
    else:
        store = root / "agent" / "teacher"
        result = {"verifier_result": {"rewards": {"reward": reward}}}
        result_path = root / "result.json"
    events = store / "trial" / "trajectory.jsonl"
    events.parent.mkdir(parents=True)
    event = {
        "task_id": root.name,
        "trial": 0,
        "candidate_id": "final",
        "context": [{"type": "human", "content": "Complete the task."}],
        "review": {"decision": "PASS"},
        "accepted": {"content": "Done."},
    }
    events.write_text(json.dumps(event) + "\n", encoding="utf-8")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return store


@pytest.mark.parametrize("layout", ["local", "harbor"])
@pytest.mark.parametrize("reward", [float("nan"), float("inf"), -float("inf")])
def test_build_sft_skips_nonfinite_rewards_without_losing_valid_trials(
    tmp_path: Path, layout: str, reward: float
) -> None:
    invalid = write_trial(tmp_path / "invalid", reward, layout)
    valid = write_trial(tmp_path / "valid", 1.0, layout)
    output = tmp_path / "sft.jsonl"

    stats = build_sft_file([invalid, valid], output)

    rows = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["task_id"] for row in rows] == ["valid"]
    assert stats["successful_trials"] == 1
    assert stats["skipped_trials"] == 1
    assert stats["examples"] == 1


@pytest.mark.parametrize("layout", ["local", "harbor"])
def test_build_sft_preserves_finite_reward_threshold(
    tmp_path: Path, layout: str
) -> None:
    trials = [
        write_trial(tmp_path / name, reward, layout)
        for name, reward in [("below", 0.25), ("equal", 0.5), ("above", 0.75)]
    ]
    output = tmp_path / "sft.jsonl"
    stats = build_sft_file(trials, output, reward_threshold=0.5)
    rows = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["task_id"] for row in rows] == ["equal", "above"]
    assert stats["skipped_trials"] == 1


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -float("inf")])
def test_build_sft_rejects_nonfinite_threshold_before_writing(
    tmp_path: Path, threshold: float
) -> None:
    trial = write_trial(tmp_path / "failed", 0.0, "local")
    output = tmp_path / "sft.jsonl"
    output.write_text("existing dataset\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reward_threshold must be finite"):
        build_sft_file([trial], output, reward_threshold=threshold)
    assert output.read_text(encoding="utf-8") == "existing dataset\n"
