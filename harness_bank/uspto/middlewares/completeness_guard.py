"""Teacher-side completeness guard for USPTO retrosynthesis.

Fires when the student is about to finish (a final response with no execute
call): a classic silent loss is a reactant set that does not cover the
product's heavy atoms — the gap names the missing co-reactant (oxidation O
from an oxidant such as mCPBA, a Boc group from Boc2O, a halogen from the
halogenating reagent, an acetyl from the anhydride). Species contributing
no product atoms (H2, hydrides, acids/bases, solvents) are usually NOT
listed, so the check runs on heavy atoms only. The teacher cannot run the
RDKit coverage check itself, so the hint names the evidence a PASS
requires.

Mirrors the student-side completeness guard that ran the RDKit atom-budget
probe in the sandbox (archive: harness_bank/.legacy/uspto/middlewares/).
"""

from __future__ import annotations

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint


class TeacherCompletenessGuardMiddleware(TeacherCandidateMiddleware):
    """Warn the teacher about finishes whose reactant set may miss co-reactants.

    The teacher cannot run the RDKit coverage check, so the hint names the
    evidence a PASS requires: every product heavy atom accounted for,
    recorded co-reactants included.
    """

    name = "teacher-completeness-guard"

    def hint(self, candidate) -> TeacherHint | None:
        if candidate.original.tool_call is not None or not candidate.original.content.strip():
            return None
        return TeacherHint(
            component_id="middleware:completeness",
            evidence="The candidate is a final response without an execute call.",
            instruction=(
                "Check for concrete lost scaffold atoms, invalid fragments, or a missing "
                "chemically necessary partner using visible evidence. Atom-count/MCS flags "
                "are diagnostic, not proof of reaction identity or reference membership. "
                "A recorded reactant list need not be a fully balanced equation: do not "
                "automatically add water, redox reagents or counterions to satisfy a count. "
                "PASS when the choice and saved output are adequately supported; REPLACE "
                "only a concrete omission, not an uncertain representation convention."
            ),
        )
