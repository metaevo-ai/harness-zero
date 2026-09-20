"""Registry for the AppWorld student harness.

The environment itself lives in envs/appworld and also supports bare miniswe.
The bank uses only execute with the CLI; skills, memory and middleware supply
workflow guidance. No component is responsible for grading isolation.
"""

from __future__ import annotations

import json
import hashlib
import sys
import types
from pathlib import Path

BANK_ROOT = Path(__file__).resolve().parent

# Resolve factories from this bank, including archived snapshots, not the live bank.
digest = hashlib.sha256()
for path in sorted(BANK_ROOT.rglob('*')):
    if path.is_file() and '__pycache__' not in path.parts:
        digest.update(path.relative_to(BANK_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
PACKAGE = '_appworld_bank_' + digest.hexdigest()[:16] + '_' + hashlib.sha256(str(BANK_ROOT).encode()).hexdigest()[:8]
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(BANK_ROOT)]
    sys.modules[PACKAGE] = package

TOOL_FACTORIES: dict[str, str] = {}

MIDDLEWARE_FACTORIES: dict[str, str] = {
    "bootstrap_instruction": f"{PACKAGE}.middlewares.bootstrap_instruction:make_middleware",
    "failure_recovery_instruction": f"{PACKAGE}.middlewares.failure_recovery_instruction:make_middleware",
    "retry_loop_guard": f"{PACKAGE}.middlewares.retry_loop_guard:make_middleware",
    "context_guard": f"{PACKAGE}.middlewares.context_guard:make_middleware",
    "submit_gate": f"{PACKAGE}.middlewares.submit_gate:make_middleware",
}


def load_manifest() -> dict:
    return json.loads((BANK_ROOT / "manifest.json").read_text(encoding="utf-8"))


def catalog_text() -> str:
    manifest = load_manifest()
    lines = ["appworld_student harness bank", f"root: {BANK_ROOT}", ""]
    lines.append("skills: " + (", ".join(manifest.get("skills", [])) or "(none)"))
    lines.append("memory: " + ("enabled" if manifest.get("memory") else "disabled"))
    tools = manifest.get("tools", [])
    lines.append("tools: " + (", ".join(tools) or "(none)"))
    middlewares = manifest.get("middlewares", [])
    lines.append("middlewares: " + (", ".join(middlewares) or "(none)"))
    return "\n".join(lines)
