"""Teacher-side turn-budget reminder.

Observed failure mode: a correct deliverable already sits on disk, then
the student burns its remaining turns on no-op re-verification passes and
the trial dies at the 40-turn wall unsubmitted. The student-side bank
reminds the model directly at turns 25/35; on the teacher side this hint
tells the teacher to steer the student toward finishing instead.

Counts distinct candidate ids per trial and fires once at ~25 and ~35
candidates — advisory only, so a legitimately long task costs nothing but
one extra hint block in the teacher update.
"""

from __future__ import annotations

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint

_MARKS = (25, 35)


class TurnBudgetMiddleware(TeacherCandidateMiddleware):
    name = "teacher-turn-budget"

    def __init__(self) -> None:
        super().__init__()
        self._seen: list[str] = []
        self._fired: set[int] = set()

    def hint(self, candidate):
        candidate_id = getattr(candidate, "candidate_id", None)
        if candidate_id is not None and candidate_id not in self._seen:
            self._seen.append(candidate_id)
        count = len(self._seen)
        for mark in _MARKS:
            if count >= mark and mark not in self._fired:
                self._fired.add(mark)
                return TeacherHint(
                    component_id="middleware:turn-budget",
                    evidence=f"{count} reviewed turns — the 40-turn wall is approaching",
                    instruction=(
                        "Budget check for the student: if a non-empty deliverable "
                        "already exists under /app/output, steer the student to "
                        "reload-assert once and finish immediately — no more "
                        "re-verification passes that do not change the file. An "
                        "unsubmitted correct file scores the same 0 as no file."
                    ),
                )
        return None
