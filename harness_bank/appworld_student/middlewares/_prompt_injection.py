"""Shared helper for the Self-Harness-style prompt-instruction middlewares.

Mirrors `_build_prompt_middleware` in the published Self-Harness baseline
(github.com/qzzqzzb/Self-Harness, eval/harness_workspace/repo_baseline.py):
append a fixed instruction to the system message on every model call whose
message history satisfies a predicate. Message-level only — never touches the
sandbox backend, so it is safe inside the harbor event loop.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, ToolMessage

from deepagents.middleware._utils import append_to_system_message
from ._observations import current_task_messages


def has_no_tool_message(messages: Sequence[BaseMessage]) -> bool:
    """True before the first tool response (bootstrap predicate)."""
    return not any(isinstance(message, ToolMessage) for message in current_task_messages(messages))


def has_tool_error(messages: Sequence[BaseMessage]) -> bool:
    from ._observations import observation

    latest = next((message for message in reversed(current_task_messages(messages))
                   if isinstance(message, ToolMessage)), None)
    result = observation(latest)
    return result is not None and result["error"]


class PromptInstructionMiddleware(AgentMiddleware):
    """Append a fixed instruction to the system message when a predicate fires.

    Subclasses must set `name` as a class attribute (AgentMiddleware exposes
    `name` as a read-only property).
    """

    def __init__(
        self,
        *,
        instruction: str,
        predicate: Callable[[Sequence[BaseMessage]], bool] | None = None,
    ) -> None:
        super().__init__()
        self._instruction = instruction
        self._predicate = predicate

    def _modify(self, request: Any) -> Any:
        messages = list(getattr(request, "messages", ()) or ())
        if self._predicate is not None and not self._predicate(messages):
            return request
        if not self._instruction.strip():
            return request
        return request.override(
            system_message=append_to_system_message(
                getattr(request, "system_message", None), self._instruction
            )
        )

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._modify(request))

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return await handler(self._modify(request))
