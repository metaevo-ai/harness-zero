"""Empty-turn guard — never finish on an empty assistant message.

Observed failure mode (SpreadsheetBench rollout, qwen student): the model's
final response is an empty content string with no tool calls — often with
a raw `<tool_call>` fragment stranded in the reasoning text — and the
react loop treats "no tool calls" as the final answer. The trial ends
silently: half-finished scripts, output never written, automatic 0.

This middleware hooks `after_model` (message level only, no sandbox): when
the latest AIMessage has no tool calls AND empty/whitespace content, the
finish is rejected with a nudge back to the model. Bounded by MAX_NUDGES
so a genuinely-stuck agent is never trapped; once the budget is spent the
finish goes through.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): your last message was empty — no "
    "tool call and no final text. This ends the task with whatever is on "
    "disk right now, finished or not. Common causes: you wrote a tool call "
    "for a tool that does not exist (`answer`/`edit_file`/`write_file` — "
    "the ONLY tool available is `execute`), or you put your final summary "
    "in the reasoning channel instead of the visible content. Either issue "
    "the next `execute` call to continue working, or write a real final "
    "summary of what you delivered and verified as visible content."
)


class EmptyTurnGuardMiddleware(AgentMiddleware):
    """Reject agent finish on an empty no-tool-call assistant message."""

    MAX_NUDGES = 3

    def __init__(self, logger: logging.Logger | None = None) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger("empty-turn-guard")
        self._nudges = 0

    def _check(self, state) -> dict[str, Any] | None:
        if self._nudges >= self.MAX_NUDGES:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        content = last.content if isinstance(last.content, str) else ""
        if content.strip():
            return None
        self._nudges += 1
        self._logger.warning(
            "[empty-turn-guard] empty finish attempt — rejecting (%d/%d)",
            self._nudges, self.MAX_NUDGES,
        )
        return {
            "messages": [
                HumanMessage(
                    content=_REJECT.format(k=self._nudges, cap=self.MAX_NUDGES),
                    name="empty-turn-guard",
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        return self._check(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        return self._check(state)


def make_middleware() -> EmptyTurnGuardMiddleware:
    """Student-harness loader factory."""
    return EmptyTurnGuardMiddleware()
