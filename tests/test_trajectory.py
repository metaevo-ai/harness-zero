"""Trajectory recorder tests: gateway-level capture + ATIF assembly.

Runs a real deepagents graph against a fake chat model (no sandbox, no LLM),
then checks the recorded artifacts: system prompt, reasoning, tool calls and
tool responses must all be present and ATIF-valid.
"""

from __future__ import annotations

import asyncio
import json
import logging

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from deepagents import create_deep_agent

from harbor.utils.trajectory_validator import validate_trajectory

from deepagents_harbor.trajectory import (
    TrajectoryRecorder,
    write_trajectory_artifacts,
)


@tool
def add_one(x: int) -> str:
    """Add one to x."""
    return str(x + 1)


class _ToolFriendlyFake(FakeMessagesListChatModel):
    """FakeMessagesListChatModel with a no-op bind_tools (deepagents binds)."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN201, ARG002
        return self


def _fake_model() -> FakeMessagesListChatModel:
    first = AIMessage(
        content="",
        tool_calls=[{"name": "add_one", "args": {"x": 1}, "id": "tc_1"}],
        usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    )
    second = AIMessage(
        content=[
            {"type": "reasoning", "reasoning": "1+1 is 2, wrap up."},
            {"type": "text", "text": "The answer is 2."},
        ],
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 7,
            "total_tokens": 27,
            "input_token_details": {"cache_read": 5},
        },
    )
    return _ToolFriendlyFake(responses=[first, second])


def _run_graph(recorder: TrajectoryRecorder) -> None:
    graph = create_deep_agent(
        model=_fake_model(),
        tools=[add_one],
        system_prompt="You are a test agent.",
    )
    asyncio.run(
        graph.ainvoke(
            {"messages": [{"role": "user", "content": "add one to 1"}]},
            config={"callbacks": [recorder]},
        )
    )


def test_recorder_captures_full_requests_and_valid_atif(tmp_path):
    recorder = TrajectoryRecorder()
    _run_graph(recorder)

    # Gateway saw every request verbatim, system prompt included.
    starts = [e for e in recorder.events if e["type"] == "llm_start"]
    assert starts, "no llm_start captured"
    assert starts[0]["request"][0]["type"] == "system"
    assert "You are a test agent." in starts[0]["request"][0]["content"]

    trajectory = recorder.build_atif(
        agent_name="t", agent_version="0", model_name="fake",
        instruction="add one to 1",
    )
    assert trajectory.steps[0].source == "user"
    agent_steps = [s for s in trajectory.steps if s.source == "agent"]
    assert len(agent_steps) == 2

    first, second = agent_steps
    # tool call + its response paired on the same step
    assert first.tool_calls[0].function_name == "add_one"
    assert first.tool_calls[0].arguments == {"x": 1}
    assert first.observation is not None
    assert first.observation.results[0].source_call_id == "tc_1"
    assert first.observation.results[0].content == "2"
    # reasoning captured separately from visible text
    assert second.reasoning_content == "1+1 is 2, wrap up."
    assert second.message == "The answer is 2."
    # metrics aggregated
    assert trajectory.final_metrics.total_prompt_tokens == 31
    assert trajectory.final_metrics.total_completion_tokens == 10
    assert trajectory.final_metrics.total_cached_tokens == 5
    assert "You are a test agent." in trajectory.extra["system_prompt"]

    write_trajectory_artifacts(
        recorder,
        logs_dir=tmp_path,
        agent_name="t",
        agent_version="0",
        model_name="fake",
        instruction="add one to 1",
        logger=logging.getLogger("test"),
    )

    # harbor's own ATIF validator accepts the file
    trajectory_path = tmp_path / "trajectory.json"
    assert validate_trajectory(trajectory_path)

    # sidecar: one record per LLM call, full request + response
    sidecar = [
        json.loads(line)
        for line in (tmp_path / "llm_calls.jsonl").read_text().splitlines()
    ]
    assert len(sidecar) == 2
    assert sidecar[0]["request"][0]["type"] == "system"
    assert sidecar[0]["response"]["tool_calls"][0]["name"] == "add_one"
    assert sidecar[0]["subagent"] is False


def test_write_artifacts_never_raises_on_empty_recorder(tmp_path):
    recorder = TrajectoryRecorder()  # no events at all
    write_trajectory_artifacts(
        recorder,
        logs_dir=tmp_path,
        agent_name="t",
        agent_version="0",
        model_name="fake",
        instruction="x",
        logger=logging.getLogger("test"),
    )
    assert json.loads((tmp_path / "trajectory.json").read_text())["steps"]
    assert (tmp_path / "llm_calls.jsonl").read_text() == ""


def test_atif_preserves_responses_reasoning_summary():
    recorder = TrajectoryRecorder()
    message = AIMessage(
        content=[
            {
                "type": "reasoning",
                "summary": [
                    {"type": "summary_text", "text": "first"},
                    {"type": "summary_text", "text": "second"},
                ],
            },
            {"type": "text", "text": "done"},
        ]
    )
    recorder.events.extend(
        [
            {
                "type": "llm_start",
                "ts": "2026-08-29T00:00:00+00:00",
                "run_id": "r",
                "request": [],
            },
            {
                "type": "llm_end",
                "ts": "2026-08-29T00:00:01+00:00",
                "run_id": "r",
                "message_obj": message,
                "message": {},
            },
        ]
    )
    trajectory = recorder.build_atif(
        agent_name="t", agent_version="0", model_name="fake", instruction="task"
    )
    agent_step = next(step for step in trajectory.steps if step.source == "agent")
    assert agent_step.reasoning_content == "first\nsecond"
    assert agent_step.message == "done"


def test_private_trace_scope_is_excluded_from_student_artifacts():
    recorder = TrajectoryRecorder(excluded_scopes={"teacher"})
    student = AIMessage(content="student action")
    private = AIMessage(
        content="",
        tool_calls=[
            {"name": "submit_review", "args": {}, "id": "private-review"}
        ],
    )
    recorder.events.extend(
        [
            {
                "type": "llm_start",
                "ts": "2026-08-29T00:00:00+00:00",
                "run_id": "student",
                "request": [],
                "trace_scope": None,
            },
            {
                "type": "llm_end",
                "ts": "2026-08-29T00:00:01+00:00",
                "run_id": "student",
                "message_obj": student,
                "message": student.model_dump(mode="json"),
            },
            {
                "type": "llm_start",
                "ts": "2026-08-29T00:00:02+00:00",
                "run_id": "teacher",
                "request": [],
                "trace_scope": "teacher",
            },
            {
                "type": "llm_end",
                "ts": "2026-08-29T00:00:03+00:00",
                "run_id": "teacher",
                "message_obj": private,
                "message": private.model_dump(mode="json"),
            },
        ]
    )

    sidecar = recorder.sidecar_records()
    assert [record["run_id"] for record in sidecar] == ["student"]
    trajectory = recorder.build_atif(
        agent_name="t", agent_version="0", model_name="fake", instruction="task"
    )
    agent_steps = [step for step in trajectory.steps if step.source == "agent"]
    assert [step.message for step in agent_steps] == ["student action"]
