from langchain_core.messages import AIMessage

from harness_zero.tool_call_adapter import normalize_qwen_tool_call


def test_native_tool_call_is_preserved():
    message = AIMessage(
        content="",
        tool_calls=[{"name": "execute", "args": {"command": "pwd"}, "id": "x"}],
    )
    assert normalize_qwen_tool_call(message) is message


def test_qwen_xml_in_reasoning_becomes_execute_call():
    message = AIMessage(
        content="",
        additional_kwargs={
            "reasoning_content": (
                "Inspect the workbook.\n<tool_call>"
                "<function=execute><parameter=command>ls -l</parameter>"
                "</function></tool_call>"
            )
        },
    )
    normalized = normalize_qwen_tool_call(message)
    assert normalized.tool_calls[0]["args"] == {"command": "ls -l"}
    assert normalized.additional_kwargs["reasoning_content"] == "Inspect the workbook."


def test_qwen_xml_in_visible_content_is_cleaned():
    message = AIMessage(
        content="<tool_call><function=execute><parameter=command>echo hi</parameter>"
        "</function></tool_call>"
    )
    normalized = normalize_qwen_tool_call(message)
    assert normalized.content == ""
    assert normalized.tool_calls[0]["args"]["command"] == "echo hi"


def test_malformed_xml_is_not_guessed():
    message = AIMessage(
        content="<tool_call><function=execute><parameter=command>echo hi</function>"
    )
    assert normalize_qwen_tool_call(message) is message


def test_separate_reasoning_does_not_hide_visible_tool_call():
    message = AIMessage(
        content="<tool_call><function=execute><parameter=command>ls</parameter></function></tool_call>",
        additional_kwargs={"reasoning_content": "Inspect the files."},
        usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
    )
    result = normalize_qwen_tool_call(message)
    assert result.tool_calls[0]["args"]["command"] == "ls"
    assert result.additional_kwargs["reasoning_content"] == "Inspect the files."
    assert result.content == ""
    assert result.usage_metadata == message.usage_metadata


def test_multiple_xml_calls_are_not_silently_reduced_to_one():
    call = "<tool_call><function=execute><parameter=command>ls</parameter></function></tool_call>"
    message = AIMessage(content=call + call)
    assert normalize_qwen_tool_call(message) is message
