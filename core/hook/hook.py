"""
Hooks + Permission Pipeline

# Hooks are intentionally outside tool handlers. The loop can add permission,
# logging, and stop behavior without changing each individual tool.
"""
import json
import os
import re
import shlex
from pathlib import Path

from openai.types.chat import ChatCompletionMessageToolCallUnion

from core.config import WORKDIR
from core.interaction import ask_permission
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

HOOKS = {"pre_llm_call": [], "pre_tool_call": [], "post_tool_call": []}

# 硬拒绝：不可逆的系统级操作，直接拒绝、不给确认机会。
DENY_LIST = [
    "rm -rf /", "rm -fr /", "rm -rf /*",  # 清根
    "sudo", "doas",  # 提权
    "shutdown", "reboot", "poweroff",  # 关机/重启
    "mkfs", "wipefs",  # 格式化/抹除签名
    "dd if=", "dd of=",  # dd 读写
    "> /dev/sd", "> /dev/hd", "> /dev/nvme", "> /dev/vd",  # 覆写裸设备
    ":(){",  # fork bomb
    "diskpart", "format c:", "clear-disk",  # Windows 磁盘操作
]

# 危险命令：弹出确认卡，用户确认后才放行。
DESTRUCTIVE = [
    "rm ", "rmdir ", "del ", "rd ", "remove-item", "shred ", "truncate -s", "mkswap",
    "> /etc/", "> /usr/", "> /var/", "> /bin/", "> /sbin/", "> /boot/", "> /lib/", "> /lib64/",
    "chmod 777", "chmod -R 777", "chown -R", "chgrp -R", "takeown", "reg delete",
    "fdisk", "parted",
    "kill -9", "kill -KILL", "killall", "pkill", "taskkill /f",
    "git reset --hard", "git clean -f", "git push --force", "git push -f",
    "git restore .", "git checkout .", "git checkout -- ", "-delete",
    "crontab -r", "iptables -F", "ufw disable", "nft flush",
    "| sh", "|sh", "| bash", "|bash", "| zsh", "|zsh", "| python", "|python", "| iex", "|iex",
    "/dev/tcp/",  # 反弹 shell
]


def register_hook(event: str, callback):
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result

    return None


def _confirm(question: str) -> bool:
    """通过 UI 面板询问用户，确认（y/yes）返回 True，其余一律拒绝"""
    return ask_permission(question).strip().lower() in ("y", "yes")


def _compile_pattern(pattern: str) -> re.Pattern:
    """大小写不敏感的子串匹配；词形态的首尾加单词边界，'sudo' 不命中 'sudoku'、'| sh' 不命中 '| shasum'"""
    left = r"(?<!\w)" if pattern[0].isalnum() else ""
    right = r"(?!\w)" if pattern[-1].isalnum() else ""
    return re.compile(left + re.escape(pattern) + right, re.IGNORECASE)


# 模式表静态不变，导入时编译一次；deny 项保留原文用于错误文案
DENY_PATTERNS = [(_compile_pattern(pattern), pattern) for pattern in DENY_LIST]
DESTRUCTIVE_PATTERNS = [_compile_pattern(pattern) for pattern in DESTRUCTIVE]

# 重定向前缀：>/tmp/f、2>>log、&>out、<in
_REDIRECT_PREFIX_RE = re.compile(r"^(?:&|[0-9])?(?:>>?|<)\s*")


def _clean_token(raw: str) -> str:
    """剥掉 token 外层的重定向前缀、引号与尾部命令分隔符，贴近真实路径。"""
    token = raw.strip().rstrip(";")
    token = _REDIRECT_PREFIX_RE.sub("", token).strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        token = token[1:-1]
    return token


def _command_path_candidates(command: str):
    """从 shell 命令提取可能是路径的候选：独立 token + `=` 后的值（`--file=/etc/x`）。

    尽力而为的文本解析：变量拼接、脚本内部路径、粘连 flag（`-o/tmp/x`）不覆盖。
    """
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:  # 引号不闭合等，退化为空白切分
        tokens = command.split()

    for index, raw in enumerate(tokens):
        token = _clean_token(raw)
        if not token:
            continue

        # 裸 cd / cd -：后续相对路径在家目录或上次目录解析，token 级判定会漏，按越界处理
        if token.lower() == "cd":
            following = _clean_token(tokens[index + 1]) if index + 1 < len(tokens) else ""
            if not following or following == "-" or following.startswith(("&&", "||", "|", "&", ";")):
                yield "~"
                continue

        yield token
        if "=" in token:
            value = _clean_token(token.split("=", 1)[1])
            if value:
                yield value


def _is_outside_workdir(path: str) -> bool:
    return not (WORKDIR / path).resolve().is_relative_to(WORKDIR)


def _outside_paths(command: str) -> list[str]:
    """命令中显式引用、且解析到工作目录之外的路径（去重、保持出现顺序）。"""
    outside = []
    for candidate in _command_path_candidates(command):
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        if expanded and _is_outside_workdir(expanded) and candidate not in outside:
            outside.append(candidate)
    return outside


def permission_hook(tool_call: ChatCompletionMessageToolCallUnion):
    # The permission layer sees the raw tool_use before dispatch. It can deny,
    # ask the user, or allow execution to continue.
    tool_args = json.loads(tool_call.function.arguments)

    if tool_call.function.name == "terminal":
        command = tool_args.get("command", "")
        for regex, pattern in DENY_PATTERNS:
            if regex.search(command):
                return f"Permission denied: '{pattern}' is on the deny list."

        destructive = any(regex.search(command) for regex in DESTRUCTIVE_PATTERNS)
        outside = _outside_paths(command)
        if destructive or outside:
            reasons = []
            if destructive:
                reasons.append("Destructive command detected")
            if outside:
                shown = outside[:5]
                listing = "\n".join(f"- {path}" for path in shown)
                if len(outside) > len(shown):
                    listing += f"\n- ... and {len(outside) - len(shown)} more"
                reasons.append("Access outside working directory:\n" + listing)
            if not _confirm("\n\n".join(reasons + [command])):
                return "Permission denied by user"

    if tool_call.function.name in ("read_file", "write_file", "edit_file"):
        path = tool_args.get("path", "")
        if _is_outside_workdir(path):
            if not _confirm(f"Access outside working directory:\n\n{tool_call.function.name}: {path}"):
                return "Permission denied by user"

    # todo: mcp tool 权限策略（mcp_* 工具当前直接放行）
    return None


def tool_call_log_hook(tool_call: ChatCompletionMessageToolCallUnion):
    _LOGGER.info(f"[HOOK] call tool {tool_call.function.name} with arguments: {tool_call.function.arguments}")
    return None


# todo: 处理工具结果过多的情况
def large_tool_output_hook(tool_call: ChatCompletionMessageToolCallUnion, tool_output: str = ""):
    size = len(str(tool_output))
    if size > 100_000:
        _LOGGER.warning(f"[HOOK] large output from {tool_call.function.name}: {size} chars")

    return None


DEFAULT_HOOKS = (
    ("pre_tool_call", permission_hook),
    ("pre_tool_call", tool_call_log_hook),
    ("post_tool_call", large_tool_output_hook),
)


def install_default_hooks() -> None:
    """注册出厂 hook（幂等）；由 core.bootstrap 在启动时调用。"""
    for event, callback in DEFAULT_HOOKS:
        if callback not in HOOKS[event]:
            HOOKS[event].append(callback)
