"""Teacher-side canonical-form guard for USPTO retrosynthesis.

Fires when the student's proposed command writes /app/answer.txt directly
(echo/printf/cat/heredoc/tee/cp/mv/sed -i). The grader is an exact string
SET match with NO chemical canonicalization and the dataset references are
RDKit-canonical, so a hand-typed spelling scores 0 even when the chemistry
is right — the file's last write must come from the canonicalizing
verification script, and the battery must re-run after any write.
"""

from __future__ import annotations

import re

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint

_ANSWER_WRITE = re.compile(
    r">{1,2}\s*(?:/app/)?answer\.txt"
    r"|\btee\b[^|;&]*(?:/app/)?answer\.txt"
    r"|\bsed\b[^|;&]*-i[^|;&]*(?:/app/)?answer\.txt"
    r"|\b(?:cp|mv)\b[^|;&]*(?:/app/)?answer\.txt"
)


class TeacherCanonicalFormGuardMiddleware(TeacherCandidateMiddleware):
    """Warn the teacher when the student hand-writes the answer file."""

    name = "teacher-canonical-form-guard"

    def hint(self, candidate) -> TeacherHint | None:
        tool_call = candidate.original.tool_call
        if tool_call is None or not _ANSWER_WRITE.search(tool_call.command):
            return None
        return TeacherHint(
            component_id="middleware:canonical-form",
            evidence=(
                "The proposed command writes /app/answer.txt directly: "
                f"`{tool_call.command[:300]}`"
            ),
            instruction=(
                "The grader string-matches exactly with no canonicalization, and the reference "
                "SMILES are RDKit-canonical — a hand-typed spelling scores 0 even when the "
                "chemistry is right. Pass only when the trajectory shows the content came from "
                "tool-canonicalized output and the mechanical battery re-runs after the write; "
                "otherwise replace with the verification-script step so the canonical rewrite "
                "is the file's last write."
            ),
        )
