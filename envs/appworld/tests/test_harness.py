"""Regression checks for the AppWorld harness defects found during review."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from harness_bank.appworld_student.middlewares.context_guard import ContextGuardMiddleware
from harness_bank.appworld_student.middlewares.submit_gate import SubmitGateMiddleware
from harness_bank.appworld_student.middlewares.retry_loop_guard import RetryLoopGuardMiddleware

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("appworld_client", ROOT / "runtime/client.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def status(value):
    return ToolMessage(content='[AppWorld result] ' + json.dumps({"task_completed": value}),
                       name="execute", tool_call_id="call")


def test_completed_answer_is_never_cleared_by_sentence_form():
    gate = SubmitGateMiddleware()
    messages = [HumanMessage(content="Name the artist most recommended to me on Spotify."),
                status(True), AIMessage(content="Done")]
    assert gate._check({"messages": messages}) is None


def test_failed_completion_call_does_not_count_as_submission():
    gate = SubmitGateMiddleware()
    messages = [AIMessage(content="", tool_calls=[{
        "id": "call", "name": "execute", "args": {
            "command": 'raise ValueError(); apis.supervisor.complete_task(answer="x")'}}]),
        status(False), AIMessage(content="Done")]
    assert gate._check({"messages": messages}) is not None


def test_nudge_budget_is_for_the_whole_run():
    gate = SubmitGateMiddleware()
    messages = []
    nudges = 0
    for _ in range(5):
        messages.append(AIMessage(content="", tool_calls=[{
            "id": "call", "name": "execute", "args": {"command": "print(1)"}}]))
        assert gate._check({"messages": messages}) is None
        messages.extend([status(False), AIMessage(content="Done")])
        nudges += gate._check({"messages": messages}) is not None
    assert nudges == 2


def test_unknown_status_does_not_make_up_a_submission_failure():
    gate = SubmitGateMiddleware()
    assert gate._check({"messages": [HumanMessage(content="Task"), AIMessage(content="Done")]}) is None


def test_tool_output_truncated_before_history_sync_and_async():
    guard = ContextGuardMiddleware()
    result = ToolMessage(content="head" + "x" * 30000 + "tail", id="id",
                         name="execute", tool_call_id="call", status="error")
    trimmed = guard.wrap_tool_call(None, lambda request: result)
    assert len(trimmed.content) < 12500
    assert trimmed.content.startswith("head") and trimmed.content.endswith("tail")
    assert trimmed.tool_call_id == "call" and trimmed.status == "error"
    assert len(result.content) > 30000

    async def handler(request):
        return result

    assert asyncio.run(guard.awrap_tool_call(None, handler)).content == trimmed.content


def test_truncation_is_wired_into_the_real_agent_graph():
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def large_output() -> str:
        """Return a large observation."""
        return "begin" + "x" * 30000 + "end"

    model = Model(responses=[AIMessage(content="", tool_calls=[{
        "id": "large", "name": "large_output", "args": {}}]), AIMessage(content="Done")])
    agent = create_agent(model, tools=[large_output], middleware=[ContextGuardMiddleware()])
    result = agent.invoke({"messages": [HumanMessage(content="Read output")]})
    observation = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert len(observation.content) < 12500
    assert observation.content.startswith("begin") and observation.content.endswith("end")


def test_cli_completion_supersedes_earlier_tool_status():
    gate = SubmitGateMiddleware()
    messages = [status(False), ToolMessage(content='[AppWorld result] {"task_completed": true}\n[exit_code=0]',
                                           tool_call_id="cli"), AIMessage(content="Done")]
    assert gate._check({"messages": messages}) is None


def test_retry_warning_stops_after_success():
    messages = [ToolMessage(content="ValueError: invalid value\n[exit_code=1]", tool_call_id="a"),
                ToolMessage(content="ValueError: invalid value\n[exit_code=1]", tool_call_id="b"),
                ToolMessage(content="success", tool_call_id="c")]
    request = SimpleNamespace(messages=messages)
    assert RetryLoopGuardMiddleware()._modify(request) is request


def test_stdout_buffer_retains_small_and_large_output():
    buffer = client.OutputBuffer()
    for text in ["first\n", "before failure\n"]:
        buffer.write(text)
    assert buffer.text() == "first\nbefore failure\n"
    buffer.write("x" * 30000)
    buffer.write("last\n")
    assert buffer.text().startswith("first\n") and buffer.text().endswith("last\n")
    assert len(buffer.text()) < 12500


def test_all_generated_tasks_use_isolated_verifier_and_executable_scripts():
    import tomllib
    data = ROOT.parents[1] / "data"
    tasks = list((data / "appworld-harbor/tasks").iterdir())
    assert len(tasks) == 732
    for task in tasks:
        config = tomllib.loads((task / "task.toml").read_text())
        assert config["verifier"]["environment_mode"] == "separate"
        assert config["artifacts"][0]["service"] == "world"
        assert (task / "tests/test.sh").stat().st_mode & 0o111
        assert (task / "tests/Dockerfile").is_file()
        assert not list((task / "environment").rglob("ground_truth"))
        assert "appworld-base:local" not in (task / "task.toml").read_text()
        compose = (task/'environment/docker-compose.yaml').read_text()
        assert 'com.docker.network.bridge.inhibit_ipv4: "true"' in compose


def test_success_payloads_and_record_ids_are_not_errors():
    from harness_bank.appworld_student.middlewares._prompt_injection import has_tool_error
    from harness_bank.appworld_student.middlewares.retry_loop_guard import _failure_marker
    for content in ['{"error": false, "output": "ok", "task_completed": false}',
                    '{"song_id": 404, "title": "A song"}', 'The email subject is: Build failed']:
        message = ToolMessage(content=content + '\n[exit_code=0]', tool_call_id='ok')
        assert not has_tool_error([message])
        assert _failure_marker(message) is None


def test_all_cli_status_formats_supersede_old_status():
    from harness_bank.appworld_student.middlewares.submit_gate import completion_status
    for content in ['{"task_completed": true}',
                    '{"output": "ok", "error": false, "task_completed": true}',
                    '[AppWorld result] {"error": false, "task_completed": true}']:
        message = ToolMessage(content=content + '\n[exit_code=0]', tool_call_id='ok')
        assert completion_status([status(False), message]) is True
    message = ToolMessage(content='[AppWorld result] {"task_completed": null}', tool_call_id='lost')
    assert completion_status([status(False), message]) is None


def test_different_runtime_errors_have_different_signatures():
    from harness_bank.appworld_student.middlewares.retry_loop_guard import _failure_marker
    errors = [ToolMessage(content=name + ': invalid\n[exit_code=1]', tool_call_id=name)
              for name in ['ValueError', 'TypeError']]
    assert _failure_marker(errors[0]) != _failure_marker(errors[1])


def test_bank_only_exposes_execute():
    from harness_zero.student import build_student_tools
    from harness_zero.student_harness import load_student_harness
    bank = ROOT.parents[1] / 'harness_bank/appworld_student'
    assert [tool.name for tool in build_student_tools(object(), load_student_harness(bank))] == ['execute']


def test_real_model_prompt_equals_review_prompt_with_dynamic_middleware():
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from harness_zero.student import HarnessReviewMiddleware, StudentRequestCaptureMiddleware
    from harness_bank.appworld_student.middlewares._prompt_injection import PromptInstructionMiddleware, has_no_tool_message
    from harness_zero.review import serialize_message
    seen, candidates = [], []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, *args, **kwargs):
            seen.append(messages)
            return super()._generate(messages, *args, **kwargs)

    class Store:
        def write_candidate(self, candidate):
            candidates.append(candidate)

        def append_review(self, *args):
            pass

    @tool
    def execute(command: str) -> str:
        """Run bash."""
        return 'ok\n[exit_code=0]'

    model = Model(responses=[AIMessage(content='', tool_calls=[{
        'id': 'call', 'name': 'execute', 'args': {'command': 'appworld status'}}]),
        AIMessage(content='done')])
    agent = create_agent(model, tools=[execute], system_prompt='BASE', middleware=[
        HarnessReviewMiddleware(None, Store(), max_replacements=0),
        PromptInstructionMiddleware(instruction="dynamic test", predicate=has_no_tool_message), StudentRequestCaptureMiddleware()])
    asyncio.run(agent.ainvoke({'messages': [HumanMessage(content='task')]}))
    assert len(candidates) == 2
    for candidate, messages in zip(candidates, seen):
        assert candidate.system_message == serialize_message(messages[0])
        assert candidate.context == [serialize_message(m) for m in messages[1:]]
    assert candidates[0].system_message != candidates[1].system_message
    assert candidates[0].candidate_issue is None


def test_teacher_accepts_changed_student_system_prompt(tmp_path):
    from harness_zero.teacher import DeepAgentReviewer, _format_teacher_update
    from harness_zero.review import ReviewCandidate, AssistantResponse
    reviewer = object.__new__(DeepAgentReviewer)
    reviewer._turn = 0
    reviewer._student_context = []
    reviewer._student_system_message = None
    def candidate(prompt):
        return ReviewCandidate(candidate_id='c', system_message={'content': prompt}, context=[],
                               original=AssistantResponse(content='done'), raw_original={})
    first, _ = reviewer._next_event(candidate('bootstrap'))
    reviewer._turn = 1
    second, _ = reviewer._next_event(candidate('base'))
    assert first['student_system_message']['content'] == 'bootstrap'
    assert second['student_system_message']['content'] == 'base'
    assert 'base' in _format_teacher_update(second)
    third, _ = reviewer._next_event(candidate('base'))
    assert 'student_system_message' not in third


def test_python_imports_use_the_same_api_proxy(monkeypatch):
    import sys
    apis = client.APIs()
    monkeypatch.setitem(sys.modules, 'apis', apis)
    namespace = {}
    exec('import apis\nfrom apis import spotify\nimport apis.supervisor', namespace)
    assert namespace['apis'] is apis
    assert namespace['spotify'] is apis.spotify
    assert namespace['apis'].supervisor is sys.modules['apis.supervisor']


def test_distinct_api_validation_errors_do_not_share_a_retry_signature():
    from harness_bank.appworld_student.middlewares.retry_loop_guard import _failure_marker
    messages = [ToolMessage(content='RuntimeError: Response status code is 422:\n' +
                            json.dumps({'message': reason}) + '\n[exit_code=1]', tool_call_id=reason)
                for reason in ['username required', 'access_token required']]
    assert _failure_marker(messages[0]) != _failure_marker(messages[1])


def test_long_json_keeps_completion_metadata_after_truncation():
    from harness_bank.appworld_student.middlewares.context_guard import truncate_result
    from harness_bank.appworld_student.middlewares.submit_gate import completion_status
    from harness_bank.appworld_student.middlewares._prompt_injection import has_tool_error
    message = ToolMessage(content=json.dumps({'output': 'x' * 30000, 'error': False,
                                              'error_type': None, 'task_completed': True}) + '\n[exit_code=0]',
                          tool_call_id='large-json')
    trimmed = truncate_result(message)
    assert completion_status([status(False), trimmed]) is True
    assert not has_tool_error([trimmed])
