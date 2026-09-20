"""Teacher-side hint for stalled students (USPTO profile)."""

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
        if proposal.content.strip() and not self._intent.search(proposal.content[-300:]) and not proposal.content.rstrip().endswith(":"):
            return None
        evidence = "no tool call" if not proposal.content.strip() else f"intent without action: `{proposal.content[-300:]}`"
        return TeacherHint(
            component_id="middleware:stall",
            evidence=evidence,
            instruction="If work remains, replace this turn with the concrete execute action now; pass only after verification.",
        )
