"""The shared AppWorld base context, independent of any evolved student harness."""
import hashlib
import json

from langchain_core.runnables import RunnableLambda
from harness_zero.harness import HarnessZeroMinisweAgent
from envs.appworld.common_context import VERSION, build_initial_messages
from envs.appworld.harbor_environment import AppWorldDockerEnvironment

PROFILE_COMMAND = """appworld exec --json <<'APPWORLD_SETUP'
import json as _appworld_setup_json
print(_appworld_setup_json.dumps({'supervisor': apis.supervisor.show_profile(), 'apps': apis.api_docs.show_app_descriptions()}))
del _appworld_setup_json
APPWORLD_SETUP
"""


def task_instruction(text):
    return text.split('\n\n## Working notes\n',1)[0].rstrip()


class AppWorldMinisweAgent(HarnessZeroMinisweAgent):
    async def run(self, instruction, environment, context):
        if not isinstance(environment, AppWorldDockerEnvironment):
            raise ValueError('AppWorld requires envs.appworld.harbor_environment:AppWorldDockerEnvironment')
        # Artifact headers show the real task, not obsolete self-written Working notes.
        return await super().run(task_instruction(instruction),environment,context)

    def build_graph(self, model, backend):
        graph=super().build_graph(model,backend)

        async def initialize(state):
            raw=state['messages'][0]
            instruction=task_instruction(raw['content'] if isinstance(raw,dict) else raw.content)
            task_response=await backend.aexecute('appworld task',timeout=60)
            if task_response.exit_code!=0:
                raise RuntimeError('Failed to read public AppWorld task descriptor')
            public_task=json.loads(task_response.output)
            if instruction!=public_task['instruction'].rstrip():
                raise ValueError('Harbor task and public AppWorld instruction differ')
            response=await backend.aexecute(PROFILE_COMMAND,timeout=120)
            if response.exit_code!=0:
                raise RuntimeError('Failed to read public AppWorld profile and app descriptions')
            result=json.loads(response.output)
            if result['error']:
                raise RuntimeError('AppWorld context initialization failed: '+result['output'])
            public=json.loads(result['output'])
            messages=build_initial_messages(instruction,public['supervisor'],public['apps'])
            serialized=[m.model_dump(mode='json',exclude_none=True) for m in messages]
            digest=hashlib.sha256(json.dumps(serialized,sort_keys=True).encode()).hexdigest()
            artifact={'version':VERSION,'task_id':public_task['task_id'],'sha256':digest,'messages':serialized}
            self.logs_dir.mkdir(parents=True,exist_ok=True)
            (self.logs_dir/'appworld_initial_context.json').write_text(json.dumps(artifact,indent=2)+'\n')
            return {**state,'messages':messages}

        # RunnableSequence otherwise injects its default recursion_limit=25,
        # overriding create_agent's graph config and truncating valid agent runs.
        return (RunnableLambda(initialize) | graph).with_config(graph.config)
