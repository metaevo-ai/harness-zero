from types import SimpleNamespace

from harness_zero.review import AssistantResponse, ExecuteCall
from harness_bank.uspto.middlewares.enumeration_guard import TeacherEnumerationGuardMiddleware
from harness_bank.uspto.middlewares.completeness_guard import TeacherCompletenessGuardMiddleware


def test_answer_write_gets_comparison_guidance_without_forced_candidates():
    candidate = SimpleNamespace(original=AssistantResponse(tool_call=ExecuteCall(command="echo CC > /app/answer.txt")))
    hint = TeacherEnumerationGuardMiddleware().hint(candidate)
    assert hint is not None
    assert "Do not force three candidates" in hint.instruction
    assert "do not identify the recorded reaction" in hint.instruction
    assert "PASS a justified write" in hint.instruction


def test_inspection_is_not_an_enumeration_trigger():
    candidate = SimpleNamespace(original=AssistantResponse(tool_call=ExecuteCall(command="ls /app")))
    assert TeacherEnumerationGuardMiddleware().hint(candidate) is None


def test_final_check_does_not_require_balanced_reactant_equation():
    hint = TeacherCompletenessGuardMiddleware().hint(SimpleNamespace(original=AssistantResponse(content="Saved.")))
    assert hint is not None
    assert "need not be a fully balanced equation" in hint.instruction
    assert "do not automatically add" in hint.instruction


def test_tool_action_is_not_a_finish_trigger():
    candidate = SimpleNamespace(original=AssistantResponse(tool_call=ExecuteCall(command="cat /app/answer.txt")))
    assert TeacherCompletenessGuardMiddleware().hint(candidate) is None
