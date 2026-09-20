"""Bash-facing, one-level subagent CLI for the miniswe student."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from openai import OpenAI

if __package__:
    from deepagents_harbor.message_content import normalize_assistant
else:
    from message_content import normalize_assistant


SYSTEM_PROMPT = """You are a subagent in a Linux task sandbox. Complete only the
bounded task you were given. Use the execute tool to inspect and modify the
shared workspace. Verify your result, then return a concise final report to the
parent. You cannot delegate to another subagent."""

MAX_TURNS = 20
COMMAND_TIMEOUT_SEC = 120

EXECUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "execute",
        "description": "Execute one bash command in a fresh shell.",
        "parameters": {
            "type": "object",
            "required": ["command"],
            "additionalProperties": False,
            "properties": {"command": {"type": "string"}},
        },
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument("task")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", type=json.loads, required=True)
    parser.add_argument("--reasoning-prefill", choices=("none", "think"), default="none")
    return parser


@contextmanager
def _concurrency_slot(limit: int = 4):
    root = Path(tempfile.gettempdir()) / "hz-agent-slots"
    root.mkdir(mode=0o700, exist_ok=True)
    claimed: Path | None = None
    for index in range(limit):
        path = root / str(index)
        if path.exists() and not _slot_owner_alive(path):
            path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        claimed = path
        break
    if claimed is None:
        raise RuntimeError("subagent concurrency limit reached; do not retry")
    try:
        yield
    finally:
        claimed.unlink(missing_ok=True)


def _slot_owner_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8"))
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def run_subagent(
    task: str,
    *,
    model: str,
    reasoning: dict,
    reasoning_prefill: str = "none",
) -> str:
    if int(os.environ.get("HARNESS_ZERO_AGENT_DEPTH", "0")) >= 1:
        raise RuntimeError("subagent depth limit reached; do not retry")
    gateway = os.environ.get("HARNESS_ZERO_GATEWAY_URL")
    token = os.environ.get("HARNESS_ZERO_GATEWAY_TOKEN")
    if not gateway or not token:
        raise RuntimeError("HARNESS_ZERO_GATEWAY_URL and HARNESS_ZERO_GATEWAY_TOKEN are required")
    if not model:
        raise RuntimeError("subagent model must be explicit")
    if not isinstance(reasoning, dict) or not reasoning:
        raise RuntimeError("subagent reasoning configuration must be explicit")

    client = OpenAI(base_url=gateway, api_key=token)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    child_env = dict(os.environ)
    child_env["HARNESS_ZERO_AGENT_DEPTH"] = "1"

    with _concurrency_slot():
        for turn in range(MAX_TURNS):
            print(f"[subagent] turn {turn + 1}/{MAX_TURNS}", file=sys.stderr)
            request = {
                "model": model,
                "messages": messages,
                "tools": [EXECUTE_TOOL],
                "tool_choice": "auto",
            }
            if "effort" in reasoning:
                request["reasoning_effort"] = reasoning["effort"]
            else:
                request["extra_body"] = {"reasoning": reasoning}
            if os.environ.get("HARNESS_ZERO_GATEWAY_THINKING") == "enabled":
                # DeepSeek-protocol gateways (Azure deployment) expect the
                # thinking flag on every request, like the host-side model.
                request.setdefault("extra_body", {})["thinking"] = {"type": "enabled"}
            response = client.chat.completions.create(
                **request,
            )
            message = response.choices[0].message
            normalized = normalize_assistant(
                message.model_dump(exclude_none=True),
                prefilled=reasoning_prefill == "think",
            )
            messages.append(normalized)
            if not message.tool_calls:
                final = normalized["content"] or ""
                _write_trajectory(messages)
                return final
            if len(message.tool_calls) != 1 or message.tool_calls[0].function.name != "execute":
                raise RuntimeError("subagent emitted an unsupported tool call")
            call = message.tool_calls[0]
            command = json.loads(call.function.arguments)["command"]
            completed = subprocess.run(
                ["bash", "-lc", command],
                text=True,
                capture_output=True,
                timeout=COMMAND_TIMEOUT_SEC,
                env=child_env,
                check=False,
            )
            observation = (
                completed.stdout + completed.stderr + f"\n[exit_code={completed.returncode}]"
            )
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": observation}
            )
    raise RuntimeError(f"subagent exceeded {MAX_TURNS} turns")


def _write_trajectory(messages: list[dict]) -> None:
    log_dir = os.environ.get("HARNESS_ZERO_SUBAGENT_LOG_DIR")
    if not log_dir:
        return
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", dir=path, delete=False
    )
    with handle:
        json.dump({"trainable": False, "messages": messages}, handle, ensure_ascii=False)


def main() -> None:
    args = _parser().parse_args()
    try:
        final = run_subagent(
            args.task,
            model=args.model,
            reasoning=args.reasoning,
            reasoning_prefill=args.reasoning_prefill,
        )
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"agent: {error}") from error
    print(final)


if __name__ == "__main__":
    main()
