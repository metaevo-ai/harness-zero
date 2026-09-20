"""Complete official ReAct teaching, corrected and adapted to the execute CLI."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ROOT = Path(__file__).resolve().parent / 'prompts'
CURRENT_TASK_MARKER = '[APPWORLD_CURRENT_TASK]'
VERSION = 'official-react-bash-v1'
DEMO_USER = {'first_name': 'Demo', 'last_name': 'User',
             'email': 'demo@example.invalid', 'phone_number': '15550001001'}
TRANSPORT = '''AppWorld interface for this benchmark:
Use the execute tool for bash commands. Run AppWorld Python with one command:
appworld exec <<'APPWORLD_PY'
print(apis.supervisor.show_profile())
APPWORLD_PY
The task is already initialized. apis and requester are pre-injected; import apis also works.
Python variables persist across appworld exec calls. Standalone python3 has a different namespace.
The simulated clock is frozen inside appworld exec; the outer shell/OS clock is not the task clock.
Only public app APIs read or change simulated apps. Local OS files are scratch space, not app files.
Printed output is shown; expressions without print produce no display. A Python error does not undo
previous API mutations. Execution has a 100-second deadline (maximum 120 with --timeout); a timed-out
worker is terminated and loses Python variables, while app state and earlier printed output persist.
Read affected state before retrying an operation that may have partially completed.
appworld docs <app> [endpoint] is an alternative way to read the same public API schemas.
The task_completed flag reports submission, not grading success.

The following is the official AppWorld ReAct worked example, adapted to this bash interface.
It uses a fictional account and mocked/abbreviated outputs. These example commands are HISTORY ONLY:
they have NOT run in your actual task, and their variables, tokens and completion state are not live.
'''


def corrected_parts():
    source = (ROOT / 'upstream/react_code_agent.txt').read_text()
    expected = json.loads((ROOT / 'provenance.json').read_text())['sha256']
    assert hashlib.sha256(source.encode()).hexdigest() == expected, 'Official source changed'
    fixes = {
        '[account_password["account_name"] == "spotify" for account_password in passwords][0]["password"]':
            'next(account_password["password"] for account_password in passwords if account_password["account_name"] == "spotify")',
        'print(login_result)': 'spotify_access_token = login_result["access_token"]\nprint(login_result)',
        'while page_index < 10:': 'while True:',
        'apis.supervisor.complete_task(answer=num_playlists)': 'print(apis.supervisor.complete_task(answer=num_playlists))',
        '- Make sure to end code blocks with ``` followed by a newline(\\n).':
            '- Invoke execute with one bash-wrapped appworld exec command per step. Do not merely print a code block as your final answer.',
        'from Python function calls like `datetime.now()`,':
            'from Python calls like `datetime.now()` INSIDE appworld exec,',
        'Do not use OS modules or functions.':
            'Do not use OS modules or functions to operate on the task\'s app files.',
        '- The Python environment supports the standard library. But system-level operations that may access or affect OS files, processes, etc., are not allowed and will raise an error if called.':
            '- The Python environment supports the standard library. In this isolated bash adapter, OS operations affect only local scratch files/processes, never the simulated apps. Use the file_system APIs for task files; do not use open, os, pathlib or shell file commands for those files.',
    }
    for old, new in fixes.items():
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    pieces = re.split(r'(?m)^(USER|ASSISTANT|SYSTEM):\n', source)
    assert not pieces[0].strip()
    parts = [(pieces[i].lower(), pieces[i+1].strip()) for i in range(1,len(pieces),2)]
    assert len(parts) == 23 and sum(role=='assistant' for role,_ in parts)==10
    return parts


def bash_command(code):
    ast.parse(code)
    assert '\nAPPWORLD_PY\n' not in '\n'+code+'\n'
    return "appworld exec <<'APPWORLD_PY'\n" + code.strip() + '\nAPPWORLD_PY'


class DemoWorld:
    """Pure in-memory teaching fixture; never connects to an actual task."""
    def __init__(self, app_descriptions, playlist_count=23):
        self.schemas = json.loads((ROOT/'public_schemas.json').read_text())
        self.pages = []
        self.submissions = []
        self.playlists = [{'playlist_id': i, 'title': f'Demo playlist {i}'}
                          for i in range(1,playlist_count+1)]
        self.apis = SimpleNamespace(
            api_docs=SimpleNamespace(
                show_app_descriptions=lambda: copy.deepcopy(app_descriptions),
                show_api_descriptions=lambda app_name: copy.deepcopy(self.schemas['indexes'][app_name]),
                show_api_doc=lambda app_name,api_name: copy.deepcopy(self.schemas[api_name])),
            supervisor=SimpleNamespace(
                show_account_passwords=lambda: [
                    {'account_name':'spotify','password':'dummy_spotify_pass'},
                    {'account_name':'file_system','password':'dummy_fs_pass'}],
                complete_task=self.complete_task),
            spotify=SimpleNamespace(login=self.login,show_playlist_library=self.show_playlist_library))
        self.namespace = {'apis':self.apis}

    def login(self, *, username, password):
        assert username==DEMO_USER['email'] and password=='dummy_spotify_pass'
        return {'access_token':'fake_access_token','token_type':'Bearer'}

    def show_playlist_library(self, *, access_token, page_index=0):
        assert access_token=='fake_access_token'
        self.pages.append(page_index)
        return self.playlists[page_index*5:(page_index+1)*5]

    def complete_task(self, *, answer):
        self.submissions.append(answer)
        return {'message':'Marked the active task complete.'}

    def execute(self, code):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(code,'<official-appworld-demo>','exec'),self.namespace)
        return output.getvalue().rstrip()


def build_initial_messages(instruction, supervisor, app_descriptions, *, playlist_count=23):
    """Preserve all 23 teaching messages; convert code/output pairs to native tools."""
    env = Environment(undefined=StrictUndefined,autoescape=False)
    apps = sorted([{'name':item['name'],'description':item['description']}
                   for item in app_descriptions if item['name']!='admin'],key=lambda item:item['name'])
    demo = DemoWorld(apps,playlist_count)
    messages = []
    pending = None
    codes = []
    parts = corrected_parts()
    for index,(role,text) in enumerate(parts):
        user = supervisor if index==len(parts)-1 else DEMO_USER
        rendered = env.from_string(text).render(main_user=user,
            instruction=instruction,app_descriptions=json.dumps(apps,indent=1))
        if role=='assistant':
            blocks = re.findall(r'```python\n(.*?)```',rendered,re.S)
            assert len(blocks)==1
            code=blocks[0].strip();codes.append(code)
            explanation=re.sub(r'```python\n.*?```','',rendered,flags=re.S).strip()
            call_id=f'appworld_demo_{len(codes)}'
            messages.append(AIMessage(content=explanation,
                additional_kwargs={'reasoning_content':explanation},
                tool_calls=[{'id':call_id,'name':'execute','args':{'command':bash_command(code)}}]))
            pending=(call_id,demo.execute(code))
        elif pending is not None:
            assert role=='user' and rendered.startswith('Output:')
            call_id,output=pending
            if 'show_api_descriptions(' in codes[-1]:
                output += '\n[Demonstration note: API index abbreviated to relevant endpoints.]'
            metadata={'error':False,'error_type':None,'session_reset':False,
                      'task_completed':bool(demo.submissions)}
            messages.append(ToolMessage(content=output+'\n[AppWorld result] '+json.dumps(metadata)+'\n[exit_code=0]',
                                        tool_call_id=call_id,name='execute'))
            pending=None
        else:
            assert role=='user'
            rendered=re.sub(r'```python\n(.*?)```',
                lambda match:'```bash\n'+bash_command(match[1])+'\n```',rendered,flags=re.S)
            if index==0:rendered=TRANSPORT+'\n'+rendered
            if index==len(parts)-1:
                rendered=CURRENT_TASK_MARKER+'\nThis is a NEW task. No demonstration code or variables are live.\n'+rendered
            messages.append(HumanMessage(content=rendered))
    assert len(messages)==23 and len(codes)==10 and pending is None
    assert demo.submissions==[playlist_count]
    assert demo.pages==list(range((playlist_count+4)//5+1))
    return messages
