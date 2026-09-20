"""Answer-range guard — mechanical health check of the deliverable cells.

Observed failure modes (SpreadsheetBench rollouts, round-3 attribution):
the student finishes with an answer range that is mechanically broken in
ways it already saw but rationalized away:

- ALL_EMPTY: values were computed but written to the wrong sheet/column/row
  (off-by-one column math, wrong anchor), so the scored range is all None.
- FORMULA_STRING: cells contain literal strings starting with `=` — the
  sandbox has no LibreOffice, so the student could never verify them, and
  the graded value is a gamble instead of a computed literal.
- NUMERIC_STRING: numbers/times written as text ('8.75', '22:00:00')
  although the task grades typed values.
- ERROR_LITERAL: '#VALUE!'/'#NAME?'/'#REF!' etc. written as cell text
  ('#N/A' excluded — lookup-miss tasks legitimately deliver it).
- SAME_VALUE: >=6 non-empty cells all identical in a range the instruction
  implies is conditional (a constant fill CAN be legitimate, so this one
  only rejects once with an explicit "examples show a constant? ignore"
  escape hatch).

Hook: `after_model` on finish attempts. Parses `### answer_position` and
`### output_path` from the task message, probes the output workbook IN THE
SANDBOX via `await backend.aexecute(...)` (sync execute would deadlock the
event loop). Bounded by MAX_NUDGES; every failure mode is fail-open.
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

_FIELD = re.compile(r"###\s*(answer_position|output_path)\s*\n([^\n]+)")

_PROBE = r"""
import json, re, sys
import openpyxl
from openpyxl.utils import range_boundaries, get_column_letter

outp, rng = sys.argv[1], sys.argv[2]
sheet = None
if "!" in rng:
    sheet, rng = rng.split("!", 1)
    sheet = sheet.strip("'")
NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s*[AP]M)?$", re.I)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
ERRORS = {"#VALUE!", "#NAME?", "#REF!", "#DIV/0!", "#NULL!", "#NUM!"}
try:
    wb = openpyxl.load_workbook(outp)
    ws = wb[sheet] if sheet else wb.active
    min_c, min_r, max_c, max_r = range_boundaries(rng)
    if (max_r - min_r + 1) * (max_c - min_c + 1) > 20000:
        print(json.dumps({"status": "skip"}))
        sys.exit(0)
    nonempty, formulas, numstrings, errlits = [], [], [], []
    values = set()
    for row in range(min_r, max_r + 1):
        for col in range(min_c, max_c + 1):
            c = ws.cell(row=row, column=col)
            coord = f"{get_column_letter(col)}{row}"
            v = c.value
            if v is None:
                continue
            nonempty.append(coord)
            values.add(repr(v))
            if isinstance(v, str):
                if v.startswith("="):
                    formulas.append(coord)
                elif v in ERRORS:
                    errlits.append(coord)
                elif NUMERIC.match(v.strip()) or TIME.match(v.strip()) or DATE.match(v.strip()):
                    numstrings.append(coord)
    flags = []
    if not nonempty:
        flags.append({"flag": "ALL_EMPTY", "detail": "every cell in the answer range is empty"})
    if formulas:
        flags.append({"flag": "FORMULA_STRING", "cells": formulas[:5]})
    if errlits:
        flags.append({"flag": "ERROR_LITERAL", "cells": errlits[:5]})
    if len(numstrings) >= 2:
        flags.append({"flag": "NUMERIC_STRING", "cells": numstrings[:5]})
    if len(nonempty) >= 6 and len(values) == 1:
        flags.append({"flag": "SAME_VALUE", "detail": f"all {len(nonempty)} non-empty cells are identical"})
    print(json.dumps({"status": "ok", "flags": flags}))
except Exception as e:
    print(json.dumps({"status": "error", "error": str(e)[:200]}))
""".strip()

_REJECT = (
    "FINISH REJECTED (attempt {k}/{cap}): the answer range in your output "
    "workbook failed mechanical checks:\n"
    "{lines}\n"
    "Fix these before finishing: deliver literal computed values (the "
    "sandbox has no LibreOffice — an unverifiable formula string is a "
    "gamble, and an empty or broken answer range scores 0). If every "
    "flagged point is exactly what the instruction and the worked examples "
    "require (e.g. a genuinely constant fill), say so explicitly and "
    "finish again."
)

_DETAIL = {
    "ALL_EMPTY": "- answer range is completely EMPTY — your values landed elsewhere (wrong sheet/column/row); re-check coordinates with ws[\"...\"] reads",
    "FORMULA_STRING": "- formula strings in cells {cells} — write computed literal values instead",
    "ERROR_LITERAL": "- error literals in cells {cells} — a broken formula's output, not an answer",
    "NUMERIC_STRING": "- numbers/times written as TEXT in cells {cells} — write typed values (float/int/datetime), strings score 0 against numeric answers",
    "SAME_VALUE": "- {detail} — a conditional column collapsing to one constant usually means the rule is wrong",
}


class AnswerRangeGuardMiddleware(AgentMiddleware):
    """Reject finish when the answer range is mechanically broken."""

    MAX_NUDGES = 2

    def __init__(self, logger: logging.Logger | None = None) -> None:
        super().__init__()
        self._logger = logger or logging.getLogger("answer-range-guard")
        self._nudges = 0

    @staticmethod
    def _task_fields(state) -> tuple[str, str] | None:
        messages = list(state.get("messages") or [])
        text = ""
        for message in messages:
            if isinstance(message, HumanMessage):
                content = message.content
                text = content if isinstance(content, str) else str(content)
                break
        fields = {m.group(1): m.group(2).strip() for m in _FIELD.finditer(text)}
        if not all(fields.get(key) for key in ("answer_position", "output_path")):
            return None
        return fields["answer_position"], fields["output_path"]

    async def _flags(self, rng: str, outp: str) -> dict[str, Any]:
        cmd = (
            "python3 - "
            + " ".join(shlex.quote(arg) for arg in (outp, rng))
            + " <<'PYEOF'\n"
            + _PROBE
            + "\nPYEOF"
        )
        try:
            r = await current_backend.get().aexecute(cmd, timeout=90)
        except Exception as e:
            self._logger.warning("[answer-range-guard] probe failed, allowing finish: %s", e)
            return {"status": "error"}
        lines = (r.output or "").strip().splitlines()
        if not lines:
            return {"status": "error"}
        try:
            return json.loads(lines[-1])
        except ValueError:
            self._logger.warning("[answer-range-guard] probe output unparsable, allowing finish")
            return {"status": "error"}

    def _check(self, state, result: dict[str, Any]) -> dict[str, Any] | None:
        if self._nudges >= self.MAX_NUDGES:
            return None
        if result.get("status") != "ok" or not result.get("flags"):
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        self._nudges += 1
        lines = "\n".join(
            _DETAIL[f["flag"]].format(cells=f.get("cells", []), detail=f.get("detail", ""))
            for f in result["flags"]
        )
        self._logger.warning(
            "[answer-range-guard] finish attempt with flags %s — rejecting (%d/%d)",
            [f["flag"] for f in result["flags"]], self._nudges, self.MAX_NUDGES,
        )
        return {
            "messages": [
                HumanMessage(
                    content=_REJECT.format(k=self._nudges, cap=self.MAX_NUDGES, lines=lines),
                    name="answer-range-guard",
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
        rng, outp = fields
        result = await self._flags(rng, outp)
        return self._check(state, result)


def make_middleware() -> AnswerRangeGuardMiddleware:
    """Student-harness loader factory."""
    return AnswerRangeGuardMiddleware()
