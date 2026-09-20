"""Harbor entry point for supervised miniswe rollout collection."""

from __future__ import annotations

import importlib
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from harness_zero.chat_model import ReasoningChatDeepSeek, ReasoningChatOpenAI

from harness_zero.student import create_supervised_miniswe
from harness_zero.student_harness import (
    StudentHarnessSpec,
    iter_upload_files,
    load_student_harness,
)
from harness_zero.review import ReviewCandidate, ReviewSubmission
from harness_zero.store import TrialStore
from harness_zero.teacher import DeepAgentReviewer
from harness_zero.teacher_models import build_teacher_model
from deepagents_harbor.backend import HarborSandboxBackend
from deepagents_harbor.base import DeepHarnessAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class PassthroughReviewer:
    """Record an exact weak-model baseline without calling a teacher model."""

    async def review(self, candidate: ReviewCandidate) -> ReviewSubmission:
        return ReviewSubmission(
            candidate_id=candidate.candidate_id,
            decision="PASS",
            reason="baseline passthrough",
        )


class HarnessZeroMinisweAgent(DeepHarnessAgent):
    """One-tool student whose every response is reviewed before execution."""

    @staticmethod
    def name() -> str:
        return "harness-zero-miniswe"

    def version(self) -> str:
        return "0.1.0"

    def __init__(
        self,
        *args,
        teacher_model: str,
        teacher_provider: str,
        teacher_reasoning_effort: str = "high",
        max_replacements_per_trial: int = 5,
        student_max_turns: int = 40,
        task_id: str | None = None,
        trial: int | str | None = None,
        components_dir: str,
        teacher_middleware_factory: str | None = None,
        student_harness_dir: str | None = None,
        teacher_prompt_suffix: str | None = None,
        teacher_prompt_path: str | None = None,
        teacher_oracle_answer_dir: str | None = None,
        student_reasoning_prefill: str = "none",
        student_azure_api: str = "deepseek",
        **kwargs,
    ) -> None:
        if teacher_provider not in {"openai", "openrouter", "azure", "azure_openai", "passthrough"}:
            raise ValueError(
                "teacher_provider must be openai, openrouter, azure, azure_openai or passthrough"
            )
        if teacher_provider != "passthrough" and not teacher_model:
            raise ValueError("teacher_model must be explicit")
        if teacher_provider == "openrouter" and teacher_reasoning_effort not in {
            "enabled",
            "disabled",
        }:
            raise ValueError(
                "openrouter teacher_reasoning_effort must be 'enabled' or 'disabled'"
            )
        super().__init__(*args, **kwargs)
        if student_reasoning_prefill not in {"none", "think"}:
            raise ValueError("student_reasoning_prefill must be none or think")
        self._student_reasoning_prefill = student_reasoning_prefill
        if student_azure_api not in {"deepseek", "responses"}:
            raise ValueError("student_azure_api must be deepseek or responses")
        self._student_azure_api = student_azure_api
        self._teacher_model = teacher_model
        self._teacher_provider = teacher_provider
        self._teacher_reasoning_effort = teacher_reasoning_effort
        self._max_replacements_per_trial = int(max_replacements_per_trial)
        if self._max_replacements_per_trial < 0:
            raise ValueError("max_replacements_per_trial must be non-negative")
        self._student_max_turns = int(student_max_turns)
        if self._student_max_turns <= 0:
            raise ValueError("student_max_turns must be positive")
        self._task_id = task_id
        self._trial = trial
        self._components_dir = Path(components_dir)
        self._teacher_middleware_factory = teacher_middleware_factory
        self._teacher_prompt_suffix = teacher_prompt_suffix
        self._teacher_prompt_path = teacher_prompt_path
        self._teacher_oracle_answer_dir = (
            Path(teacher_oracle_answer_dir) if teacher_oracle_answer_dir else None
        )
        self._student_harness: StudentHarnessSpec | None = (
            load_student_harness(Path(student_harness_dir))
            if student_harness_dir is not None
            else None
        )
        self._private_teacher_dir: Path | None = None

    def _normalized_model_spec(self) -> str:
        if not self.model_name:
            raise ValueError("student model must be passed explicitly with harbor -m")
        return super()._normalized_model_spec()

    def _build_model(self) -> BaseChatModel:
        spec = self._normalized_model_spec()
        if spec.startswith("openai:") and self._model_kwargs.get("base_url"):
            return ReasoningChatOpenAI(
                model=spec.split(":", 1)[1],
                reasoning_prefill=self._student_reasoning_prefill,
                request_log_path=self.logs_dir / "student_model_requests.jsonl",
                **{**self._model_kwargs, "use_responses_api": False},
            )
        if self._student_reasoning_prefill != "none":
            raise ValueError("student_reasoning_prefill requires an OpenAI-compatible base_url")
        if not spec.startswith("azure:"):
            return super()._build_model()
        deployment = spec.split(":", 1)[1]
        if not deployment:
            raise ValueError("azure student deployment name must be explicit")
        effort = self._student_reasoning().get("effort", "")
        if effort not in {"low", "high", "max"}:
            raise ValueError(
                "azure student reasoning effort must be low, high or max "
                "(DeepSeek maps other values: medium/xhigh -> high)"
            )
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        if not api_key:
            raise ValueError("azure student requires AZURE_OPENAI_API_KEY")
        base_url = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not base_url:
            raise ValueError("azure student requires AZURE_OPENAI_ENDPOINT")
        if self._student_azure_api == "responses":
            # Azure OpenAI-model deployments (gpt-*): the DeepSeek-style
            # thinking extra_body is rejected with a 400; the proven request
            # shape is the Responses API with a detailed reasoning summary,
            # same as the azure_openai teacher.
            return ChatOpenAI(
                model=deployment,
                api_key=api_key,
                base_url=base_url,
                timeout=600,
                max_retries=3,
                use_responses_api=True,
                output_version="responses/v1",
                reasoning={"effort": effort, "summary": "detailed"},
            )
        # Plain ChatDeepSeek, not the teacher's ThrottledChatDeepSeek: the
        # teacher semaphore would cap all student traffic at 6 concurrent
        # calls process-wide. Same request shape as the Azure teacher
        # (reasoning_effort + thinking enabled).
        return ReasoningChatDeepSeek(
            model=deployment,
            request_log_path=self.logs_dir / "student_model_requests.jsonl",
            api_key=api_key,
            api_base=base_url,
            timeout=600,
            max_retries=3,
            reasoning_effort=effort,
            extra_body={"thinking": {"type": "enabled"}},
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        prepare = await environment.exec(
            "mkdir -p /opt/ahd /logs/agent/subagents",
            timeout_sec=30,
            user="root",
        )
        if prepare.return_code != 0:
            output = prepare.stderr or prepare.stdout or "no output"
            raise RuntimeError(f"failed to prepare subagent directory: {output}")

        if self._student_harness is not None:
            harness_dir = await environment.exec(
                "mkdir -p /opt/ahd/harness",
                timeout_sec=30,
                user="root",
            )
            if harness_dir.return_code != 0:
                output = harness_dir.stderr or harness_dir.stdout or "no output"
                raise RuntimeError(f"failed to prepare harness directory: {output}")
            uploads = iter_upload_files(self._student_harness)
            # docker compose cp cannot create missing intermediate dirs and
            # falls back to a slow tar-stream retry per file; pre-creating the
            # parent dirs keeps uploads on the fast path (matters with many skills).
            parent_dirs = sorted(
                {str(Path(sandbox_path).parent) for _, sandbox_path in uploads}
            )
            if parent_dirs:
                make_dirs = await environment.exec(
                    "mkdir -p " + " ".join(shlex.quote(d) for d in parent_dirs),
                    timeout_sec=30,
                    user="root",
                )
                if make_dirs.return_code != 0:
                    output = make_dirs.stderr or make_dirs.stdout or "no output"
                    raise RuntimeError(f"failed to prepare harness upload dirs: {output}")
            for local_path, sandbox_path in uploads:
                await environment.upload_file(
                    source_path=local_path,
                    target_path=sandbox_path,
                )

        await environment.upload_file(
            source_path=Path(__file__).with_name("agent_cli.py"),
            target_path="/opt/ahd/agent_cli.py",
        )
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("host uv executable is required for isolated setup")
        await environment.upload_file(
            source_path=Path(uv),
            target_path="/opt/ahd/uv",
        )
        install = await environment.exec(
            "chmod 755 /opt/ahd/uv && "
            "(/opt/ahd/venv/bin/python -c 'import openai' 2>/dev/null || "
            "(/opt/ahd/uv venv -q /opt/ahd/venv --python python3 && "
            "/opt/ahd/uv pip install -q --python /opt/ahd/venv/bin/python "
            "'openai>=2.54.0'))",
            timeout_sec=300,
            user="root",
        )
        if install.return_code != 0:
            output = install.stderr or install.stdout or "no output"
            raise RuntimeError(f"failed to install isolated subagent runtime: {output}")
        await environment.upload_file(
            source_path=Path(__file__).parents[1] / "deepagents_harbor" / "message_content.py",
            target_path="/opt/ahd/message_content.py",
        )
        wrapper = self._agent_wrapper()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as handle:
            handle.write(wrapper)
            handle.flush()
            await environment.upload_file(
                source_path=Path(handle.name),
                target_path="/usr/local/bin/agent",
            )
        chmod = await environment.exec(
            "chmod 755 /usr/local/bin/agent",
            timeout_sec=30,
            user="root",
        )
        if chmod.return_code != 0:
            output = chmod.stderr or chmod.stdout or "no output"
            raise RuntimeError(f"failed to enable subagent CLI: {output}")

    def _agent_wrapper(self) -> str:
        argv = [
            "/opt/ahd/venv/bin/python",
            "/opt/ahd/agent_cli.py",
            "--model",
            self._inherited_model_name(),
            "--reasoning-prefill",
            self._student_reasoning_prefill,
            "--reasoning",
            json.dumps(self._student_reasoning(), separators=(",", ":")),
            '"$@"',
        ]
        command = " ".join(
            item if item == '"$@"' else shlex.quote(item) for item in argv
        )
        return (
            "#!/bin/sh\n"
            "export HARNESS_ZERO_SUBAGENT_LOG_DIR=/logs/agent/subagents\n"
            "export NO_PROXY=127.0.0.1,localhost,172.17.0.1,${NO_PROXY:-${no_proxy:-}}\n"
            "export no_proxy=$NO_PROXY\n"
            f"exec {command}\n"
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        try:
            await super().run(instruction, environment, context)
        finally:
            if self._private_teacher_dir is not None:
                destination = self.logs_dir / "teacher"
                if destination.exists():
                    raise FileExistsError(f"teacher artifact destination exists: {destination}")
                shutil.copytree(self._private_teacher_dir, destination)
                shutil.rmtree(self._private_teacher_dir.parent)
                self._private_teacher_dir = None

    def build_graph(self, model: BaseChatModel, backend: HarborSandboxBackend):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if self._student_harness is not None:
            (self.logs_dir / "student_harness_bank.txt").write_text(
                self._student_harness.bank_sha256 + "\n", encoding="utf-8"
            )
        if self._private_teacher_dir is not None:
            raise RuntimeError("teacher workspace already exists")
        private_root = Path(tempfile.mkdtemp(prefix="ahd-teacher-"))
        self._private_teacher_dir = private_root / "teacher"
        store = TrialStore(
            self._private_teacher_dir,
            task_id=self._task_id or backend.environment_name,
            trial=self._trial if self._trial is not None else backend.session_id,
            components_dir=self._components_dir,
        )
        if self._teacher_oracle_answer_dir is not None:
            task_name = (self._task_id or backend.environment_name).rstrip("/").split("/")[-1]
            oracle_src = self._teacher_oracle_answer_dir / task_name / "tests" / "answer.txt"
            if not oracle_src.is_file():
                raise FileNotFoundError(
                    f"oracle answer not found for task {task_name}: {oracle_src}"
                )
            shutil.copy2(oracle_src, store.root / "oracle_answer.txt")
        if self._teacher_provider == "passthrough":
            reviewer = PassthroughReviewer()
        else:
            teacher_model = build_teacher_model(
                provider=self._teacher_provider,
                model=self._teacher_model,
                reasoning_effort=self._teacher_reasoning_effort,
            )
            reviewer = DeepAgentReviewer(
                model=teacher_model,
                workspace=store.root,
                max_replacements_per_trial=self._max_replacements_per_trial,
                teacher_middlewares=self._build_teacher_middlewares(),
                teacher_prompt_suffix=self._teacher_prompt_suffix,
                teacher_prompt_path=self._teacher_prompt_path,
            )
        return create_supervised_miniswe(
            student_model=model,
            reviewer=reviewer,
            backend=backend,
            store=store,
            max_replacements=self._max_replacements_per_trial,
            max_turns=self._student_max_turns,
            student_harness=self._student_harness,
        )

    def _build_teacher_middlewares(self):
        if not self._teacher_middleware_factory:
            return []
        module_name, separator, factory_name = self._teacher_middleware_factory.partition(
            ":"
        )
        if not separator or not module_name or not factory_name:
            raise ValueError(
                "teacher_middleware_factory must use the form 'module:function'"
            )
        factory = getattr(importlib.import_module(module_name), factory_name)
        return list(factory())

    def _inherited_model_name(self) -> str:
        model = self._normalized_model_spec()
        if ":" in model:
            return model.split(":", 1)[1]
        return model

    def _student_reasoning(self) -> dict:
        reasoning = self._model_kwargs.get("reasoning")
        if isinstance(reasoning, dict) and reasoning:
            return reasoning
        # Chat-completions form (base_url students): reasoning_effort is a
        # plain string; the responses-API dict would be rejected by the
        # chat completions endpoint, so it arrives in this shape instead.
        effort = self._model_kwargs.get("reasoning_effort")
        if isinstance(effort, str) and effort:
            return {"effort": effort}
        raise ValueError("student model reasoning configuration must be explicit")
