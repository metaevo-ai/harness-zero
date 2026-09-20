"""Teacher-side candidate-enumeration guard for USPTO retrosynthesis.

Fires on the same direct answer-file writes as the canonical-form guard:
a hand-written /app/answer.txt is the observable signature of the dominant
failure on this task family — committing to the first in-head disconnection
without mechanical enumeration or filtering.
"""

from __future__ import annotations

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint

from harness_bank.uspto.middlewares.canonical_form_guard import _ANSWER_WRITE


class TeacherEnumerationGuardMiddleware(TeacherCandidateMiddleware):
    """Warn the teacher when an answer write may be a first-guess commit."""

    name = "teacher-enumeration-guard"

    def hint(self, candidate) -> TeacherHint | None:
        tool_call = candidate.original.tool_call
        if tool_call is None or not _ANSWER_WRITE.search(tool_call.command):
            return None
        return TeacherHint(
            component_id="middleware:enumeration",
            evidence=(
                "The proposed command writes an answer set directly: "
                f"`{tool_call.command[:300]}`"
            ),
            instruction=(
                "Check whether the candidate follows a parsed product graph and a chemically "
                "explained site/family choice. If competing families remain plausible, compare "
                "them before varying halides or protecting groups. Parsing, MCS and atom deltas "
                "validate representation; they do not identify the recorded reaction. Do not "
                "force three candidates, minimum-atom ranking or unobserved dataset frequencies. "
                "PASS a justified write; REPLACE only a material unsupported choice or graph error."
            ),
        )
