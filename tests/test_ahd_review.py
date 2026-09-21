from __future__ import annotations

import asyncio
import json

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool

from harness_zero.student import HarnessReviewMiddleware, StudentTurnLimitError
from harness_zero.review import AssistantResponse, ExecuteCall, ReviewSubmission
from harness_zero.sft import build_sft_file
from harness_zero.store import TrialStore
from harness_zero.teacher import DeepAgentReviewer
from harness_zero.teacher_models import build_openai_model
from harness_zero.rollout import build_rollout_command, shell_command
from harness_zero.harness import HarnessZeroMinisweAgent
from deepagents_harbor.trajectory import TrajectoryRecorder
from harness_bank.uspto.middlewares import build_teacher_middlewares


COMPONENTS = __import__("pathlib").Path(__file__).parents[1] / "harness_bank" / "uspto"


def test_harbor_agent_is_concrete():
    assert not HarnessZeroMinisweAgent.__abstractmethods__
    assert HarnessZeroMinisweAgent.name() == "harness-zero-miniswe"


def test_training_execute_schema_matches_rollout_tool_exactly():
    from harness_zero.student import execute_tool
    from harness_zero.tools import execute_tool_schema

    rollout_schema = convert_to_openai_tool(execute_tool(object()))
    assert {"type": "function", "function": execute_tool_schema()} == rollout_schema


def test_teacher_is_pinned_to_official_openai_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.invalid/v1")
    model = build_openai_model(model="gpt-5.6-sol", reasoning_effort="high")
    assert str(model.openai_api_base).rstrip("/") == "https://api.openai.com/v1"


def test_subagent_wrapper_inherits_student_model_and_reasoning(tmp_path):
    reasoning = {"enabled": True}
    agent = HarnessZeroMinisweAgent(
        logs_dir=tmp_path,
        model_name="openrouter:qwen/qwen3.5-9b",
        model_kwargs={"reasoning": reasoning},
        teacher_model="none",
        teacher_provider="passthrough",
        components_dir=str(COMPONENTS),
    )

    wrapper = agent._agent_wrapper()

    assert "--model qwen/qwen3.5-9b" in wrapper
    assert "--reasoning '{\"enabled\":true}'" in wrapper
    assert "--max-turns" not in wrapper
    assert "--command-timeout" not in wrapper


class StaticReviewer:
    def __init__(self, submission_factory):
        self.submission_factory = submission_factory

    async def review(self, candidate):
        return self.submission_factory(candidate)


def _request() -> ModelRequest:
    model = FakeMessagesListChatModel(responses=[])
    return ModelRequest(
        model=model,
        system_message=SystemMessage(content="student system"),
        messages=[HumanMessage(content="make out.txt")],
    )


def _store(tmp_path) -> TrialStore:
    return TrialStore(
        tmp_path / "teacher",
        task_id="task-1",
        trial=0,
        components_dir=COMPONENTS,
    )


def test_pass_preserves_original_model_response(tmp_path):
    original = AIMessage(
        content="I will inspect it.",
        additional_kwargs={"reasoning_content": "Need evidence first."},
        tool_calls=[
            {"name": "execute", "args": {"command": "ls -la"}, "id": "call-1"}
        ],
    )
    response = ModelResponse(result=[original])
    reviewer = StaticReviewer(
        lambda c: ReviewSubmission(
            candidate_id=c.candidate_id,
            decision="PASS",
            reason="The inspection is useful and bounded.",
        )
    )
    middleware = HarnessReviewMiddleware(reviewer, _store(tmp_path))

    async def handler(_request):
        return response

    result = asyncio.run(middleware.awrap_model_call(_request(), handler))
    assert result is response
    assert result.result[0] is original


def test_replace_returns_complete_student_response_and_keeps_original(tmp_path):
    original = AIMessage(
        content="Overwrite it.",
        tool_calls=[
            {
                "name": "execute",
                "args": {"command": "echo x > evidence.txt"},
                "id": "call-1",
            }
        ],
    )
    replacement = AssistantResponse(
        reasoning="Preserve the source and inspect it first.",
        content="I will inspect the existing evidence before writing.",
        tool_call=ExecuteCall(command="ls -l evidence.txt && sed -n '1,80p' evidence.txt"),
    )
    reviewer = StaticReviewer(
        lambda c: ReviewSubmission(
            candidate_id=c.candidate_id,
            decision="REPLACE",
            replacement=replacement,
            components_used=["middleware:overwrite"],
            reason="The original command destroys evidence.",
        )
    )
    store = _store(tmp_path)
    middleware = HarnessReviewMiddleware(reviewer, store)

    async def handler(_request):
        return ModelResponse(result=[original])

    result = asyncio.run(middleware.awrap_model_call(_request(), handler))
    accepted = result.result[0]
    assert accepted.tool_calls[0]["args"]["command"].startswith("ls -l")
    assert accepted.additional_kwargs["reasoning_content"].startswith("Preserve")
    event = store.events()[0]
    assert event["raw_original"]["tool_calls"][0]["args"]["command"] == "echo x > evidence.txt"
    assert event["accepted"]["tool_call"]["command"].startswith("ls -l")


def test_teacher_can_replace_reasoning_only_student_response(tmp_path):
    original = AIMessage(
        content="",
        additional_kwargs={
            "reasoning_content": "I should inspect the workspace first."
        },
    )
    reviewer_saw_original = False

    def replace(candidate):
        nonlocal reviewer_saw_original
        reviewer_saw_original = True
        assert candidate.original.reasoning.startswith("I should inspect")
        assert not candidate.original.is_actionable
        return ReviewSubmission(
            candidate_id=candidate.candidate_id,
            decision="REPLACE",
            replacement=AssistantResponse(
                reasoning="Inspect the workspace.",
                tool_call=ExecuteCall(command="ls -la"),
            ),
            reason="The model emitted reasoning without a structured action.",
        )

    middleware = HarnessReviewMiddleware(
        StaticReviewer(replace),
        _store(tmp_path),
    )

    async def handler(_request):
        return ModelResponse(result=[original])

    result = asyncio.run(middleware.awrap_model_call(_request(), handler))
    assert reviewer_saw_original
    assert result.result[0].tool_calls[0]["args"]["command"] == "ls -la"


def test_teacher_can_replace_unsupported_student_tool(tmp_path):
    original = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "I should ensure the result."},
        tool_calls=[
            {
                "name": "ensure",
                "args": {"command": "test -f result.txt"},
                "id": "bad-tool",
            }
        ],
    )

    def replace(candidate):
        assert candidate.candidate_issue == "student emitted unsupported tool 'ensure'"
        assert candidate.raw_original["tool_calls"][0]["name"] == "ensure"
        return ReviewSubmission(
            candidate_id=candidate.candidate_id,
            decision="REPLACE",
            replacement=AssistantResponse(
                reasoning="Check the result with the available tool.",
                tool_call=ExecuteCall(command="test -f result.txt"),
            ),
            reason="The proposed tool is unavailable.",
        )

    middleware = HarnessReviewMiddleware(StaticReviewer(replace), _store(tmp_path))

    async def handler(_request):
        return ModelResponse(result=[original])

    result = asyncio.run(middleware.awrap_model_call(_request(), handler))
    assert result.result[0].tool_calls[0]["name"] == "execute"


def test_teacher_replacement_must_be_actionable():
    with pytest.raises(ValueError, match="replacement must contain"):
        ReviewSubmission(
            candidate_id="candidate",
            decision="REPLACE",
            replacement=AssistantResponse(reasoning="Only internal reasoning."),
            reason="invalid replacement",
        )


def test_replacement_budget_is_enforced_per_trial(tmp_path):
    original = AIMessage(
        content="",
        tool_calls=[
            {"name": "execute", "args": {"command": "original"}, "id": "call-1"}
        ],
    )
    review_calls = 0

    def replace(candidate):
        nonlocal review_calls
        review_calls += 1
        return ReviewSubmission(
            candidate_id=candidate.candidate_id,
            decision="REPLACE",
            replacement=AssistantResponse(
                reasoning="Use the corrected command.",
                tool_call=ExecuteCall(command="replacement"),
            ),
            reason="material correction",
        )

    store = _store(tmp_path)
    middleware = HarnessReviewMiddleware(
        StaticReviewer(replace), store, max_replacements=1
    )

    async def handler(_request):
        return ModelResponse(result=[original])

    first = asyncio.run(middleware.awrap_model_call(_request(), handler))
    second = asyncio.run(middleware.awrap_model_call(_request(), handler))

    assert first.result[0].tool_calls[0]["args"]["command"] == "replacement"
    assert second.result[0] is original
    assert review_calls == 1
    events = store.events()
    assert [event["review"]["decision"] for event in events] == ["REPLACE", "PASS"]
    assert all("replacements_remaining" not in event for event in events)
    assert "budget exhausted" in events[1]["review"]["reason"]


def test_main_student_turn_limit_stops_before_extra_model_call(tmp_path):
    original = AIMessage(content="done")
    middleware = HarnessReviewMiddleware(
        StaticReviewer(
            lambda candidate: ReviewSubmission(
                candidate_id=candidate.candidate_id,
                decision="PASS",
                reason="valid",
            )
        ),
        _store(tmp_path),
        max_turns=2,
    )
    model_calls = 0

    async def handler(_request):
        nonlocal model_calls
        model_calls += 1
        return ModelResponse(result=[original])

    asyncio.run(middleware.awrap_model_call(_request(), handler))
    asyncio.run(middleware.awrap_model_call(_request(), handler))
    with pytest.raises(StudentTurnLimitError, match="exceeded 2 model turns"):
        asyncio.run(middleware.awrap_model_call(_request(), handler))

    assert model_calls == 2


def test_only_successful_trials_enter_positive_sft(tmp_path):
    success = _store(tmp_path / "ok")
    failure = _store(tmp_path / "bad")
    for store, reward in ((success, 1.0), (failure, 0.0)):
        candidate = __import__("harness_zero.review", fromlist=["ReviewCandidate"]).ReviewCandidate(
            candidate_id=f"c-{reward}",
            system_message=SystemMessage(content="student system").model_dump(mode="json"),
            context=[HumanMessage(content="write a file").model_dump(mode="json")],
            original=AssistantResponse(
                reasoning="Do the task.",
                tool_call=ExecuteCall(command="printf x > out.txt"),
            ),
            raw_original=AIMessage(content="").model_dump(mode="json"),
        )
        review = ReviewSubmission(
            candidate_id=candidate.candidate_id,
            decision="PASS",
            reason="valid action",
        )
        store.append_review(candidate, review, candidate.original)
        store.finalize(reward=reward)

    output = tmp_path / "sft.jsonl"
    stats = build_sft_file(
        [success.root, failure.root],
        output,
    )
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert stats == {
        "successful_trials": 1,
        "skipped_trials": 1,
        "leaked_trials": 0,
        "masked_reasoning_turns": 0,
        "examples": 1,
    }
    assert rows[0]["messages"][-1]["role"] == "assistant"
    assert "printf x" in rows[0]["messages"][-1]["tool_calls"][0]["function"]["arguments"]
    assert "components_used" not in output.read_text()


def test_sft_emits_one_complete_session_per_trial(tmp_path):
    store = _store(tmp_path / "session")
    first = __import__("harness_zero.review", fromlist=["ReviewCandidate"]).ReviewCandidate(
        candidate_id="first",
        system_message=SystemMessage(content="student system").model_dump(mode="json"),
        context=[HumanMessage(content="inspect and fix").model_dump(mode="json")],
        original=AssistantResponse(
            reasoning="Inspect first.",
            tool_call=ExecuteCall(command="ls"),
        ),
        raw_original=AIMessage(content="").model_dump(mode="json"),
    )
    store.append_review(
        first,
        ReviewSubmission(candidate_id="first", decision="PASS", reason="valid"),
        first.original,
    )
    second = __import__("harness_zero.review", fromlist=["ReviewCandidate"]).ReviewCandidate(
        candidate_id="second",
        system_message=first.system_message,
        context=[
            HumanMessage(content="inspect and fix").model_dump(mode="json"),
            first.original.to_message(candidate_id="first").model_dump(mode="json"),
            ToolMessage(content="file.py\n[exit_code=0]", tool_call_id="first").model_dump(
                mode="json"
            ),
        ],
        original=AssistantResponse(
            reasoning="Apply the fix.",
            tool_call=ExecuteCall(command="sed -i 's/a/b/' file.py"),
        ),
        raw_original=AIMessage(content="").model_dump(mode="json"),
    )
    replacement = AssistantResponse(
        reasoning="Make the minimal correction.",
        tool_call=ExecuteCall(command="sed -i 's/a/b/' file.py"),
    )
    store.append_review(
        second,
        ReviewSubmission(
            candidate_id="second",
            decision="REPLACE",
            replacement=replacement,
            reason="make it on policy",
        ),
        replacement,
    )
    store.finalize(reward=1.0)

    output = tmp_path / "session-sft.jsonl"
    stats = build_sft_file([store.root], output)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert stats["examples"] == 1
    assert len(rows) == 1
    assert rows[0]["kind"] == "session"
    assert rows[0]["turns"] == 2
    assert rows[0]["replacements"] == 1
    assert [message["role"] for message in rows[0]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert "Make the minimal correction" in json.dumps(
        rows[0]["messages"][-1], ensure_ascii=False
    )


def test_sft_marks_only_reviewer_reasoning_for_loss_masking(tmp_path):
    store = _store(tmp_path / "leak")
    first = __import__("harness_zero.review", fromlist=["ReviewCandidate"]).ReviewCandidate(
        candidate_id="clean",
        system_message=SystemMessage(content="student system").model_dump(mode="json"),
        context=[HumanMessage(content="fix it").model_dump(mode="json")],
        original=AssistantResponse(
            reasoning="Inspect the file first.",
            tool_call=ExecuteCall(command="ls"),
        ),
        raw_original=AIMessage(content="").model_dump(mode="json"),
    )
    store.append_review(
        first,
        ReviewSubmission(candidate_id="clean", decision="PASS", reason="valid"),
        first.original,
    )
    leaking = __import__("harness_zero.review", fromlist=["ReviewCandidate"]).ReviewCandidate(
        candidate_id="leaking",
        system_message=first.system_message,
        context=[
            HumanMessage(content="fix it").model_dump(mode="json"),
            first.original.to_message(candidate_id="clean").model_dump(mode="json"),
            ToolMessage(content="file.py\n[exit_code=0]", tool_call_id="clean").model_dump(
                mode="json"
            ),
        ],
        original=AssistantResponse(tool_call=ExecuteCall(command="sed -n '1,20p' file.py")),
        raw_original=AIMessage(content="").model_dump(mode="json"),
    )
    replacement = AssistantResponse(
        reasoning="The proposed command is malformed; I should inspect another file.",
        tool_call=ExecuteCall(command="sed -n '1,20p' other.py"),
    )
    store.append_review(
        leaking,
        ReviewSubmission(
            candidate_id="leaking",
            decision="REPLACE",
            replacement=replacement,
            reason="correct it",
        ),
        replacement,
    )
    store.finalize(reward=1.0)

    output = tmp_path / "leak-sft.jsonl"
    stats = build_sft_file([store.root], output)
    row = json.loads(output.read_text(encoding="utf-8"))

    assert stats["leaked_trials"] == 1
    assert stats["masked_reasoning_turns"] == 1
    assert row["turns"] == 2
    assert row["masked_reasoning_turns"] == [1]
    assert "proposed" in output.read_text(encoding="utf-8").lower()


def test_teacher_has_only_read_tools_and_submit(tmp_path):
    reviewer = DeepAgentReviewer(
        model=ToolFriendlyFake(responses=[]),
        workspace=tmp_path,
        max_replacements_per_trial=10,
        teacher_middlewares=[],
    )
    assert reviewer.tool_names == {
        "ls",
        "read_file",
        "glob",
        "grep",
        "submit_review",
    }
    assert not reviewer.tool_names.intersection(
        {"execute", "write_file", "edit_file", "delete", "task"}
    )


def test_teacher_raw_calls_are_persisted_with_reasoning_blocks(tmp_path):
    from harness_zero.review import ReviewCandidate

    (tmp_path / "trial").mkdir()
    candidate = ReviewCandidate(
        candidate_id="fixed-candidate",
        system_message=None,
        context=[HumanMessage(content="task").model_dump(mode="json")],
        original=AssistantResponse(content="done"),
        raw_original=AIMessage(content="done").model_dump(mode="json"),
    )
    teacher = ToolFriendlyFake(
        responses=[
            AIMessage(
                content=[
                    {"type": "reasoning", "reasoning": "inspect the evidence"}
                ],
                tool_calls=[
                    {
                        "name": "submit_review",
                        "args": {
                            "decision": "PASS",
                            "replacement": None,
                            "components_used": [],
                            "reason": "sound action",
                        },
                        "id": "submit-1",
                    }
                ],
            )
        ]
    )
    reviewer = DeepAgentReviewer(
        model=teacher,
        workspace=tmp_path,
        max_replacements_per_trial=7,
        teacher_middlewares=[],
    )

    submission = asyncio.run(reviewer.review(candidate))

    assert submission.decision == "PASS"
    rows = [
        json.loads(line)
        for line in (tmp_path / "trial" / "teacher_llm_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[0]["candidate_id"] == candidate.candidate_id
    assert rows[0]["response"]["content"][0]["reasoning"] == "inspect the evidence"
    request = json.dumps(rows[0]["request"], ensure_ascii=False)
    assert "You are a strong harnessing agent" in request
    assert "You may replace at most `7` student responses" in request
    assert "{{MAX_REPLACEMENTS_PER_TRIAL}}" not in request


def test_teacher_middlewares_inject_current_candidate_guidance(tmp_path):
    from harness_zero.review import ReviewCandidate

    (tmp_path / "trial").mkdir()
    command = "cat > result.txt <<'EOF'\nvalue\nEOF\npython server.py"
    candidate = ReviewCandidate(
        candidate_id="guarded-candidate",
        system_message=None,
        context=[HumanMessage(content="create result.txt").model_dump(mode="json")],
        original=AssistantResponse(tool_call=ExecuteCall(command=command)),
        raw_original=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": command},
                    "id": "student-call",
                }
            ],
        ).model_dump(mode="json"),
    )
    teacher = ToolFriendlyFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_review",
                        "args": {
                            "decision": "PASS",
                            "replacement": None,
                            "components_used": [],
                            "reason": "accepted for test",
                        },
                        "id": "submit",
                    }
                ],
            )
        ]
    )

    asyncio.run(
        DeepAgentReviewer(
            model=teacher,
            workspace=tmp_path,
            max_replacements_per_trial=10,
            teacher_middlewares=build_teacher_middlewares(),
        ).review(candidate)
    )

    call = json.loads(
        (tmp_path / "trial" / "teacher_llm_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    request = json.dumps(call["request"], ensure_ascii=False)
    assert request.count("## Triggered middleware guidance") == 1
    assert request.count("### `middleware:command-timeout`") == 1
    assert "result.txt" in request
    session = (tmp_path / "trial" / "teacher_session.jsonl").read_text(
        encoding="utf-8"
    )
    assert "Triggered middleware guidance" not in session


def test_teacher_candidate_format_middleware_flags_malformed_response(tmp_path):
    from harness_zero.review import ReviewCandidate

    (tmp_path / "trial").mkdir()
    candidate = ReviewCandidate(
        candidate_id="malformed-candidate",
        system_message=None,
        context=[HumanMessage(content="inspect files").model_dump(mode="json")],
        original=AssistantResponse(reasoning="I should inspect first."),
        raw_original=AIMessage(
            content="", additional_kwargs={"reasoning_content": "I should inspect first."}
        ).model_dump(mode="json"),
        candidate_issue="student emitted reasoning but no visible response or supported tool call",
    )
    teacher = ToolFriendlyFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_review",
                        "args": {
                            "decision": "PASS",
                            "replacement": None,
                            "components_used": [],
                            "reason": "accepted for test",
                        },
                        "id": "submit",
                    }
                ],
            )
        ]
    )

    asyncio.run(
        DeepAgentReviewer(
            model=teacher,
            workspace=tmp_path,
            max_replacements_per_trial=10,
            teacher_middlewares=build_teacher_middlewares(),
        ).review(candidate)
    )

    call = json.loads(
        (tmp_path / "trial" / "teacher_llm_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    request = json.dumps(call["request"], ensure_ascii=False)
    assert request.count("### `middleware:candidate-format`") == 1
    assert request.count("### `middleware:stall`") == 1
    assert "A PASS preserves this malformed response" in request


def test_teacher_session_persists_and_receives_only_student_delta(tmp_path):
    from harness_zero.review import ReviewCandidate

    (tmp_path / "trial").mkdir()

    task = HumanMessage(content="task").model_dump(mode="json")
    accepted = AIMessage(
        content="",
        tool_calls=[
            {"name": "execute", "args": {"command": "pwd"}, "id": "call-1"}
        ],
    ).model_dump(mode="json")
    observation = ToolMessage(content="/workdir", tool_call_id="call-1").model_dump(
        mode="json"
    )

    def candidate(candidate_id: str, context: list[dict]) -> ReviewCandidate:
        return ReviewCandidate(
            candidate_id=candidate_id,
            system_message=None,
            context=context,
            original=AssistantResponse(content="done"),
            raw_original=AIMessage(content="done").model_dump(mode="json"),
        )

    teacher = ToolFriendlyFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_review",
                        "args": {
                            "decision": "PASS",
                            "replacement": None,
                            "components_used": [],
                            "reason": "sound action",
                        },
                        "id": f"submit-{candidate_id}",
                    }
                ],
            )
            for candidate_id in ("first-candidate", "second-candidate")
        ]
    )
    reviewer = DeepAgentReviewer(
        model=teacher,
        workspace=tmp_path,
        max_replacements_per_trial=10,
        teacher_middlewares=[],
    )

    asyncio.run(reviewer.review(candidate("first-candidate", [task])))
    asyncio.run(
        reviewer.review(candidate("second-candidate", [task, accepted, observation]))
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "trial" / "teacher_llm_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 2
    second_request = json.dumps(rows[1]["request"])
    assert "first-candidate" in second_request
    assert "second-candidate" in second_request
    human_messages = [
        message
        for message in rows[1]["request"]
        if message.get("type") in {"human", "user"}
    ]
    latest_content = human_messages[-1]["content"]
    latest_text = "\n".join(
        block["text"]
        for block in latest_content
        if isinstance(block, dict) and block.get("type") == "text"
    )
    assert latest_text.startswith("# Student update — turn 1")
    assert "## New student-visible events" in latest_text
    assert "### 1. Tool result" in latest_text
    assert "/workdir" in latest_text
    assert "## Current unexecuted proposal" in latest_text
    assert "This is a final response with no tool call." in latest_text
    assert '"new_student_events"' not in latest_text
    assert '"proposal"' not in latest_text

    session_events = [
        json.loads(line)
        for line in (tmp_path / "trial" / "teacher_session.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert session_events[0]["new_student_events"][0]["content"] == "task"
    assert session_events[1]["new_student_events"] == [observation]
    assert "student_system_message" not in session_events[1]
    assert all("replacements_remaining" not in event for event in session_events)


class ToolFriendlyFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_local_graph_executes_replacement_not_original(tmp_path):
    executed: list[str] = []

    @tool
    def execute(command: str) -> str:
        """Record a command as if it ran in the sandbox."""
        executed.append(command)
        return "ok"

    student = ToolFriendlyFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "printf unsafe > evidence.txt"},
                        "id": "bad",
                    }
                ],
            ),
            AIMessage(content="Verified and complete."),
        ]
    )

    def decide(candidate):
        if candidate.original.tool_call is not None:
            return ReviewSubmission(
                candidate_id=candidate.candidate_id,
                decision="REPLACE",
                replacement=AssistantResponse(
                    reasoning="Write a fresh output without destroying evidence.",
                    tool_call=ExecuteCall(command="printf safe > result.txt"),
                ),
                reason="preserve evidence",
            )
        return ReviewSubmission(
            candidate_id=candidate.candidate_id,
            decision="PASS",
            reason="completion follows a successful observation",
        )

    from langchain.agents import create_agent

    graph = create_agent(
        model=student,
        tools=[execute],
        middleware=[HarnessReviewMiddleware(StaticReviewer(decide), _store(tmp_path))],
    )
    recorder = TrajectoryRecorder(excluded_scopes={"teacher"})
    result = asyncio.run(
        graph.ainvoke(
            {"messages": [{"role": "user", "content": "make output"}]},
            config={"callbacks": [recorder]},
        )
    )
    assert executed == ["printf safe > result.txt"]
    assert result["messages"][-1].content == "Verified and complete."
    trajectory = recorder.build_atif(
        agent_name="test",
        agent_version="0",
        model_name="fake",
        instruction="make output",
    )
    commands = [
        call.arguments["command"]
        for step in trajectory.steps
        for call in step.tool_calls or []
    ]
    assert commands == ["printf safe > result.txt"]
    raw_commands = [
        call["args"]["command"]
        for row in recorder.sidecar_records()
        for call in (row.get("response") or {}).get("tool_calls", [])
    ]
    assert raw_commands == ["printf unsafe > evidence.txt"]


def test_sft_preserves_multimodal_user_blocks(tmp_path):
    store = _store(tmp_path)
    from harness_zero.review import ReviewCandidate

    image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }
    candidate = ReviewCandidate(
        candidate_id="multimodal",
        system_message=SystemMessage(content="student system").model_dump(mode="json"),
        context=[
            HumanMessage(
                content=[{"type": "text", "text": "inspect this"}, image]
            ).model_dump(mode="json")
        ],
        original=AssistantResponse(tool_call=ExecuteCall(command="file input.png")),
        raw_original=AIMessage(content="").model_dump(mode="json"),
    )
    review = ReviewSubmission(
        candidate_id=candidate.candidate_id,
        decision="PASS",
        reason="inspection is required",
    )
    store.append_review(candidate, review, candidate.original)
    store.finalize(reward=1.0)

    output = tmp_path / "sft.jsonl"
    build_sft_file([store.root], output)
    row = json.loads(output.read_text(encoding="utf-8"))
    user = next(message for message in row["messages"] if message["role"] == "user")
    assert user["content"] == [{"type": "text", "text": "inspect this"}, image]


def test_inline_thinking_is_stored_as_reasoning():
    response = AssistantResponse.from_message(
        AIMessage(content="<think>check evidence first</think>\n\nI will inspect it.")
    )
    assert response.reasoning == "check evidence first"
    assert response.content == "I will inspect it."


def test_responses_reasoning_summary_is_stored_as_reasoning():
    response = AssistantResponse.from_message(
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "inspect evidence"},
                        {"type": "summary_text", "text": "then repair"},
                    ],
                },
                {"type": "text", "text": "I will inspect it."},
            ]
        )
    )
    assert response.reasoning == "inspect evidence\nthen repair"
    assert response.content == "I will inspect it."


def test_rollout_command_is_explicit(tmp_path):
    command = shell_command(
        build_rollout_command(
            repo_root=tmp_path,
            dataset=tmp_path / "tb-dev",
            components_dir=COMPONENTS,
            teacher_middleware_factory="harness_bank.uspto.middlewares:build_teacher_middlewares",
            tasks=["task-a", "task-b"],
            attempts=2,
            concurrency=20,
            student_model="openai:student-checkpoint",
            student_base_url="http://127.0.0.1:9000/v1",
            student_reasoning_effort="medium",
            student_reasoning_enabled=None,
            teacher_provider="openai",
            teacher_model="gpt-5.6-sol",
            teacher_reasoning_effort="high",
            job_name="hz-positive-01",
            output_dir=tmp_path / "jobs",
            student_harness_dir=str(tmp_path / "student-bank"),
        )
    )
    assert "gpt-5.6-sol" in command
    assert "--env docker" in command
    assert "--env e2b" not in command.lower()
    assert f'student_harness_dir="{tmp_path / "student-bank"}"' in command
    assert "max_replacements_per_trial=5" in command
    assert f'components_dir="{COMPONENTS}"' in command
    assert (
        "teacher_middleware_factory="
        '"harness_bank.uspto.middlewares:build_teacher_middlewares"'
    ) in command
    assert "openai:student-checkpoint" in command
    assert "HARNESS_ZERO_GATEWAY_URL=http://127.0.0.1:9000/v1" in command
    assert "HARNESS_ZERO_GATEWAY_TOKEN=dummy" in command


def test_official_openai_command_sources_env_without_embedding_key_or_dummy(tmp_path):
    command = shell_command(
        build_rollout_command(
            repo_root=tmp_path,
            dataset=tmp_path / "tb-dev",
            components_dir=COMPONENTS,
            teacher_middleware_factory="harness_bank.uspto.middlewares:build_teacher_middlewares",
            tasks=["task-a"],
            attempts=1,
            concurrency=1,
            student_model="openai:gpt-5.4-nano",
            student_base_url=None,
            student_reasoning_effort="medium",
            student_reasoning_enabled=None,
            teacher_provider="openai",
            teacher_model="gpt-5.6-sol",
            teacher_reasoning_effort="high",
            job_name="official-openai",
            output_dir=tmp_path / "jobs",
        )
    )

    assert f"--env-file {tmp_path / '.env'}" in command
    assert "gpt-5.6-sol" in command
    assert "gpt-5.4-nano" in command
    assert 'api_key\\\":\\\"dummy' not in command
    assert "'HARNESS_ZERO_GATEWAY_TOKEN=${OPENAI_API_KEY}'" in command


def test_openrouter_student_command_uses_openrouter_reasoning_options(tmp_path):
    command = shell_command(
        build_rollout_command(
            repo_root=tmp_path,
            dataset=tmp_path / "tb-dev",
            components_dir=COMPONENTS,
            teacher_middleware_factory="harness_bank.uspto.middlewares:build_teacher_middlewares",
            tasks=["task-a"],
            attempts=1,
            concurrency=1,
            student_model="openrouter:qwen/qwen3.5-9b",
            student_base_url=None,
            student_reasoning_effort=None,
            student_reasoning_enabled=True,
            teacher_provider="openai",
            teacher_model="gpt-5.6-sol",
            teacher_reasoning_effort="high",
            job_name="qwen-openrouter",
            output_dir=tmp_path / "jobs",
        )
    )

    assert "openrouter:qwen/qwen3.5-9b" in command
    assert "gpt-5.6-sol" in command
    assert "HARNESS_ZERO_GATEWAY_URL=https://openrouter.ai/api/v1" in command
    assert "'HARNESS_ZERO_GATEWAY_TOKEN=${OPENROUTER_API_KEY}'" in command
    assert "use_responses_api" not in command
    assert "output_version" not in command
    assert "summary" not in command
    assert '\"reasoning\":{\"enabled\":true}' in command
    assert "subagent_model" not in command
    assert "subagent_reasoning_enabled" not in command


def test_openrouter_student_rejects_base_url_override(tmp_path):
    with pytest.raises(ValueError, match="official endpoint"):
        build_rollout_command(
            repo_root=tmp_path,
            dataset=tmp_path / "tb-dev",
            components_dir=COMPONENTS,
            teacher_middleware_factory="harness_bank.uspto.middlewares:build_teacher_middlewares",
            tasks=["task-a"],
            attempts=1,
            concurrency=1,
            student_model="openrouter:qwen/qwen3.5-9b",
            student_base_url="https://example.invalid/v1",
            student_reasoning_effort=None,
            student_reasoning_enabled=True,
            teacher_provider="openai",
            teacher_model="gpt-5.6-sol",
            teacher_reasoning_effort="high",
            job_name="bad-openrouter",
            output_dir=tmp_path / "jobs",
        )


def test_openrouter_student_rejects_reasoning_effort(tmp_path):
    with pytest.raises(ValueError, match="does not accept a reasoning effort"):
        build_rollout_command(
            repo_root=tmp_path,
            dataset=tmp_path / "tb-dev",
            components_dir=COMPONENTS,
            teacher_middleware_factory="harness_bank.uspto.middlewares:build_teacher_middlewares",
            tasks=["task-a"],
            attempts=1,
            concurrency=1,
            student_model="openrouter:qwen/qwen3.5-9b",
            student_base_url=None,
            student_reasoning_effort="medium",
            student_reasoning_enabled=True,
            teacher_provider="openai",
            teacher_model="gpt-5.6-sol",
            teacher_reasoning_effort="high",
            job_name="bad-reasoning-effort",
            output_dir=tmp_path / "jobs",
        )


def _azure_command(tmp_path, **overrides):
    kwargs = dict(
        repo_root=tmp_path,
        dataset=tmp_path / "spreadsheetbench-verified",
        components_dir=COMPONENTS,
        teacher_middleware_factory=None,
        tasks=["task-a"],
        attempts=1,
        concurrency=1,
        student_model="azure:DeepSeek-V4-Pro",
        student_base_url=None,
        student_reasoning_effort="high",
        student_reasoning_enabled=None,
        teacher_provider="passthrough",
        teacher_model="none",
        teacher_reasoning_effort="none",
        job_name="azure-student",
        output_dir=tmp_path / "jobs",
    )
    kwargs.update(overrides)
    return shell_command(build_rollout_command(**kwargs))


def test_azure_student_command_uses_env_gateway_and_thinking_flag(tmp_path):
    command = _azure_command(tmp_path)

    assert "azure:DeepSeek-V4-Pro" in command
    assert "'HARNESS_ZERO_GATEWAY_URL=${AZURE_OPENAI_ENDPOINT}'" in command
    assert "'HARNESS_ZERO_GATEWAY_TOKEN=${AZURE_OPENAI_API_KEY}'" in command
    assert "HARNESS_ZERO_GATEWAY_THINKING=enabled" in command
    assert '"reasoning_effort":"high"' in command
    assert "use_responses_api" not in command
    assert "output_version" not in command


def test_azure_student_rejects_base_url_override(tmp_path):
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        _azure_command(tmp_path, student_base_url="https://example.invalid/v1")


def test_azure_student_rejects_reasoning_enabled(tmp_path):
    with pytest.raises(ValueError, match="reasoning effort, not enabled"):
        _azure_command(tmp_path, student_reasoning_enabled=True)


def test_azure_student_rejects_unknown_effort(tmp_path):
    with pytest.raises(ValueError, match="low, high or max"):
        _azure_command(tmp_path, student_reasoning_effort="medium")


def test_azure_student_model_build_reads_env_and_skips_teacher_throttle(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.invalid/v1")
    agent = HarnessZeroMinisweAgent(
        logs_dir=tmp_path,
        model_name="azure:DeepSeek-V4-Pro",
        model_kwargs={"reasoning_effort": "high"},
        teacher_model="none",
        teacher_provider="passthrough",
        components_dir=str(COMPONENTS),
    )

    model = agent._build_model()

    from langchain_deepseek import ChatDeepSeek

    assert isinstance(model, ChatDeepSeek)
    replacement = AssistantResponse(reasoning="Check evidence.", tool_call=ExecuteCall(command="ls"))
    payload = model._get_request_payload([replacement.to_message(candidate_id="check")])
    assert payload["messages"][0]["reasoning_content"] == "Check evidence."
    assert model.model_name == "DeepSeek-V4-Pro"
    assert str(model.api_base).rstrip("/") == "https://azure.invalid/v1"


def test_azure_student_model_build_requires_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    agent = HarnessZeroMinisweAgent(
        logs_dir=tmp_path,
        model_name="azure:DeepSeek-V4-Pro",
        model_kwargs={"reasoning_effort": "high"},
        teacher_model="none",
        teacher_provider="passthrough",
        components_dir=str(COMPONENTS),
    )

    with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
        agent._build_model()


'''REMOVED_HINT_TESTS
    with pytest.raises(ValueError, match="PASS must not include a hint"):
        HintReviewSubmission(
            candidate_id="c1", decision="PASS", hint="do x", reason="ok"
        )
    with pytest.raises(ValueError, match="REVERT_HINT requires a non-empty hint"):
        HintReviewSubmission(candidate_id="c1", decision="REVERT_HINT", reason="ok")
    ok = HintReviewSubmission(
        candidate_id="c1", decision="REVERT_HINT", hint="probe all actions in one loop", reason="process"
    )
    assert ok.hint.startswith("probe")


def test_revert_hint_retries_with_user_hint_and_drops_rejected(tmp_path):
    rejected = AIMessage(
        content="I will probe one action.",
        tool_calls=[{"name": "execute", "args": {"command": "printf 'ACTION1\\n' | python3 /game.py"}, "id": "call-1"}],
    )
    accepted = AIMessage(
        content="I will probe all actions in one loop.",
        tool_calls=[{"name": "execute", "args": {"command": "for a in ACTION1 ACTION2; do printf \"$a\\n\" | python3 /game.py; done"}, "id": "call-2"}],
    )
    responses = iter([ModelResponse(result=[rejected]), ModelResponse(result=[accepted])])
    seen_requests = []

    async def handler(request):
        seen_requests.append(request)
        return next(responses)

    submissions = iter([
        HintReviewSubmission(
            candidate_id="placeholder", decision="REVERT_HINT",
            hint="Batch every action probe into one scripted loop instead of one probe per command.",
            reason="one-action probes waste the turn budget",
        ),
        HintReviewSubmission(
            candidate_id="placeholder", decision="PASS", reason="batched probe is correct",
        ),
    ])

    class SequentialReviewer:
        async def review(self, candidate):
            submission = next(submissions)
            return submission.model_copy(update={"candidate_id": candidate.candidate_id})

    store = _store(tmp_path)
    middleware = HarnessReviewMiddleware(SequentialReviewer(), store)
    result = asyncio.run(middleware.awrap_model_call(_request(), handler))

    assert len(result.result) == 2
    assert isinstance(result.result[0], HumanMessage)
    assert "Batch every action probe" in result.result[0].content
    assert result.result[1] is accepted
    assert rejected not in result.result
    # the retry request carried the hint as a user message
    retry_messages = seen_requests[1].messages
    assert isinstance(retry_messages[-1], HumanMessage)
    assert rejected not in retry_messages
    # every regenerated candidate was reviewed; hint turn recorded without accepted
    events = store.events()
    assert [e["review"]["decision"] for e in events] == ["REVERT_HINT", "PASS"]
    assert events[0]["accepted"] is None
    assert events[1]["accepted"]["tool_call"]["command"].startswith("for a in")
    # each handler call consumed one model turn
    assert middleware._turn_count == 2
    assert middleware._replacement_count == 1


def test_revert_hint_counts_against_intervention_budget(tmp_path):
    first = AIMessage(content="a", tool_calls=[{"name": "execute", "args": {"command": "ls"}, "id": "c1"}])
    second = AIMessage(content="b", tool_calls=[{"name": "execute", "args": {"command": "pwd"}, "id": "c2"}])
    responses = iter([ModelResponse(result=[first]), ModelResponse(result=[second])])

    async def handler(_request):
        return next(responses)

    class AlwaysHintReviewer:
        async def review(self, candidate):
            return HintReviewSubmission(
                candidate_id=candidate.candidate_id, decision="REVERT_HINT",
                hint="do better", reason="always hint",
            )

    store = _store(tmp_path)
    middleware = HarnessReviewMiddleware(AlwaysHintReviewer(), store, max_replacements=1)
    result = asyncio.run(middleware.awrap_model_call(_request(), handler))
    # second candidate passed without review because the budget was exhausted
    assert result.result[-1] is second
    events = store.events()
    assert events[-1]["review"]["decision"] == "PASS"
    assert "budget exhausted" in events[-1]["review"]["reason"]


def test_revert_hint_retry_cap_passes_without_further_review(tmp_path):
    responses = iter(
        ModelResponse(result=[AIMessage(content="x", tool_calls=[{"name": "execute", "args": {"command": f"echo {i}"}, "id": f"c{i}"}])])
        for i in range(10)
    )

    async def handler(_request):
        return next(responses)

    class AlwaysHintReviewer:
        async def review(self, candidate):
            return HintReviewSubmission(
                candidate_id=candidate.candidate_id, decision="REVERT_HINT",
                hint="again", reason="always hint",
            )

    store = _store(tmp_path)
    middleware = HarnessReviewMiddleware(AlwaysHintReviewer(), store, max_replacements=20)
    result = asyncio.run(middleware.awrap_model_call(_request(), handler))
    events = store.events()
    assert [e["review"]["decision"] for e in events].count("REVERT_HINT") == HarnessReviewMiddleware._MAX_HINT_RETRIES
    assert events[-1]["review"]["decision"] == "PASS"
    assert "hint retry cap" in events[-1]["review"]["reason"]
    assert len(result.result) == HarnessReviewMiddleware._MAX_HINT_RETRIES + 1


'''
