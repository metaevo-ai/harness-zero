"""Teacher-side hint for malformed student candidates (USPTO profile)."""

from __future__ import annotations

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint


class CandidateFormatMiddleware(TeacherCandidateMiddleware):
    name = "teacher-candidate-format-guard"

    def hint(self, candidate):
        if candidate.candidate_issue is None:
            return None
        return TeacherHint(
            component_id="middleware:candidate-format",
            evidence=candidate.candidate_issue,
            instruction=(
                "A PASS preserves this malformed response. Recover its useful intent and submit "
                "a complete replacement containing either one valid `execute` call or a visible "
                "final response."
            ),
        )
