"""estimate_size 必须把 assistant 消息的 tool_calls（函数名 + arguments）计入 token。"""
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from core.context.token import estimate_token, estimate_size

ARGS = '{"path": "a.py", "content": "print(1)"}'
TOOL_CALL_TOKENS = estimate_token("write_file") + estimate_token(ARGS)


def test_dict_message_with_tool_calls():
    messages = [
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "write_file", "arguments": ARGS}},
        ]},
    ]
    assert estimate_size(messages) == estimate_size(messages[:1]) + TOOL_CALL_TOKENS


def test_chat_completion_message_with_tool_calls():
    message = ChatCompletionMessage(role="assistant", tool_calls=[
        ChatCompletionMessageToolCall(id="call_1", type="function",
                                      function=Function(name="write_file", arguments=ARGS)),
    ])
    assert estimate_size([message]) == TOOL_CALL_TOKENS
