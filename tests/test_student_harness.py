"""Student harness bank: loading, sandbox upload mapping, and miniswe assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_zero.review import ReviewSubmission
from harness_zero.store import TrialStore
from harness_zero.student import HarnessReviewMiddleware, StudentRequestCaptureMiddleware, build_student_middleware
from harness_zero.student_harness import (
    SANDBOX_MEMORY_PATH,
    SANDBOX_SKILLS_DIR,
    hash_student_harness_bank,
    iter_upload_files,
    load_student_harness,
)
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware

REPO = Path(__file__).resolve().parent.parent
SEED_BANK = REPO / "harness_bank" / "spreadsheetbench_student"
TEACHER_BANK = REPO / "harness_bank" / "spreadsheetbench"


class _PassthroughReviewer:
    async def review(self, candidate):
        return ReviewSubmission(
            candidate_id=candidate.candidate_id, decision="PASS", reason="test"
        )


def _write_minimal_bank(root: Path, manifest: dict) -> Path:
    bank = root / "bank"
    (bank / "skills" / "demo").mkdir(parents=True)
    (bank / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    (bank / "memory.md").write_text("# Memory\n", encoding="utf-8")
    (bank / "registry.py").write_text(
        "TOOL_FACTORIES = {}\nMIDDLEWARE_FACTORIES = {}\n", encoding="utf-8"
    )
    (bank / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bank


def test_hash_bank_deterministic_and_content_sensitive(tmp_path):
    bank = _write_minimal_bank(tmp_path, {"skills": ["demo"], "memory": True})
    first = hash_student_harness_bank(bank)
    assert first == hash_student_harness_bank(bank)
    (bank / "memory.md").write_text("# Memory\n\nchanged\n", encoding="utf-8")
    assert hash_student_harness_bank(bank) != first


def test_hash_bank_missing_dir(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        hash_student_harness_bank(tmp_path / "nope")


def test_load_seed_bank():
    spec = load_student_harness(SEED_BANK)
    assert spec.skills_enabled == ("spreadsheet-manipulation",)
    assert spec.memory_enabled is True
    assert spec.tool_factories == ()
    assert len(spec.middleware_factories) == 5
    assert spec.bank_sha256.startswith("sha256:")


def test_load_rejects_unregistered_component(tmp_path):
    bank = _write_minimal_bank(
        tmp_path, {"skills": ["demo"], "memory": True, "tools": ["ghost"]}
    )
    with pytest.raises(ValueError, match="unregistered tool"):
        load_student_harness(bank)


def test_load_rejects_missing_skill(tmp_path):
    bank = _write_minimal_bank(tmp_path, {"skills": ["nope"], "memory": False})
    with pytest.raises(ValueError, match="missing skill"):
        load_student_harness(bank)


def test_iter_upload_files_maps_to_sandbox_paths():
    spec = load_student_harness(SEED_BANK)
    uploads = dict(iter_upload_files(spec))
    targets = set(uploads.values())
    assert f"{SANDBOX_SKILLS_DIR}/spreadsheet-manipulation/SKILL.md" in targets
    assert uploads[SEED_BANK / "memory.md"] == SANDBOX_MEMORY_PATH


def test_seed_bank_skill_frontmatter_parses():
    from deepagents.middleware.skills import _parse_skill_metadata

    for skill_dir in (SEED_BANK / "skills").iterdir():
        if not skill_dir.is_dir():
            continue
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        meta = _parse_skill_metadata(
            content, str(skill_dir / "SKILL.md"), skill_dir.name
        )
        assert meta is not None, f"SKILL.md frontmatter failed to parse: {skill_dir}"
        assert meta["name"] == skill_dir.name
        assert meta["description"]


def _build_middleware(tmp_path, spec):
    store = TrialStore(
        tmp_path / "teacher",
        task_id="t",
        trial="0",
        components_dir=TEACHER_BANK,
    )
    return build_student_middleware(
        reviewer=_PassthroughReviewer(),
        store=store,
        backend=object(),  # never invoked at assembly time
        student_harness=spec,
    )


def test_assembly_bare_unchanged(tmp_path):
    middleware = _build_middleware(tmp_path, None)
    assert len(middleware) == 1
    assert isinstance(middleware[0], HarnessReviewMiddleware)


def test_assembly_with_harness_orders_injection_before_review(tmp_path):
    spec = load_student_harness(SEED_BANK)
    middleware = _build_middleware(tmp_path, spec)
    kinds = [type(m) for m in middleware]
    from harness_bank.spreadsheetbench_student.middlewares.empty_turn_guard import (
        EmptyTurnGuardMiddleware,
    )
    from harness_bank.spreadsheetbench_student.middlewares.output_guard import (
        OutputGuardMiddleware,
    )
    from harness_bank.spreadsheetbench_student.middlewares.prefilled_guard import (
        PrefilledGuardMiddleware,
    )
    from harness_bank.spreadsheetbench_student.middlewares.answer_range_guard import (
        AnswerRangeGuardMiddleware,
    )
    from harness_bank.spreadsheetbench_student.middlewares.turn_budget import (
        TurnBudgetMiddleware,
    )

    assert kinds == [
        SkillsMiddleware,
        MemoryMiddleware,
        HarnessReviewMiddleware,
        EmptyTurnGuardMiddleware,
        OutputGuardMiddleware,
        PrefilledGuardMiddleware,
        AnswerRangeGuardMiddleware,
        TurnBudgetMiddleware,
        StudentRequestCaptureMiddleware,
    ]
