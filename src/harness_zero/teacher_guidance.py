"""Shared boundary for middleware that advises the harnessing teacher."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage


class TeacherCandidate(Protocol):
    candidate_id: str
    candidate_issue: str | None
    original: Any


@dataclass(frozen=True)
class TeacherHint:
    component_id: str
    evidence: str
    instruction: str

    def markdown(self, *, include_heading: bool) -> str:
        lines = []
        if include_heading:
            lines.extend(["## Triggered middleware guidance", ""])
        lines.extend(
            [
                f"### `{self.component_id}`",
                "",
                f"Evidence: {self.evidence}",
                "",
                f"Hint: {self.instruction}",
            ]
        )
        return "\n".join(lines)


_active_candidate: ContextVar[TeacherCandidate | None] = ContextVar(
    "hz_teacher_candidate", default=None
)


def activate_teacher_candidate(candidate: TeacherCandidate) -> Token:
    return _active_candidate.set(candidate)


def reset_teacher_candidate(token: Token) -> None:
    _active_candidate.reset(token)


class TeacherCandidateMiddleware(AgentMiddleware):
    """Append a candidate-specific guard result to the teacher update."""

    def hint(self, candidate: TeacherCandidate) -> TeacherHint | None:
        raise NotImplementedError

    def _inject(self, request: ModelRequest) -> ModelRequest:
        candidate = _active_candidate.get()
        if candidate is None or (hint := self.hint(candidate)) is None:
            return request

        messages = list(request.messages)
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if not isinstance(message, HumanMessage):
                continue
            content = message.content
            if isinstance(content, str):
                blocks: list[Any] = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                blocks = list(content)
            else:
                continue
            text = "\n".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if "# Student update" not in text:
                continue
            marker = f"### `{hint.component_id}`"
            if marker in text:
                return request
            blocks.append(
                {
                    "type": "text",
                    "text": hint.markdown(
                        include_heading="## Triggered middleware guidance" not in text
                    ),
                }
            )
            messages[index] = message.model_copy(update={"content": blocks})
            return request.override(messages=messages)
        return request

    def wrap_model_call(self, request, handler) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(self, request, handler) -> ModelResponse:
        return await handler(self._inject(request))
