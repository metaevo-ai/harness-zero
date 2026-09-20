"""Completeness-guard middleware — catch reactant sets missing a co-reactant.

This middleware hooks `after_model`: when the agent is about to finish
(latest AIMessage has no tool calls), it reads /app/answer.txt and runs an
RDKit check IN THE SANDBOX (rdkit is baked into the uspto image): every
heavy-atom count of the product must be covered by the summed reactant
counts (H ignored — records legitimately omit H2/reductants). A coverage
gap means a recorded co-reactant is almost certainly missing, so the
finish is rejected with a nudge naming the gap. Bounded by MAX_NUDGES;
once the budget is spent the finish goes through. Backend hiccups and a
missing/empty answer file pass through (that is answer-guard's job).
"""

from __future__ import annotations

import logging
import re
import shlex
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from deepagents_harbor.current import current_backend

_ANSWER_PATH = "/app/answer.txt"

# Reads the answer file itself; prints MISSING / EMPTY / UNPARSEABLE:<frag> /
# GAP:<el>x<n>,... / OK. Product SMILES arrives as argv[1].
_CHECK_SCRIPT = r"""
import sys
from collections import Counter
from pathlib import Path

p = Path("/app/answer.txt")
if not p.exists():
    print("MISSING"); sys.exit(0)
text = p.read_text().strip()
if not text:
    print("EMPTY"); sys.exit(0)
from rdkit import Chem

def counts(mol):
    c = Counter()
    for a in mol.GetAtoms():
        if a.GetAtomicNum() > 1:  # heavy atoms only; H coverage is noise
            c[a.GetSymbol()] += 1
    return c

prod = Chem.MolFromSmiles(sys.argv[1])
pc = counts(prod)
rc = Counter()
for frag in text.split("."):
    m = Chem.MolFromSmiles(frag.strip())
    if m is None:
        print("UNPARSEABLE:" + frag.strip()[:60]); sys.exit(0)
    rc += counts(m)
gap = {k: pc[k] - rc.get(k, 0) for k in pc if pc[k] > rc.get(k, 0)}
print("GAP:" + ",".join(f"{k}x{v}" for k, v in sorted(gap.items())) if gap else "OK")
"""

_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): the product carries heavy atoms "
    "your reactants do not contain ({gap}). That almost always means a "
    "recorded co-reactant is missing from the answer — the new atoms must "
    "come from somewhere: oxidation O from an oxidant (e.g. mCPBA), a Boc "
    "group from Boc2O, a halogen from the halogenating reagent. Re-check "
    "your enumeration output for co-reactant variants, add the missing "
    "reactant, re-verify, and only then finish."
)


class CompletenessGuardMiddleware(AgentMiddleware):
    """Reject finish when reactants don't cover the product's heavy atoms."""

    name = "completeness-guard"

    MAX_NUDGES = 2

    def __init__(self, logger: logging.Logger | None = None) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger(__name__)
        self._nudges = 0
        self._product: str | None = None

    @staticmethod
    def _product_from_messages(messages: list) -> str | None:
        """The task's product SMILES = the `Input:` line of the instruction."""
        for msg in messages:
            if isinstance(msg, HumanMessage):
                m = re.search(r"^Input:\s*(\S+)", str(msg.content), re.MULTILINE)
                if m:
                    return m.group(1)
        return None

    def _parse(self, out: str) -> tuple[str, str]:
        out = (out or "").strip()
        if out.startswith("GAP:"):
            return "gap", out[4:]
        return "ok", ""  # MISSING/EMPTY/UNPARSEABLE/OK: not this guard's call

    def _command(self, product: str) -> str:
        return f"python3 -c {shlex.quote(_CHECK_SCRIPT)} {shlex.quote(product)}"

    def _finish_check(self, state, status: str, gap: str) -> dict[str, Any] | None:
        if status != "gap":
            return None
        self._nudges += 1
        self._logger.warning(
            "[completeness-guard] finish attempt with atom-coverage gap %s — rejecting (%d/%d)",
            gap, self._nudges, self.MAX_NUDGES,
        )
        return {
            "messages": [
                HumanMessage(
                    content=_REJECT.format(
                        k=self._nudges, cap=self.MAX_NUDGES, gap=gap
                    ),
                    name="completeness-guard",
                )
            ],
            "jump_to": "model",
        }

    def _should_check(self, state) -> str | None:
        """Return the shell command to run, or None when no check is due."""
        messages = list(state.get("messages") or [])
        if self._nudges >= self.MAX_NUDGES or not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if self._product is None:
            self._product = self._product_from_messages(messages)
        if not self._product:
            return None
        return self._command(self._product)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        cmd = self._should_check(state)
        if cmd is None:
            return None
        try:
            r = current_backend.get().execute(cmd, timeout=60)
        except Exception as e:  # backend hiccup must not block finishing
            self._logger.warning("[completeness-guard] probe failed, allowing finish: %s", e)
            return None
        status, gap = self._parse(r.output)
        return self._finish_check(state, status, gap)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        cmd = self._should_check(state)
        if cmd is None:
            return None
        try:
            r = await current_backend.get().aexecute(cmd, timeout=60)
        except Exception as e:
            self._logger.warning("[completeness-guard] probe failed, allowing finish: %s", e)
            return None
        status, gap = self._parse(r.output)
        return self._finish_check(state, status, gap)


def make_middleware() -> CompletenessGuardMiddleware:
    """Student-harness loader factory: the heavy-atom completeness gate."""
    return CompletenessGuardMiddleware()
