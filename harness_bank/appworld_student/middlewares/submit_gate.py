"""Nudge only when an observed supervisor status says the task is unfinished."""

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from ._observations import current_task_messages, observation


def completion_status(messages):
    for message in reversed(current_task_messages(messages)):
        result = observation(message)
        if result is not None and "completed" in result:
            return result["completed"]
    return None


class SubmitGateMiddleware(AgentMiddleware):
    name = "submit-gate"
    MAX_NUDGES = 2

    def __init__(self):
        super().__init__()
        self._nudges = 0

    def _check(self, state):
        messages = list(state.get("messages") or [])
        if not messages or self._nudges >= self.MAX_NUDGES:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if completion_status(messages) is not False:
            return None
        self._nudges += 1
        return {
            "messages": [HumanMessage(
                content=("The last observed AppWorld status says the task is unfinished. "
                         "Check your deliverables and the current status. When ready, call "
                         "aw.answer(value) through appworld exec if the instruction requests "
                         "an answer, or aw.finish_actions() for actions only. Do not clear or replace an "
                         "existing answer unless you have identified a concrete mistake."),
                name="submit-gate")],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        return self._check(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        return self._check(state)


def make_middleware():
    return SubmitGateMiddleware()
