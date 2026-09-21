"""Build and launch Harbor commands for reviewed rollouts."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path


def load_task_ids(path: Path) -> list[str]:
    tasks = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not tasks:
        raise ValueError("task list is empty")
    if len(tasks) != len(set(tasks)):
        raise ValueError("task list contains duplicates")
    return tasks


def build_rollout_command(
    *,
    repo_root: Path,
    dataset: Path,
    components_dir: Path,
    teacher_middleware_factory: str | None,
    tasks: list[str],
    attempts: int,
    concurrency: int,
    student_model: str,
    student_base_url: str | None,
    student_reasoning_effort: str | None,
    student_reasoning_enabled: bool | None,
    teacher_provider: str,
    teacher_model: str,
    teacher_reasoning_effort: str,
    job_name: str,
    output_dir: Path,
    max_replacements_per_trial: int = 5,
    student_max_turns: int = 40,
    max_retries: int = 2,
    student_harness_dir: str | None = None,
    agent_timeout_multiplier: float | None = None,
    teacher_prompt_suffix: str | None = None,
    teacher_prompt_path: str | None = None,
    teacher_oracle_answer_dir: str | None = None,
    student_reasoning_prefill: str = "none",
    student_azure_api: str = "deepseek",
) -> list[str]:
    if student_reasoning_prefill not in {"none", "think"}:
        raise ValueError("student_reasoning_prefill must be none or think")
    if student_azure_api not in {"deepseek", "responses"}:
        raise ValueError("student_azure_api must be deepseek or responses")
    if student_reasoning_prefill != "none" and not student_base_url:
        raise ValueError("student_reasoning_prefill requires student_base_url")
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    if not 1 <= concurrency <= 100:
        raise ValueError("rollout concurrency must be between 1 and 100")
    if max_replacements_per_trial < 0:
        raise ValueError("max_replacements_per_trial must be non-negative")
    if student_max_turns <= 0:
        raise ValueError("student_max_turns must be positive")
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if agent_timeout_multiplier is not None and agent_timeout_multiplier <= 0:
        raise ValueError("agent_timeout_multiplier must be positive")
    explicit = {
        "student_model": student_model,
        "teacher_provider": teacher_provider,
        "teacher_model": teacher_model,
        "teacher_reasoning_effort": teacher_reasoning_effort,
        "job_name": job_name,
    }
    missing = [name for name, value in explicit.items() if not value]
    if missing:
        raise ValueError(f"rollout parameters must be explicit: {missing}")

    harbor = repo_root / ".venv" / "bin" / "harbor"
    executable = str(harbor) if harbor.is_file() else "harbor"
    gateway_extra_env: dict[str, str] = {}
    if student_model.startswith("openrouter:"):
        if student_base_url:
            raise ValueError("OpenRouter student must use its configured official endpoint")
        if student_reasoning_effort is not None:
            raise ValueError("OpenRouter student does not accept a reasoning effort")
        if student_reasoning_enabled is None:
            raise ValueError("OpenRouter student reasoning must be explicitly enabled or disabled")
        model_options = {"reasoning": {"enabled": student_reasoning_enabled}}
        inherited_gateway_url = "https://openrouter.ai/api/v1"
        inherited_gateway_token = "${OPENROUTER_API_KEY}"
    elif student_model.startswith("azure:"):
        if not student_model.split(":", 1)[1]:
            raise ValueError("Azure student deployment name must be explicit")
        if student_base_url:
            raise ValueError("Azure student uses AZURE_OPENAI_ENDPOINT from the env file")
        if student_reasoning_enabled is not None:
            raise ValueError("Azure student uses reasoning effort, not enabled")
        if student_reasoning_effort not in {"low", "high", "max"}:
            raise ValueError("azure student reasoning effort must be low, high or max")
        model_options = {
            # harness.py::_student_reasoning reads the chat-completions string
            # form; the host-side model build adds thinking/extra_body itself.
            "reasoning_effort": student_reasoning_effort
        }
        inherited_gateway_url = "${AZURE_OPENAI_ENDPOINT}"
        inherited_gateway_token = "${AZURE_OPENAI_API_KEY}"
        gateway_extra_env["HARNESS_ZERO_GATEWAY_THINKING"] = "enabled"
    else:
        if not student_reasoning_effort:
            raise ValueError("OpenAI student reasoning effort must be explicit")
        if student_reasoning_enabled is not None:
            raise ValueError("OpenAI student uses reasoning effort, not enabled")
        model_options = {
            "use_responses_api": True,
            "output_version": "responses/v1",
            "reasoning": {"effort": student_reasoning_effort, "summary": "detailed"},
        }
        inherited_gateway_url = student_base_url or "https://api.openai.com/v1"
        inherited_gateway_token = "dummy" if student_base_url else "${OPENAI_API_KEY}"
    configured_reasoning = model_options.get(
        "reasoning", {"effort": student_reasoning_effort}
    )
    if student_base_url:
        model_options = {
            "base_url": student_base_url,
            "api_key": "dummy",
            "use_responses_api": False,
            # harness.py::_student_reasoning requires the reasoning config to be
            # explicit for every student; chat completions only accept the
            # reasoning_effort string form (a responses-API dict would 400).
            "reasoning_effort": configured_reasoning["effort"],
        }
    model_kwargs = json.dumps(model_options, separators=(",", ":"))
    command = [
        executable,
        "run",
        "--path",
        str(dataset),
        "--env",
        "docker",
        "--env-file",
        str(repo_root / ".env"),
        "--agent",
        "harness_zero.harness:HarnessZeroMinisweAgent",
        "--model",
        student_model,
        "--agent-kwarg",
        f"model_kwargs={model_kwargs}",
        "--agent-kwarg",
        f'teacher_provider={json.dumps(teacher_provider)}',
        "--agent-kwarg",
        f'teacher_model={json.dumps(teacher_model)}',
        "--agent-kwarg",
        f'teacher_reasoning_effort={json.dumps(teacher_reasoning_effort)}',
        '--agent-kwarg',
        f'student_reasoning_prefill={json.dumps(student_reasoning_prefill)}',
        "--agent-kwarg",
        f"max_replacements_per_trial={max_replacements_per_trial}",
        "--agent-kwarg",
        f"student_max_turns={student_max_turns}",
        "--agent-kwarg",
        f"components_dir={json.dumps(str(components_dir))}",
        "--agent-kwarg",
        f"teacher_middleware_factory={json.dumps(teacher_middleware_factory)}",
        "--agent-env",
        f"HARNESS_ZERO_GATEWAY_URL={inherited_gateway_url}",
        "--agent-env",
        f"HARNESS_ZERO_GATEWAY_TOKEN={inherited_gateway_token}",
    ]
    for env_name, env_value in gateway_extra_env.items():
        command.extend(["--agent-env", f"{env_name}={env_value}"])
    command += [
        "--n-attempts",
        str(attempts),
        "--n-concurrent",
        str(concurrency),
        "--max-retries",
        str(max_retries),
        "--job-name",
        job_name,
        "--jobs-dir",
        str(output_dir),
        "--yes",
    ]
    if student_harness_dir is not None:
        command[command.index("--n-attempts"):command.index("--n-attempts")] = [
            "--agent-kwarg",
            f"student_harness_dir={json.dumps(student_harness_dir)}",
        ]
    if student_model.startswith("azure:"):
        command[command.index("--n-attempts"):command.index("--n-attempts")] = [
            "--agent-kwarg",
            f"student_azure_api={json.dumps(student_azure_api)}",
        ]
    if teacher_prompt_suffix is not None:
        command[command.index("--n-attempts"):command.index("--n-attempts")] = [
            "--agent-kwarg",
            f"teacher_prompt_suffix={json.dumps(teacher_prompt_suffix)}",
        ]
    if teacher_prompt_path is not None:
        command[command.index("--n-attempts"):command.index("--n-attempts")] = [
            "--agent-kwarg",
            f"teacher_prompt_path={json.dumps(teacher_prompt_path)}",
        ]
    if teacher_oracle_answer_dir is not None:
        command[command.index("--n-attempts"):command.index("--n-attempts")] = [
            "--agent-kwarg",
            f"teacher_oracle_answer_dir={json.dumps(teacher_oracle_answer_dir)}",
        ]
    if agent_timeout_multiplier is not None:
        command[command.index("--yes"):command.index("--yes")] = [
            "--agent-timeout-multiplier",
            str(agent_timeout_multiplier),
        ]
    for task in tasks:
        command.extend(["--include-task-name", task])

    return command


def shell_command(argv: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in argv)


def run_rollout(argv: list[str]) -> int:
    print(f"running: {shell_command(argv)}", file=sys.stderr, flush=True)
    return subprocess.run(argv, check=False).returncode
