"""内置权限扩展：.permissions.json 的解析、校验与规则匹配，以及三层权限门裁决。

规则只覆盖 terminal 与三个文件工具（read_file / write_file / edit_file）；
文件整体非法时不生效并记 ERROR，单条规则非法只丢弃自身，未知键忽略并记 WARN。
权限门按「地板 → 规则/扩展表态 → 默认层」裁决；地板与默认层迁移自旧 hook 权限门，
行为与文案不变。
"""
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.config import WORKDIR
from core.extension.api import ExtensionAPI
from core.extension.context import BeforeToolContext, Decision
from core.interaction import ask_permission
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

ACTIONS = ("allow", "ask", "deny")
_FILE_TOOLS = ("read_file", "write_file", "edit_file")


@dataclass
class Rules:
    """规则文件解析结果：terminal 按文件顺序保存，命中的后者覆盖前者。"""

    terminal: list[tuple[re.Pattern, str]] = field(default_factory=list)
    external_directory: dict[str, str] = field(default_factory=dict)


def load_rules(path: Path) -> Rules:
    """读取并校验规则文件；文件缺失或整体非法时返回空规则（不抛异常）。"""
    if not path.exists():
        return Rules()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _LOGGER.error(f"[Permission] 规则文件解析失败，忽略全部规则: {path}")
        return Rules()
    if not isinstance(data, dict):
        _LOGGER.error(f"[Permission] 规则文件顶层必须是对象，忽略全部规则: {path}")
        return Rules()
    return _parse_sections(data)


def match_terminal(rules: Rules, command: str) -> tuple[str, str] | None:
    """按文件顺序返回最后一个命中命令的 (action, 正则原文)；无命中返回 None。"""
    matched = None
    for pattern, action in rules.terminal:
        if pattern.search(command):
            matched = (action, pattern.pattern)
    return matched


def external_directory_action(rules: Rules, tool_name: str) -> str | None:
    """返回该文件工具的越界目录策略；未配置或工具未覆盖时返回 None。"""
    return rules.external_directory.get(tool_name)


def _parse_sections(data: dict) -> Rules:
    rules = Rules()
    for tool_name, section in data.items():
        if tool_name == "terminal":
            _parse_terminal_section(section, rules)
        elif tool_name in _FILE_TOOLS:
            _parse_external_directory_section(tool_name, section, rules)
        else:
            _LOGGER.warning(f"[Permission] 未知工具键，忽略: {tool_name}")
    return rules


def _parse_terminal_section(section: object, rules: Rules) -> None:
    if not isinstance(section, dict):
        _LOGGER.error("[Permission] terminal 段必须是对象，忽略该段")
        return
    for key in section:
        if key != "command":
            _LOGGER.warning(f"[Permission] terminal 段未知字段，忽略: {key}")
    commands = section.get("command")
    if commands is None:
        return
    if not isinstance(commands, dict):
        _LOGGER.error("[Permission] terminal.command 必须是对象，忽略该段")
        return
    for pattern_text, action in commands.items():
        if action not in ACTIONS:
            _LOGGER.error(f"[Permission] 非法 action，忽略规则 {pattern_text!r}: {action!r}")
            continue
        try:
            compiled = re.compile(pattern_text, re.IGNORECASE)
        except re.error:
            _LOGGER.error(f"[Permission] 非法正则，忽略规则: {pattern_text!r}")
            continue
        rules.terminal.append((compiled, action))


def _parse_external_directory_section(tool_name: str, section: object, rules: Rules) -> None:
    if not isinstance(section, dict):
        _LOGGER.error(f"[Permission] {tool_name} 段必须是对象，忽略该段")
        return
    for key, action in section.items():
        if key != "external_directory":
            _LOGGER.warning(f"[Permission] {tool_name} 段未知字段，忽略: {key}")
            continue
        if action not in ACTIONS:
            _LOGGER.error(
                f"[Permission] 非法 action，忽略 {tool_name}.external_directory: {action!r}")
            continue
        rules.external_directory[tool_name] = action


# ---------- 地板与默认层（迁移自旧 hook 权限门，行为与文案不变） ----------

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


def _compile_pattern(pattern: str) -> re.Pattern:
    """大小写不敏感的子串匹配；词形态的首尾加单词边界，'sudo' 不命中 'sudoku'、'| sh' 不命中 '| shasum'"""
    left = r"(?<!\w)" if pattern[0].isalnum() else ""
    right = r"(?!\w)" if pattern[-1].isalnum() else ""
    return re.compile(left + re.escape(pattern) + right, re.IGNORECASE)


# 模式表静态不变，导入时编译一次；地板项保留原文用于错误文案
FLOOR_PATTERNS = [(_compile_pattern(pattern), pattern) for pattern in DENY_LIST]
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


# Git Bash 盘符写法：/e/foo 指 E:\foo（terminal 在 Windows 上优先走 Git Bash，见 core.tools.shell）。
# pathlib 会把它误解析为 E:\e\foo，需先还原盘符；POSIX 下 /e 是真实绝对路径，仅 Windows 翻译。
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(?:/|$)")


def _msys_drive_to_windows(path: str) -> str:
    """Windows 上将 /e/foo 还原为 e:/foo；其余路径原样返回。"""
    if os.name != "nt":
        return path
    return _MSYS_DRIVE_RE.sub(lambda match: f"{match.group(1)}:/", path, count=1)


def _is_outside_workdir(path: str) -> bool:
    return not (WORKDIR / path).resolve().is_relative_to(WORKDIR)


def _outside_paths(command: str) -> list[str]:
    """命令中显式引用、且解析到工作目录之外的路径（去重、保持出现顺序）。"""
    outside = []
    for candidate in _command_path_candidates(command):
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        # 只有 terminal 候选按 Git Bash 语义还原盘符：Python 文件工具不认 /e/...，
        # 在这里翻译可避免「误报越界」，同时不放宽文件工具的路径检查。
        resolved = _msys_drive_to_windows(expanded)
        if expanded and _is_outside_workdir(resolved) and candidate not in outside:
            outside.append(candidate)
    return outside


def _confirm(question: str) -> bool:
    """通过 UI 面板询问用户，确认（y/yes）返回 True，其余一律拒绝"""
    return ask_permission(question).strip().lower() in ("y", "yes")


# ---------- 权限门 ----------

def _call_desc(tool_name: str, args: dict) -> str:
    """裁决卡中的调用摘要：terminal 用命令原文，文件工具用「工具: 路径」，其余回显参数。"""
    if tool_name == "terminal":
        return str(args.get("command", ""))
    if tool_name in _FILE_TOOLS:
        return f"{tool_name}: {args.get('path', '')}"
    return f"{tool_name}: {json.dumps(args, ensure_ascii=False)}"


def _rule_vote(rules: Rules, tool_name: str, args: dict) -> Decision | None:
    """规则层每层最多一票；文件工具仅在路径越界且配置了 external_directory 时投票。"""
    if tool_name == "terminal":
        matched = match_terminal(rules, args.get("command", ""))
        if matched is None:
            return None
        action, pattern_text = matched
        return Decision(action=action, reason=pattern_text, source="rule")
    if tool_name in _FILE_TOOLS:
        if not _is_outside_workdir(args.get("path", "")):
            return None
        action = external_directory_action(rules, tool_name)
        if action is None:
            return None
        return Decision(action=action, reason=f"{tool_name}.external_directory", source="rule")
    return None


def _apply_votes(ctx: BeforeToolContext, votes: list[Decision], desc: str) -> None:
    """用户表态：动作唯一时采用；不唯一时弹冲突卡，拒绝统一收口为 Permission denied by user。"""
    if len({vote.action for vote in votes}) > 1:
        listing = "\n".join(
            f"- {vote.source}: {vote.action} ({vote.reason})" for vote in votes)
        if not _confirm(f"Decision conflict:\n{listing}\n\n{desc}"):
            _block_by_user(ctx)
        return

    action = votes[0].action
    if action == "allow":
        return
    if action == "deny":
        ctx.blocked = True
        ctx.block_reason = (f"Permission denied by rule: {votes[0].reason}"
                            if votes[0].source == "rule"
                            else f"[Extension blocked] {votes[0].reason}")
        return
    if not _confirm(f"{votes[0].reason}\n\n{desc}"):  # ask
        _block_by_user(ctx)


def _block_by_user(ctx: BeforeToolContext) -> None:
    ctx.blocked = True
    ctx.block_reason = "Permission denied by user"


def _apply_defaults(ctx: BeforeToolContext, tool_name: str, args: dict) -> None:
    """默认层：无任何用户表态时生效，沿用旧 hook 的确认卡文案。"""
    if tool_name == "terminal":
        command = args.get("command", "")
        destructive = any(regex.search(command) for regex in DESTRUCTIVE_PATTERNS)
        outside = _outside_paths(command)
        if not destructive and not outside:
            return
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
            _block_by_user(ctx)
        return

    if tool_name in _FILE_TOOLS:
        path = args.get("path", "")
        if _is_outside_workdir(path):
            if not _confirm(f"Access outside working directory:\n\n{tool_name}: {path}"):
                _block_by_user(ctx)


def build_gate(rules: Rules) -> Callable[[BeforeToolContext], None]:
    """构造权限门回调：地板 → 规则/扩展表态 → 默认层。"""

    def gate(ctx: BeforeToolContext) -> None:
        tool_name = ctx.tool_name
        args = ctx.args

        if tool_name == "terminal":
            command = args.get("command", "")
            for regex, pattern in FLOOR_PATTERNS:
                if regex.search(command):
                    ctx.blocked = True
                    ctx.block_reason = f"Permission denied: '{pattern}' is on the deny list."
                    return

        rule_vote = _rule_vote(rules, tool_name, args)
        votes = ([rule_vote] if rule_vote else []) + list(ctx.decisions)
        if votes:
            _apply_votes(ctx, votes, _call_desc(tool_name, args))
            return

        _apply_defaults(ctx, tool_name, args)

    return gate


def register(api: ExtensionAPI, rules: Rules) -> None:
    """注册内置权限门：固定 gate 阶段并声明 fail-closed。"""
    api.on("before_tool", build_gate(rules), stage="gate", fail_closed=True)
