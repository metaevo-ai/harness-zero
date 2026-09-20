"""Registry for the spreadsheetbench student-side harness bank.

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

TOOL_FACTORIES: dict[str, str] = {}

MIDDLEWARE_FACTORIES: dict[str, str] = {
    "empty_turn_guard": "harness_bank.spreadsheetbench_student.middlewares.empty_turn_guard:make_middleware",
    "output_guard": "harness_bank.spreadsheetbench_student.middlewares.output_guard:make_middleware",
    "prefilled_guard": "harness_bank.spreadsheetbench_student.middlewares.prefilled_guard:make_middleware",
    "answer_range_guard": "harness_bank.spreadsheetbench_student.middlewares.answer_range_guard:make_middleware",
    "turn_budget": "harness_bank.spreadsheetbench_student.middlewares.turn_budget:make_middleware",
}


def load_manifest() -> dict:
    return json.loads((BANK_ROOT / "manifest.json").read_text(encoding="utf-8"))


def catalog_text() -> str:
    manifest = load_manifest()
    lines = ["spreadsheetbench_student harness bank", f"root: {BANK_ROOT}", ""]
    lines.append("skills: " + (", ".join(manifest.get("skills", [])) or "(none)"))
    lines.append("memory: " + ("enabled" if manifest.get("memory") else "disabled"))
    tools = manifest.get("tools", [])
    lines.append("tools: " + (", ".join(tools) or "(none)"))
    middlewares = manifest.get("middlewares", [])
    lines.append("middlewares: " + (", ".join(middlewares) or "(none)"))
    return "\n".join(lines)
