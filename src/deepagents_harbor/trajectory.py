"""Always-on trajectory recording for deepagents harbor harnesses.

Capture point: the LLM gateway (langchain callback boundary), deliberately NOT
the final graph state. The gateway sees every request exactly as sent — system
prompt included, after skills/memory/priming injection and after any
summarization rewrites — and every raw response (reasoning blocks, tool calls,
usage). The final state alone would lose all three.

Artifacts per trial, written to the agent's `logs_dir` (harbor downloads it to
`<trial>/agent/`; failures here never fail the rollout):

- `trajectory.json` — ATIF Trajectory (harbor spec, see
  `harbor.models.trajectories`): main-agent flow only — user instruction, one
  `agent` step per LLM response (message + reasoning + tool_calls + metrics),
  tool results attached as the same step's observation.
- `llm_calls.jsonl` — full-fidelity sidecar: EVERY LLM call (including
  subagent-internal and summarization calls) with the complete request message
  list and raw response. Trajectory analysis that needs the exact prompt of
  step N goes here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage

from harbor.models.trajectories.agent import Agent
from harbor.models.trajectories.final_metrics import FinalMetrics
from harbor.models.trajectories.metrics import Metrics
from harbor.models.trajectories.observation import Observation
from harbor.models.trajectories.observation_result import ObservationResult
from harbor.models.trajectories.step import Step
from harbor.models.trajectories.tool_call import ToolCall
from harbor.models.trajectories.trajectory import Trajectory
from harbor.utils.trajectory_utils import format_trajectory_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_to_sidecar_dict(message: BaseMessage) -> dict[str, Any]:
    """Lossless-ish message dump for the sidecar (content blocks kept as-is)."""
    data = message.model_dump(mode="json", exclude_none=True)
    data.pop("id", None)
    return data


def _content_to_text(content: Any) -> str:
    """Render message content (plain string or content blocks) as plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _text_and_reasoning(message: AIMessage) -> tuple[str, str | None]:
    """Split an AI message into visible text and reasoning/thinking content."""
    from deepagents_harbor.message_content import assistant_text

    reasoning, text = assistant_text(message.model_dump())
    return text, reasoning or None


def _metrics(usage: dict[str, Any] | None) -> Metrics | None:
    if not usage:
        return None
    details = usage.get("input_token_details") or {}
    return Metrics(
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
        cached_tokens=details.get("cache_read"),
    )


class TrajectoryRecorder(AsyncCallbackHandler):
    """LangChain callback that records the raw LLM/tool event stream.

    Attached by `DeepHarnessAgent.run()` via `ainvoke(config={"callbacks"})`;
    nested subagent graphs propagate the same callbacks, so their LLM calls
    show up too and are separated by walking the run-parent chain (any LLM
    call whose ancestors include a tool run belongs to a subagent/tool).
    """

    def __init__(self, *, excluded_scopes: set[str] | None = None) -> None:
        super().__init__()
        self._excluded_scopes = excluded_scopes or set()
        self.events: list[dict[str, Any]] = []
        self._parent_of: dict[str, str | None] = {}
        self._tool_run_ids: set[str] = set()

    # ------------------------------------------------------------------ LLM
    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._parent_of[str(run_id)] = str(parent_run_id) if parent_run_id else None
        flat = messages[0] if messages else []
        md = metadata or {}
        self.events.append(
            {
                "type": "llm_start",
                "ts": _utc_now(),
                "run_id": str(run_id),
                "node": md.get("langgraph_node"),
                "trace_scope": md.get("hz_trace_scope"),
                # Middleware-internal calls (e.g. conversation summarization)
                # carry langchain's lc_source tag; they stay out of the ATIF
                # main flow but remain in the sidecar.
                "lc_source": md.get("lc_source"),
                # Explicit worker tag (self-harness tool passes it via config
                # metadata): deterministic subagent attribution that does not
                # depend on the run-parent chain.
                "worker": md.get("self_harness_worker"),
                "request": [_message_to_sidecar_dict(m) for m in flat],
            }
        )

    async def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        message = None
        try:
            message = response.generations[0][0].message
        except (IndexError, AttributeError):
            pass
        if not isinstance(message, BaseMessage):
            message = None
        self.events.append(
            {
                "type": "llm_end",
                "ts": _utc_now(),
                "run_id": str(run_id),
                "message_obj": message,  # live object for ATIF assembly
                "message": _message_to_sidecar_dict(message) if message else None,
            }
        )

    async def on_llm_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        self.events.append(
            {"type": "llm_error", "ts": _utc_now(), "run_id": str(run_id),
             "error": str(error)}
        )

    async def on_custom_event(
        self,
        name: str,
        data: Any,
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        if name != "hz_accepted_response":
            return
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, AIMessage):
            raise TypeError("hz_accepted_response requires an AIMessage")
        self.events.append(
            {
                "type": "accepted_response",
                "ts": _utc_now(),
                "run_id": str(run_id),
                "message_obj": message,
            }
        )

    # ----------------------------------------------------------------- tool
    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        self._parent_of[str(run_id)] = str(parent_run_id) if parent_run_id else None
        self._tool_run_ids.add(str(run_id))
        self.events.append(
            {
                "type": "tool_start",
                "ts": _utc_now(),
                "run_id": str(run_id),
                "name": serialized.get("name"),
            }
        )

    async def on_tool_end(
        self, output: Any, *, run_id: Any, **kwargs: Any
    ) -> None:
        content = getattr(output, "content", output)
        tool_call_id = getattr(output, "tool_call_id", None)
        self.events.append(
            {
                "type": "tool_end",
                "ts": _utc_now(),
                "run_id": str(run_id),
                "tool_call_id": tool_call_id,
                "content": content if isinstance(content, str) else str(content),
            }
        )

    async def on_tool_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        self.events.append(
            {"type": "tool_error", "ts": _utc_now(), "run_id": str(run_id),
             "error": str(error)}
        )

    # ------------------------------------------------------------- analysis
    def _is_subagent_call(self, run_id: str) -> bool:
        """True if the run's ancestor chain passes through a tool run."""
        seen: set[str] = set()
        rid: str | None = run_id
        while rid and rid not in seen:
            seen.add(rid)
            if rid in self._tool_run_ids:
                return True
            rid = self._parent_of.get(rid)
        return False

    def _is_worker_run(self, run_id: str, start: dict[str, Any] | None) -> bool:
        """Subagent attribution: explicit worker metadata tag, or a tool run
        anywhere in the ancestor chain."""
        if start and start.get("worker"):
            return True
        return self._is_subagent_call(run_id)

    def sidecar_records(self) -> list[dict[str, Any]]:
        """One record per LLM call, request joined with response/error."""
        starts = {e["run_id"]: e for e in self.events if e["type"] == "llm_start"}
        records: list[dict[str, Any]] = []
        for seq, event in enumerate(self.events):
            if event["type"] not in ("llm_end", "llm_error"):
                continue
            start = starts.get(event["run_id"], {})
            if start.get("trace_scope") in self._excluded_scopes:
                continue
            records.append(
                {
                    "seq": seq,
                    "ts": event["ts"],
                    "run_id": event["run_id"],
                    "node": start.get("node"),
                    "subagent": self._is_worker_run(event["run_id"], start),
                    "worker": start.get("worker"),
                    "lc_source": start.get("lc_source"),
                    "request": start.get("request"),
                    "response": event.get("message"),
                    "error": event.get("error"),
                }
            )
        return records

    def build_atif(
        self,
        *,
        agent_name: str,
        agent_version: str,
        model_name: str,
        instruction: str,
    ) -> Trajectory:
        """Assemble the main-agent flow into an ATIF Trajectory."""
        steps: list[Step] = [
            Step(step_id=1, timestamp=self.events[0]["ts"] if self.events else _utc_now(),
                 source="user", message=instruction)
        ]
        starts = {e["run_id"]: e for e in self.events if e["type"] == "llm_start"}
        reviewed = any(e["type"] == "accepted_response" for e in self.events)
        pending_student_call: tuple[AIMessage, dict[str, Any], str] | None = None
        system_prompt: str | None = None
        total_in = total_out = total_cached = 0
        pending: dict[int, dict[str, Any]] = {}  # step_index -> {"call_ids": set, "results": []}

        for event in self.events:
            if event["type"] == "llm_end" and event.get("message_obj") is not None:
                start = starts.get(event["run_id"])
                if (
                    start
                    and (
                        start.get("trace_scope") in self._excluded_scopes
                        # Summarization calls are context maintenance, not
                        # agent steps: they must never become a step or pose
                        # as the pending student call in reviewed mode.
                        or start.get("lc_source") == "summarization"
                    )
                ) or self._is_worker_run(event["run_id"], start):
                    continue
                if reviewed:
                    pending_student_call = (
                        event["message_obj"],
                        start or {},
                        event["ts"],
                    )
                    continue
                ai: AIMessage = event["message_obj"]
                step_start = start or {}
                step_ts = event["ts"]
            elif event["type"] == "accepted_response":
                if pending_student_call is None:
                    raise ValueError("accepted response has no student model call")
                original, step_start, step_ts = pending_student_call
                pending_student_call = None
                ai = event["message_obj"]
                if ai.usage_metadata is None and original.usage_metadata:
                    ai = ai.model_copy(update={"usage_metadata": original.usage_metadata})
            else:
                ai = None

            if ai is not None:
                text, reasoning = _text_and_reasoning(ai)
                tool_calls = [
                    ToolCall(
                        tool_call_id=tc.get("id") or f"call_{len(steps)}_{i}",
                        function_name=tc.get("name", ""),
                        arguments=tc.get("args") or {},
                    )
                    for i, tc in enumerate(ai.tool_calls or [])
                ]
                if system_prompt is None:
                    request = step_start.get("request") or []
                    if request and request[0].get("type") == "system":
                        system_prompt = _content_to_text(request[0].get("content"))
                usage = ai.usage_metadata or {}
                total_in += usage.get("input_tokens") or 0
                total_out += usage.get("output_tokens") or 0
                total_cached += (usage.get("input_token_details") or {}).get(
                    "cache_read"
                ) or 0
                step = Step(
                    step_id=len(steps) + 1,
                    timestamp=step_ts,
                    source="agent",
                    message=text,
                    reasoning_content=reasoning,
                    tool_calls=tool_calls or None,
                    metrics=_metrics(usage),
                    llm_call_count=1,
                    extra=(
                        {"langgraph_node": step_start["node"]}
                        if step_start.get("node")
                        else None
                    ),
                )
                steps.append(step)
                if tool_calls:
                    pending[step.step_id] = {
                        "call_ids": {tc.tool_call_id for tc in tool_calls},
                        "results": [],
                    }
            if event["type"] == "tool_end":
                call_id = event.get("tool_call_id")
                for step_id, slot in pending.items():
                    if call_id in slot["call_ids"]:
                        slot["results"].append(
                            ObservationResult(
                                source_call_id=call_id, content=event["content"]
                            )
                        )
                        break

        for step in steps:
            slot = pending.get(step.step_id)
            if slot and slot["results"]:
                step.observation = Observation(results=slot["results"])

        return Trajectory(
            agent=Agent(
                name=agent_name,
                version=agent_version,
                model_name=model_name,
            ),
            steps=steps,
            notes=(
                "Main-agent flow only; subagent-internal and summarization LLM "
                "calls (and the exact request messages of every step, system "
                "prompt included) are in llm_calls.jsonl next to this file."
            ),
            final_metrics=FinalMetrics(
                total_prompt_tokens=total_in or None,
                total_completion_tokens=total_out or None,
                total_cached_tokens=total_cached or None,
                total_steps=len(steps),
            ),
            extra={"system_prompt": system_prompt} if system_prompt else None,
        )


def write_trajectory_artifacts(
    recorder: TrajectoryRecorder,
    *,
    logs_dir: Path,
    agent_name: str,
    agent_version: str,
    model_name: str,
    instruction: str,
    logger: logging.Logger,
) -> None:
    """Write trajectory.json (ATIF) + llm_calls.jsonl; never raises."""
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        trajectory = recorder.build_atif(
            agent_name=agent_name,
            agent_version=agent_version,
            model_name=model_name,
            instruction=instruction,
        )
        (logs_dir / "trajectory.json").write_text(
            format_trajectory_json(trajectory.to_json_dict()), encoding="utf-8"
        )
        with (logs_dir / "llm_calls.jsonl").open("w", encoding="utf-8") as fh:
            for record in recorder.sidecar_records():
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        logger.info(
            "[trajectory] %d steps, %d LLM calls -> %s",
            len(trajectory.steps),
            len(recorder.sidecar_records()),
            logs_dir,
        )
    except Exception:
        logger.exception("[trajectory] failed to write artifacts (ignored)")
