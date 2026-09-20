"""Reasoning conversion shared by collection, training and the sandbox CLI.

This module only uses the standard library so the same code can be uploaded
alongside agent_cli.py into the student sandbox.
"""

from __future__ import annotations

import re
from typing import Any


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def split_think(content: str, *, prefilled: bool = False) -> tuple[str, str]:
    """Parse inline reasoning; a missing opening tag requires explicit context."""
    if prefilled and not content.lstrip().lower().startswith("<think>"):
        content = "<think>" + content
    if content.lstrip().lower().startswith("<think>") and "</think>" not in content.lower():
        # A truncated reasoning response is not a visible final answer.
        return content.lstrip()[len("<think>"):].strip(), ""
    chunks = _THINK_RE.findall(content)
    if not chunks:
        return "", content
    return (
        "\n".join(chunk.strip() for chunk in chunks if chunk.strip()),
        _THINK_RE.sub("", content).strip(),
    )


def reasoning_block_text(block: dict[str, Any]) -> str:
    direct = block.get("reasoning") or block.get("thinking")
    if direct:
        return str(direct)
    summary = block.get("summary")
    if isinstance(summary, str):
        return summary
    if isinstance(summary, list):
        return "\n".join(
            str(part["text"]) for part in summary
            if isinstance(part, dict) and part.get("text")
        )
    return ""


def assistant_text(message: dict[str, Any], *, prefilled: bool = False) -> tuple[str, str]:
    """Return reasoning and visible text without duplicating inline/field copies."""
    extra = message.get("additional_kwargs") or {}
    explicit = (
        message.get("reasoning_content") or message.get("reasoning")
        or extra.get("reasoning_content") or extra.get("reasoning")
    )
    content = message.get("content") or ""
    reasoning_parts = []
    if isinstance(content, list):
        reasoning_parts = [
            reasoning_block_text(block) for block in content
            if isinstance(block, dict) and block.get("type") in {"reasoning", "thinking"}
        ]
        text = "\n".join(
            block["text"] for block in content
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        )
    else:
        text = content
    # With a separate reasoning field the content is already the visible part.
    # Still strip complete inline copies, but never assume a missing opening tag.
    inline, text = split_think(text, prefilled=prefilled and not explicit and not reasoning_parts)
    reasoning = explicit or "\n".join(part for part in reasoning_parts if part) or inline
    return str(reasoning or ""), text


def thinking_content(reasoning: str, text: str) -> str | list[dict[str, str]]:
    if not reasoning:
        return text
    parts = [{"type": "thinking", "thinking": reasoning}]
    if text:
        parts.append({"type": "text", "text": text})
    return parts


def normalize_assistant(message: dict[str, Any], *, prefilled: bool = False) -> dict[str, Any]:
    """Keep native protocol fields (including tools) while normalizing text."""
    reasoning, text = assistant_text(message, prefilled=prefilled)
    out = {**message, "content": text or None}
    out.pop("reasoning", None)
    if reasoning:
        out["reasoning_content"] = reasoning
    return out
