"""Turn-budget nudges — spend the 40-turn budget on delivery, not loops.

Observed failure modes (SpreadsheetBench rollouts hitting the 40-turn wall):

- Correct deliverable already saved, then the agent burns its last turns on
  no-op re-verification passes and dies on the turn limit before finishing
  (evidence: output saved at step 39, killed at step 41). An advisory at
  turns 25/35 converts these into scored trials.
- The same exception signature repeated 3+ times in a row (13 turns lost
  on one ValueError while the fix sat in the skill text the whole time).
  Trial-and-error loops need a "stop, re-read the docs" interrupt.

Message-level only: no sandbox probes, safe in both sync and async hooks.
Nudges are appended via `before_model`, so the trailing message is always
a tool result or a human message — appending a HumanMessage there keeps
the tool-call protocol valid (unlike injecting between an AIMessage with
tool_calls and its tool results). Everything is advisory and bounded: the
budget reminders fire once each, the error-loop nudge at most twice.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

_BUDGET_MARKS = (25, 35)

_BUDGET_NOTE = (
    "Budget check: you have used about {n} of 40 turns. If a non-empty "
    "deliverable already exists in /app/output, STOP re-verifying — reload "
    "it once, assert the answer cells against the worked examples, and "
    "finish. Passes that do not change the file only burn budget; an "
    "unsubmitted correct file scores the same 0 as no file."
)

_LOOP_NOTE = (
    "You hit the same error {k} times in a row ({sig}). Stop "
    "trial-and-error: re-read the skill "
    "(cat /opt/ahd/harness/skills/spreadsheet-manipulation/SKILL.md) and "
    "the relevant library documentation, change the approach, then run "
    "once. Repeating the same failing command with cosmetic edits does "
    "not converge."
)

_ERROR_LINE = re.compile(
    r"(?:^|\n)\s*(?:[A-Za-z_][\w.]*?(?:Error|Exception|Interrupt|Timeout|KeyError|ValueError|TypeError|AttributeError|IndexError|FileNotFoundError))\b[^\n]{0,80}"
)
_NORMALIZE = re.compile(r"(0x[0-9a-fA-F]+|\d+)")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)


def _signature(message: ToolMessage) -> str | None:
    text = _text(message.content)
    if "exit_code=0]" in text:
        return None
    matches = _ERROR_LINE.findall(text)
    if not matches:
        return None
    sig = matches[-1].strip()[:80]
    return _NORMALIZE.sub("#", sig)


class TurnBudgetMiddleware(AgentMiddleware):
    """Advisory nudges at turn 25/35 and on repeated identical exceptions."""

    MAX_LOOP_NUDGES = 2

    def __init__(self, logger: logging.Logger | None = None) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger("turn-budget")
        self._calls = 0
        self._budget_fired: set[int] = set()
        self._loop_nudges = 0
        self._last_loop_sig: str | None = None

    def _check(self, state) -> dict[str, Any] | None:
        self._calls += 1
        notes: list[HumanMessage] = []
        for mark in _BUDGET_MARKS:
            if self._calls >= mark and mark not in self._budget_fired:
                self._budget_fired.add(mark)
                notes.append(
                    HumanMessage(content=_BUDGET_NOTE.format(n=mark), name="turn-budget")
                )
                self._logger.warning("[turn-budget] budget reminder at call %d", mark)
                break
        if self._loop_nudges < self.MAX_LOOP_NUDGES:
            messages = list(state.get("messages") or [])
            recent_tools = [m for m in messages[-8:] if isinstance(m, ToolMessage)]
            signatures = [s for s in (_signature(m) for m in recent_tools[-6:]) if s]
            if len(signatures) >= 3 and len(set(signatures)) == 1:
                sig = signatures[0]
                if sig != self._last_loop_sig:
                    self._last_loop_sig = sig
                    self._loop_nudges += 1
                    notes.append(
                        HumanMessage(
                            content=_LOOP_NOTE.format(k=len(signatures), sig=sig[:100]),
                            name="turn-budget",
                        )
                    )
                    self._logger.warning(
                        "[turn-budget] repeated error loop (%s) — nudging (%d/%d)",
                        sig, self._loop_nudges, self.MAX_LOOP_NUDGES,
                    )
        if not notes:
            return None
        return {"messages": notes}

    def before_model(self, state, runtime) -> dict[str, Any] | None:
        return self._check(state)

    async def abefore_model(self, state, runtime) -> dict[str, Any] | None:
        return self._check(state)


def make_middleware() -> TurnBudgetMiddleware:
    """Student-harness loader factory."""
    return TurnBudgetMiddleware()
