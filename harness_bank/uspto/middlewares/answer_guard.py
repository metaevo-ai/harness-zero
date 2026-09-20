"""Teacher-side answer-file guard for USPTO retrosynthesis.

Fires when the student is about to finish (a final response with no execute
call): the cheapest automatic zero on this task family is burning the trial
on analysis and never writing /app/answer.txt — the instruction's contract
scores a missing or empty answer file 0 regardless of the reasoning. The
teacher cannot probe the sandbox, so the hint names the evidence a PASS
requires instead: the file written, read back, and verified after its last
write.

Mirrors the student-side answer guard that probed the sandbox in an
after_model hook (archive: harness_bank/.legacy/uspto/middlewares/).
"""

from __future__ import annotations

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint


class TeacherAnswerGuardMiddleware(TeacherCandidateMiddleware):
    """Warn the teacher when the student finishes without an execute call."""

    name = "teacher-answer-guard"

    def hint(self, candidate) -> TeacherHint | None:
        if candidate.original.tool_call is not None or not candidate.original.content.strip():
            return None
        return TeacherHint(
            component_id="middleware:answer-file",
            evidence="The candidate is a final response without an execute call.",
            instruction=(
                "PASS only when fresh student-visible evidence shows /app/answer.txt exists, "
                "holds exactly one dot-separated SMILES line (no prose, no empty fragments, no "
                "atom-mapping numbers), was read back, and passed the mechanical contract "
                "battery AFTER its last write. A missing or empty answer file scores 0 "
                "regardless of the reasoning."
            ),
        )
