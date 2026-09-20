import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

BANK = Path(__file__).resolve().parents[3] / 'harness_bank/appworld_student'


@pytest.fixture
def workflow(monkeypatch):
    fake = SimpleNamespace()
    fake.supervisor = SimpleNamespace(show_profile=lambda: {'email': 'user@example.invalid', 'phone_number': '123'},
        show_account_passwords=lambda: [{'account_name': app, 'password': 'test'} for app in ['spotify','phone','file_system']])
    monkeypatch.setitem(__import__('sys').modules, 'apis', fake)
    spec = importlib.util.spec_from_file_location('workflow_test', BANK/'skills/appworld-workflow/aw.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schemas, calls, logins = {}, [], []
    for app in ['spotify','phone','file_system']:
        def login(username, password, app=app):
            logins.append((app, username))
            return {'access_token': f'{app}-{len(logins)}'}
        setattr(fake, app, SimpleNamespace(login=login))
        schemas[(app,'login')]={'method':'POST','parameters':[
            {'name':'username','required':True,'description':'Your account phone_number.' if app=='phone' else 'Your account email.'},
            {'name':'password','required':True}]}
    class Opener:
        def open(self, url, timeout):
            from urllib.parse import urlsplit,parse_qs
            query=parse_qs(urlsplit(url).query)
            return io.StringIO(json.dumps(schemas[(query['app'][0],query['endpoint'][0])]))
    module.aw.opener=Opener()
    return SimpleNamespace(module=module, aw=module.aw, apis=fake, schemas=schemas, calls=calls, logins=logins)


def endpoint(w, app='spotify', api='read', method='GET', parameters=(), function=None):
    w.schemas[(app,api)]={'method':method,'parameters':list(parameters)}
    def default(**kwargs):
        w.calls.append(kwargs)
        return {'ok':True}
    setattr(getattr(w.apis,app),api,function or default)


def test_auto_auth_is_per_app_and_preserves_zero_false(workflow):
    w=workflow
    params=[{'name':'access_token','required':True},{'name':'n','required':True},{'name':'flag','required':True}]
    endpoint(w,parameters=params)
    assert w.aw.call('spotify','read',n=0,flag=False)=={'ok':True}
    assert w.calls[0]=={'n':0,'flag':False,'access_token':'spotify-1'}
    w.aw.call('spotify','read',n=0,flag=False)
    assert len(w.logins)==1
    assert w.aw.login('phone').startswith('phone-')
    assert w.logins[-1]==('phone','123')


def test_unknown_and_missing_fields_fail_before_mutation(workflow):
    w=workflow
    endpoint(w,method='POST',parameters=[{'name':'item_id','required':True}])
    with pytest.raises(ValueError,match='Undocumented'):w.aw.call('spotify','read',id=1)
    with pytest.raises(ValueError,match='Missing required'):w.aw.call('spotify','read')
    assert not w.calls and not w.logins


def test_cross_app_tokens_and_explicit_identity(workflow):
    w=workflow
    endpoint(w,parameters=[{'name':'access_token','required':True},{'name':'file_system_access_token','required':False}])
    w.aw.call('spotify','read',access_token='explicit-other-user')
    assert w.calls[0]['access_token']=='explicit-other-user'
    assert w.calls[0]['file_system_access_token'].startswith('file_system-')
    assert [x[0] for x in w.logins]==['file_system']


def test_token_response_dictionary_is_rejected_without_changing_identity(workflow):
    w = workflow
    endpoint(w,parameters=[{'name':'access_token','required':True},
                           {'name':'file_system_access_token','required':False}])
    value = {'access_token':'another-account','token_type':'Bearer'}
    with pytest.raises(TypeError,match='login_result'):
        w.aw.call('spotify','read',access_token=value)
    assert not w.calls and not w.logins and value['access_token']=='another-account'
    w.aw.call('spotify','read',access_token=value['access_token'],file_system_access_token=None)
    assert w.calls[-1]=={'access_token':'another-account','file_system_access_token':None}


def test_401_requires_explicit_retry_with_refreshed_token(workflow):
    w=workflow
    def read(**kwargs):
        w.calls.append(kwargs)
        if len(w.calls)==1:raise RuntimeError('Response status code is 401: expired')
        return 'ok'
    params=[{'name':'access_token','required':True}]
    endpoint(w,parameters=params,function=read)
    with pytest.raises(RuntimeError,match='not replayed'):w.aw.call('spotify','read')
    assert len(w.calls)==1
    assert w.aw.call('spotify','read')=='ok'
    assert w.calls[0]['access_token']!=w.calls[1]['access_token']
    def mutate(**kwargs):
        w.calls.append(kwargs)
        raise RuntimeError('Response status code is 401: downstream failed')
    endpoint(w,api='write',method='POST',parameters=params,function=mutate)
    before=len(w.calls)
    with pytest.raises(RuntimeError,match='[Rr]ead back'):w.aw.call('spotify','write')
    assert len(w.calls)==before+1


def test_successful_logout_invalidates_only_the_matching_cached_token(workflow):
    w = workflow
    params = [{'name':'access_token','required':True}]
    endpoint(w,api='logout',method='POST',parameters=params)
    endpoint(w,parameters=params)
    w.aw.call('spotify','read')
    original = w.aw.tokens['spotify']
    w.aw.call('spotify','logout',access_token='another-session')
    assert w.aw.tokens['spotify'] == original
    w.aw.call('spotify','logout')
    assert 'spotify' not in w.aw.tokens
    w.aw.call('spotify','read')
    assert w.calls[-1]['access_token'] != original


def paged(w, pages, bound='value >= 1, <= 20'):
    def fetch(**kwargs):
        w.calls.append(kwargs)
        value=pages[kwargs['page_index']]
        if isinstance(value,Exception):raise value
        return value
    endpoint(w,api='items',parameters=[{'name':'page_index','required':False,'default':0},
        {'name':'page_limit','required':False,'default':5,'constraints':[bound]}],function=fetch)


def test_pages_continue_after_short_pages_and_keep_distinct_records(workflow):
    w=workflow
    records=[{'id':1,'artist':'same'},{'id':2,'artist':'same'}]
    paged(w,[[records[0]],[records[1]],[]])
    assert w.aw.pages('spotify','items')==records
    assert [c['page_index'] for c in w.calls]==[0,1,2]


def test_string_pages_use_safe_size_for_huge_schema_max(workflow):
    w=workflow
    paged(w,[['a','b'],[]],bound='value >= 1, <= 9.223372036854776e+18')
    assert w.aw.pages('spotify','items')==['a','b']
    assert all(c['page_limit']==20 for c in w.calls)


@pytest.mark.parametrize('pages,limit', [([['a'],['b'],['a']],4),([['a']],1),([['a'],RuntimeError('network failed')],3),([['a'],TimeoutError('network timeout')],3)])
def test_incomplete_pages_never_return_partial_success(workflow,pages,limit):
    w=workflow;paged(w,pages)
    with pytest.raises(w.module.PaginationIncomplete):w.aw.pages('spotify','items',max_pages=limit)


def test_nonpaginated_collections_are_not_guessed(workflow):
    w=workflow;endpoint(w,api='nested',function=lambda: {'a':[],'b':[]})
    with pytest.raises(ValueError,match='non-paginated'):w.aw.pages('spotify','nested')


def test_action_completion_has_no_answer_and_query_keeps_value(workflow):
    w=workflow
    endpoint(w,app='supervisor',api='complete_task',method='POST',parameters=[{'name':'answer','required':False}])
    w.aw.finish_actions();w.aw.answer(0)
    assert w.calls==[{}, {'answer':0}]


def test_snapshot_factories_resolve_to_snapshot_files(tmp_path):
    import inspect,shutil
    from harness_zero.student_harness import load_student_harness
    target=tmp_path/'snapshot'
    shutil.copytree(BANK,target,ignore=shutil.ignore_patterns('__pycache__'))
    spec=load_student_harness(target)
    assert not spec.tool_factories and not spec.memory_enabled
    assert all(Path(inspect.getfile(factory)).is_relative_to(target) for factory in spec.middleware_factories)


def test_bootstrap_uses_async_backend_and_persistent_concise_guide():
    import asyncio
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import SystemMessage, ToolMessage
    from deepagents_harbor.current import current_backend
    from harness_bank.appworld_student.middlewares.bootstrap_instruction import BootstrapInstructionMiddleware
    called=[]
    class Backend:
        async def aexecute(self, command, timeout):
            called.append((command,timeout))
            return SimpleNamespace(exit_code=0,output='ready')
        def execute(self, *args, **kwargs):
            raise AssertionError('sync backend must not run on event loop')
    middleware=BootstrapInstructionMiddleware()
    token=current_backend.set(Backend())
    try: asyncio.run(middleware.abefore_agent({},None))
    finally: current_backend.reset(token)
    assert len(called)==1 and 'import aw' in called[0][0]
    for messages in [[],[ToolMessage(content='ok',tool_call_id='done')]]:
        request=ModelRequest(model=None,messages=messages,system_message=SystemMessage(content='BASE'),tools=[])
        result=middleware._modify(request)
        text=str(result.system_message.content)
        assert 'aw.finish_actions()' in text and 'aw.pages' in text
        # The added per-entity and payload-purity guidance must stay concise.
        assert 'memory_guidelines' not in text and len(text)<3800


def test_file_teaching_is_selected_only_from_the_real_task():
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import HumanMessage, SystemMessage
    from harness_bank.appworld_student.middlewares.bootstrap_instruction import BootstrapInstructionMiddleware
    middleware = BootstrapInstructionMiddleware()
    for task, expected in [('Start music playback.',False),('Export my records to a CSV backup.',True)]:
        request = ModelRequest(model=None,tools=[],system_message=SystemMessage(content='BASE'),messages=[
            HumanMessage(content='Demonstration about files and backups.'),
            HumanMessage(content='[APPWORLD_CURRENT_TASK]\n'+task)])
        text = str(middleware._modify(request).system_message.content)
        assert ('MULTIPLE FILE OPERATIONS' in text) is expected
        if expected:
            assert "'create_file'" in text and "'show_file'" in text
            assert 'ONE output row' in text and 'aw.move_files(original_paths' in text


def test_paginated_call_cannot_silently_return_default_first_page(workflow):
    w=workflow;paged(w,[['first-page'],[]])
    with pytest.raises(ValueError,match='paginated'):w.aw.call('spotify','items')
    assert not w.calls
    assert w.aw.call('spotify','items',page_index=0)==['first-page']


@pytest.fixture
def bound_workflow(workflow, monkeypatch):
    from envs.appworld.runtime import client
    w = workflow
    # Use the actual student proxy classes, while replacing only HTTP transport.
    original = client.App.call
    monkeypatch.setattr(client.App, 'call', original)
    names = ('amazon','api_docs','file_system','gmail','phone','simple_note',
             'splitwise','spotify','supervisor','todoist','venmo')
    for name in names:
        monkeypatch.delitem(__import__('sys').modules, 'apis.'+name, raising=False)
    public = client.APIs()
    monkeypatch.setitem(__import__('sys').modules, 'apis', public)
    for name in names:
        monkeypatch.setitem(__import__('sys').modules, 'apis.'+name, getattr(public,name))
    w.aw.apis = public
    def request(base, path, payload):
        assert path == '/call'
        w.calls.append(payload)
        return {'result': {'ok': True}}
    monkeypatch.setattr(client, 'request', request)
    w.requester = client.Requester()
    w.aw.bind_apis()
    return w


def test_native_binding_covers_imports_requester_and_repeated_install(bound_workflow):
    w = bound_workflow
    from apis import spotify
    w.schemas[('spotify','read')] = {'parameters': [{'name':'access_token','required':True}]}
    w.aw.tokens['spotify'] = 'supervisor-token'
    w.aw.bind_apis()
    spotify.read()
    w.requester.request('spotify','read',access_token='explicit-other-account')
    w.aw.call('spotify','read')
    assert [c['kwargs']['access_token'] for c in w.calls] == [
        'supervisor-token','explicit-other-account','supervisor-token']
    with pytest.raises(ValueError,match='Undocumented'):
        spotify.read(ignored_filter='bad')
    assert len(w.calls) == 3


def test_native_pagination_does_not_silently_expand_a_page(bound_workflow):
    w = bound_workflow
    w.schemas[('phone','contacts')] = {'parameters': [
        {'name':'page_index','required':False,'default':0},
        {'name':'page_limit','required':False,'default':5}]}
    with pytest.raises(ValueError,match='paginated'):
        w.aw.apis.phone.contacts()
    assert not w.calls
    w.aw.apis.phone.contacts(page_index=2,page_limit=5)
    assert [c['kwargs'] for c in w.calls] == [{'page_index':2,'page_limit':5}]


def test_bound_automatic_login_and_pages_use_original_transport(bound_workflow,monkeypatch):
    from envs.appworld.runtime import client
    w = bound_workflow
    for api in ('show_profile','show_account_passwords'):
        w.schemas[('supervisor',api)] = {'parameters': []}
    w.schemas[('spotify','items')] = {'method':'GET','parameters': [
        {'name':'access_token','required':True},
        {'name':'page_index','required':False,'default':0},
        {'name':'page_limit','required':False,'default':5}]}
    def request(base,path,payload):
        w.calls.append(payload)
        api = payload['api']
        if api == 'show_profile': result = {'email':'user@example.invalid'}
        elif api == 'show_account_passwords': result = [{'account_name':'spotify','password':'test'}]
        elif api == 'login': result = {'access_token':'new-token'}
        else:
            assert payload['kwargs']['access_token'] == 'new-token'
            result = ['record'] if payload['kwargs']['page_index'] == 0 else []
        return {'result':result}
    monkeypatch.setattr(client,'request',request)
    assert w.aw.pages('spotify','items') == ['record']
    assert [c['api'] for c in w.calls] == [
        'show_profile','show_account_passwords','login','items','items']


@pytest.mark.parametrize('answer', [0,False,'summary'])
def test_native_answer_requires_explicit_choice_without_changing_state(bound_workflow,answer):
    w = bound_workflow
    w.schemas[('supervisor','complete_task')] = {'parameters': [
        {'name':'answer','required':False},{'name':'status','required':False}]}
    with pytest.raises(ValueError,match='NOT submitted'):
        w.aw.apis.supervisor.complete_task(answer=answer,status='fail')
    assert not w.calls
    w.aw.answer(answer,status='fail')
    assert w.calls[-1]['kwargs'] == {'answer':answer,'status':'fail'}
    w.aw.finish_actions()
    assert w.calls[-1]['kwargs'] == {}
    w.aw.apis.supervisor.complete_task(answer=None,status='fail')
    assert w.calls[-1]['kwargs'] == {'answer':None,'status':'fail'}


@pytest.fixture
def file_workflow(workflow):
    w = workflow
    w.files = {f'/source/{name}.txt':{'file_id':i,'path':f'/source/{name}.txt',
        'content':name,'created_at':'old','updated_at':'old'} for i,name in enumerate(['a','b'],1)}
    w.files['/target/keep.txt'] = {'file_id':3,'path':'/target/keep.txt',
        'content':'untouched','created_at':'old','updated_at':'old'}
    w.mutations = []
    schema_path = BANK.parents[1]/'data/api_docs/standard/file_system.json'
    if not schema_path.is_file():
        pytest.skip("official AppWorld api_docs not present (provided by `appworld install`)")
    schemas = json.loads(schema_path.read_text())
    for api in ['show_file','file_exists','create_directory','move_file']:
        w.schemas[('file_system',api)] = schemas[api]
    w.apis.file_system.show_file = lambda file_path,**kwargs: dict(w.files[file_path])
    w.apis.file_system.file_exists = lambda file_path,**kwargs: {'exists':file_path in w.files}
    w.apis.file_system.create_directory = lambda **kwargs: w.mutations.append(('mkdir',kwargs))
    def move(source_file_path,destination_file_path,retain_dates,**kwargs):
        assert retain_dates is True and destination_file_path not in w.files
        w.mutations.append(('move',source_file_path,destination_file_path))
        record = w.files.pop(source_file_path)
        w.files[destination_file_path] = {**record,'path':destination_file_path}
        return {'destination_file_path':destination_file_path}
    w.apis.file_system.move_file = move
    return w


def test_move_plan_renames_every_group_and_preserves_destination_only_files(file_workflow):
    w = file_workflow
    from pathlib import PurePosixPath
    result = w.aw.move_files(['/source/a.txt','/source/b.txt'],
        rename=lambda f:'new-'+PurePosixPath(f['path']).name,
        directory_for=lambda f:'/target' if f['file_id']==1 else '/source')
    assert result==['/target/new-a.txt','/source/new-b.txt']
    assert set(w.files)==set(result)|{'/target/keep.txt'}
    assert w.files['/target/keep.txt']['content']=='untouched'
    assert [x[1] for x in w.mutations if x[0]=='move']==['/source/a.txt','/source/b.txt']


def test_move_plan_collision_fails_before_any_mutation(file_workflow):
    w = file_workflow
    with pytest.raises(FileExistsError):
        w.aw.move_files(['/source/a.txt'],rename=lambda f:'keep.txt',directory_for=lambda f:'/target')
    assert not w.mutations and '/source/a.txt' in w.files


def test_move_plan_uses_documented_home_directory_spelling(file_workflow):
    w = file_workflow
    assert w.aw.move_files(['/source/a.txt'],rename=lambda f:'new.txt',directory_for=lambda f:'~')==['~/new.txt']
    assert next(m[1]['directory_path'] for m in w.mutations if m[0]=='mkdir')=='~/'


def test_move_plan_does_not_report_success_or_replay_after_bad_readback(file_workflow):
    w = file_workflow
    move = w.apis.file_system.move_file
    def corrupt(**kwargs):
        result = move(**kwargs)
        w.files[kwargs['destination_file_path']]['content'] = 'bad'
        return result
    w.apis.file_system.move_file = corrupt
    with pytest.raises(RuntimeError,match='partial state'):
        w.aw.move_files(['/source/a.txt','/source/b.txt'],rename=lambda f:f"new-{f['file_id']}.txt")
    assert sum(m[0]=='move' for m in w.mutations)==1
    assert '/source/b.txt' in w.files
