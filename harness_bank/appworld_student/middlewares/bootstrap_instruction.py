"""Load the public workflow helper and keep one concise guide model-visible."""
from pathlib import Path
import re

from deepagents.middleware._utils import append_to_system_message
from deepagents_harbor.current import current_backend
from langchain_core.messages import HumanMessage
from ._observations import CURRENT_TASK_MARKER
from ._prompt_injection import PromptInstructionMiddleware

BOOTSTRAP_INSTRUCTION = (Path(__file__).resolve().parents[1] / 'memory.md').read_text()
FILE_INSTRUCTION = (Path(__file__).resolve().parents[1] / 'skills/appworld-workflow/files.md').read_text()
FILE_TASK = re.compile(r'\b(?:files?|folders?|director(?:y|ies)|backups?|exports?|csv|rename|attachments?)\b', re.I)
INIT_COMMAND = """appworld exec <<'PY'
import sys
sys.path.insert(0, '/opt/ahd/harness/skills/appworld-workflow')
import aw
aw.bind_apis()
print('AppWorld workflow helper ready')
PY
"""


class BootstrapInstructionMiddleware(PromptInstructionMiddleware):
    name = 'bootstrap-instruction'

    def __init__(self):
        super().__init__(instruction=BOOTSTRAP_INSTRUCTION)

    def _modify(self, request):
        result = super()._modify(request)
        task = next((m.content for m in reversed(request.messages)
                     if isinstance(m, HumanMessage) and isinstance(m.content, str)
                     and m.content.startswith(CURRENT_TASK_MARKER)), '')
        if FILE_TASK.search(task):
            return result.override(system_message=append_to_system_message(result.system_message, FILE_INSTRUCTION))
        return result

    async def abefore_agent(self, state, runtime):
        response = await current_backend.get().aexecute(INIT_COMMAND, timeout=120)
        if response.exit_code != 0:
            raise RuntimeError('AppWorld helper initialization failed: ' + response.output[:2000])


def make_middleware():
    return BootstrapInstructionMiddleware()
