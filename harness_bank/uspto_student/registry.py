"""Registry for the uspto student-side harness bank.

The loader (`harness_zero.student_harness`) consumes this module by file path:
- `TOOL_FACTORIES`: name -> "module:callable" import spec; the callable takes
  the sandbox backend and returns a StructuredTool.
- `MIDDLEWARE_FACTORIES`: name -> "module:callable" import spec; the callable
  takes no arguments and returns an AgentMiddleware.
- `catalog_text()`: human-readable component listing for logs and audits.

Evolution adds components by dropping files into tools/ or middlewares/,
registering them here, and enabling them in manifest.json. Skills and memory
are enabled directly by manifest.json (skills/<name>/SKILL.md, memory.md).
"""

from __future__ import annotations

import json
from pathlib import Path

BANK_ROOT = Path(__file__).resolve().parent

TOOL_FACTORIES: dict[str, str] = {
    # Candidate-set generation: named-transform library enumeration — run
    # FIRST, before hand-deriving a disconnection.
    "propose_retrosynthesis": (
        "harness_bank.uspto_student.tools.propose_retrosynthesis:make_tool"
    ),
    # Candidate-set scoring (validity, canonical forms, atom budget, MCS
    # skeleton-drift coverage) — run BEFORE choosing a candidate.
    "smiles_check": "harness_bank.uspto_student.tools.smiles_check:make_tool",
    # Answer-file contract battery + canonical rewrite — the final gate.
    "verify_answer": "harness_bank.uspto_student.tools.verify_answer:make_tool",
}

MIDDLEWARE_FACTORIES: dict[str, str] = {
    # Finish gate: reject no-tool-call endings while /app/answer.txt is
    # missing/empty (sandbox-probed).
    "answer_guard": (
        "harness_bank.uspto_student.middlewares.answer_guard:make_middleware"
    ),
    # Finish gate: reject while the reactant set doesn't cover the product's
    # heavy atoms (sandbox RDKit) — catches the missing-co-reactant loss
    # (right substrate, no mCPBA/Boc2O/...).
    "completeness_guard": (
        "harness_bank.uspto_student.middlewares.completeness_guard:make_middleware"
    ),
}


def load_manifest() -> dict:
    return json.loads((BANK_ROOT / "manifest.json").read_text(encoding="utf-8"))


def catalog_text() -> str:
    manifest = load_manifest()
    lines = ["uspto_student harness bank", f"root: {BANK_ROOT}", ""]
    lines.append("skills: " + (", ".join(manifest.get("skills", [])) or "(none)"))
    lines.append("memory: " + ("enabled" if manifest.get("memory") else "disabled"))
    tools = manifest.get("tools", [])
    lines.append("tools: " + (", ".join(tools) or "(none)"))
    middlewares = manifest.get("middlewares", [])
    lines.append("middlewares: " + (", ".join(middlewares) or "(none)"))
    return "\n".join(lines)
