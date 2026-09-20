"""Evaluate only trusted sidecar artifacts in a new verifier container."""

import json
import shutil
import sys
from pathlib import Path

from appworld.evaluator import evaluate_task

task_id = sys.argv[1]
artifact = Path("/export/appworld")
detail = {"success": False, "pass_count": 0, "num_tests": None, "note": None}
try:
    metadata = json.loads((artifact / "metadata.json").read_text())
    if metadata["task_id"] != task_id:
        raise ValueError("World snapshot task does not match verifier task")
    task_root = Path("/opt/appworld/data/tasks") / task_id
    shutil.copytree("/tests/ground_truth", task_root / "ground_truth")
    destination = Path("/opt/appworld/experiments/outputs/rollout/tasks") / task_id / "dbs"
    shutil.copytree(artifact / "dbs", destination)
    tracker = evaluate_task(task_id=task_id, experiment_name="rollout", suppress_errors=True)
    detail.update(success=bool(tracker.success), pass_count=tracker.pass_count, num_tests=tracker.num_tests)
except Exception as exc:
    # A missing/corrupt snapshot or evaluation failure earns zero, never a stale reward.
    detail["note"] = f"{type(exc).__name__}: {exc}"[:500]
logs = Path("/logs/verifier")
logs.mkdir(parents=True, exist_ok=True)
(logs / "resolution.json").write_text(json.dumps(detail, indent=2))
(logs / "reward.txt").write_text("1.0" if detail["success"] else "0.0")
print(json.dumps(detail))
