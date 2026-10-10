"""消息级模板与跨层消息约定（叶子模块：不 import 任何 core 模块）。

- 工具失败统一前缀：由 ToolResult.error() 生成（handler 返回的字符串一律是内容），
  TUI 回放历史时按它回退识别 error 卡；
- 注入消息包装 / 续跑提示 / 用户中断占位：主循环、TUI 渲染、后台任务共用。

与 prompt_template.py 的分工：这里放消息级模板（单条消息的包装与占位），
prompt_template.py 放 agent 级提示词模板（系统提示词 / 子代理 / 摘要）。
"""

TOOL_ERROR_PREFIX = "[Tool Error]:"
UNKNOWN_TOOL_PREFIX = "[Unknown Tool]:"

INJECTION_MESSAGES_PREFIX = "<injection_messages>\n"
INJECTION_MESSAGES_SUFFIX = "\n</injection_messages>"
INJECTION_MESSAGES_TEMPLATE = INJECTION_MESSAGES_PREFIX + "{content}" + INJECTION_MESSAGES_SUFFIX

PERSIST_TOOL_MESSAGE_PREFIX = "<persisted-output>\n"
PERSIST_TOOL_MESSAGE_SUFFIX = "\n</persisted-output>"
PERSIST_TOOL_MESSAGE_TEMPLATE = (
    PERSIST_TOOL_MESSAGE_PREFIX
    + "<saved-path>{path}</saved-path>\n"
    + "<content-preview>{preview}</content-preview>"
    + PERSIST_TOOL_MESSAGE_SUFFIX
)

TRUNCATED_MESSAGE_PREFIX = "[Truncated: "

CONTINUATION_PROMPT = INJECTION_MESSAGES_TEMPLATE.format(
    content="Continue from the previous response. Do not repeat completed work."
)

USER_INTERRUPT_PROMPT = "[User interrupted]"
