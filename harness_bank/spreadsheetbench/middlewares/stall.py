"""Teacher-side hint for stalled spreadsheetbench students.

Observed failure mode (qwen 9B student): the candidate is an empty message
(often with a raw tool-call fragment stranded in the reasoning text, or an
intent declaration like "Let me ..." with no action). Passing it ends the
turn with nothing executed; three rounds of student-side evolution showed
these candidates should always become a concrete action instead.

The hint fires when the candidate has no tool call AND either empty visible
content or content that is pure intent-without-action. A non-empty final
summary (a real finish) does not trigger it.
"""

from __future__ import annotations

import re

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint


class StallMiddleware(TeacherCandidateMiddleware):
    name = "teacher-stall-guard"
    _intent = re.compile(r"(let me|i will|i'll|going to|next|step \d|then i)", re.I)

    def hint(self, candidate):
        proposal = candidate.original
        if proposal.tool_call is not None:
            return None
        if (
            proposal.content.strip()
            and not self._intent.search(proposal.content[-300:])
            and not proposal.content.rstrip().endswith(":")
        ):
            return None
        evidence = (
            "no tool call"
            if not proposal.content.strip()
            else f"intent without action: `{proposal.content[-300:]}`"
        )
        return TeacherHint(
            component_id="middleware:stall",
            evidence=evidence,
            instruction=(
                "An empty or intent-only turn wastes the student's 40-turn "
                "budget and can silently end the trial. REPLACE with the "
                "concrete next execute action now (the first action of a "
                "trial should explore the input workbook: list sheets, "
                "dimensions, headers, and any pre-filled cells in the "
                "answer range); PASS only a finish that carries a real "
                "visible summary of what was delivered and verified."
            ),
        )
