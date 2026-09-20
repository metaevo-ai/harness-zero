"""Teacher-side candidate middlewares for the SpreadsheetBench harness lib."""

from harness_bank.spreadsheetbench.middlewares.error_loop import (
    ErrorLoopMiddleware,
)
from harness_bank.spreadsheetbench.middlewares.finish_guard import (
    FinishGuardMiddleware,
)
from harness_bank.spreadsheetbench.middlewares.stall import (
    StallMiddleware,
)
from harness_bank.spreadsheetbench.middlewares.turn_budget import (
    TurnBudgetMiddleware,
)


def build_teacher_middlewares():
    return [
        StallMiddleware(),
        ErrorLoopMiddleware(),
        TurnBudgetMiddleware(),
        FinishGuardMiddleware(),
    ]
