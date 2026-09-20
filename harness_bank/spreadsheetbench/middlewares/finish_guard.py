"""Teacher-side finish guard — delivery evidence must exist before a PASS.

Observed failure mode (three student-side evolution rounds): the student
declares done while the trajectory lacks the checks that actually score —
no written workbook at `output_path`, no input↔output diff over the
pre-filled example cells, no assertion-style verification of the answer
range. Student-side these became mechanical middleware rejections; on the
teacher side the sandbox is unreachable, so the check looks for the
evidence in the student-visible trajectory instead.

Fires only on finish candidates (no tool call, non-empty visible content).
Scans the candidate's own commands and messages (assistant entries only —
tool results are excluded on purpose: a checklist the student merely READ
is not evidence the student RAN the check) for three evidence classes and
reports the missing ones; when in doubt it stays silent (fail-open) — the
teacher makes the final call from the full trajectory.
"""

from __future__ import annotations

import re
from typing import Any

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint

_EXAMPLE_PROOF = re.compile(
    r"(pre-?filled|prefilled|worked example|byte-?identical|replay|prefilled_diff)", re.I
)
_ASSERT_PROOF = re.compile(r"\bassert\b", re.I)


def _message_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
    for call in message.get("tool_calls") or []:
        args = call.get("args") or {}
        if isinstance(args.get("command"), str):
            parts.append(args["command"])
    return "\n".join(parts)


class FinishGuardMiddleware(TeacherCandidateMiddleware):
    name = "teacher-finish-guard"

    def hint(self, candidate):
        proposal = candidate.original
        if proposal.tool_call is not None or not proposal.content.strip():
            return None
        try:
            transcript = "\n".join(
                _message_text(m)
                for m in (candidate.context or [])
                if isinstance(m, dict) and m.get("type") == "ai"
            )
        except Exception:
            return None
        if not transcript.strip():
            return None
        missing = []
        if "/app/output/" not in transcript:
            missing.append(
                "no write under `/app/output/` appears — a missing deliverable scores 0"
            )
        if not _EXAMPLE_PROOF.search(transcript):
            missing.append(
                "no input↔output comparison over the pre-filled example cells "
                "(deploy `tool:prefilled_diff` or an equivalent diff)"
            )
        if not _ASSERT_PROOF.search(transcript):
            missing.append(
                "no assertion-style verification of the answer range "
                "(deploy `tool:answer_range_check` or assert cells against recomputation)"
            )
        if not missing:
            return None
        return TeacherHint(
            component_id="middleware:finish-guard",
            evidence="finish attempt with missing delivery evidence: " + "; ".join(missing),
            instruction=(
                "Before PASSing this finish, confirm the missing checks actually "
                "happened in the trajectory. If they did not, REPLACE with the "
                "corresponding verification action now — write the deliverable / "
                "run the diff / assert the answer cells — and let the student "
                "finish only after the evidence exists."
            ),
        )
