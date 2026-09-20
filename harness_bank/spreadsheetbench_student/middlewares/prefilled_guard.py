"""Prefilled-cell guard — never let the output clobber pre-filled answer cells.

Observed failure mode (SpreadsheetBench rollouts, repeated across rounds):
the student writes its own rule across the whole answer range and silently
overwrites pre-filled worked-example cells (input non-empty, non-formula)
that pin the expected rule/format/order. Memory prose said "never
overwrite" for three rounds and the model kept quoting it and violating
it — so the check is mechanical now.

Hook: `after_model`. When the latest AIMessage has no tool calls (the
agent is about to finish), parse the task message for `### spreadsheet_path`,
`### answer_position`, `### output_path`, then diff input vs output INSIDE
the answer range IN THE SANDBOX: every cell that held a literal (non-formula)
value in the input must be unchanged in the output. A violation rejects the
finish with the offending coordinates. Bounded by MAX_NUDGES=1 so tasks that
legitimately rewrite pre-filled cells (the instruction says so) cost exactly
one nudge; every failure mode (parse failure, missing files, probe error)
is fail-open.

Async hooks probe the sandbox ONLY via `await backend.aexecute(...)` — the
sync `execute(...)` deadlocks the event loop when called from the loop
thread.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from deepagents_harbor.current import current_backend

_FIELD = re.compile(
    r"###\s*(spreadsheet_path|answer_position|output_path)\s*\n([^\n]+)"
)

_PROBE = r"""
import json, sys
import openpyxl
from openpyxl.utils import range_boundaries, get_column_letter

inp, outp, rng = sys.argv[1], sys.argv[2], sys.argv[3]
sheet = None
if "!" in rng:
    sheet, rng = rng.split("!", 1)
    sheet = sheet.strip("'")
try:
    wb_in = openpyxl.load_workbook(inp)
    wb_out = openpyxl.load_workbook(outp)
    ws_in = wb_in[sheet] if sheet else wb_in.active
    ws_out = wb_out[sheet] if sheet else wb_out.active
    min_c, min_r, max_c, max_r = range_boundaries(rng)
    if (max_r - min_r + 1) * (max_c - min_c + 1) > 20000:
        print(json.dumps({"status": "skip"}))
        sys.exit(0)
    bad = []
    for row in range(min_r, max_r + 1):
        for col in range(min_c, max_c + 1):
            ci = ws_in.cell(row=row, column=col)
            if ci.value is None or ci.data_type == "f":
                continue
            co = ws_out.cell(row=row, column=col)
            if co.value != ci.value:
                coord = f"{get_column_letter(col)}{row}"
                bad.append({"cell": coord, "was": repr(ci.value)[:40], "now": repr(co.value)[:40]})
    print(json.dumps({"status": "ok", "violations": bad[:8], "total": len(bad)}))
except Exception as e:
    print(json.dumps({"status": "error", "error": str(e)[:200]}))
""".strip()

_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): your output workbook changed "
    "{total} pre-filled cell(s) inside the answer range — these worked "
    "examples are the specification and must stay byte-identical:\n"
    "{lines}\n"
    "Restore the original values, re-derive your rule FROM those examples "
    "(a rule that contradicts an example is wrong, not the example), and "
    "write only into blank cells. If the instruction explicitly requires "
    "changing these exact cells, say so and finish again."
)


class PrefilledGuardMiddleware(AgentMiddleware):
    """Reject finish when pre-filled literal cells in the answer range differ."""

    MAX_NUDGES = 1

    def __init__(self, logger: logging.Logger | None = None) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger("prefilled-guard")
        self._nudges = 0

    @staticmethod
    def _task_fields(state) -> tuple[str, str, str] | None:
        messages = list(state.get("messages") or [])
        text = ""
        for message in messages:
            if isinstance(message, HumanMessage):
                content = message.content
                text = content if isinstance(content, str) else str(content)
                break
        fields = {m.group(1): m.group(2).strip() for m in _FIELD.finditer(text)}
        if not all(fields.get(key) for key in ("spreadsheet_path", "answer_position", "output_path")):
            return None
        return fields["spreadsheet_path"], fields["answer_position"], fields["output_path"]

    async def _violations(self, inp: str, rng: str, outp: str) -> dict[str, Any]:
        cmd = (
            "python3 - "
            + " ".join(shlex.quote(arg) for arg in (inp, outp, rng))
            + " <<'PYEOF'\n"
            + _PROBE
            + "\nPYEOF"
        )
        try:
            r = await current_backend.get().aexecute(cmd, timeout=90)
        except Exception as e:  # backend hiccup must not block finishing
            self._logger.warning("[prefilled-guard] probe failed, allowing finish: %s", e)
            return {"status": "error"}
        lines = (r.output or "").strip().splitlines()
        if not lines:
            return {"status": "error"}
        try:
            return json.loads(lines[-1])
        except ValueError:
            self._logger.warning("[prefilled-guard] probe output unparsable, allowing finish")
            return {"status": "error"}

    def _check(self, state, result: dict[str, Any]) -> dict[str, Any] | None:
        if self._nudges >= self.MAX_NUDGES:
            return None
        if result.get("status") != "ok" or not result.get("violations"):
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        self._nudges += 1
        lines = "\n".join(
            f"- {v['cell']}: was {v['was']}, now {v['now']}" for v in result["violations"]
        )
        self._logger.warning(
            "[prefilled-guard] finish attempt overwrites %d pre-filled cells — rejecting (%d/%d)",
            result.get("total", 0), self._nudges, self.MAX_NUDGES,
        )
        return {
            "messages": [
                HumanMessage(
                    content=_REJECT.format(
                        k=self._nudges,
                        cap=self.MAX_NUDGES,
                        total=result.get("total", 0),
                        lines=lines,
                    ),
                    name="prefilled-guard",
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict[str, Any] | None:
        # Sync path must not probe the sandbox (deadlock); allow finish here.
        return None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime) -> dict[str, Any] | None:
        fields = self._task_fields(state)
        if fields is None:
            return None
        inp, rng, outp = fields
        result = await self._violations(inp, rng, outp)
        return self._check(state, result)


def make_middleware() -> PrefilledGuardMiddleware:
    """Student-harness loader factory."""
    return PrefilledGuardMiddleware()
