"""展示值构造：把域层事件变成可挂载的卡片值（纯，无 Textual / 线程 / App）。

端口 Adapter（core.tui.render）只负责线程桥接与调用 App；「一条工具调用长什么样、结果是否
并回调用卡、键帽怎样降级」这些成像规则全部在本模块。测试不需要启动 UI。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from rich.text import Text

from core.template import TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX

DEFAULT_MAX_LINES = 10  # 可折叠正文默认行数上限（渲染宽度下换行的视觉行）
TOOL_ARGS_MAX_LINES = 4  # 工具调用卡参数段上限：结果到达后卡片仍保持紧凑

DIM = "dim #e2e8f0"  # 卡片正文（暗灰）
_DIFF_STYLES = {"+": "#b5bd68", "-": "#f87171"}  # diff 行：+ 绿 / - 淡红，其余 dim
_CONFIRM_STYLE = "bold #fef3c7"  # 权限确认文本（原因 / 冲突标题）

# 键帽 emoji（数字 / # / * + VS16 + U+20E3）：Windows Terminal 缺组合圈字形时把 U+20E3
# 画成独立一格，终端走 3 格、Textual 只记 2 格，该行剩余单元格整体右移 1 列
# （分隔线与聊天滚动条看起来「错位」）。入 UI 前降级为基字符，宽度口径与终端一致。
_KEYCAP_CHAR = r"[0-9#*]"  # 键帽基字符：数字 / # / *
_KEYCAP = re.compile(rf"({_KEYCAP_CHAR})\ufe0f?\u20e3")  # 完整序列：捕获组只含基字符
_KEYCAP_TAIL = re.compile(rf"{_KEYCAP_CHAR}\ufe0f?\Z")  # 可能跨 chunk 的尾部前缀


@dataclass(frozen=True)
class Block:
    """一张待挂载的卡片：正文 + 折叠 / 头行 / 尾段 / 并卡目标。

    Attributes:
        kind: 卡片 CSS 类（user / assistant / tool / result / error / perm / notice / sessions）。
        body: 正文；assistant 为 str（Markdown 部件），其余为 Text。
        max_lines: 折叠行数上限；None 表示不折叠。
        head_lines: 正文里的固定头行数，不参与折叠。
        tail: 固定尾段，不参与折叠，排在正文之后（带 diff 时的结果正文）。
        tool_call_id: 非空表示结果段应并回该 id 的调用卡；空串表示独立成卡。
    """

    kind: str
    body: Text | str
    max_lines: Optional[int] = None
    head_lines: int = 0
    tail: Optional[Text] = None
    tool_call_id: str = ""


def strip_keycaps(text: str) -> str:
    """把键帽 emoji 降级为基字符。

    Args:
        text: 原始文本。

    Returns:
        键帽 emoji（如 1️⃣）替换为基字符（1）后的文本；跨 chunk 的半截序列见
        split_trailing_keycap。
    """
    return _KEYCAP.sub(r"\1", text)


def split_trailing_keycap(text: str) -> tuple[str, str]:
    """切出尾部可能不完整的键帽前缀。

    Args:
        text: 本次拼接后的流式片段。

    Returns:
        (可立即渲染的部分, 留给下一片的前缀)。流式正文不能把半截键帽（如单独的 `1`）
        当正文发出去，否则下一片的 U+20E3 会落到下一个渲染行上。
    """
    tail = _KEYCAP_TAIL.search(text)
    if tail is None:
        return text, ""
    return text[: tail.start()], tail.group()


def dim_body(text: str) -> Text:
    """构造暗灰正文。

    Args:
        text: 原始正文。

    Returns:
        降级键帽后的暗灰 Text；用 Text 而非 Rich 标记，正文里的方括号不会被解析成样式标签。
    """
    return Text(strip_keycaps(text), style=DIM)


def _with_first_line(first_line: Text, body: Text) -> tuple[Text, int]:
    """把固定首行拼到正文前，并给出首行占用的固定行数。

    Args:
        first_line: 固定首行，通常以换行结尾；不参与折叠。
        body: 正文。

    Returns:
        (合并后的正文, 首行固定行数)。
    """
    return first_line + body, first_line.plain.count("\n")


def _format_arg_value(value: Any) -> str:
    """把单个工具参数值转成单行文本。

    Args:
        value: 参数值。

    Returns:
        单行文本；字符串换行转义为字面 \\n，bool / null 用小写字面量，
        嵌套 dict / list 用紧凑 JSON，序列化失败回退 str。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def format_args(tool_args: Any) -> str:
    """把工具参数转成展示文本。

    Args:
        tool_args: 工具参数。

    Returns:
        dict 每参一行 `key: value`；list 为缩进 JSON；其余为 str。
    """
    if isinstance(tool_args, dict):
        if not tool_args:
            return "{}"
        return "\n".join(f"{key}: {_format_arg_value(value)}" for key, value in tool_args.items())
    if not isinstance(tool_args, list):
        return str(tool_args)
    try:
        return json.dumps(tool_args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(tool_args)


def _diff_body(rows: list[tuple[str, int, str]]) -> Text:
    """构造 diff 正文。

    Args:
        rows: (标记, 行号, 内容) 列表。

    Returns:
        每行 "标记 行号 │ 内容" 的 Text（+ 绿 / - 淡红，其余 dim）。
    """
    width = max((len(str(n)) for _, n, _ in rows), default=1)
    return Text("\n").join(
        Text(f"{kind}{n:>{width}} │ {strip_keycaps(line)}", style=_DIFF_STYLES.get(kind, "dim"))
        for kind, n, line in rows
    )


def user_input(text: str) -> Block:
    """用户输入卡。

    Args:
        text: 用户消息正文（调用方已取出 content 数组的文本部分）。

    Returns:
        亮色加粗正文的 user 卡。
    """
    return Block("user", Text(strip_keycaps(text), style="bold #f9fafb"))


def assistant_markdown(content: str) -> Block:
    """Assistant Markdown 卡。

    Args:
        content: Markdown 正文。

    Returns:
        assistant 卡；正文为 str（Markdown 部件要求），已降级键帽。
    """
    return Block("assistant", strip_keycaps(content))


def tool_call(tool_call_id: str, name: str, tool_args: Any,
              max_lines: int = TOOL_ARGS_MAX_LINES) -> Block:
    """工具调用卡。

    Args:
        tool_call_id: 本次调用的 id，结果据此并回本卡；空串表示无法配对。
        name: 工具名。
        tool_args: 工具参数。
        max_lines: 参数段折叠行数上限。

    Returns:
        带 `Tool Call:` 固定头行的 tool 卡；参数段按 max_lines 折叠。
    """
    first_line = Text.assemble(("Tool Call: ", "bold #fde68a"), (name, "bold white"), "\n")
    body, head_lines = _with_first_line(first_line, dim_body(format_args(tool_args)))
    return Block("tool", body, max_lines=max_lines, head_lines=head_lines,
                 tool_call_id=tool_call_id)


def tool_result(
    tool_call_id: str,
    output: Any,
    *,
    is_error: Optional[bool] = None,
    diff: Optional[list[tuple[str, int, str]]] = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> Block:
    """工具结果卡（或并回调用卡的结果段）。

    Args:
        tool_call_id: 对应调用的 id；为空或无对应卡时独立成卡。
        output: 工具输出。
        is_error: 是否为错误；省略时按输出前缀识别。
        diff: write / edit 的 diff 行；提供时与结果同卡渲染。
        max_lines: 结果段（或 diff 段）折叠行数上限。

    Returns:
        result / error 卡；带 diff 时 diff 在前、超长可折叠，结果正文固定收尾（tail），
        结果段以固定首行 `Tool Result:` 开头且不参与折叠。
    """
    output_str = str(output)
    failed = is_error if is_error is not None else output_str.startswith(
        (TOOL_ERROR_PREFIX, UNKNOWN_TOOL_PREFIX))
    first_line = Text.assemble(("Tool Result: ", "bold #fde68a"), "\n")
    content = _diff_body(diff) if diff else dim_body(output_str)
    body, head_lines = _with_first_line(first_line, content)
    tail = dim_body(output_str) if diff else None
    return Block("error" if failed else "result", body, max_lines=max_lines,
                 head_lines=head_lines, tail=tail, tool_call_id=tool_call_id)


def background_notice(message: str) -> Block:
    """后台任务结果通知卡。

    Args:
        message: 通知正文。

    Returns:
        notice 卡：加粗固定标题行 + 暗灰正文，正文超出折叠。
    """
    first_line = Text.assemble(("🔔 Background Task", "bold #f8fafc"), "\n")
    body, head_lines = _with_first_line(first_line, dim_body(message))
    return Block("notice", body, max_lines=DEFAULT_MAX_LINES, head_lines=head_lines)


def sessions_empty() -> Block:
    """空会话列表提示卡。

    Returns:
        sessions 卡：正文 `No sessions yet`。
    """
    return Block("sessions", dim_body("No sessions yet"))


def permission(message: str, details: list[str]) -> Block:
    """权限确认卡。

    Args:
        message: 确认文本。
        details: 触发数据段落（`command: ...` / `path: ...` 等）。

    Returns:
        perm 卡：确认文本保持 _CONFIRM_STYLE，details 按工具卡正文（dim_body）逐段展示，
        段间空一行。
    """
    body = Text()
    body.append(message, style=_CONFIRM_STYLE)
    for detail in details:
        if not detail:
            continue
        body.append("\n\n")
        body.append_text(dim_body(detail))
    return Block("perm", body)
