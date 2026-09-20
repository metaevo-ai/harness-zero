"""Warn when the latest failed execution repeats an earlier error signature.

Signatures come from execution metadata and exception details, including API
validation bodies. Ordinary business text and successful JSON are not errors.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from deepagents.middleware._utils import append_to_system_message
from ._observations import current_task_messages

def _failure_marker(message: ToolMessage):
    from ._observations import observation

    result = observation(message)
    return result["signature"] if result is not None and result["error"] else None


class RetryLoopGuardMiddleware(AgentMiddleware):
    """Warn when the latest failure repeats an earlier failure signature."""

    name = "retry-loop-guard"

    def _modify(self, request: Any) -> Any:
        messages = current_task_messages(list(getattr(request, "messages", ()) or ()))
        latest_tool = next((message for message in reversed(messages)
                            if isinstance(message, ToolMessage)), None)
        if latest_tool is None or _failure_marker(latest_tool) is None:
            return request
        markers = [
            marker
            for message in messages
            if isinstance(message, ToolMessage)
            for marker in [_failure_marker(message)]
            if marker
        ]
        if not markers:
            return request
        latest = markers[-1]
        repeats = markers.count(latest)
        if repeats < 2:
            return request
        note = (
            f"Execution note: the SAME failure has now occurred {repeats} "
            "times (identical error signature). Re-issuing it with small "
            "variations will keep failing. Stop and change the approach: "
            "read the error text for the exact contract it names, check "
            "the endpoint's documentation, or run a smaller probe to see "
            "the real state before acting again."
        )
        return request.override(
            system_message=append_to_system_message(
                getattr(request, "system_message", None), note
            )
        )

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._modify(request))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return await handler(self._modify(request))


def make_middleware() -> RetryLoopGuardMiddleware:
    return RetryLoopGuardMiddleware()
