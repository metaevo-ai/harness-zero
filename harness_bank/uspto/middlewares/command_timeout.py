"""Teacher-side hint for unbounded execute commands (USPTO profile)."""

from __future__ import annotations

import re

from harness_zero.teacher_guidance import TeacherCandidateMiddleware, TeacherHint


class CommandTimeoutMiddleware(TeacherCandidateMiddleware):
    name = "teacher-command-timeout-guard"
    _timeout = re.compile(r"(^|[;&|]\s*)timeout\s")

    def hint(self, candidate):
        tool = candidate.original.tool_call
        if tool is None or self._timeout.search(tool.command):
            return None
        return TeacherHint(
            component_id="middleware:command-timeout",
            evidence=f"The execute command has no shell timeout: `{tool.command[:500]}`",
            instruction="Bound potentially blocking commands with a timeout while preserving useful diagnostics.",
        )
