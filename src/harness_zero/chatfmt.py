"""OpenAI/LangChain message conversion shared by training and inference."""

from __future__ import annotations

import json
from deepagents_harbor.message_content import assistant_text, thinking_content


def create_qwen35_renderer(tokenizer, *, image_processor=None):
    """Build the cookbook renderer used by SFT and the Tinker gateway.

    strip_thinking_from_history=False: the rollout stack preserves reasoning
    across turns, so training and serving keep historical thinking too. This
    gives the renderer the sequence-extension property, letting SFT train a
    whole session as one datum with all assistant messages as targets.
    """
    from tinker_cookbook.renderers.qwen3_5 import Qwen3_5Renderer

    return Qwen3_5Renderer(
        tokenizer,
        image_processor=image_processor,
        strip_thinking_from_history=False,
    )


def openai_tools_to_specs(tools: list[dict] | None) -> list[dict]:
    """OpenAI wraps function specs in {"type": "function", "function": {...}};
    the cookbook ToolSpec is the bare function dict."""
    specs = []
    for t in tools or []:
        fn = t.get("function", t)
        specs.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return specs


def system_text(messages: list[dict]) -> str:
    # ChatOpenAI may send content as list-of-parts; normalize via text_of.
    return "\n\n".join(
        text_of(m.get("content")) for m in messages if m.get("role") == "system"
    ).strip()


def to_renderer_messages(
    renderer, messages: list[dict], tools: list[dict] | None, hint: str | None = None
) -> list[dict]:
    """OpenAI messages -> cookbook renderer messages.

    Hint is APPENDED to the system prompt.
    Tools go through create_conversation_prefix_with_tools, matching inference.
    """
    specs = openai_tools_to_specs(tools)
    from tinker_cookbook.renderers.base import ToolCall

    sys_prompt = system_text(messages)
    if hint:
        sys_prompt = (sys_prompt + "\n\n" + hint) if sys_prompt else hint
    out: list[dict] = []
    if specs or sys_prompt:
        out.extend(renderer.create_conversation_prefix_with_tools(specs, sys_prompt))
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "user":
            raw = m.get("content")
            if not isinstance(raw, list) or all(part.get("type") == "text" for part in raw):
                content = text_of(raw)
            else:
                content = []
                for part in raw:
                    kind = part.get("type")
                    if kind == "text":
                        content.append({"type": "text", "text": part["text"]})
                    elif kind == "image_url":
                        image_url = part["image_url"]
                        content.append({
                            "type": "image",
                            "image": image_url["url"] if isinstance(image_url, dict) else image_url,
                        })
                    elif kind == "image":
                        if "image" in part:
                            image = part["image"]
                        elif part.get("source_type") == "base64":
                            image = f"data:{part['mime_type']};base64,{part['data']}"
                        elif "url" in part:
                            image = part["url"]
                        else:
                            raise ValueError("unsupported image block; expected image, URL, or base64 data")
                        content.append({"type": "image", "image": image})
                    else:
                        raise ValueError(f"unsupported user content block: {kind}")
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            reasoning, text = assistant_text(m)
            if "</think>" in text.lower() or "<think>" in text.lower():
                raise ValueError(
                    "unparsed think tag in assistant text; recover from the original "
                    "response with an explicit reasoning-prefill setting before training"
                )
            msg: dict = {"role": "assistant", "content": thinking_content(reasoning, text)}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    tc if not isinstance(tc, dict) else ToolCall(
                        type="function",
                        id=tc.get("id") or f"call_{i}",
                        function=ToolCall.FunctionBody(
                            name=tc["function"]["name"],
                            arguments=tc["function"].get("arguments") or "",
                        ),
                    )
                    for i, tc in enumerate(m["tool_calls"])
                ]
            out.append(msg)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "content": text_of(m.get("content")),
                    "name": m.get("name"),
                    "tool_call_id": m.get("tool_call_id"),
                }
            )
        else:
            raise ValueError(f"unsupported role: {role}")
    return out


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def lc_to_openai(m: dict) -> dict | None:
    """langchain type system -> OpenAI messages with ThinkingParts."""
    t = m.get("type") or m.get("role")
    if t in ("system", "user", "human"):
        content = m.get("content")
        return {
            "role": "system" if t == "system" else "user",
            "content": (
                content
                if t in ("user", "human") and isinstance(content, list)
                else text_of(content)
            ),
        }
    if t in ("ai", "assistant"):
        reasoning, text = assistant_text(m)
        msg = {"role": "assistant", "content": thinking_content(reasoning, text)}
        tcs = m.get("tool_calls") or []
        if tcs:
            msg["tool_calls"] = [_lc_tool_call(tc) for tc in tcs]
        return msg
    if t == "tool":
        return {
            "role": "tool",
            "tool_call_id": m.get("tool_call_id"),
            "content": text_of(m.get("content")),
        }
    return None


def _lc_tool_call(tc: dict) -> dict:
    if isinstance(tc.get("function"), dict):
        fn = tc["function"]
        return {
            "id": tc.get("id"),
            "type": "function",
            "function": {
                "name": fn["name"],
                "arguments": fn.get("arguments") or "",
            },
        }
    return {
        "id": tc.get("id"),
        "type": "function",
        "function": {
            "name": tc["name"],
            "arguments": json.dumps(tc.get("args") or {}, ensure_ascii=False),
        },
    }


def call_id_of(response: dict | None) -> str | None:
    if not response:
        return None
    meta = response.get("response_metadata") or {}
    return meta.get("system_fingerprint") or meta.get("id") or None
