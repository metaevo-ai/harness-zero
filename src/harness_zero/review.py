"""Typed boundary between a miniswe student and its harnessing teacher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator


from deepagents_harbor.message_content import assistant_text


class ExecuteCall(BaseModel):
    """The only native tool call a replacement student response may contain."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["execute"] = "execute"
    command: str = Field(min_length=1)


class AssistantResponse(BaseModel):
    """A student response representation, including model-visible reasoning."""

    model_config = ConfigDict(extra="forbid")

    reasoning: str = ""
    content: str = ""
    tool_call: ExecuteCall | None = None

    @property
    def is_actionable(self) -> bool:
        return self.tool_call is not None or bool(self.content.strip())

    @classmethod
    def from_message(cls, message: AIMessage) -> AssistantResponse:
        tool_call = None
        if len(message.tool_calls) == 1:
            raw = message.tool_calls[0]
            command = (raw.get("args") or {}).get("command")
            if raw.get("name") == "execute" and isinstance(command, str) and command:
                tool_call = ExecuteCall(command=command)

        reasoning, content = assistant_text(message.model_dump())
        return cls(reasoning=reasoning, content=content, tool_call=tool_call)

    def to_message(self, *, candidate_id: str) -> AIMessage:
        tool_calls = []
        if self.tool_call is not None:
            tool_calls.append(
                {
                    "name": "execute",
                    "args": {"command": self.tool_call.command},
                    "id": f"review_{candidate_id}",
                    "type": "tool_call",
                }
            )
        return AIMessage(
            content=self.content,
            additional_kwargs={"reasoning_content": self.reasoning},
            tool_calls=tool_calls,
        )


class ReviewSubmission(BaseModel):
    """Exactly one teacher decision for one unexecuted student response."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    decision: Literal["PASS", "REPLACE"]
    replacement: AssistantResponse | None = None
    components_used: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def decision_matches_replacement(self) -> ReviewSubmission:
        if self.decision == "PASS" and self.replacement is not None:
            raise ValueError("PASS must not include a replacement")
        if self.decision == "REPLACE" and self.replacement is None:
            raise ValueError("REPLACE requires a complete replacement")
        if self.replacement is not None and not self.replacement.is_actionable:
            raise ValueError("a replacement must contain visible content or a tool call")
        return self


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str
    system_message: dict[str, Any] | None
    context: list[dict[str, Any]]
    original: AssistantResponse
    raw_original: dict[str, Any]
    candidate_issue: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "system_message": self.system_message,
            "context": self.context,
            "original": self.original.model_dump(mode="json"),
            "raw_original": self.raw_original,
            "candidate_issue": self.candidate_issue,
        }


class Reviewer(Protocol):
    async def review(
        self, candidate: ReviewCandidate
    ) -> ReviewSubmission: ...


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    data = message.model_dump(mode="json", exclude_none=True)
    data.pop("id", None)
    return data


def candidate_issue(message: AIMessage, response: AssistantResponse) -> str | None:
    if len(message.tool_calls) > 1:
        return f"student emitted {len(message.tool_calls)} tool calls; at most one is allowed"
    if message.tool_calls:
        raw = message.tool_calls[0]
        if raw.get("name") != "execute":
            return f"student emitted unsupported tool {raw.get('name')!r}"
        command = (raw.get("args") or {}).get("command")
        if not isinstance(command, str) or not command:
            return "student emitted an execute call without a non-empty command"
    if not response.is_actionable:
        return "student emitted neither visible content nor a structured tool call"
    return None
