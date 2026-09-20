"""Bound tool observations before they enter message history or model requests."""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from ._observations import observation

MAX_TOOL_CHARS = 12000


def truncate_result(result):
    if not isinstance(result, ToolMessage) or not isinstance(result.content, str):
        return result
    text = result.content
    if len(text) <= MAX_TOOL_CHARS:
        return result
    half = MAX_TOOL_CHARS // 2
    content = (text[:half] + f"\n... [{len(text) - MAX_TOOL_CHARS} characters omitted; "
               "narrow the query or page through results] ...\n" + text[-half:])
    if text.lstrip().startswith("{"):
        metadata = observation(result)
        if metadata is not None and "completed" in metadata:
            # Slicing JSON makes it unparsable. Preserve the public status outside it.
            content += "\n[AppWorld result] " + json.dumps({
                "task_completed": metadata["completed"], "error": metadata["error"],
                "error_type": metadata["signature"][0] if metadata["signature"] else None,
            })
    return result.model_copy(update={"content": content})


class ContextGuardMiddleware(AgentMiddleware):
    name = "context-guard"

    def wrap_tool_call(self, request, handler):
        return truncate_result(handler(request))

    async def awrap_tool_call(self, request, handler):
        return truncate_result(await handler(request))


def make_middleware():
    return ContextGuardMiddleware()
