import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain.agents.middleware.types import ModelRequest

from harness_zero.review import AssistantResponse, ExecuteCall, ReviewCandidate
from harness_zero.teacher_guidance import activate_teacher_candidate, reset_teacher_candidate
from harness_bank.appworld.middlewares import AppWorldReviewMiddleware

BANK = Path(__file__).resolve().parents[3]/'harness_bank/appworld'


def candidate(command=None, *, task='Read the requested value.', observations=(), marked=True):
    messages=[{'type':'human','content':('[APPWORLD_CURRENT_TASK]\n' if marked else '')+task}]
    messages += [{'type':'tool','content':x} for x in observations]
    return ReviewCandidate('test',None,messages,
        AssistantResponse(content='Continue' if command else 'Done',
                          tool_call=ExecuteCall(command=command) if command else None),{})


@pytest.mark.parametrize('command,task,needle',[
    ('apis.ApiDocs.show_api_doc()', 'Read a record.','namespace'),
    ('aw.pages("phone", "show_alarms")','Read a record.','namespace'),
    ('apis.supervisor.complete_task(answer=12)','Count records.','submits'),
    ('apis.file_system.create_file(file_path=p, overwrite=True)','Back up files.','changes'),
    ('open("~/records.csv", "w")','Export a CSV file.','local OS'),
])
def test_positive_review_cues(command,task,needle):
    hint=AppWorldReviewMiddleware().hint(candidate(command,task=task))
    assert hint is not None and needle in hint.evidence


def test_normal_discovery_and_demo_state_do_not_trigger():
    m=AppWorldReviewMiddleware()
    assert m.hint(candidate('print(apis.api_docs.show_app_descriptions())')) is None
    c=candidate('print(1)',marked=False,observations=['ValueError: bad\n[exit_code=1]'])
    assert m.hint(c) is None
    c=candidate('print(1)',observations=['an email says Error\n[AppWorld result] {"error":false}'])
    assert m.hint(c) is None


def test_real_error_is_guidance_only_and_does_not_rewrite_student():
    c=candidate('print(1)',observations=['[AppWorld result] {"error":true,"task_completed":false}'])
    before=c.to_json_dict()
    req=ModelRequest(model=None,tools=[],system_message=SystemMessage(content='TEACHER'),
                     messages=[HumanMessage(content='# Student update\nObserved execution')])
    token=activate_teacher_candidate(c)
    try: changed=AppWorldReviewMiddleware()._inject(req)
    finally: reset_teacher_candidate(token)
    assert 'Triggered middleware guidance' in str(changed.messages[-1].content)
    assert req.messages[-1].content=='# Student update\nObserved execution'
    assert c.to_json_dict()==before


def test_valid_answer_and_scratch_are_not_declared_wrong_by_syntax():
    m=AppWorldReviewMiddleware()
    hint=m.hint(candidate('apis.supervisor.complete_task(answer=12)'))
    assert 'preserve the valid computed answer' in hint.instruction
    hint=m.hint(candidate('open("scratch.csv", "w")',task='Export a file.'))
    assert 'PASS necessary scratch work' in hint.instruction


def test_teacher_workspace_and_replacement_use_raw_interface(tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from harness_zero.store import TrialStore
    from harness_zero.teacher import DeepAgentReviewer
    class Model(FakeMessagesListChatModel):
        def bind_tools(self,tools,**kwargs):return self
    replacement={'reasoning':'I need the public schema before changing records.','content':'Inspect the endpoint.',
                 'tool_call':{'name':'execute','command':"appworld exec <<'PY'\nprint(apis.api_docs.show_api_descriptions(app_name='phone'))\nPY"}}
    model=Model(responses=[AIMessage(content='',tool_calls=[{'id':'submit','name':'submit_review',
        'args':{'decision':'REPLACE','replacement':replacement,'components_used':['skill:api-workflow'],'reason':'Inspect the missing contract.'}}])])
    store=TrialStore(tmp_path/'teacher',task_id='fake',trial='one',components_dir=BANK)
    reviewer=DeepAgentReviewer(model=model,workspace=store.root,max_replacements_per_trial=5,
        teacher_middlewares=[AppWorldReviewMiddleware()])
    assert reviewer.tool_names=={'ls','read_file','glob','grep','submit_review'}
    c=candidate('apis.ApiDocs.show_api_doc()')
    result=asyncio.run(reviewer.review(c))
    assert result.decision=='REPLACE' and result.replacement.tool_call.command==replacement['tool_call']['command']
    assert 'aw.' not in result.replacement.tool_call.command
    assert not list(store.root.rglob('ground_truth'))
    assert not (store.root/'components/middlewares').exists()
    session=json.loads((store.root/'trial/teacher_session.jsonl').read_text().splitlines()[0])
    assert session['new_student_events']==c.context


def test_index_and_patterns_are_valid_and_do_not_embed_task_values():
    import ast,re
    index=json.loads((BANK/'index.json').read_text())
    assert len({c['id'] for c in index['components']})==len(index['components'])
    tasks=(BANK.parents[1]/'data/appworld-harbor/split_train147.txt').read_text().split()
    for item in index['components']:
        p=BANK/item['path'];assert p.is_file()
        text=p.read_text();assert not any(task in text for task in tasks)
        for code in re.findall(r'```python\n(.*?)```',text,re.S):ast.parse(code)
