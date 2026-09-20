import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage

from envs.appworld.common_context import (CURRENT_TASK_MARKER,DEMO_USER,VERSION,
                                        build_initial_messages,corrected_parts)
from envs.appworld.agent import task_instruction

PROFILE={**DEMO_USER,'email':'actual@example.invalid','first_name':'Actual'}
APPS=[{'name':'spotify','description':'Music'},{'name':'supervisor','description':'Personal information'}]


@pytest.mark.parametrize('count',[0,23,53])
def test_complete_official_demo_executes_to_terminal_page(count):
    messages=build_initial_messages('Count something in the actual task.',PROFILE,APPS,playlist_count=count)
    assert len(messages)==23
    assert sum(isinstance(m,AIMessage) for m in messages)==10
    assert sum(isinstance(m,ToolMessage) for m in messages)==10
    assert sum(isinstance(m,HumanMessage) for m in messages)==3
    assert messages[-1].content.startswith(CURRENT_TASK_MARKER)
    assert 'actual@example.invalid' in messages[-1].content
    assert 'demo@example.invalid' in messages[0].content
    code='\n'.join(m.tool_calls[0]['args']['command'] for m in messages if isinstance(m,AIMessage))
    assert 'while True:' in code and 'while page_index < 10:' not in code
    assert 'spotify_access_token = login_result["access_token"]' in code
    assert all(m.tool_calls[0]['name']=='execute' for m in messages if isinstance(m,AIMessage))
    assert any(m.content.startswith(str(count)+'\n') for m in messages if isinstance(m,ToolMessage))


def test_every_official_key_instruction_section_is_retained():
    messages=build_initial_messages('Actual task',PROFILE,APPS)
    rules=messages[-2].content
    for phrase in ['A. General instructions','B. App-specific instructions',
                   'C. Code-operation instructions','D. Task-completion instructions',
                   "phone's contacts list",'Always process all results','minimal',
                   'Avoid collateral damage','variables','None/null','00:00:00 to 23:59:59']:
        assert phrase in rules,phrase
    assert 'open, os, pathlib' in rules
    assert 'INSIDE appworld exec' in rules
    assert 'not allowed and will raise an error' not in rules
    assert len(corrected_parts())==23


def test_current_public_demo_schema_has_no_obsolete_parameters():
    messages=build_initial_messages('Actual task',PROFILE,APPS)
    schema=json.loads((Path(__file__).resolve().parents[1]/'prompts/public_schemas.json').read_text())
    params={p['name'] for p in schema['show_playlist_library']['parameters']}
    assert 'query' not in params and 'sort_by' not in params
    assert {'access_token','page_index','page_limit'}<=params
    assert any("'page_index'" in m.content for m in messages if isinstance(m,ToolMessage))


def test_demo_completion_and_errors_are_not_current_task_evidence():
    from harness_bank.appworld_student.middlewares._observations import current_task_messages
    from harness_bank.appworld_student.middlewares._prompt_injection import has_no_tool_message,has_tool_error
    from harness_bank.appworld_student.middlewares.submit_gate import completion_status
    from harness_bank.appworld_student.middlewares.failure_recovery_instruction import FailureRecoveryInstructionMiddleware
    from harness_bank.appworld_student.middlewares.retry_loop_guard import RetryLoopGuardMiddleware
    messages=build_initial_messages('Actual task',PROFILE,APPS)
    messages.insert(-1,ToolMessage(content='ValueError: demo failure\n[exit_code=1]',tool_call_id='demo-error'))
    assert current_task_messages(messages)==[messages[-1]]
    assert has_no_tool_message(messages)
    assert not has_tool_error(messages)
    assert completion_status(messages) is None
    request=SimpleNamespace(messages=messages)
    assert FailureRecoveryInstructionMiddleware()._modify(request) is request
    assert RetryLoopGuardMiddleware()._modify(request) is request
    messages.extend([ToolMessage(content='[AppWorld result] {"task_completed": false,"error":false}',tool_call_id='real'),
                     HumanMessage(content='Reminder',name='submit-gate')])
    assert completion_status(messages) is False
    assert not has_no_tool_message(messages)


def test_both_arms_receive_identical_foundation_and_original_task():
    task='Do the original task exactly.\nSecond paragraph.'
    assert task_instruction(task+'\n\n## Working notes\nold manual notes')==task
    a=build_initial_messages(task,PROFILE,APPS)
    b=build_initial_messages(task,PROFILE,list(reversed(APPS)))
    assert [m.model_dump() for m in a]==[m.model_dump() for m in b]
    assert a[-1].content.endswith('Task: '+task)
    assert 'aw.call' not in '\n'.join(str(m.content) for m in a)


def test_official_agent_rejects_an_environment_without_trusted_stop_checks():
    from envs.appworld.agent import AppWorldMinisweAgent
    agent=object.__new__(AppWorldMinisweAgent)
    with pytest.raises(ValueError,match='AppWorldDockerEnvironment'):
        asyncio.run(agent.run('task',object(),None))


def test_teacher_sees_prefilled_assistant_commands_on_first_review():
    from harness_zero.review import ReviewCandidate,AssistantResponse
    from harness_zero.teacher import DeepAgentReviewer,_format_teacher_update
    messages=build_initial_messages('Actual task',PROFILE,APPS)
    context=[m.model_dump(mode='json') for m in messages]
    reviewer=object.__new__(DeepAgentReviewer)
    reviewer._student_context=[];reviewer._student_system_message=None;reviewer._turn=0
    candidate=ReviewCandidate(candidate_id='c',system_message=None,context=context,
                              original=AssistantResponse(content='done'),raw_original={})
    event,_=reviewer._next_event(candidate)
    assert len(event['new_student_events'])==23
    text=_format_teacher_update(event)
    assert 'appworld_demo_1' in text and 'appworld exec' in text
    assert CURRENT_TASK_MARKER in text


def test_adapter_preserves_graph_limits_and_does_not_execute_demos(monkeypatch,tmp_path):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from harness_zero.harness import HarnessZeroMinisweAgent
    from envs.appworld.agent import AppWorldMinisweAgent
    actual_commands=[]
    class Model(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    @tool
    def execute(command:str)->str:
        """Run a test command."""
        actual_commands.append(command)
        return 'ok'
    model=Model(responses=[AIMessage(content='',tool_calls=[{
        'id':f'real-{n}','name':'execute','args':{'command':f'echo {n}'}}]) for n in range(14)]+[AIMessage(content='done')])
    graph=create_agent(model,tools=[execute],system_prompt='BASE')
    monkeypatch.setattr(HarnessZeroMinisweAgent,'build_graph',lambda self,model,backend:graph)
    class Backend:
        async def aexecute(self,command,timeout):
            if command=='appworld task':
                output=json.dumps({'task_id':'test','instruction':'Actual task'})
            else:
                assert 'fake_access_token' not in command and 'num_playlists' not in command
                output=json.dumps({'error':False,'output':json.dumps({'supervisor':PROFILE,'apps':APPS})})
            return SimpleNamespace(exit_code=0,output=output)
    agent=object.__new__(AppWorldMinisweAgent);agent.logs_dir=tmp_path
    wrapped=agent.build_graph(model,Backend())
    result=asyncio.run(wrapped.ainvoke({'messages':[{'role':'user','content':'Actual task'}]}))
    assert result['messages'][-1].content=='done'
    assert actual_commands==[f'echo {n}' for n in range(14)]
    recorded=json.loads((tmp_path/'appworld_initial_context.json').read_text())
    assert recorded['version']==VERSION and len(recorded['messages'])==23
