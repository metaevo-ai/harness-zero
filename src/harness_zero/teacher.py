"""Harnessing agent."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness_zero.review import AssistantResponse, ReviewCandidate, ReviewSubmission
from harness_zero.teacher_guidance import activate_teacher_candidate, reset_teacher_candidate
from deepagents_harbor.trajectory import TrajectoryRecorder
_PROMPT_PATH = Path(__file__).with_name("prompts") / "harness_teacher.md"
_submission_sink: ContextVar[list[ReviewSubmission] | None] = ContextVar(
    "hz_submission_sink", default=None
)
_active_candidate_id: ContextVar[str | None] = ContextVar(
    "hz_active_candidate_id", default=None
)


class ReviewToolInput(BaseModel):
    """Teacher-facing fields; candidate identity is bound by the runtime."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["PASS", "REPLACE"]
    replacement: AssistantResponse | None = None
    components_used: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("replacement", mode="before")
    @classmethod
    def parse_stringified_replacement(cls, value: Any) -> Any:
        # Some teacher models (qwen via OpenRouter) serialize the nested
        # replacement object as a JSON string; accept both forms. They also
        # tend to drop the final closing braces of long payloads, so retry
        # with the missing closers appended (append-only, never rewriting
        # content; a wrong repair fails AssistantResponse validation anyway).
        if not isinstance(value, str):
            return value
        for suffix in ("", "}", "}}", "}}}", '"}', '"}}', '"}}}', '"}}}}'):
            try:
                parsed = json.loads(value + suffix)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("replacement is not parseable JSON")

    @model_validator(mode="after")
    def decision_matches_replacement(self) -> ReviewToolInput:
        if self.decision == "PASS" and self.replacement is not None:
            raise ValueError("PASS must not include a replacement")
        if self.decision == "REPLACE" and self.replacement is None:
            raise ValueError("REPLACE requires a complete replacement")
        return self


def _record_submission(model: Any, payload: dict[str, Any]) -> str:
    sink = _submission_sink.get()
    if sink is None:
        raise RuntimeError("submit_review called outside an active review")
    if sink:
        raise ValueError("submit_review may be called only once per candidate")
    candidate_id = _active_candidate_id.get()
    if candidate_id is None:
        raise RuntimeError("submit_review has no active candidate")
    submission = model.model_validate({"candidate_id": candidate_id, **payload})
    sink.append(submission)
    return "Review accepted."


def _submit_tool() -> StructuredTool:
    input_model: type[BaseModel] = ReviewToolInput
    submission_model: Any = ReviewSubmission
    description = (
        "Submit exactly one PASS or REPLACE decision for the current unexecuted "
        "student candidate. Keep the private reason under 500 characters. "
        "For PASS, replacement is null. For REPLACE, provide the complete "
        "assistant response with reasoning, content, and tool_call; content may "
        "be empty when a tool call is present. Put runnable "
        "bash in tool_call={name: 'execute', command: '<complete command>'}; "
        "code in content is not executed. A null tool_call ends the student trial "
        "and is appropriate only for a completed final answer."
    )
    async def submit_review(**payload: Any) -> str:
        return _record_submission(submission_model, payload)

    return StructuredTool.from_function(
        coroutine=submit_review,
        name="submit_review",
        description=description,
        args_schema=input_model,
        return_direct=True,
    )


class DeepAgentReviewer:
    """One incremental teacher conversation for a complete student trial."""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        workspace: Path,
        max_replacements_per_trial: int,
        teacher_middlewares: Sequence[AgentMiddleware],
        teacher_prompt_suffix: str | None = None,
        teacher_prompt_path: str | None = None,
    ) -> None:
        if max_replacements_per_trial < 0:
            raise ValueError("max_replacements_per_trial must be non-negative")
        self._workspace = workspace
        backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
        filesystem = FilesystemMiddleware(
            backend=backend,
            tools=["ls", "read_file", "glob", "grep"],
            tool_token_limit_before_evict=None,
            human_message_token_limit_before_evict=None,
        )
        self._tool_names = frozenset(
            [tool.name for tool in filesystem.tools] + ["submit_review"]
        )
        self._thread_id = uuid.uuid4().hex
        self._student_context: list[dict[str, Any]] = []
        self._student_system_message: dict[str, Any] | None = None
        self._turn = 0
        prompt_path = _PROMPT_PATH
        if teacher_prompt_path is not None:
            prompt_path = Path(teacher_prompt_path)
            if not prompt_path.is_file():
                raise ValueError(
                    f"teacher_prompt_path file does not exist: {prompt_path}"
                )
        system_prompt = prompt_path.read_text(encoding="utf-8").replace(
            "{{MAX_REPLACEMENTS_PER_TRIAL}}", str(max_replacements_per_trial)
        )
        if "{{MAX_REPLACEMENTS_PER_TRIAL}}" in system_prompt:
            raise ValueError("teacher prompt contains an unresolved replacement budget")
        domain_prompt_path = workspace / "components" / "teacher_prompt.md"
        if domain_prompt_path.is_file():
            system_prompt += "\n\n" + domain_prompt_path.read_text(encoding="utf-8")
        if teacher_prompt_suffix is not None:
            suffix_path = Path(teacher_prompt_suffix)
            if not suffix_path.is_file():
                raise ValueError(
                    f"teacher_prompt_suffix file does not exist: {suffix_path}"
                )
            system_prompt += "\n\n" + suffix_path.read_text(encoding="utf-8")
        self._graph = create_agent(
            model=model,
            tools=[_submit_tool()],
            system_prompt=system_prompt,
            middleware=[
                filesystem,
                *teacher_middlewares,
            ],
            checkpointer=InMemorySaver(),
        )

    @property
    def tool_names(self) -> frozenset[str]:
        return self._tool_names

    async def review(self, candidate: ReviewCandidate) -> ReviewSubmission:
        event, multimodal_blocks = self._next_event(candidate)
        self._append_session_event(event)
        sink: list[ReviewSubmission] = []
        sink_token = _submission_sink.set(sink)
        candidate_token = _active_candidate_id.set(candidate.candidate_id)
        guidance_token = activate_teacher_candidate(candidate)
        recorder = TrajectoryRecorder()
        reminder: str | None = None
        try:
            for _attempt in range(3):
                if reminder is None:
                    message: HumanMessage = HumanMessage(
                        content=[
                            {
                                "type": "text",
                                "text": _format_teacher_update(event),
                            },
                            *multimodal_blocks,
                        ]
                    )
                else:
                    message = HumanMessage(content=reminder)
                await self._graph.ainvoke(
                    {"messages": [message]},
                    config={
                        "callbacks": [recorder],
                        "metadata": {"hz_trace_scope": "teacher"},
                        "configurable": {"thread_id": self._thread_id},
                    },
                )
                if len(sink) == 1:
                    break
                reminder = (
                    "No valid submit_review call was recorded for the current "
                    "candidate (the previous submission was missing or failed "
                    "validation). Call submit_review exactly once now."
                )
        finally:
            reset_teacher_candidate(guidance_token)
            _active_candidate_id.reset(candidate_token)
            _submission_sink.reset(sink_token)
            self._append_teacher_calls(candidate.candidate_id, recorder.sidecar_records())
        if len(sink) != 1:
            raise RuntimeError(
                f"teacher must submit exactly one review, received {len(sink)}"
            )
        self._student_context = candidate.context
        self._turn += 1
        return sink[0]

    def _next_event(
        self, candidate: ReviewCandidate
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if candidate.context[: len(self._student_context)] != self._student_context:
            raise ValueError("student-visible context is not an extension of the prior turn")
        system_changed = candidate.system_message != self._student_system_message

        context_delta = candidate.context[len(self._student_context) :]
        student_events = [
            message
            for message in context_delta
            if self._turn == 0 or message.get("type") not in {"ai", "assistant"}
        ]
        event: dict[str, Any] = {
            "type": "student_candidate",
            "turn": self._turn,
            "candidate_id": candidate.candidate_id,
            "new_student_events": [
                _compact_multimodal_message(message) for message in student_events
            ],
            "proposal": candidate.original.model_dump(mode="json"),
            "candidate_issue": candidate.candidate_issue,
        }
        if self._turn == 0 or system_changed:
            event["student_system_message"] = candidate.system_message
        self._student_system_message = candidate.system_message
        return event, _multimodal_blocks(student_events)

    def _append_session_event(self, event: dict[str, Any]) -> None:
        path = self._workspace / "trial" / "teacher_session.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _append_teacher_calls(
        self, candidate_id: str, records: list[dict[str, Any]]
    ) -> None:
        path = self._workspace / "trial" / "teacher_llm_calls.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                record = {"candidate_id": candidate_id, **record}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _multimodal_blocks(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for message in messages:
        if message.get("type") not in ("human", "user"):
            continue
        content = message.get("content")
        if isinstance(content, list):
            blocks.extend(
                block
                for block in content
                if isinstance(block, dict) and block.get("type") != "text"
            )
    return blocks


def _compact_multimodal_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    compact = dict(message)
    compact["content"] = [
        block
        if not isinstance(block, dict) or block.get("type") == "text"
        else {"type": block.get("type"), "attached_to_event": True}
        for block in content
    ]
    return compact


def _format_teacher_update(event: dict[str, Any]) -> str:
    lines = [
        f"# Student update — turn {event['turn']}",
        "",
        f"Review candidate `{event['candidate_id']}`.",
    ]
    if system_message := event.get("student_system_message"):
        lines.extend(
            [
                "",
                "## Student system prompt",
                "",
                _format_message_content(system_message.get("content")),
            ]
        )

    lines.extend(["", "## New student-visible events"])
    student_events = event["new_student_events"]
    if not student_events:
        lines.extend(["", "No new student-visible events."])
    for index, message in enumerate(student_events, start=1):
        message_type = message.get("type", "unknown")
        label = {
            "human": "User message",
            "user": "User message",
            "tool": "Tool result",
        }.get(message_type, f"{message_type.title()} message")
        details = []
        if name := message.get("name"):
            details.append(str(name))
        if status := message.get("status"):
            details.append(str(status))
        suffix = f" ({', '.join(details)})" if details else ""
        lines.extend(
            [
                "",
                f"### {index}. {label}{suffix}",
                "",
                _format_message_content(message.get("content")),
            ]
        )
        if message_type in {"ai", "assistant"} and message.get("tool_calls"):
            lines.extend(["", "Earlier assistant tool calls:",
                          json.dumps(message["tool_calls"], ensure_ascii=False, indent=2)])

    proposal = event["proposal"]
    lines.extend(["", "## Current unexecuted proposal"])
    if issue := event.get("candidate_issue"):
        lines.extend(["", f"Candidate issue: {issue}. A PASS would preserve this issue."])
    if reasoning := proposal.get("reasoning"):
        lines.extend(["", "### Reasoning", "", reasoning])
    if content := proposal.get("content"):
        lines.extend(["", "### Visible response", "", content])
    if tool_call := proposal.get("tool_call"):
        command = str(tool_call.get("command") or "")
        indented_command = (
            "\n".join(f"    {line}" for line in command.splitlines()) or "    (empty)"
        )
        lines.extend(["", "### Execute command", "", indented_command])
    elif proposal.get("content", "").strip():
        lines.extend(["", "This is a final response with no tool call."])
    else:
        lines.extend(
            [
                "",
                "This proposal is malformed: it has neither visible content nor a "
                "structured tool call. Replace it with a complete student response.",
            ]
        )
    return "\n".join(lines)


def _format_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content or "(empty)"
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict) and block.get("attached_to_event"):
                parts.append(f"[{block.get('type', 'multimodal')} attachment follows]")
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        return "\n".join(part for part in parts if part) or "(empty)"
    return json.dumps(content, ensure_ascii=False)
