"""消息级模板与跨层消息约定（叶子模块：不 import 任何 core 模块）。

- 工具失败统一前缀（`TOOL_ERROR_PREFIX`）：工具执行失败经 `ToolResult.error()` 生成，解析失败 / 未执行等
  占位错误消息也用它；handler 返回的字符串一律是内容。TUI 回放历史时按前缀回退识别 error 卡；
  拦截类占位（`is_error=True`，文案由权限门给定）不带前缀，回放时即显示为普通结果卡；
- 消息正文的模板常量用 `{占位符}` 定义，调用方经 `.format(...)` 渲染；动态片段（如异常详情）
  可作为参数直接传入，不必是常量；
- 注入消息包装 / 续跑提示 / 用户中断占位 / 后台任务通知、派发与取消占位：主循环、TUI 渲染、后台任务共用。

与 prompt_template.py 的分工：这里放消息级模板（单条消息的包装与占位），
prompt_template.py 放 agent 级提示词模板（系统提示词 / 子代理 / 摘要）。
"""

TOOL_ERROR_PREFIX = "[Tool Error]:"
UNKNOWN_TOOL_PREFIX = "[Unknown Tool]:"
TOOL_ERROR_MESSAGE_TEMPLATE = TOOL_ERROR_PREFIX + " {detail}"
UNKNOWN_TOOL_MESSAGE_TEMPLATE = UNKNOWN_TOOL_PREFIX + " {name}"

INJECTION_MESSAGES_PREFIX = "<injection_messages>\n"
INJECTION_MESSAGES_SUFFIX = "\n</injection_messages>"
INJECTION_MESSAGES_TEMPLATE = INJECTION_MESSAGES_PREFIX + "{content}" + INJECTION_MESSAGES_SUFFIX

PERSIST_TOOL_MESSAGE_PREFIX = "<persisted-output>\n"
PERSIST_TOOL_MESSAGE_SUFFIX = "\n</persisted-output>"
PERSIST_TOOL_MESSAGE_TEMPLATE = (
    PERSIST_TOOL_MESSAGE_PREFIX
    + "  <saved-path>{path}</saved-path>\n"
    + "  <content-preview>{preview}</content-preview>"
    + PERSIST_TOOL_MESSAGE_SUFFIX
)

BACKGROUND_MESSAGE_PREFIX = "<background-task-notification>\n"
BACKGROUND_MESSAGE_SUFFIX = "\n</background-task-notification>"
BACKGROUND_MESSAGE_TEMPLATE = (
    BACKGROUND_MESSAGE_PREFIX
    + "  <task-id>{task_id}</task-id>\n"
    + "  <status>{status}</status>\n"
    + "  <tool>{tool}</tool>\n"
    + "  <result>{result}</result>"
    + BACKGROUND_MESSAGE_SUFFIX
)

TODO_ITEM_MESSAGE_TEMPLATE = (
    "  <todo>\n"
    "    <content>{content}</content>\n"
    "    <status>{status}</status>\n"
    "  </todo>"
)
TODOS_MESSAGE_TEMPLATE = (
    "<todos>\n{todos}\n</todos>"
)

SKILL_MESSAGE_TEMPLATE = (
    "<skill>\n"
    + "  <location>{location}</location>\n"
    + "  <content>{content}</content>\n"
    + "</skill>"
)

TRUNCATED_MESSAGE_PREFIX = "[Truncated: "

CONTINUATION_PROMPT = INJECTION_MESSAGES_TEMPLATE.format(
    content="Continue from the previous response. Do not repeat completed work."
)

USER_INTERRUPT_PROMPT = "[User interrupted]"
BACKGROUND_CANCELLED_PROMPT = "[Background task cancelled]"
BACKGROUND_STARTED_PROMPT = (
    "[Background task `{task_id}` started] "
    "Result will arrive with a `<background-task-notification>` tag."
)
TOOL_ABORTED_PROMPT = TOOL_ERROR_MESSAGE_TEMPLATE.format(detail="tool call aborted before execution")
