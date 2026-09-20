"""Output guard — never finish with a missing/empty /app/output directory.

Observed failure mode (SpreadsheetBench rollout): the agent reasons through
the task, sometimes even writes analysis code, but finishes without any
xlsx landing in /app/output/ — the instruction's delivery contract makes
that an automatic 0 ("Output file not found"), and nothing in the loop
noticed.

This middleware hooks `after_model`: when the latest AIMessage has no tool
calls (the agent is about to finish), it checks IN THE SANDBOX that at
least one non-empty .xlsx exists under /app/output/. If not, the finish is
rejected with a nudge back to the model. Bounded by MAX_NUDGES so a
genuinely-stuck agent is never trapped; once the budget is spent the
finish goes through (a hard-blocked agent scores the same 0 but burns the
whole trial).

Async hooks probe the sandbox ONLY via `await backend.aexecute(...)` — the
sync `execute(...)` blocks on run_coroutine_threadsafe(...).result() and
deadlocks the event loop when called from the loop thread.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from deepagents_harbor.current import current_backend

_PROBE = (
    'if [ ! -d /app/output ]; then echo MISSING; '
    'elif ! ls /app/output/*.xlsx >/dev/null 2>&1; then echo EMPTY; '
    'else for f in /app/output/*.xlsx; do [ -s "$f" ] && echo HAS_OUTPUT && exit 0; done; echo EMPTY; fi'
)

_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): no non-empty .xlsx found in "
    "/app/output/ ({why}). A missing deliverable scores 0 regardless of "
    "your analysis. Write the required output workbook there NOW — the "
    "input workbook edited in place and saved to the instructed output "
    "path — then reload it, verify the answer cells, and only then finish."
)


class OutputGuardMiddleware(AgentMiddleware):
    """Reject agent finish while /app/output/ has no non-empty xlsx."""

    MAX_NUDGES = 2

    def __init__(self, logger: logging.Logger | None = None) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger("output-guard")
        self._nudges = 0

    async def _output_state(self) -> str:
        try:
            r = await current_backend.get().aexecute(_PROBE, timeout=30)
        except Exception as e:  # backend hiccup must not block finishing
            self._logger.warning("[output-guard] probe failed, allowing finish: %s", e)
            return "HAS_OUTPUT"
        return (r.output or "").strip().splitlines()[0] if r.output else "HAS_OUTPUT"

    def _check(self, state, status: str) -> dict[str, Any] | None:
        if self._nudges >= self.MAX_NUDGES or status == "HAS_OUTPUT":
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        self._nudges += 1
        self._logger.warning(
            "[output-guard] finish attempt with /app/output %s — rejecting (%d/%d)",
            status, self._nudges, self.MAX_NUDGES,
        )
        return {
            "messages": [
                HumanMessage(
                    content=_REJECT.format(
                        k=self._nudges, cap=self.MAX_NUDGES, why=status
                    ),
                    name="output-guard",
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        # Sync path must not probe the sandbox (deadlock); allow finish here
        # and let the async hook do the real check in async runs.
        return None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        status = await self._output_state()
        return self._check(state, status)


def make_middleware() -> OutputGuardMiddleware:
    """Student-harness loader factory."""
    return OutputGuardMiddleware()
