"""The supervised miniswe student harness: one execute tool, one loop, review before execution."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import var_child_runnable_config
from langchain_core.tools import StructuredTool

from harness_zero.review import (
    AssistantResponse,
    ReviewCandidate,
    ReviewSubmission,
    Reviewer,
    candidate_issue,
    serialize_message,
)
from harness_zero.store import TrialStore
from harness_zero.tools import EXECUTE_DESCRIPTION, ExecuteArgs
from harness_zero.student_harness import (
    SANDBOX_MEMORY_PATH,
    SANDBOX_SKILLS_DIR,
    StudentHarnessSpec,
)
from harness_zero.tool_call_adapter import normalize_qwen_tool_call
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents_harbor.backend import HarborSandboxBackend

_PROMPT_PATH = Path(__file__).with_name("prompts") / "student.md"
OBSERVATION_TEMPLATE = "{output}\n[exit_code={exit_code}]"
MAIN_AGENT_MAX_TURNS = 40
_model_request_capture: ContextVar[dict | None] = ContextVar("student_model_request_capture", default=None)

MINISWE_SKILLS_SYSTEM_PROMPT = """## Skills Library

You have access to a skills library with specialized domain knowledge.

{skills_locations}{skills_load_warnings}

**Available Skills:**

{skills_list}

**How to use skills:** the entries above show each skill's name, description, \
and file path. When the task matches a skill's domain, read the full \
instructions first with `execute` (for example `cat <path>`), then follow \
them step by step. Skills may include helper scripts; always run them by \
absolute path."""


class StudentTurnLimitError(RuntimeError):
    """The main student exhausted its model-call budget."""


class StudentRequestCaptureMiddleware(AgentMiddleware):
    """Record the final model request without changing response/review ordering."""

    name = "student-request-capture"

    async def awrap_model_call(self, request, handler):
        capture = _model_request_capture.get()
        if capture is not None:
            capture["request"] = request
        return await handler(request)


def execute_tool(backend: HarborSandboxBackend) -> StructuredTool:
    async def execute(command: str) -> str:
        result = await backend.aexecute(command)
        return OBSERVATION_TEMPLATE.format(
            output=result.output,
            exit_code=result.exit_code,
        )

    return _structured_execute_tool(execute)


def _structured_execute_tool(
    coroutine: Callable[..., Awaitable[str]],
) -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=coroutine,
        name="execute",
        description=EXECUTE_DESCRIPTION,
        args_schema=ExecuteArgs,
    )


class HarnessReviewMiddleware(AgentMiddleware):
    """Ask the teacher to review every complete student response.

    The teacher answers PASS or REPLACE before the response is accepted.
    """

    name = "harness-zero-review"

    def __init__(
        self,
        reviewer: Reviewer,
        store: TrialStore,
        *,
        max_replacements: int = 20,
        max_turns: int = MAIN_AGENT_MAX_TURNS,
    ) -> None:
        super().__init__()
        if max_replacements < 0:
            raise ValueError("max_replacements must be non-negative")
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        self._reviewer = reviewer
        self._store = store
        self._max_replacements = max_replacements
        self._replacement_count = 0
        self._max_turns = max_turns
        self._turn_count = 0

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        while True:
            if self._turn_count == self._max_turns:
                raise StudentTurnLimitError(
                    f"main student exceeded {self._max_turns} model turns"
                )
            self._turn_count += 1
            capture = {}
            token = _model_request_capture.set(capture)
            try:
                response = await handler(request)
            finally:
                _model_request_capture.reset(token)
            actual_request = capture.get("request", request)
            normalized = [
                normalize_qwen_tool_call(message)
                if isinstance(message, AIMessage)
                else message
                for message in response.result
            ]
            if any(new is not old for new, old in zip(normalized, response.result)):
                response = ModelResponse(
                    result=normalized,
                    structured_response=response.structured_response,
                )
            if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
                raise ValueError("student model must return exactly one AIMessage")

            original_message = response.result[0]
            original = AssistantResponse.from_message(original_message)
            candidate_id = uuid.uuid4().hex
            candidate = ReviewCandidate(
                candidate_id=candidate_id,
                system_message=(
                    serialize_message(actual_request.system_message)
                    if actual_request.system_message is not None
                    else None
                ),
                context=[serialize_message(message) for message in actual_request.messages],
                original=original,
                raw_original=serialize_message(original_message),
                candidate_issue=candidate_issue(original_message, original),
            )
            self._store.write_candidate(candidate)
            if self._replacement_count == self._max_replacements:
                submission: ReviewSubmission = ReviewSubmission(
                    candidate_id=candidate_id,
                    decision="PASS",
                    reason="replacement budget exhausted; candidate passed without review",
                )
            else:
                submission = await self._reviewer.review(candidate)
            if submission.candidate_id != candidate_id:
                raise ValueError(
                    f"teacher reviewed {submission.candidate_id}, expected {candidate_id}"
                )

            if submission.decision == "PASS":
                accepted = candidate.original
                accepted_message = original_message
            else:
                accepted = submission.replacement
                assert accepted is not None
                accepted_message = accepted.to_message(candidate_id=candidate_id)
                self._replacement_count += 1

            self._store.append_review(candidate, submission, accepted)
            if config := var_child_runnable_config.get():
                await adispatch_custom_event(
                    "hz_accepted_response",
                    {"message": accepted_message},
                    config=config,
                )
            if submission.decision == "PASS":
                return response
            return ModelResponse(
                result=[accepted_message],
                structured_response=response.structured_response,
            )


def build_student_tools(
    backend: HarborSandboxBackend,
    student_harness: StudentHarnessSpec | None = None,
) -> list:
    tools = [execute_tool(backend)]
    if student_harness is not None:
        tools.extend(factory(backend) for factory in student_harness.tool_factories)
    return tools


def build_student_middleware(
    *,
    reviewer: Reviewer,
    store: TrialStore,
    backend: HarborSandboxBackend,
    max_replacements: int = 20,
    max_turns: int = MAIN_AGENT_MAX_TURNS,
    student_harness: StudentHarnessSpec | None = None,
) -> list:
    middleware: list = []
    if student_harness is not None:
        # Skills/memory injection must precede the review middleware so the
        # teacher sees the same system context the student received.
        if student_harness.skills_enabled:
            middleware.append(
                SkillsMiddleware(
                    backend=backend,
                    sources=[SANDBOX_SKILLS_DIR + "/"],
                    system_prompt=MINISWE_SKILLS_SYSTEM_PROMPT,
                )
            )
        if student_harness.memory_enabled:
            middleware.append(
                MemoryMiddleware(backend=backend, sources=[SANDBOX_MEMORY_PATH])
            )
    middleware.append(
        HarnessReviewMiddleware(
            reviewer,
            store,
            max_replacements=max_replacements,
            max_turns=max_turns,
        )
    )
    if student_harness is not None:
        middleware.extend(factory() for factory in student_harness.middleware_factories)
        middleware.append(StudentRequestCaptureMiddleware())
    return middleware


def create_supervised_miniswe(
    *,
    student_model: BaseChatModel,
    reviewer: Reviewer,
    backend: HarborSandboxBackend,
    store: TrialStore,
    max_replacements: int = 20,
    max_turns: int = MAIN_AGENT_MAX_TURNS,
    student_harness: StudentHarnessSpec | None = None,
):
    return create_agent(
        model=student_model,
        tools=build_student_tools(backend, student_harness),
        system_prompt=_PROMPT_PATH.read_text(encoding="utf-8"),
        middleware=build_student_middleware(
            reviewer=reviewer,
            store=store,
            backend=backend,
            max_replacements=max_replacements,
            max_turns=max_turns,
            student_harness=student_harness,
        ),
    )


def student_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")
