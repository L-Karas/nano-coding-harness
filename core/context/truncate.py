"""工具输出截断：行数 / 字节双上限，任一先到即停，停处附截断提示。

read 工具在工具侧用它限制单次返回；上下文压缩用它保证所有工具消息不超限。
叶子模块（只依赖消息级截断前缀），避免 core.context.compact 反向依赖 core.tools 造成循环导入。
"""
from typing import Optional

from core.template.message_template import TRUNCATED_MESSAGE_PREFIX

MAX_OUTPUT_BYTES = 50 * 1024  # 单次返回上限，防止超长行绕过行数限制
MAX_OUTPUT_LINES = 2000  # 默认行数上限


def truncate_lines(
        lines: list[str],
        limit: Optional[int],
        offset: Optional[int],
        max_bytes: int = MAX_OUTPUT_BYTES
) -> tuple[str, bool]:
    """行数 / 字节上限，任一先到即停；停处附截断提示，offset 指向下一条未完整返回的行。

    返回 (文本, 是否截断)。offset 是行粒度：被切掉的行尾无法用它续读，需要时用 terminal 读。
    """
    offset = max(int(offset or 1) - 1, 0)  # 1 起始转 0 起始（None/旧 0 起始调用方 → 0）
    limit = int(limit) if limit is not None else None
    remaining_lines = lines[offset:]

    kept_lines: list[str] = []
    used_bytes, cut_info = 0, None
    for line_no, line in enumerate(remaining_lines, start=offset + 1):
        if limit is not None and len(kept_lines) >= limit:
            break
        encoded_line = line.encode("utf-8")
        if used_bytes + len(encoded_line) + 1 > max_bytes:  # +1 为换行
            if used_bytes < max_bytes:  # 本行放不下：按剩余额度 UTF-8 安全截断
                partial_line = encoded_line[:max_bytes - used_bytes].decode("utf-8", "ignore")
                kept_lines.append(partial_line)
                cut_info = (line_no, len(encoded_line) - len(partial_line.encode("utf-8")))
            break
        kept_lines.append(line)
        used_bytes += len(encoded_line) + 1

    remaining_count = len(remaining_lines) - len(kept_lines)
    next_offset = offset + len(kept_lines) + 1
    if cut_info:
        line_no, omitted_bytes = cut_info
        notice = f"{TRUNCATED_MESSAGE_PREFIX}line {line_no} cut at {max_bytes // 1024} KB, " \
                 f"{(omitted_bytes + 1023) // 1024} KB omitted"
        if remaining_count:
            notice += f"; {remaining_count} more lines, continue with 'offset={next_offset}'"
        kept_lines.append(notice + ".]")
    elif remaining_count:
        kept_lines.append(
            f"{TRUNCATED_MESSAGE_PREFIX}({remaining_count}) more lines. Use 'offset={next_offset}' to continue.]")
    return "\n".join(kept_lines), bool(cut_info) or remaining_count > 0


def truncate_tool_output(
        text: str,
        limit: Optional[int] = MAX_OUTPUT_LINES,
        offset: Optional[int] = 1,
        max_bytes: int = MAX_OUTPUT_BYTES
) -> tuple[str, bool]:
    """通用工具输出截断入口：按行数 / 字节上限截断，返回 (文本, 是否截断)。"""
    return truncate_lines(text.splitlines(), limit, offset, max_bytes)
