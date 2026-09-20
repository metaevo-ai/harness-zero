"""Student tool definitions shared by rollout and training, without a runtime."""

from pydantic import BaseModel, ConfigDict, Field


EXECUTE_DESCRIPTION = (
    "Execute one bash command in the task sandbox. Each call uses a fresh "
    "shell. Use the `agent \"<task>\"` command for optional subagent "
    "delegation."
)


class ExecuteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1)


def execute_tool_schema() -> dict:
    """Match LangChain's function schema without constructing a runnable tool."""
    schema = ExecuteArgs.model_json_schema()
    properties = {
        name: {key: value for key, value in prop.items() if key != "title"}
        for name, prop in schema["properties"].items()
    }
    return {
        "name": "execute",
        "description": EXECUTE_DESCRIPTION,
        "parameters": {
            "properties": properties,
            "required": schema["required"],
            "type": "object",
        },
    }
