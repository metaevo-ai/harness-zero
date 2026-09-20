"""Append-only local artifacts for supervised student trajectories."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from harness_zero.review import AssistantResponse, ReviewCandidate, ReviewSubmission


class TrialStore:
    def __init__(
        self,
        root: Path,
        *,
        task_id: str,
        trial: int | str,
        components_dir: Path,
    ) -> None:
        self.root = root
        self.task_id = task_id
        self.trial = trial
        self.root.mkdir(parents=True, exist_ok=False)
        self._copy_components(components_dir)
        (self.root / "trial").mkdir()

    def _copy_components(self, components_dir: Path) -> None:
        """Copy only the components the teacher can see: index.json plus the
        files it lists. Middleware code, profiles, and caches are runtime
        details, not teacher-facing components."""
        index_path = components_dir / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        dest = self.root / "components"
        dest.mkdir()
        shutil.copy2(index_path, dest / "index.json")
        domain_prompt = components_dir / "teacher_prompt.md"
        if domain_prompt.is_file():
            shutil.copy2(domain_prompt, dest / "teacher_prompt.md")
        for component in index.get("components", []):
            rel = component["path"]
            src = components_dir / rel
            if not src.is_file():
                raise ValueError(f"index.json lists a missing component: {rel}")
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ValueError(f"index.json component path escapes the bank: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)

    @property
    def candidate_path(self) -> Path:
        return self.root / "trial" / "candidate.json"

    @property
    def events_path(self) -> Path:
        return self.root / "trial" / "trajectory.jsonl"

    @property
    def result_path(self) -> Path:
        return self.root / "trial" / "result.json"

    def write_candidate(self, candidate: ReviewCandidate) -> None:
        self.candidate_path.write_text(
            json.dumps(candidate.to_json_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_review(
        self,
        candidate: ReviewCandidate,
        submission: ReviewSubmission,
        accepted: AssistantResponse | None,
    ) -> None:
        event = {
            "type": "reviewed_turn",
            "task_id": self.task_id,
            "trial": self.trial,
            **candidate.to_json_dict(),
            "review": submission.model_dump(mode="json"),
            "accepted": accepted.model_dump(mode="json") if accepted is not None else None,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def finalize(self, *, reward: float, metadata: dict[str, Any] | None = None) -> None:
        self.result_path.write_text(
            json.dumps(
                {"task_id": self.task_id, "trial": self.trial, "reward": reward,
                 "metadata": metadata or {}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
