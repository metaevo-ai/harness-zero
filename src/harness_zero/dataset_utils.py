"""Shared conversion and validation for SFT sessions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harness_zero.chatfmt import lc_to_openai
from harness_zero.review import AssistantResponse
from deepagents_harbor.message_content import assistant_text, thinking_content


PRIVATE_MARKERS: tuple = (
    # Teacher component files are referenced by absolute "/components/..." paths;
    # require a non-word, non-slash predecessor so sandbox paths like
    # "src/components/Button.tsx" do not false-positive.
    re.compile(r"(?<![\w/])/components/"),
    "/trial/candidate.json",
    "components_used",
    "submit_review",
    "harness-zero-review",
)

REVIEW_REASONING_PATTERNS = (
    re.compile(r"\b(?:proposal|proposed)\b", re.IGNORECASE),
    re.compile(r"\b(?:the|this|that|its) draft\b", re.IGNORECASE),
    re.compile(
        r"\b(?:student(?:'s)?|candidate) "
        r"(?:response|proposal|answer|reasoning|command|action|message)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:pass|replace|rewrite) (?:this|the) "
        r"(?:response|proposal|candidate|student message)\b",
        re.IGNORECASE,
    ),
)


def trial_result(trial_dir: Path) -> tuple[float | None, Any]:
    local = trial_dir / "trial" / "result.json"
    if local.is_file():
        data = json.loads(local.read_text(encoding="utf-8"))
        return float(data["reward"]), data.get("exception_info")

    # Harbor writes agent artifacts under <trial>/agent/teacher/ and the
    # verifier result beside the agent directory.
    harbor = trial_dir.parent.parent / "result.json"
    if not harbor.is_file():
        return None, "missing result.json"
    data = json.loads(harbor.read_text(encoding="utf-8"))
    try:
        return (
            float(data["verifier_result"]["rewards"]["reward"]),
            data.get("exception_info"),
        )
    except (KeyError, TypeError, ValueError):
        return None, data.get("exception_info") or "invalid verifier result"


def response_to_message(response: AssistantResponse, *, id_prefix: str, candidate_id: str) -> dict[str, Any]:
    reasoning, text = assistant_text(response.model_dump())
    message: dict[str, Any] = {"role": "assistant", "content": thinking_content(reasoning, text)}
    if response.tool_call is not None:
        message["tool_calls"] = [
            {
                "id": f"{id_prefix}_{candidate_id}",
                "type": "function",
                "function": {
                    "name": "execute",
                    "arguments": json.dumps(
                        {"command": response.tool_call.command}, ensure_ascii=False
                    ),
                },
            }
        ]
    return message


def prompt_messages(event: dict[str, Any]) -> list[dict[str, Any]]:
    raw = []
    if event.get("system_message"):
        raw.append(event["system_message"])
    raw.extend(event.get("context") or [])
    return [m for m in (lc_to_openai(message) for message in raw) if m is not None]


def assert_student_only(messages: list[dict[str, Any]]) -> None:
    text = json.dumps(messages, ensure_ascii=False)
    leaked = [
        marker
        for marker in PRIVATE_MARKERS
        if (marker.search(text) if hasattr(marker, "search") else marker in text)
    ]
    if leaked:
        raise ValueError(f"teacher-private data leaked into training messages: {leaked}")


def reasoning_leaks_review(response: dict[str, Any]) -> bool:
    reasoning, _ = assistant_text(response)
    return any(pattern.search(reasoning) for pattern in REVIEW_REASONING_PATTERNS)


def conversation_messages(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *prompt_messages(event),
        response_to_message(
            AssistantResponse.model_validate(event["accepted"]),
            id_prefix="sft",
            candidate_id=event["candidate_id"],
        ),
    ]
