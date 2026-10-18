"""内置权限扩展：规则文件解析与三层权限门（地板 → 规则/扩展表态 → 默认层）。

规则只覆盖 terminal 与三个文件工具（read_file / write_file / edit_file）；
文件整体非法时忽略全部规则并记 ERROR，单条规则非法只丢弃自身，未知键记 WARN。
地板（硬拒绝）与危险命令模式表在 core.extension.builtin.permission_patterns；
默认层受 `approval_mode` 控制：Manual 询问危险命令与越界访问，Yolo 关闭默认层
（地板与用户表态不受影响）。越界命中统一用「标题 + 触发数据」文案。
完整语义见 docs/permissions.md。
"""
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from core.config import CONFIGMANAGER, WORKDIR
from core.extension.api import ExtensionAPI
from core.extension.builtin.permission_patterns import floor_denial, is_destructive
from core.extension.context import BeforeToolContext, Decision
from core.extension.protocol import ACTIONS
from core.interaction import ask_permission
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

_FILE_TOOLS = ("read_file", "write_file", "edit_file")
_PATTERN_KEYS = {"terminal": "command", **{tool: "path" for tool in _FILE_TOOLS}}

_OUTSIDE_TITLE = "Access outside working directory:"
_MAX_SHOWN_OUTSIDE_PATHS = 3  # 确认卡与规则拒绝文案统一的最多展示路径数


@dataclass
class Rules:
    """规则文件解析结果。

    - `terminal`：`terminal.command` 的 (正则, action)，按文件顺序。
    - `paths`：文件工具名 → `path` 的 (正则, action)，按文件顺序。
    - `external_directory`：工具名 → 越界访问策略 action。
    """

    terminal: list[tuple[re.Pattern, str]] = field(default_factory=list)
    paths: dict[str, list[tuple[re.Pattern, str]]] = field(default_factory=dict)
    external_directory: dict[str, str] = field(default_factory=dict)


def load_rules(path: Path) -> Rules:
    """读取并校验规则文件；文件缺失或整体非法时返回空规则（不抛异常）。"""
    if not path.exists():
        return Rules()
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        _LOGGER.error(
            f"[Permission] 规则文件读取失败（{type(exc).__name__}: {exc}），忽略全部规则: {path}")
        return Rules()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _LOGGER.error(
            f"[Permission] 规则文件 JSON 解析失败（{type(exc).__name__}: {exc}），忽略全部规则: {path}")
        return Rules()
    if not isinstance(data, dict):
        _LOGGER.error(f"[Permission] 规则文件顶层必须是对象，忽略全部规则: {path}")
        return Rules()
    return _parse_sections(data)


def match_command(rules: Rules, command: str) -> list[tuple[str, str]]:
    """按文件顺序返回命令规则的全部命中 (action, 正则原文)。"""
    return [(action, pattern.pattern) for pattern, action in rules.terminal
            if pattern.search(command)]


def match_path(rules: Rules, tool_name: str, relative_path: str) -> list[tuple[str, str]]:
    """按文件顺序返回该工具 path 规则的全部命中 (action, 正则原文)。"""
    return [(action, pattern.pattern) for pattern, action in rules.paths.get(tool_name, [])
            if pattern.search(relative_path)]


_SEVERITY = {"deny": 0, "ask": 1, "allow": 2}


def resolve_matches(matches: list[tuple[str, str]]) -> tuple[str, str] | None:
    """按 deny > ask > allow 取最严；同 severity 取列表中最后一条；空列表返回 None。"""
    winner = None
    for match in matches:
        if winner is None or _SEVERITY[match[0]] <= _SEVERITY[winner[0]]:
            winner = match
    return winner


def external_directory_action(rules: Rules, tool_name: str) -> str | None:
    """返回该工具的越界目录策略；未配置或工具未覆盖时返回 None。"""
    return rules.external_directory.get(tool_name)


def _parse_sections(data: dict) -> Rules:
    rules = Rules()
    for tool_name, section in data.items():
        if tool_name in _PATTERN_KEYS:
            _parse_tool_section(tool_name, section, rules)
        else:
            _LOGGER.warning(f"[Permission] 未知工具键，忽略: {tool_name}")
    return rules


def _parse_tool_section(tool_name: str, section: object, rules: Rules) -> None:
    """解析一个工具段：external_directory + command/path 规则表；非法条目只丢自身。"""
    pattern_key = _PATTERN_KEYS[tool_name]
    if not isinstance(section, dict):
        _LOGGER.error(f"[Permission] {tool_name} 段必须是对象，忽略该段")
        return
    for key in section:
        if key not in ("external_directory", pattern_key):
            _LOGGER.warning(f"[Permission] {tool_name} 段未知字段，忽略: {key}")

    produced = 0
    if "external_directory" in section:
        action = section["external_directory"]
        if action in ACTIONS:
            rules.external_directory[tool_name] = action
            produced += 1
        else:
            _LOGGER.error(
                f"[Permission] 非法 action，忽略 {tool_name}.external_directory: {action!r}")
    if pattern_key in section:
        parsed = _parse_pattern_table(f"{tool_name}.{pattern_key}", section[pattern_key])
        if tool_name == "terminal":
            target = rules.terminal
        else:
            target = rules.paths.setdefault(tool_name, [])
        target.extend(parsed)
        produced += len(parsed)
    if produced == 0:
        _LOGGER.warning(f"[Permission] {tool_name} 段未产出任何有效规则")


def _parse_pattern_table(label: str, table: object) -> list[tuple[re.Pattern, str]]:
    """解析 `{正则: action}` 表并逐条校验；非法条目只丢自身，其余照常返回。"""
    if not isinstance(table, dict):
        _LOGGER.error(f"[Permission] {label} 必须是对象，忽略该表")
        return []
    parsed = []
    for pattern_text, action in table.items():
        if action not in ACTIONS:
            _LOGGER.error(f"[Permission] 非法 action，忽略规则 {pattern_text!r}: {action!r}")
            continue
        try:
            compiled = re.compile(pattern_text, re.IGNORECASE)
        except re.error:
            _LOGGER.error(f"[Permission] 非法正则，忽略规则: {pattern_text!r}")
            continue
        parsed.append((compiled, action))
    return parsed


# 重定向前缀：>/tmp/f、2>>log、&>out、<in
_REDIRECT_PREFIX_RE = re.compile(r"^(?:&|[0-9])?(?:>>?|<)\s*")


def _clean_token(raw: str) -> str:
    """剥掉 token 外层的重定向前缀、引号与尾部命令分隔符，贴近真实路径。"""
    token = raw.strip().rstrip(";")
    token = _REDIRECT_PREFIX_RE.sub("", token).strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        token = token[1:-1]
    return token


def _split_tokens(command: str) -> list[str]:
    """按 shell 词法切分；引号不闭合等非法输入退化为空白切分。"""
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


def _is_bare_cd(tokens: list[str], index: int) -> bool:
    """裸 `cd` / `cd -`：目标由 shell 解析（家目录、上次目录），token 级看不到，按越界处理。"""
    following = _clean_token(tokens[index + 1]) if index + 1 < len(tokens) else ""
    return (not following or following == "-"
            or following.startswith(("&&", "||", "|", "&", ";")))


def _command_path_candidates(command: str) -> Iterator[str]:
    """从 shell 命令提取可能是路径的候选：独立 token + `=` 后的值（`--file=/etc/x`）。

    尽力而为的文本解析：变量拼接、脚本内部路径、粘连 flag（`-o/tmp/x`）不覆盖。
    """
    tokens = _split_tokens(command)
    for index, raw in enumerate(tokens):
        token = _clean_token(raw)
        if not token:
            continue
        if token.lower() == "cd" and _is_bare_cd(tokens, index):
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


def _workdir(ctx: BeforeToolContext) -> Path:
    """调用方工作目录（`ToolContext.cwd`，缺省 `WORKDIR`）；resolve 与文件工具的路径解析口径一致。"""
    return (ctx.cwd or WORKDIR).resolve()


def _resolve_path(raw: str, workdir: Path) -> Path:
    """按工具口径解析路径：相对路径基于工作目录，符号链接解析到真实位置。"""
    return (workdir / raw).resolve()


def _is_outside_workdir(path: str, workdir: Path) -> bool:
    """路径是否落在调用方工作目录之外。"""
    return not _resolve_path(path, workdir).is_relative_to(workdir)


def _relative_path(resolved: Path, workdir: Path) -> str:
    """path 规则的匹配串：工作区相对 POSIX 路径；跨盘符没有相对形式，退化为绝对路径。"""
    try:
        return Path(os.path.relpath(resolved, workdir)).as_posix()
    except ValueError:
        return resolved.as_posix()


def _outside_paths(command: str, workdir: Path) -> list[str]:
    """命令中显式引用、且解析到调用方工作目录之外的路径（去重、保持出现顺序）。"""
    outside = []
    for candidate in _command_path_candidates(command):
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        if not expanded or candidate in outside:
            continue
        # 只有 terminal 候选按 Git Bash 语义还原盘符：Python 文件工具不认 /e/...，
        # 在这里翻译可避免「误报越界」，同时不放宽文件工具的路径检查。
        if _is_outside_workdir(_msys_drive_to_windows(expanded), workdir):
            outside.append(candidate)
    return outside


def _outside_listing(paths: list[str]) -> str:
    """越界路径清单（确认卡 details 与规则 deny 文案共用）：最多三条，其余只报数量。"""
    shown = paths[:_MAX_SHOWN_OUTSIDE_PATHS]
    listing = "\n".join(f"- {path}" for path in shown)
    if len(paths) > len(shown):
        listing += f"\n- ... and {len(paths) - len(shown)} more"
    return listing


def _confirm(question: str, details: list[str]) -> bool:
    """通过 UI 面板询问用户（details 为触发数据段落，单独样式展示），
    确认（y/yes）返回 True，其余一律拒绝"""
    return ask_permission(question, details).strip().lower() in ("y", "yes")


# ---------- 权限门 ----------

@dataclass
class RuleVote:
    """规则层的一票（每次调用至多一票）；details 为越界命中胜出时的触发数据。"""

    decision: Decision | None = None
    details: list[str] = field(default_factory=list)


def _call_summary(tool_name: str, args: dict) -> str:
    """裁决卡中的调用摘要：terminal / 文件工具与工具卡参数行同构
    （`command: <命令原文>` / `path: <路径>`），其余回显参数。"""
    if tool_name == "terminal":
        return f"command: {args.get('command', '')}"
    if tool_name in _FILE_TOOLS:
        return f"path: {args.get('path', '')}"
    return f"{tool_name}: {str(args)}"


def _rule_vote(rules: Rules, ctx: BeforeToolContext) -> RuleVote:
    """规则层每次调用最多一票：全部命中按 deny > ask > allow 收敛。

    文件工具 path 规则按工作区相对路径匹配；external_directory 仅在路径越界时参与，
    其命中追加在直接规则之后（同 severity 取最后一条），胜出时 details 带上触发数据。
    """
    tool_name, args = ctx.tool_name, ctx.args
    outside_match: tuple[str, str] | None = None
    outside_details: list[str] = []
    if tool_name == "terminal":
        command = args.get("command", "")
        matches = match_command(rules, command)
        action = external_directory_action(rules, tool_name)
        if action is not None:
            paths = _outside_paths(command, _workdir(ctx))
            if paths:
                outside_match = (action, _OUTSIDE_TITLE)
                outside_details = [_outside_listing(paths)]
    elif tool_name in _FILE_TOOLS:
        workdir = _workdir(ctx)
        resolved = _resolve_path(args.get("path", ""), workdir)
        matches = match_path(rules, tool_name, _relative_path(resolved, workdir))
        action = external_directory_action(rules, tool_name)
        if action is not None and not resolved.is_relative_to(workdir):
            outside_match = (action, _OUTSIDE_TITLE)
            outside_details = [_call_summary(tool_name, args)]
    else:
        return RuleVote()

    if outside_match is not None:
        matches.append(outside_match)
    winner = resolve_matches(matches)
    if winner is None:
        return RuleVote()
    # outside_match 在末位且同 severity 取最后一条：winner is outside_match 即越界命中胜出
    details = outside_details if winner is outside_match else []
    return RuleVote(Decision(action=winner[0], reason=winner[1], source="rule"), details)


def _card_details(details: list[str], summary: str) -> list[str]:
    """确认卡触发数据段落：规则层段落在前，调用摘要去重后追加
    （文件工具的摘要行就是它自己的 `path:` 触发数据）。"""
    details = list(details)
    if summary not in details:
        details.append(summary)
    return details


def _apply_votes(ctx: BeforeToolContext, extension_votes: list[Decision],
                 rule_vote: RuleVote) -> None:
    """用户表态：动作唯一时采用；不唯一时弹冲突卡，拒绝统一收口为 Permission denied by user。

    extension_votes 为扩展决定票；规则票由 rule_vote 提供。rule_vote.details 是规则层越界
    命中的触发数据（路径清单 / path 行），与调用摘要同进确认卡。"""
    votes = ([rule_vote.decision] if rule_vote.decision is not None
             else []) + list(extension_votes)
    summary = _call_summary(ctx.tool_name, ctx.args)
    if len({vote.action for vote in votes}) > 1:
        sources = "\n".join(
            f"- {vote.source}: {vote.action} ({vote.reason})" for vote in votes)
        details = _card_details([sources, *rule_vote.details], summary)
        if not _confirm("Decision conflict:", details):
            _block_by_user(ctx)
        return

    vote = votes[0]
    if vote.action == "allow":
        return
    if vote.action == "deny":
        ctx.blocked = True
        if vote is rule_vote.decision:
            reason = "\n".join([vote.reason, *rule_vote.details])
            ctx.block_reason = f"Permission denied by rule: {reason}"
        else:
            ctx.block_reason = f"[Extension blocked] {vote.reason}"
        return
    if not _confirm(vote.reason, _card_details(rule_vote.details, summary)):  # ask
        _block_by_user(ctx)


def _block_by_user(ctx: BeforeToolContext) -> None:
    ctx.blocked = True
    ctx.block_reason = "Permission denied by user"


def _apply_defaults(ctx: BeforeToolContext) -> None:
    """默认层：无任何用户表态时生效。

    Manual（含未实现的 LLM / Decision Model）对危险命令与越界访问询问；
    Yolo 信任所有 terminal 命令、越界访问同样放行，默认层整体关闭。
    地板与规则 / 扩展表态的优先级不变。"""
    if CONFIGMANAGER.config.approval_mode == "Yolo":
        return

    tool_name, args = ctx.tool_name, ctx.args
    if tool_name == "terminal":
        command = args.get("command", "")
        destructive = is_destructive(command)
        outside = _outside_paths(command, _workdir(ctx))
        if not destructive and not outside:
            return
        reasons, details = [], []
        if destructive:
            reasons.append("Destructive command detected")
        if outside:
            reasons.append(_OUTSIDE_TITLE)
            details.append(_outside_listing(outside))  # 路径清单属触发数据，与确认文本分开着色
        details.append(_call_summary(tool_name, args))
        if not _confirm("\n\n".join(reasons), details):
            _block_by_user(ctx)
        return

    if tool_name in _FILE_TOOLS and _is_outside_workdir(args.get("path", ""), _workdir(ctx)):
        if not _confirm(_OUTSIDE_TITLE, [_call_summary(tool_name, args)]):
            _block_by_user(ctx)


def build_gate(rules: Rules) -> Callable[[BeforeToolContext], None]:
    """构造权限门回调：地板 → 规则/扩展表态 → 默认层。"""

    def gate(ctx: BeforeToolContext) -> None:
        if ctx.tool_name == "terminal":
            denial = floor_denial(ctx.args.get("command", ""))
            if denial is not None:
                ctx.blocked = True
                ctx.block_reason = f"Permission denied: '{denial}' is on the deny list."
                return

        rule_vote = _rule_vote(rules, ctx)
        if rule_vote.decision is not None or ctx.decisions:
            _apply_votes(ctx, ctx.decisions, rule_vote)
            return

        _apply_defaults(ctx)
        # 其余工具（含 mcp_*）没有规则与默认层：直接放行

    return gate


def register(api: ExtensionAPI, rules: Rules) -> None:
    """注册内置权限门：固定 gate 阶段并声明 fail-closed。"""
    api.on("before_tool", build_gate(rules), stage="gate", fail_closed=True)
