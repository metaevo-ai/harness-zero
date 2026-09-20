"""Teacher-side candidate middlewares for the USPTO harness lib."""

from harness_bank.uspto.middlewares.answer_guard import (
    TeacherAnswerGuardMiddleware,
)
from harness_bank.uspto.middlewares.candidate_format import (
    CandidateFormatMiddleware,
)
from harness_bank.uspto.middlewares.canonical_form_guard import (
    TeacherCanonicalFormGuardMiddleware,
)
from harness_bank.uspto.middlewares.command_timeout import (
    CommandTimeoutMiddleware,
)
from harness_bank.uspto.middlewares.completeness_guard import (
    TeacherCompletenessGuardMiddleware,
)
from harness_bank.uspto.middlewares.enumeration_guard import (
    TeacherEnumerationGuardMiddleware,
)
from harness_bank.uspto.middlewares.stall import (
    StallMiddleware,
)


def build_teacher_middlewares():
    return [
        CandidateFormatMiddleware(),
        CommandTimeoutMiddleware(),
        TeacherEnumerationGuardMiddleware(),
        TeacherCanonicalFormGuardMiddleware(),
        StallMiddleware(),
        TeacherAnswerGuardMiddleware(),
        TeacherCompletenessGuardMiddleware(),
    ]
