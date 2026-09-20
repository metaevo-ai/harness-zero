"""Normalize legacy Qwen XML tool calls into LangChain tool calls."""

from __future__ import annotations

import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage
from deepagents_harbor.message_content import assistant_text


_EXECUTE_RE = re.compile(
    r"<tool_call>\s*<function=execute>\s*"
    r"<parameter=command>(?P<command>.*?)</parameter>\s*"
    r"</function>\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)


def normalize_qwen_tool_call(message: AIMessage) -> AIMessage:
    """Convert one strict legacy XML execute call, preserving native calls.

    Malformed XML is intentionally left untouched so the normal candidate
    validation path can reject it instead of guessing a command.
    """
    if message.tool_calls:
        return message
    reasoning, visible = assistant_text(message.model_dump())
    matches = [(kind, source, match) for kind, source in (("text", visible), ("reasoning", reasoning))
               for match in _EXECUTE_RE.finditer(source)]
    if len(matches) != 1:
        return message
    kind, source, match = matches[0]
    command = match.group("command").strip()
    if not command:
        return message
    cleaned = source[: match.start()] + source[match.end() :]
    cleaned = cleaned.strip()
    kwargs: dict[str, Any] = dict(message.additional_kwargs)
    if kind == "reasoning":
        kwargs["reasoning_content"] = cleaned
        content = visible
    else:
        content = cleaned
        if reasoning:
            kwargs["reasoning_content"] = reasoning
    return AIMessage(
        content=content,
        additional_kwargs=kwargs,
        response_metadata=dict(message.response_metadata),
        tool_calls=[
            {
                "name": "execute",
                "args": {"command": command},
                "id": f"qwen_xml_{uuid.uuid4().hex}",
                "type": "tool_call",
            }
        ],
        invalid_tool_calls=list(message.invalid_tool_calls),
        usage_metadata=message.usage_metadata,
    )
