"""Read execution metadata without treating app data as error messages."""

import json
import re

from langchain_core.messages import HumanMessage, ToolMessage

CURRENT_TASK_MARKER = '[APPWORLD_CURRENT_TASK]'


def current_task_messages(messages):
    """Teaching examples remain model-visible but are not live task evidence."""
    for index in range(len(messages)-1, -1, -1):
        message = messages[index]
        if (isinstance(message, HumanMessage) and isinstance(message.content, str)
                and message.content.startswith(CURRENT_TASK_MARKER)):
            return messages[index:]
    return messages

_EXIT = re.compile(r"\n?\[exit_code=(-?\d+)\]\s*$")
_EXCEPTION = re.compile(r"^[\w.]+(?:Error|Exception):.*$", re.MULTILINE)


def observation(message):
    if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
        return None
    content = message.content.strip()
    exit_match = _EXIT.search(content)
    exit_code = int(exit_match[1]) if exit_match else None
    if exit_match:
        content = content[:exit_match.start()].rstrip()
    for line in reversed(content.splitlines()):
        if line.startswith("[AppWorld result] "):
            payload = line.removeprefix("[AppWorld result] ")
            break
    else:
        payload = content
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        result = None
    if isinstance(result, dict) and "task_completed" in result:
        completed = result["task_completed"]
        if completed is None or isinstance(completed, bool):
            failed = result.get("error") is True or exit_code not in (None, 0) or message.status == "error"
            return {"completed": completed, "error": failed,
                    "signature": (result.get("error_type"), _exception(result.get("output", content)))
                    if failed else None}
    if message.status == "error" or exit_code not in (None, 0):
        return {"error": True, "signature": (exit_code, _exception(content))}
    return {"error": False, "signature": None}


def _exception(text):
    if not isinstance(text, str):
        text = json.dumps(text, sort_keys=True)
    matches = list(_EXCEPTION.finditer(text))
    detail = text[matches[-1].start():] if matches else text
    # HTTP failures put the useful validation/auth detail on the following line.
    return detail.split("\n[AppWorld result] ", 1)[0].strip()[:500]
