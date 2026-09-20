"""SpreadsheetBench teacher bank: middleware factory and hint behavior."""

from __future__ import annotations

from types import SimpleNamespace

from harness_zero.review import AssistantResponse
from harness_bank.spreadsheetbench.middlewares import build_teacher_middlewares
from harness_bank.spreadsheetbench.middlewares.error_loop import ErrorLoopMiddleware
from harness_bank.spreadsheetbench.middlewares.finish_guard import FinishGuardMiddleware
from harness_bank.spreadsheetbench.middlewares.stall import StallMiddleware
from harness_bank.spreadsheetbench.middlewares.turn_budget import TurnBudgetMiddleware


def _candidate(content="", command=None, context=(), candidate_id="c1"):
    tool_call = None
    if command is not None:
        tool_call = {"name": "execute", "command": command}
    original = AssistantResponse.model_validate(
        {"reasoning": "", "content": content, "tool_call": tool_call}
    )
    return SimpleNamespace(
        candidate_id=candidate_id,
        candidate_issue=None,
        original=original,
        context=list(context),
    )


def test_factory_returns_four_middlewares():
    kinds = [type(m).__name__ for m in build_teacher_middlewares()]
    assert kinds == [
        "StallMiddleware",
        "ErrorLoopMiddleware",
        "TurnBudgetMiddleware",
        "FinishGuardMiddleware",
    ]


def test_stall_hints_on_empty_candidate():
    hint = StallMiddleware().hint(_candidate(content=""))
    assert hint is not None
    assert hint.component_id == "middleware:stall"


def test_stall_hints_on_intent_without_action():
    hint = StallMiddleware().hint(_candidate(content="Let me inspect the workbook next:"))
    assert hint is not None


def test_stall_passes_real_finish_and_actions():
    assert StallMiddleware().hint(_candidate(content="Done — wrote and verified the output.")) is None
    assert StallMiddleware().hint(_candidate(command="ls /app")) is None


def test_finish_guard_silent_when_evidence_present():
    context = [
        {"type": "ai", "content": "", "tool_calls": [{"name": "execute", "args": {"command": "cp in.xlsx /app/output/out.xlsx"}}]},
        {"type": "ai", "content": "", "tool_calls": [{"name": "execute", "args": {"command": "python3 .agent-tools/prefilled_diff.py in.xlsx /app/output/out.xlsx B2:B6"}}]},
        {"type": "tool", "content": "PASS: all pre-filled cells preserved [exit_code=0]"},
        {"type": "ai", "content": "", "tool_calls": [{"name": "execute", "args": {"command": "python3 -c \"assert ws['B2'].value == 42\""}}]},
    ]
    assert FinishGuardMiddleware().hint(_candidate(content="All done.", context=context)) is None


def test_finish_guard_ignores_evidence_in_tool_results():
    # Checklist text inside tool results (e.g. read-back skill content) is not evidence
    context = [
        {"type": "ai", "content": "", "tool_calls": [{"name": "execute", "args": {"command": "cp in.xlsx /app/output/out.xlsx"}}]},
        {"type": "tool", "content": "Worked examples are the spec; assert byte-identical pre-filled cells [exit_code=0]"},
    ]
    hint = FinishGuardMiddleware().hint(_candidate(content="All done.", context=context))
    assert hint is not None


def test_finish_guard_hints_on_missing_evidence():
    context = [
        {"type": "ai", "content": "", "tool_calls": [{"name": "execute", "args": {"command": "echo hello"}}]},
        {"type": "tool", "content": "hello [exit_code=0]"},
    ]
    hint = FinishGuardMiddleware().hint(_candidate(content="All done.", context=context))
    assert hint is not None
    assert "/app/output/" in hint.evidence


def test_finish_guard_ignores_actions_and_empty():
    mw = FinishGuardMiddleware()
    assert mw.hint(_candidate(command="ls")) is None
    assert mw.hint(_candidate(content="")) is None


def test_error_loop_hints_on_repeated_signature():
    result = "Traceback (most recent call last):\nValueError: Colors must be aRGB hex values [exit_code=1]"
    context = [{"type": "tool", "content": result} for _ in range(3)]
    hint = ErrorLoopMiddleware().hint(_candidate(command="python3 fix.py", context=context))
    assert hint is not None
    assert "Colors must be aRGB" in hint.evidence


def test_error_loop_silent_on_success_and_mixed_errors():
    ok = [{"type": "tool", "content": "fine [exit_code=0]"} for _ in range(3)]
    assert ErrorLoopMiddleware().hint(_candidate(command="ls", context=ok)) is None
    mixed = [
        {"type": "tool", "content": "ValueError: a [exit_code=1]"},
        {"type": "tool", "content": "TypeError: b [exit_code=1]"},
        {"type": "tool", "content": "KeyError: c [exit_code=1]"},
    ]
    assert ErrorLoopMiddleware().hint(_candidate(command="ls", context=mixed)) is None


def test_turn_budget_fires_once_per_mark():
    mw = TurnBudgetMiddleware()
    fired = []
    for i in range(36):
        hint = mw.hint(_candidate(command="ls", candidate_id=f"c{i}"))
        if hint is not None:
            fired.append(i + 1)
    assert fired == [25, 35]


def test_hints_fail_open_on_sparse_candidates():
    for mw in build_teacher_middlewares():
        mw.hint(_candidate())  # must not raise
