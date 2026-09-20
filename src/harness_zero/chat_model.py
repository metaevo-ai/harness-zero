"""SDK-backed student clients that retain reasoning across tool turns."""

import json
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from pydantic import Field

from deepagents_harbor.message_content import assistant_text


def _preserve_history(payload, messages, log_path):
    for source, target in zip(messages, payload["messages"], strict=True):
        if isinstance(source, AIMessage):
            reasoning, text = assistant_text(source.model_dump())
            target["content"] = text or None
            if reasoning:
                target["reasoning_content"] = reasoning
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


class ReasoningChatOpenAI(ChatOpenAI):
    # Normalize a complete response, never individual fragments of a think tag.
    disable_streaming: Literal[True] = True
    streaming: Literal[False] = False
    reasoning_prefill: Literal["none", "think"] = "none"
    request_log_path: Path | None = Field(default=None, exclude=True)

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, raw["choices"], strict=True):
            source = choice["message"]
            reasoning, text = assistant_text(
                source, prefilled=self.reasoning_prefill == "think"
            )
            if "</think>" in text.lower() and self.reasoning_prefill == "none":
                raise ValueError(
                    "unparsed </think> in the student response; verify the server template "
                    "and pass --student-reasoning-prefill think for prefilled raw continuations"
                )
            message = generation.message
            message.content = text
            if reasoning:
                message.additional_kwargs["reasoning_content"] = reasoning
            # Retain what arrived and the parsing setting for later audits.
            message.response_metadata["reasoning_prefill"] = self.reasoning_prefill
            if source.get("content") != text:
                message.response_metadata["raw_content"] = source.get("content")
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if "messages" not in payload:
            raise ValueError("ReasoningChatOpenAI requires use_responses_api=False")
        if payload.get("stream"):
            raise ValueError("reasoning normalization requires a complete non-streaming response")
        return _preserve_history(payload, messages, self.request_log_path)


class ReasoningChatDeepSeek(ChatDeepSeek):
    """ChatDeepSeek already reads reasoning; also send it in tool history."""

    request_log_path: Path | None = Field(default=None, exclude=True)

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        return _preserve_history(payload, messages, self.request_log_path)
