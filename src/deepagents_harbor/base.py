"""Base class for deepagents-based harbor harnesses.

Outer harnesses (top-level packages next to this one) subclass this and only
implement `build_graph()` — every degree of freedom the deepagents framework
exposes (`system_prompt`, `tools`, `middleware`, `subagents`, `skills`,
`memory`, `backend` composition, `response_format`, ...) belongs to the
subclass. Everything harbor/deepagents plumbing stays hidden here:

- model construction from harbor's `-m` (`provider/model` -> `init_chat_model`,
  `OPENROUTER_API_KEY` falls back to `OPENAI_API_KEY` from the repo `.env`)
- the sandbox backend bridge (`HarborSandboxBackend`, see `backend.py`)
- graph invocation with the task instruction
- token-usage accounting into harbor's `AgentContext`
- always-on trajectory recording (ATIF `trajectory.json` + `llm_calls.jsonl`
  sidecar into the trial's agent logs dir, see `trajectory.py`)
"""

from __future__ import annotations

import asyncio
import os
from abc import abstractmethod
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from deepagents_harbor.backend import HarborSandboxBackend
from deepagents_harbor.current import current_backend
from deepagents_harbor.trajectory import (
    TrajectoryRecorder,
    write_trajectory_artifacts,
)


class DeepHarnessAgent(BaseAgent):
    """Harbor `BaseAgent` that runs a deepagents graph against the sandbox."""

    def __init__(
        self,
        *args: Any,
        model_kwargs: dict[str, Any] | None = None,
        command_timeout_sec: int = 600,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._model_kwargs = {"timeout": 120, "max_retries": 3, **(model_kwargs or {})}
        self._command_timeout_sec = int(command_timeout_sec)

    @abstractmethod
    def build_graph(self, model: BaseChatModel, backend: HarborSandboxBackend):
        """Build the deepagents graph for this harness.

        Args:
            model: The chat model built from harbor's `-m` spec.
            backend: Sandbox backend bridged to the trial's harbor environment;
                pass it to `create_deep_agent(backend=...)`, wrap it in a
                `CompositeBackend`, or replace it entirely.

        Returns:
            A compiled LangGraph graph invokable with `{"messages": [...]}`.
        """

    async def setup(self, environment: BaseEnvironment) -> None:
        """Nothing to install: the graph runs host-side in the harbor process."""

    # Provider prefixes langchain's init_chat_model recognizes (subset; the
    # point is only to tell "openai/gpt-5" apart from a bare OpenRouter model
    # id like "qwen/qwen3.6-35b-a3b", whose first segment is NOT a provider).
    _KNOWN_PROVIDERS = frozenset(
        {
            "anthropic", "azure_openai", "baseten", "bedrock", "cohere",
            "deepseek", "fireworks", "google_genai", "google_vertexai",
            "groq", "huggingface", "litellm", "mistralai", "nvidia",
            "ollama", "openai", "openrouter", "perplexity", "together",
            "upstage", "xai",
        }
    )

    def _normalized_model_spec(self) -> str:
        spec = (
            self.model_name
            or os.environ.get("HARBOR_MODEL")
            or os.environ.get("MODEL_ID")
            or ""
        )
        if not spec:
            msg = "no model: pass -m to harbor or set MODEL_ID in .env"
            raise ValueError(msg)
        if ":" in spec:
            return spec
        if "/" in spec:
            provider, name = spec.split("/", maxsplit=1)
            if provider.lower() in self._KNOWN_PROVIDERS:
                return f"{provider}:{name}"
        # No provider prefix: default to OpenRouter when the environment
        # points at it (OPENAI_API_KEY is accepted as a fallback key).
        if "openrouter" in (os.environ.get("OPENAI_BASE_URL") or "").lower() or os.environ.get("OPENROUTER_API_KEY"):
            return f"openrouter:{spec}"
        return spec

    def _build_model(self) -> BaseChatModel:
        spec = self._normalized_model_spec()
        kwargs = dict(self._model_kwargs)
        if spec.startswith("openrouter:"):
            if not os.environ.get("OPENROUTER_API_KEY") and os.environ.get("OPENAI_API_KEY"):
                # Fall back to OPENAI_API_KEY when no dedicated OpenRouter key is set.
                os.environ["OPENROUTER_API_KEY"] = os.environ["OPENAI_API_KEY"]
            provider: dict = {}
            if "35b" in spec.lower():
                # Venice's qwen3.6-35b-a3b checkpoint never emits tool calls
                # under tool_choice=auto; other checkpoints on the same
                # provider behave, so only the 35b variant is ignored.
                provider["ignore"] = ["Venice"]
            if provider:
                kwargs.setdefault("openrouter_provider", provider)
            # OpenRouter is contacted directly here because an HTTP proxy can
            # stall during its TLS handshake. Keep this client-specific so
            # official OpenAI traffic can still go through a proxy.
            import httpx
            import openrouter

            timeout = float(kwargs.get("timeout", 120))
            kwargs["client"] = openrouter.OpenRouter(
                api_key=os.environ["OPENROUTER_API_KEY"],
                client=httpx.Client(trust_env=False, timeout=timeout),
                async_client=httpx.AsyncClient(trust_env=False, timeout=timeout),
            )
        return init_chat_model(spec, **kwargs)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        model = self._build_model()
        backend = HarborSandboxBackend(
            environment,
            loop=asyncio.get_running_loop(),
            default_timeout_sec=self._command_timeout_sec,
        )
        # Publish for sandbox-bound tools (see deepagents_harbor/current.py).
        current_backend.set(backend)
        graph = self.build_graph(model, backend)

        # Trajectory recording is built into the base layer (always on): the
        # recorder sits at the LLM gateway (callback boundary) and sees every
        # request/response verbatim; see deepagents_harbor/trajectory.py.
        recorder = TrajectoryRecorder(excluded_scopes={"teacher"})
        try:
            result = await graph.ainvoke(
                {"messages": [{"role": "user", "content": instruction}]},
                config={"callbacks": [recorder]},
            )
        finally:
            write_trajectory_artifacts(
                recorder,
                logs_dir=self.logs_dir,
                agent_name=self.name(),
                agent_version=self.version() or "unknown",
                model_name=self._normalized_model_spec(),
                instruction=instruction,
                logger=self.logger,
            )

        n_in = n_out = 0
        for message in result.get("messages", []):
            usage = getattr(message, "usage_metadata", None)
            if usage:
                n_in += usage.get("input_tokens") or 0
                n_out += usage.get("output_tokens") or 0
        context.n_input_tokens = n_in
        context.n_output_tokens = n_out

        messages = result.get("messages", [])
        context.metadata = {
            "n_messages": len(messages),
            "final_message": (
                str(messages[-1].content)[:2000] if messages else None
            ),
        }
