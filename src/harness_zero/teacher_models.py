"""Explicit teacher provider configuration and deployment concurrency limits."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from harness_zero.chat_model import ReasoningChatDeepSeek


def build_openai_model(
    *, model: str, reasoning_effort: str, timeout_sec: int = 120
) -> BaseChatModel:
    """Build an official OpenAI teacher while keeping model choice explicit."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("official OpenAI teacher requires OPENAI_API_KEY")
    if not model:
        raise ValueError("teacher model must be passed explicitly")
    if not reasoning_effort:
        raise ValueError("teacher reasoning effort must be passed explicitly")
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url="https://api.openai.com/v1",
        timeout=timeout_sec,
        max_retries=3,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": reasoning_effort, "summary": "detailed"},
    )


_TEACHER_ASYNC_SEMAPHORE: "asyncio.Semaphore | None" = None
_TEACHER_SYNC_SEMAPHORE: "threading.Semaphore | None" = None


def _teacher_call_limit() -> int:
    return int(os.environ.get("HARNESS_ZERO_TEACHER_MAX_CONCURRENCY", "6"))


def _teacher_async_semaphore() -> "asyncio.Semaphore":
    global _TEACHER_ASYNC_SEMAPHORE
    if _TEACHER_ASYNC_SEMAPHORE is None:
        _TEACHER_ASYNC_SEMAPHORE = asyncio.Semaphore(_teacher_call_limit())
    return _TEACHER_ASYNC_SEMAPHORE


def _teacher_sync_semaphore() -> "threading.Semaphore":
    global _TEACHER_SYNC_SEMAPHORE
    if _TEACHER_SYNC_SEMAPHORE is None:
        _TEACHER_SYNC_SEMAPHORE = threading.Semaphore(_teacher_call_limit())
    return _TEACHER_SYNC_SEMAPHORE


class ThrottledChatOpenAI(ChatOpenAI):
    """ChatOpenAI under the same process-wide teacher call budget as
    ThrottledChatDeepSeek (see its docstring)."""

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        async with _teacher_async_semaphore():
            return await super()._agenerate(*args, **kwargs)

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        with _teacher_sync_semaphore():
            return super()._generate(*args, **kwargs)


class ThrottledChatDeepSeek(ReasoningChatDeepSeek):
    """ChatDeepSeek with a process-wide cap on concurrent API calls.

    Every trial in a harbor job builds its own teacher model inside one
    process, so a module-level semaphore flattens their combined burst
    traffic under the deployment's TPM limit. The semaphore is held for the
    whole _agenerate/_generate call, which includes the openai client's
    internal retries, so retry traffic counts against the same budget.
    The async and sync paths carry independent budgets; a harbor job only
    ever drives the async one.
    """

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        async with _teacher_async_semaphore():
            return await super()._agenerate(*args, **kwargs)

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        with _teacher_sync_semaphore():
            return super()._generate(*args, **kwargs)


def build_azure_model(
    *, model: str, reasoning_effort: str, timeout_sec: int = 600
) -> BaseChatModel:
    """Build an Azure AI Foundry DeepSeek teacher while keeping model choice explicit."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Azure teacher requires AZURE_OPENAI_API_KEY")
    base_url = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not base_url:
        raise ValueError("Azure teacher requires AZURE_OPENAI_ENDPOINT")
    if not model:
        raise ValueError("teacher model must be passed explicitly")
    if not reasoning_effort:
        raise ValueError("teacher reasoning effort must be passed explicitly")
    if reasoning_effort not in {"low", "high", "max"}:
        raise ValueError(
            "azure teacher reasoning_effort must be low, high or max "
            "(DeepSeek maps other values: medium/xhigh -> high)"
        )
    # Keep reasoning in tool-call history, as required by the DeepSeek API.
    return ThrottledChatDeepSeek(
        model=model,
        api_key=api_key,
        api_base=base_url,
        timeout=timeout_sec,
        max_retries=3,
        reasoning_effort=reasoning_effort,
        extra_body={"thinking": {"type": "enabled"}},
    )


def build_azure_openai_model(
    *, model: str, reasoning_effort: str, timeout_sec: int = 600
) -> BaseChatModel:
    """Build an Azure AI Foundry OpenAI-model teacher while keeping model choice explicit.

    Same client shape as build_openai_model (Responses API + reasoning summary)
    but pointed at the Azure AI Foundry /openai/v1 endpoint; both
    /chat/completions and /responses are supported there.
    """
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Azure OpenAI teacher requires AZURE_OPENAI_API_KEY")
    base_url = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not base_url:
        raise ValueError("Azure OpenAI teacher requires AZURE_OPENAI_ENDPOINT")
    if not model:
        raise ValueError("teacher model must be passed explicitly")
    if not reasoning_effort:
        raise ValueError("teacher reasoning effort must be passed explicitly")
    return ThrottledChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_sec,
        max_retries=3,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={"effort": reasoning_effort, "summary": "detailed"},
    )


def build_openrouter_model(
    *, model: str, reasoning_enabled: bool, timeout_sec: int = 120
) -> BaseChatModel:
    """Build an OpenRouter teacher for self-teaching rollouts.

    Mirrors deepagents_harbor.base's OpenRouter branch: a direct client that
    bypasses the proxy and ignores the Venice route for 35b checkpoints (it
    never emits tool calls). Model choice stays explicit.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OpenRouter teacher requires OPENROUTER_API_KEY")
    if not model:
        raise ValueError("teacher model must be passed explicitly")
    import httpx
    import openrouter
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {
        "timeout": timeout_sec,
        "max_retries": 3,
        "reasoning": {"enabled": reasoning_enabled},
    }
    if "35b" in model.lower():
        kwargs["openrouter_provider"] = {"ignore": ["Venice"]}
    kwargs["client"] = openrouter.OpenRouter(
        api_key=api_key,
        client=httpx.Client(trust_env=False, timeout=timeout_sec),
        async_client=httpx.AsyncClient(trust_env=False, timeout=timeout_sec),
    )
    return init_chat_model(f"openrouter:{model}", **kwargs)


def build_teacher_model(*, provider: str, model: str, reasoning_effort: str) -> BaseChatModel:
    if provider == "openrouter":
        if reasoning_effort not in {"enabled", "disabled"}:
            raise ValueError("OpenRouter teacher reasoning must be enabled or disabled")
        return build_openrouter_model(model=model, reasoning_enabled=reasoning_effort == "enabled")
    factories = {
        "openai": build_openai_model,
        "azure": build_azure_model,
        "azure_openai": build_azure_openai_model,
    }
    if provider not in factories:
        raise ValueError(f"unsupported teacher provider: {provider}")
    return factories[provider](model=model, reasoning_effort=reasoning_effort)

