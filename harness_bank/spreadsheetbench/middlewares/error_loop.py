"""Teacher-side hint for repeated-error loops.

Observed failure mode: the student re-issues near-identical commands and
hits the same exception over and over — one trial burned a third of its
40-turn budget on a single ValueError whose fix sat in the skill text the
whole time. The student-side bank nudges mechanically; on the teacher side
the loop is directly visible in the trajectory, so this hint just surfaces
it at the moment it forms.

Scans the tail of the candidate context (tool results); when the same
normalized error signature appears 3+ times in the recent results, the
hint recommends replacing the next action with a docs-consulting step.
"""

from __future__ import annotations

import re
from typing import Any

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint

_ERROR_LINE = re.compile(
    r"(?:^|\n)\s*(?:[A-Za-z_][\w.]*?(?:Error|Exception|Interrupt|Timeout))\b[^\n]{0,80}"
)
_NORMALIZE = re.compile(r"(0x[0-9a-fA-F]+|\d+)")


def _result_text(message: dict[str, Any]) -> str:
    if message.get("type") not in {"tool", "function"}:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _signature(text: str) -> str | None:
    if "exit_code=0]" in text:
        return None
    matches = _ERROR_LINE.findall(text)
    if not matches:
        return None
    return _NORMALIZE.sub("#", matches[-1].strip()[:80])


class ErrorLoopMiddleware(TeacherCandidateMiddleware):
    name = "teacher-error-loop"

    def hint(self, candidate):
        try:
            results = [
                _result_text(m) for m in (candidate.context or [])[-10:] if isinstance(m, dict)
            ]
        except Exception:
            return None
        signatures = [s for s in (_signature(text) for text in results) if s]
        if len(signatures) < 3 or len(set(signatures[-4:])) != 1:
            return None
        sig = signatures[-1]
        return TeacherHint(
            component_id="middleware:error-loop",
            evidence=f"same error {len(signatures)}x in recent tool results: `{sig}`",
            instruction=(
                "The student is trial-and-error looping on one exception. REPLACE "
                "the next action with a docs step — inspect the relevant library "
                "source in the sandbox (e.g. `python3 -c \"import inspect, openpyxl.utils; "
                "print(inspect.getsource(...))\"`) or embed the documented fix "
                "directly in your replacement — then one changed attempt. "
                "Cosmetic edits to the same failing command do not converge."
            ),
        )
