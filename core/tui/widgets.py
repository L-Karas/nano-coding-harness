"""可复用部件与弹窗：会话列表行 / 会话选择弹窗 / 带指令与 @ 文件补全的输入框。

- _SessionRow + SessionPickerScreen：/sessions 弹窗（Enter 切换 / Delete 删除）；
- ProviderScreen + ApiKeyScreen：/provider 弹窗（Enter 录入 API Key / Delete 删除配置）；
- ModelPickerScreen：/model 弹窗（Enter 切换当前模型）；
- EffortScreen：/effort 弹窗（Tabs 五档思考深度，Enter 应用）；
- SkillsScreen：/skills 弹窗（展示全部已扫描技能）；
- _match_project_entries 及辅助函数：@ 提及候选匹配（utils.list_project_files 之上的纯函数层）；
- _CommandInput：TextArea 子类，拦截按键实现 / 指令、@ 文件两组候选列表交互。
"""

from __future__ import annotations

from typing import Any, Optional

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.measure import Measurement
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, Tab, Tabs, TextArea
from textual.widgets.option_list import Option

from core.tui.utils import SLASH_COMMAND_ALIASES, SLASH_COMMANDS, list_project_files, slash_command_rows


def _error_text(exc: Exception) -> str:
    """异常文本带类型名前缀：str 为空的异常（如 KeyError('')）不至于只显示空串。"""
    return f"{type(exc).__name__}: {exc}"


def _rebuild_options(olist: OptionList, options: list[Option]) -> None:
    """整列重建并尽量保留原高亮位置（OptionList 无就地更新行的 API）。"""
    old_index = olist.highlighted
    olist.clear_options()
    olist.add_options(options)
    if options:
        olist.highlighted = min(old_index or 0, len(options) - 1)


class _InstantTabs(Tabs):
    """滑块无滑行延迟的 Tabs：原生切档时对 Underline 高亮段做 0.3s 位移动画，文字高亮
    即时切换、滑块滞后半拍观感拖沓。这里强制动画关闭（动画只影响位置过渡，滑块色仍随
    TabActivated 即时同步）。"""

    def _highlight_active(self, animate: bool = True) -> None:
        super()._highlight_active(False)


class _SessionRow:
    """会话列表项：标题靠左、(current session) 标记随后、时间戳顶到行最右。

    OptionList 把每行 prompt 按行宽渲染后再尾部补白（默认左对齐），无法表达右对齐列；
    因此在渲染时按 OptionList 给到的实际行宽自排版，时间戳恒在行最右。宽度随终端布局
    变化，每次渲染按当时行宽重排，无需预知终端宽度；空间不足时先舍 (current) 标记、
    再截断标题（…），时间戳保持完整。
    """

    _TITLE_STYLE = "#f8fafc"
    _MARK_STYLE = "#34d399"
    _TS_STYLE = "#94a3b8"

    def __init__(self, title: str, current: bool, timestamp: str) -> None:
        self._title = title
        self._current = current
        self._timestamp = timestamp

    def __rich_measure__(self, options: ConsoleOptions) -> Measurement:
        # 与普通文本一致：自然宽度 = 完整未截断行（超宽行由列表裁剪）
        full = f"{self._title}{'  (current session)' if self._current else ''}  {self._timestamp}"
        return Measurement(1, cell_len(full))

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = max(0, options.max_width or 0)
        ts = Text(f"  {self._timestamp}", style=self._TS_STYLE)
        row = Text()
        free = width - ts.cell_len
        if free >= 0:  # 常规宽度：标题 + 可选标记 + 空白填隙 + 时间戳
            title = Text(self._title, style=self._TITLE_STYLE)
            if title.cell_len > free:
                # rich 的 truncate(0) 不清空文本（ellipsis 分支按 max_width-1 取宽），故单独处理
                if free >= 1:
                    title.truncate(free, overflow="ellipsis")
                else:
                    title = Text("")
            row.append_text(title)
            free -= title.cell_len
            if self._current:
                mark = Text("  (current session)", style=self._MARK_STYLE)
                if mark.cell_len <= free:
                    row.append_text(mark)
                    free -= mark.cell_len
            row.append(" " * free)  # 空白填隙：时间戳顶到行最右
        row.append_text(ts)
        row.no_wrap = True  # 极端窄行不折行，超宽部分交给 OptionList 裁剪
        yield row


class SessionPickerScreen(ModalScreen[tuple[Optional[str], bool]]):
    """会话选择弹窗：Enter 切换 / Delete 删除 / Esc 取消。
    dismiss 结果: (选中的 session id 或 None, 列表是否已删空)。"""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("delete", "remove_selected", "Delete"),
    ]

    def __init__(self, manager: Any) -> None:
        super().__init__()
        self._manager = manager

    def compose(self) -> ComposeResult:
        # 不包 CenterMiddle 等全屏容器：会盖掉底层主界面合成（原因见 app.css ModalScreen 注释）
        yield Vertical(
            Static("📂 Select a session (current one is marked)", classes="picker-title"),
            OptionList(id="sess-list"),
            Static("  ↑/↓ browse    Enter switch    Delete remove    Esc close", classes="picker-hint"),
            classes="picker",
        )

    def on_mount(self) -> None:
        self._reload()

    def _row(self, session) -> _SessionRow:
        return _SessionRow(
            session.title or session.id,
            current=session.id == self._manager.current_session,
            timestamp=str(session.timestamp)[:16],  # 只展示到分钟；排序仍按完整时间戳
        )

    def _reload(self) -> None:
        sessions = sorted(self._manager.load_session_list(), key=lambda s: s.timestamp, reverse=True)
        _rebuild_options(self.query_one("#sess-list", OptionList),
                         [Option(self._row(s), id=s.id) for s in sessions])

    def action_cancel(self) -> None:
        self.dismiss((None, False))

    def action_remove_selected(self) -> None:
        olist = self.query_one("#sess-list", OptionList)
        if olist.highlighted is None:
            return
        self._manager.delete_session(olist.get_option_at_index(olist.highlighted).id)
        if self._manager.load_session_list():
            self._reload()
        else:
            self.dismiss((None, True))  # 删空后退出，让主界面显示空列表卡片

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id:
            self.dismiss((event.option_id, False))


class ProviderScreen(ModalScreen[None]):
    """模型提供商配置弹窗（/provider）：OptionList 展示配置状态（↑/↓ 原生首尾循环），
    Enter/点击行 → ApiKeyScreen 录入该 provider 的 API Key（掩码显示），再次 Enter 经
    core.client.configure_provider 落盘并重取列表刷新；Delete → 经
    core.client.unconfigure_provider 删除该 provider 配置；Esc 关闭。

    rows = (provider, 是否已配置) 结构化状态，由调用方从 core.client.get_provider_list()
    取值；本类只负责展示，状态变更后重取真源而非本地维护镜像。
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("delete", "remove_selected", "Delete"),
    ]

    _NAME_STYLE = "#f8fafc"
    _UNCONFIGURED_STYLE = "#94a3b8"  # 灰：未配置
    _CONFIGURED_STYLE = "#16a34a"  # 暗绿：已配置

    def __init__(self, rows: list[tuple[str, bool]]) -> None:
        super().__init__()
        self._rows = list(rows)  # 快照仅作首屏；配置变更后经 _refresh 重取真源

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("🔌 Model Providers (config status)", classes="picker-title"),
            OptionList(id="provider-list"),
            Static("  ↑/↓ browse    Enter set API Key    Delete remove config    Esc close", classes="picker-hint"),
            classes="picker",
        )

    def on_mount(self) -> None:
        self._reload()
        self.query_one("#provider-list", OptionList).focus()

    def _reload(self) -> None:
        _rebuild_options(self.query_one("#provider-list", OptionList),
                         [Option(self._row_text(provider, configured), id=provider)
                          for provider, configured in self._rows])

    def _row_text(self, provider: str, configured: bool) -> Text:
        """行 = provider 首字母大写 + 状态（未配置灰 / 已配置暗绿）"""
        status = "[● configured]" if configured else "[○ unconfigured]"
        status_style = self._CONFIGURED_STYLE if configured else self._UNCONFIGURED_STYLE
        return Text.assemble((provider[:1].upper() + provider[1:], self._NAME_STYLE),
                             (" " + status, status_style))

    def _refresh(self) -> None:
        """配置落盘后重取 core.client 最新状态重建列表（configure_provider 可能新增整行，
        本地改镜像会漏；能走到这里说明 configure/unconfigure 已成功导入 core.client）。"""
        from core.client import get_provider_list
        self._rows = get_provider_list()
        self._reload()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中行：弹出 ApiKeyScreen 录入该 provider 的 API Key"""
        event.stop()
        if event.option_id is None:
            return
        provider = event.option_id
        self.app.push_screen(ApiKeyScreen(provider),
                             callback=lambda api_key: self._on_key_submitted(provider, api_key))

    def _on_key_submitted(self, provider: str, api_key: Optional[str]) -> None:
        """ApiKeyScreen 关闭回调：None=Esc 取消；有 key 则 configure_provider 落盘，
        成功后重取列表刷新状态。"""
        if not api_key:
            return
        try:
            from core.client import configure_provider
            configure_provider(provider, api_key)
        except Exception as exc:
            self.app.notify(f"Failed to configure {provider}: {_error_text(exc)}",
                            title="⚠️ Provider", severity="error")
            return
        self._refresh()
        self.query_one("#provider-list", OptionList).focus()  # 焦点回列表，可继续配置下一家

    def action_remove_selected(self) -> None:
        """Delete：删除选中 provider 的配置并重取列表刷新；已未配置行无可删内容，提示后返回。"""
        olist = self.query_one("#provider-list", OptionList)
        if olist.highlighted is None:
            return
        provider, configured = self._rows[olist.highlighted]
        if not configured:
            self.app.notify(f"{provider} is not configured — nothing to delete", title="ℹ️ Provider")
            return
        try:
            from core.client import unconfigure_provider
            unconfigure_provider(provider)
        except Exception as exc:
            self.app.notify(f"Failed to delete {provider} config: {_error_text(exc)}",
                            title="⚠️ Provider", severity="error")
            return
        self._refresh()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelPickerScreen(ModalScreen[None]):
    """模型选择弹窗（/model）：OptionList 展示可用模型（↑/↓ 原生首尾循环），Enter/点击行
    → 经 core.client.shared_model_client().set_model_client 切换当前模型（thinking_level
    不参与）并关闭弹窗；失败 notify 留在窗内重试，Esc 关闭。

    行 = 模型名 + [Provider Name]（暗灰）+ 可选 (current model) 标记。
    rows 由调用方从 core.client.get_model_list() 取值，本类只负责展示与切换。
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    _NAME_STYLE = "#f8fafc"
    _PROVIDER_STYLE = "#64748b"  # 暗灰：所属提供商
    _MARK_STYLE = "#34d399"  # 当前模型标记（同 _SessionRow 的 current session 标记色）

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self._rows = rows
        self._current = ("", "")  # 当前模型 (provider, model)；on_mount 经懒导入解析

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("🤖 Select a model (Enter switches the current model)", classes="picker-title"),
            OptionList(id="model-list"),
            Static("  ↑/↓ browse    Enter switch model    Esc close", classes="picker-hint"),
            classes="picker",
        )

    def on_mount(self) -> None:
        # 当前模型取自 shared_model_client（懒导入失败 / 冒烟用无属性假 client → 保持空默认，无标记）
        try:
            from core.client import shared_model_client
            client = shared_model_client()
            self._current = (getattr(client, "current_provider", "") or "",
                             getattr(client, "current_model", "") or "")
        except Exception:
            pass
        olist = self.query_one("#model-list", OptionList)
        _rebuild_options(olist, [Option(self._row_text(model, tag), id=str(i))
                                 for i, (model, tag) in enumerate(self._rows)])
        olist.focus()

    def _row_text(self, model: str, provider_tag: str) -> Text:
        """行 = 模型名（亮色）+ [Provider Name]（暗灰）+ 当前模型标记（暗绿，同会话列表）"""
        row = Text.assemble((model, self._NAME_STYLE), (" " + provider_tag, self._PROVIDER_STYLE))
        if self._current == (provider_tag.strip("[]").lower(), model):
            row.append("  (current model)", style=self._MARK_STYLE)
        return row

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中行：set_model_client 切换当前模型（thinking_level 不参与，走默认档位）；
        成功关窗，失败 notify 留在窗内。"""
        event.stop()
        index = event.option_index
        if not 0 <= index < len(self._rows):
            return
        model, provider_tag = self._rows[index]
        # 行内 Provider 为 "[Provider]"（展示用）；注册键一律小写（configure_provider 落盘小写）
        provider = provider_tag.strip("[]").lower()
        try:
            from core.client import shared_model_client
            shared_model_client().set_model_client(provider, model)
        except Exception as exc:
            self.app.notify(f"Failed to switch to {model}: {_error_text(exc)}",
                            title="⚠️ Model", severity="error")
            return
        self.app.notify(f"Switched to {model} {provider_tag}", title="✅ Model")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# /effort 思考深度档位（键 = shared_model_client().set_thinking_level 的 map 键，顺序即 Tabs 从左到右）
_EFFORT_LEVELS = ("minimal", "low", "medium", "high", "max")


class EffortScreen(ModalScreen[None]):
    """思考深度设置弹窗（/effort）：Tabs 标签从左到右 minimal/low/medium/high/max（←/→ 或
    点击切换，原生首尾循环）；Enter 经 core.client.shared_model_client().set_thinking_level
    设置当前档位并关窗；失败 notify 留在窗内重试，Esc 关闭。

    current = 当前档位（调用方取值；未知/空档位留在默认首档，Enter 即按首档重设）。
    布局纯 CSS（见 app.css #effort-picker）：弹窗定宽 46，标题/提示行与标签排居中由 CSS
    承担——auto 宽度容器里 align 不生效（Textual _arrange 对 auto 宽度按 0 算对齐偏移），
    故宽度以常量写死而非运行时量宽摆位。
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "apply", "Apply"),
    ]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("🎯 Thinking effort (Enter applies)", classes="picker-title"),
            _InstantTabs(*(Tab(level, id=level) for level in _EFFORT_LEVELS), id="effort-tabs"),
            Static("  ←/→ switch   Enter apply   Esc close", classes="picker-hint"),
            classes="picker",
            id="effort-picker",
        )

    def on_mount(self) -> None:
        tabs = self.query_one("#effort-tabs", Tabs)
        if self._current in _EFFORT_LEVELS:  # 当前档位点亮；空/未知值保持默认首档
            tabs.active = self._current
        self._sync_level_class(tabs, tabs.active)
        tabs.focus()

    def _sync_level_class(self, tabs: Tabs, level: str) -> None:
        """在 Tabs 节点上维护 -effort-<level> 类：CSS 按类把滑块（Underline 高亮段）染成
        对应档位色（见 app.css），与标签文字同色同映射。"""
        for lvl in _EFFORT_LEVELS:
            tabs.set_class(lvl == level, f"-effort-{lvl}")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """←/→ 或点击切档：滑块色随活动标签同步（on_mount 时 Tabs 首个激活事件可能早于
        本屏挂载完成，故 on_mount 里再显式同步一次）。"""
        event.stop()
        self._sync_level_class(event.tabs, event.tab.id or "")

    def action_apply(self) -> None:
        """Enter：当前标签档位 → set_thinking_level（懒导入：演示/冒烟环境可不装 openai）。
        Tabs 不消费 Enter，按键冒泡到本屏 BINDINGS 才轮到 apply。"""
        level = self.query_one("#effort-tabs", Tabs).active
        if level not in _EFFORT_LEVELS:
            return
        try:
            from core.client import shared_model_client
            shared_model_client().set_thinking_level(level)
        except Exception as exc:
            self.app.notify(f"Failed to set effort: {_error_text(exc)}",
                            title="⚠️ Effort", severity="error")
            return
        self.app.notify(f"Thinking effort → {level}", title="✅ Effort")
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SkillsScreen(ModalScreen[Optional[str]]):
    """技能列表弹窗（/skills）：OptionList 展示全部已扫描技能（↑/↓ 原生首尾循环），
    行 = 技能名（天蓝）+ 描述（暗灰多行）；Enter/点击选中 → dismiss 技能名，由 ChatApp
    填入输入条并发送 "Invoke skill '<name>'"（本类只负责展示与回传）；Esc 关闭。
    skills 由调用方从 core.skill.SKILL_REGISTRY 取值。"""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    _NAME_STYLE = "#7dd3fc"  # 天蓝：技能名（同右栏原 Skills 区配色）
    _DESC_STYLE = "#64748b"  # 暗灰：描述

    def __init__(self, skills: list[dict]) -> None:
        super().__init__()
        self._skills = skills  # [{name, description, content}] 快照

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("🧩 Available Skills", classes="picker-title"),
            OptionList(id="skills-list"),
            Static("  ↑/↓ browse    Enter invoke    Esc close", classes="picker-hint"),
            classes="picker",
        )

    def on_mount(self) -> None:
        olist = self.query_one("#skills-list", OptionList)
        _rebuild_options(olist, [Option(self._row_text(skill), id=skill["name"])
                                 for skill in self._skills])
        olist.focus()

    def _row_text(self, skill: dict) -> Text:
        """行 = 首行技能名（天蓝）；描述 strip 头尾空白后整段自第二行插入
        （内部换行 / 缩进原样保留，rich 按多行渲染）。"""
        row = Text()
        row.append(skill["name"], style=self._NAME_STYLE)
        desc = (skill.get("description") or "").strip()
        if desc:
            row.append("\n" + desc, style=self._DESC_STYLE)
        return row

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter/点击选中技能：关窗并把技能名交回 ChatApp（填入输入条 + 发送）"""
        event.stop()
        if event.option_id:
            self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ApiKeyScreen(ModalScreen[Optional[str]]):
    """API Key 录入弹窗（ProviderScreen 选中行后弹出）：Input(password=True) 掩码显示，
    Enter 提交返回 key（空 key 不关闭，防误提交），Esc 取消返回 None。"""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, provider: str) -> None:
        super().__init__()
        self._provider = provider

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"🔑 Enter the API Key for {self._provider}", classes="picker-title"),
            Input(placeholder="API Key (masked)", password=True, id="api-key-input"),
            Static("  Enter save    Esc cancel", classes="picker-hint"),
            classes="picker",
        )

    def on_mount(self) -> None:
        self.query_one("#api-key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter 提交：key 非空才关闭返回（空输入留在窗口内重新录入）"""
        event.stop()
        key = event.value.strip()
        if key:
            self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)


# / 指令候选行的显示文本（指令名列定宽 + 简短说明，列宽与分段规则见 utils.slash_command_rows）
_CMD_ROWS: dict[str, tuple[str, str]] = dict(zip(SLASH_COMMANDS, slash_command_rows()))
_CMD_DESC_STYLE = "#94a3b8"


def _cmd_candidates(value: str) -> list[str]:
    """/ 指令候选：整行以 / 开头且未含空白时按前缀过滤；指令或别名完整输入后不再提示。"""
    if not (value.startswith("/") and " " not in value):
        return []
    candidates = []
    for cmd in SLASH_COMMANDS:
        names = (cmd, *SLASH_COMMAND_ALIASES.get(cmd, ()))
        if value not in names and any(name.startswith(value) for name in names):
            candidates.append(cmd)
    return candidates


def _cmd_row(cmd: str) -> Text:
    """候选行 = 指令名列（定宽，含别名括注）+ 空距 + 简短说明（淡灰）；
    各行说明左端对齐于同一列。"""
    label, desc = _CMD_ROWS[cmd]
    row = Text(label, no_wrap=True)
    row.append(f"    {desc}", style=_CMD_DESC_STYLE)
    return row


def _ancestor_dirs(files: list[str]) -> set[str]:
    """list_project_files 只返回文件；全部祖先目录（含中间层）由文件路径推导。"""
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")[:-1]
        dirs.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    return dirs


def _level_entries(files: list[str], level: str, rem: str) -> list[str]:
    """level 目录的直接子项（同级列表）：名称含 rem 才入选；目录显示 name/。"""
    prefix = f"{level}/" if level else ""
    children: dict[str, str] = {}  # 子项名 -> 展示串（文件为完整路径，目录带结尾 /）
    for f in files:
        if f.startswith(prefix):
            name, _, deeper = f[len(prefix):].partition("/")
            children[name] = f"{prefix}{name}/" if deeper else f
    r = rem.lower()
    return [d for name, d in children.items() if r in name.lower()]


def _match_project_entries(query: str) -> list[str]:
    """@ 提及的补全候选：相对项目根的路径，目录以 / 结尾；大小写不敏感的子串匹配。

    - 空输入 → 项目根同级列表（目录 name/，不递归不展开）；
    - 纯名称（无 /，如 ui）→ 全树搜索名称含 query 的文件与目录，命中深层展示完整路径；
    - 带 / → 目录前缀必须真实存在，只在其直接子项中匹配（同级规则，逐级下钻）。"""
    files = list_project_files()
    dirs = _ancestor_dirs(files)
    if "/" in query:
        level, rem = (query[:-1], "") if query.endswith("/") else query.rsplit("/", 1)
        if level and level not in dirs:
            return []  # 目录前缀不存在：还没输入到任何文件夹内容里
        hits = set(_level_entries(files, level, rem))
    elif query:  # 纯名称：全树相似匹配
        q = query.lower()
        hits = {d + "/" for d in dirs if q in d.rsplit("/", 1)[-1].lower()}
        hits |= {f for f in files if q in f.rsplit("/", 1)[-1].lower()}
    else:  # 根目录同级列表
        hits = set(_level_entries(files, "", ""))
    hits.discard(query)  # 已完整输入条目名则不再提示（对齐 / 指令的完整输入即隐藏）
    return sorted(hits)


# 换行键组：Shift+Enter / Ctrl+Enter 需终端支持 kitty 键盘协议才可分（如 WezTerm、Linux
# 原生终端）；Ctrl+J 发送 LF，与 Enter 的 CR 在所有终端都区分，是通用兜底。
_NEWLINE_KEYS = ("shift+enter", "ctrl+j", "ctrl+enter")


class _CommandInput(TextArea):
    """带指令与 @ 文件补全的多行输入框：内容超出终端宽度自动换行多行显示（TextArea）。

    - / 指令：整行以 / 开头且未含空白时，在输入框上方弹出候选 OptionList（仅此场景可见），
      按前缀过滤，↑/↓ 选择、Tab/Enter 接受、Esc 关闭；
    - @ 文件提及：行首/空白后起始的 @ 弹出项目文件候选，Tab/Enter/点击把 @路径 补进输入
      （不发送）；带目录前缀时同级逐级下钻、目录显示为 name/，匹配到文件夹内内容时展示完整路径；
    - 普通消息 Enter 提交、Shift+Enter / Ctrl+J 换行。

    两组候选同刻至多一组非空，互斥显示。按键全部在 _on_key 拦截：TextArea 的 _on_key
    会消费 Enter（插换行）/Tab（缩进）/Esc，普通 BINDINGS 要等键未被消费、冒泡到 App
    才检查，永远轮不到。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._candidates: list[str] = []  # / 指令补全候选
        self._hl = 0  # 高亮项在 _candidates 中的下标
        self._file_candidates: list[str] = []  # @ 文件补全候选（完整相对路径；目录以 / 结尾）
        self._file_hl = 0  # 高亮项在 _file_candidates 中的下标

    # ---------- 候选列表（/ 指令与 @ 文件提及；同刻至多一组候选，互斥显示） ----------

    def _list_widget(self, list_id: str) -> Optional[OptionList]:
        """按 id 取候选 OptionList；部件尚未挂载时返回 None（早期事件空转）。"""
        try:
            return self.screen.query_one(f"#{list_id}", OptionList)
        except Exception:
            return None

    def _sync_list(self, list_id: str, entries: list[tuple[str, Text]]) -> None:
        """重建候选列表（选项 id = 候选串）；无候选则隐藏，部件未挂载则空转。
        set_options 同步整批重建，顺带复位 scroll_y 与高亮。"""
        ol = self._list_widget(list_id)
        if ol is None:
            return
        if not entries:
            ol.styles.display = "none"
            ol.highlighted = None
            return
        ol.styles.display = "block"
        ol.set_options(Option(prompt, id=value) for value, prompt in entries)
        ol.highlighted = 0  # None→0 触发 watch：高亮首项并滚回顶部

    def _set_highlight(self, list_id: str, index: int) -> None:
        """把高亮同步到候选列表（部件未挂载时空转）。"""
        ol = self._list_widget(list_id)
        if ol is not None:
            ol.highlighted = index

    def _find_mention(self) -> Optional[tuple[int, int, str]]:
        """光标所在行内、光标之前最后一个「行首/空白后」的 @；
        返回 (行, @列, @ 到光标之间的查询串)，无有效 @ 返回 None。"""
        line, col = self.cursor_location
        lines = self.text.split("\n")
        if line >= len(lines):
            return None
        before = lines[line][:col]
        at = before.rfind("@")
        if at < 0 or (at > 0 and not before[at - 1].isspace()):
            return None
        return line, at, before[at + 1:]

    def _refresh_suggestions(self) -> None:
        """输入变化后重算两组候选并重建对应列表（选项为纯数据，整批重建全同步）。"""
        self._candidates = _cmd_candidates(self.text.strip())
        mention = self._find_mention()
        # ponytail: 每次按键全树扫描（utils.list_project_files 已剪 .git/venv 等）；
        # 项目极大导致输入卡顿时再按目录树增量缓存
        self._file_candidates = _match_project_entries(mention[2]) if mention else []
        self._hl = 0
        self._file_hl = 0
        self._sync_list("cmd-suggest", [(cmd, _cmd_row(cmd)) for cmd in self._candidates])
        self._sync_list("file-suggest", [(c, Text(c, no_wrap=True)) for c in self._file_candidates])

    def _apply_cmd_candidate(self) -> None:
        """接受高亮的 / 指令候选：整行替换为指令并复位光标（Enter 时随后提交）。"""
        self.text = self._candidates[self._hl]
        self.cursor_location = (0, len(self.text))

    def _apply_file_candidate(self, index: int) -> bool:
        """把第 index 个文件候选补进输入：以 @完整路径 替换 @ 到光标处（不发送消息）。
        目录候选自带结尾 /，随后的刷新即展开其同级内容；返回是否成功应用。"""
        mention = self._find_mention()
        if mention is None or not 0 <= index < len(self._file_candidates):
            return False
        line, at, _ = mention
        _, col = self.cursor_location
        lines = self.text.split("\n")
        candidate = self._file_candidates[index]
        lines[line] = lines[line][:at] + "@" + candidate + lines[line][col:]
        self.text = "\n".join(lines)
        self.cursor_location = (line, at + 1 + len(candidate))
        self._refresh_suggestions()
        return True

    def _cycle_highlight(self, delta: int) -> None:
        """↑/↓：在可见候选中循环移动高亮（两组候选互斥，同刻至多一组非空）。"""
        if self._file_candidates:
            self._file_hl = (self._file_hl + delta) % len(self._file_candidates)
            self._set_highlight("file-suggest", self._file_hl)
        elif self._candidates:
            self._hl = (self._hl + delta) % len(self._candidates)
            self._set_highlight("cmd-suggest", self._hl)

    def _close_suggestions(self) -> None:
        """Esc：清空候选状态并隐藏两个列表（之后 Enter 恢复为普通发送）。"""
        self._candidates = []
        self._file_candidates = []
        self._sync_list("cmd-suggest", [])
        self._sync_list("file-suggest", [])

    # ---------- 键盘 ----------

    async def _on_key(self, event: events.Key) -> None:
        """拦截 Enter/换行/Tab 与候选导航键，其余交给 TextArea 编辑。
        - / 指令候选：Enter/Tab 接受（整行替换），Enter 随即提交消息；
        - @ 文件候选：Enter/Tab/点击接受，只把 @完整路径 补进输入不发送；
        - 无候选时：Enter 提交，Tab 吞掉（不插入缩进），方向键/Esc 交回 TextArea。"""
        key = event.key
        has_candidates = bool(self._candidates or self._file_candidates)
        if key not in ("enter", "tab", *_NEWLINE_KEYS) and not (
                has_candidates and key in ("up", "down", "escape")):
            await super()._on_key(event)
            return
        event.stop()
        event.prevent_default()
        if key in _NEWLINE_KEYS:
            self.insert("\n")
        elif key == "escape":
            self._close_suggestions()
        elif key in ("up", "down"):
            self._cycle_highlight(-1 if key == "up" else 1)
        elif self._file_candidates:  # enter / tab：优先 @ 文件候选 → 补进输入，不发送
            self._apply_file_candidate(self._file_hl)
        else:  # / 指令候选（接受后 Enter 一并提交）或无候选（Enter 直接提交）
            if self._candidates:
                self._apply_cmd_candidate()
            if key == "enter":
                self.post_message(Input.Submitted(self, self.text))

    # ---------- 事件 ----------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()
        self._refresh_suggestions()
