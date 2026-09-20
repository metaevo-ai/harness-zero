"""AppWorld teacher profile, version 1; candidate-only guidance."""
from harness_bank.appworld.middlewares import AppWorldReviewMiddleware


def build_teacher_middlewares():
    return [AppWorldReviewMiddleware()]
