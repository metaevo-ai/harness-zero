"""Answer-guard middleware — never finish with a missing/empty answer file.

Observed failure mode (USPTO retrosynthesis rollout): the model burned its
single turn hand-parsing a long SMILES, produced an empty message with no
tool calls, and the react loop treated "no tool calls" as the final answer
— the trial ended with /app/answer.txt never written. The instruction's
contract makes that an automatic 0, and nothing in the loop noticed.

This middleware hooks `after_model`: when the latest AIMessage has no tool
calls (the agent is about to finish), it checks the answer file IN THE
SANDBOX (`test -s` + a format probe: single line, no whitespace-only
content). If the file is missing, empty, or grossly malformed, the finish
is rejected with a nudge back to the model. Bounded by MAX_NUDGES so a
genuinely-stuck agent is never trapped; once the budget is spent the
finish goes through (a hard-blocked agent scores the same 0 but burns the
whole trial).

Default mode rejects on missing/empty. Strict mode (`strict=True`)
additionally requires the file to look like a dot-separated SMILES line
(only SMILES alphabet characters) — catching placeholder prose like
"see above" written to the answer file.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from deepagents_harbor.current import current_backend

_ANSWER_PATH = "/app/answer.txt"

_PROBE = (
    f'if [ ! -f {_ANSWER_PATH} ]; then echo MISSING; '
    f'elif [ ! -s {_ANSWER_PATH} ] || ! grep -q "[^[:space:]]" {_ANSWER_PATH}; '
    f'then echo EMPTY; else head -c 300 {_ANSWER_PATH}; fi'
)

_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): {path} is {why}. A missing or "
    "empty answer file scores 0 regardless of your reasoning. Write your "
    "final reactant SMILES (dot-separated, one line) to {path} NOW, read it "
    "back, run your final verification, and only then finish."
)

_FORMAT_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): {path} does not look like a "
    "dot-separated SMILES line (found: {found!r}). The grader string-matches "
    "the file content — write ONLY the reactant SMILES joined by '.', no "
    "prose, no labels, then verify and finish."
)


class AnswerGuardMiddleware(AgentMiddleware):
    """Reject agent finish while the answer file is missing/empty/malformed.

    strict=True: also require the content to match the SMILES-line alphabet
    — blocks prose placeholders.
    """

    name = "answer-guard"

    MAX_NUDGES = 3

    def __init__(
        self, logger: logging.Logger | None = None, strict: bool = False
    ) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger(__name__)
        self._strict = strict
        self._nudges = 0

    def _parse_probe(self, out: str) -> tuple[str, str]:
        out = (out or "").strip()
        if out == "MISSING":
            return "missing", ""
        if out == "EMPTY":
            return "empty", ""
        if self._strict:
            import re

            if not re.fullmatch(r"[A-Za-z0-9@+\-\[\]()=#$:/\\.%*]+", out.splitlines()[0].strip()):
                return "bad_format", out[:80]
        return "ok", out

    def _answer_state(self) -> tuple[str, str]:
        """Sync probe — only for the sync `after_model` path. NEVER call this
        from async hooks: HarborSandboxBackend.execute blocks on
        run_coroutine_threadsafe(...).result(), which deadlocks the event
        loop when invoked from the loop thread itself."""
        try:
            r = current_backend.get().execute(_PROBE, timeout=30)
        except Exception as e:  # backend hiccup must not block finishing
            self._logger.warning("[answer-guard] probe failed, allowing finish: %s", e)
            return "ok", ""
        return self._parse_probe(r.output)

    async def _answer_state_async(self) -> tuple[str, str]:
        try:
            r = await current_backend.get().aexecute(_PROBE, timeout=30)
        except Exception as e:  # backend hiccup must not block finishing
            self._logger.warning("[answer-guard] probe failed, allowing finish: %s", e)
            return "ok", ""
        return self._parse_probe(r.output)

    def _check(self, state, status: str, found: str) -> dict[str, Any] | None:
        if self._nudges >= self.MAX_NUDGES:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if status == "ok":
            return None
        self._nudges += 1
        self._logger.warning(
            "[answer-guard] finish attempt with answer file %s — rejecting (%d/%d)",
            status, self._nudges, self.MAX_NUDGES,
        )
        if status == "bad_format":
            text = _FORMAT_REJECT.format(k=self._nudges, cap=self.MAX_NUDGES,
                                         path=_ANSWER_PATH, found=found)
        else:
            text = _REJECT.format(k=self._nudges, cap=self.MAX_NUDGES,
                                  path=_ANSWER_PATH, why=status)
        return {
            "messages": [HumanMessage(content=text, name="answer-guard")],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        status, found = self._answer_state()
        return self._check(state, status, found)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        status, found = await self._answer_state_async()
        return self._check(state, status, found)


def make_middleware() -> AnswerGuardMiddleware:
    """Student-harness loader factory: the default (non-strict) finish gate."""
    return AnswerGuardMiddleware()
