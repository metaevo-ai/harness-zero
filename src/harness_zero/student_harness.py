"""Load a student-side harness bank into the miniswe assembly.

A student harness bank (`harness_bank/<domain>_student/`) contributes, per its
manifest.json and registry.py:

- prebuilt tools appended after `execute` (real StructuredTools);
- student-side middlewares appended after the review middleware;
- skills mounted into the sandbox and exposed through deepagents'
  SkillsMiddleware (progressive disclosure: index in system prompt, full text
  read via `execute cat`);
- memory.md primed into the system prompt through deepagents' MemoryMiddleware.

Bank bytes are hashed into `StudentHarnessSpec.bank_sha256`; the rollout agent
writes that hash into its log directory (`student_harness_bank.txt`) purely for
provenance — nothing asserts on it. Loading nothing (student_harness_dir=None)
keeps the bare miniswe behavior.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SANDBOX_HARNESS_DIR = "/opt/ahd/harness"
SANDBOX_SKILLS_DIR = f"{SANDBOX_HARNESS_DIR}/skills"
SANDBOX_MEMORY_PATH = f"{SANDBOX_HARNESS_DIR}/memory.md"


@dataclass(frozen=True)
class StudentHarnessSpec:
    """Resolved, importable form of a student harness bank."""

    bank_dir: Path
    bank_sha256: str
    tool_factories: tuple[Callable[..., Any], ...]
    middleware_factories: tuple[Callable[[], Any], ...]
    skills_enabled: tuple[str, ...]
    memory_enabled: bool


def hash_student_harness_bank(bank_dir: Path) -> str:
    """sha256 over every file in the bank (relative path + content bytes)."""
    if not bank_dir.is_dir():
        raise ValueError(f"student harness bank does not exist: {bank_dir}")
    digest = hashlib.sha256()
    files = sorted(p for p in bank_dir.rglob("*") if p.is_file())
    if not files:
        raise ValueError(f"student harness bank is empty: {bank_dir}")
    for path in files:
        rel = path.relative_to(bank_dir).as_posix()
        if "__pycache__" in rel:
            continue
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _import_registry(bank_dir: Path) -> Any:
    registry_path = bank_dir / "registry.py"
    if not registry_path.is_file():
        raise ValueError(f"student harness bank lacks registry.py: {bank_dir}")
    spec = importlib.util.spec_from_file_location(
        f"hz_student_harness_{bank_dir.name}", registry_path
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import registry.py from {bank_dir}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_factory(spec: str, *, kind: str, name: str) -> Callable:
    module_name, separator, func_name = spec.partition(":")
    if not separator or not module_name or not func_name:
        raise ValueError(f"{kind} factory '{name}' must use 'module:function', got {spec!r}")
    module = importlib.import_module(module_name)
    factory = getattr(module, func_name, None)
    if not callable(factory):
        raise ValueError(f"{kind} factory '{name}' not callable: {spec}")
    return factory


def load_student_harness(bank_dir: Path) -> StudentHarnessSpec:
    """Resolve a bank directory into a StudentHarnessSpec, validating references."""
    bank_dir = bank_dir.resolve()
    bank_hash = hash_student_harness_bank(bank_dir)
    registry = _import_registry(bank_dir)
    manifest = json.loads((bank_dir / "manifest.json").read_text(encoding="utf-8"))

    tool_factories = {}
    for name in manifest.get("tools", []):
        spec = registry.TOOL_FACTORIES.get(name)
        if spec is None:
            raise ValueError(f"manifest lists unregistered tool: {name}")
        tool_factories[name] = _resolve_factory(spec, kind="tool", name=name)
    middleware_factories = {}
    for name in manifest.get("middlewares", []):
        spec = registry.MIDDLEWARE_FACTORIES.get(name)
        if spec is None:
            raise ValueError(f"manifest lists unregistered middleware: {name}")
        middleware_factories[name] = _resolve_factory(spec, kind="middleware", name=name)

    skills = tuple(manifest.get("skills", []))
    for name in skills:
        skill_path = bank_dir / "skills" / name / "SKILL.md"
        if not skill_path.is_file():
            raise ValueError(f"manifest lists missing skill: {skill_path}")
    memory_enabled = bool(manifest.get("memory"))
    if memory_enabled and not (bank_dir / "memory.md").is_file():
        raise ValueError(f"manifest enables memory but {bank_dir}/memory.md is missing")

    return StudentHarnessSpec(
        bank_dir=bank_dir,
        bank_sha256=bank_hash,
        tool_factories=tuple(tool_factories[name] for name in manifest.get("tools", [])),
        middleware_factories=tuple(
            middleware_factories[name] for name in manifest.get("middlewares", [])
        ),
        skills_enabled=skills,
        memory_enabled=memory_enabled,
    )


def iter_upload_files(spec: StudentHarnessSpec) -> list[tuple[Path, str]]:
    """(local file, sandbox path) pairs to upload before the agent runs."""
    uploads: list[tuple[Path, str]] = []
    for name in spec.skills_enabled:
        skill_dir = spec.bank_dir / "skills" / name
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(spec.bank_dir / "skills").as_posix()
                uploads.append((path, f"{SANDBOX_SKILLS_DIR}/{rel}"))
    if spec.memory_enabled:
        uploads.append((spec.bank_dir / "memory.md", SANDBOX_MEMORY_PATH))
    return uploads
