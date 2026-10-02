"""右栏信息面板：Todos / Background Tasks / Subagents 分区（自 ui_textual.py 拆出）。

数据源为模块级状态（core.todo.CURRENT_TODOS / core.background_task.BACKGROUND_TASKS /
core.sub_agent.SUBAGENT_TASKS），由 agent / 后台线程 / 子代理协程随时写入；
面板 1s 轮询同步，出现进行中项时以 0.1s 轮播字形。
分区与条目点击在本部件内处理（阻止冒泡，App 不再感知）。"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

import core.background_task as _bg  # 数据源：模块引用，随 agent 线程写入实时可见
import core.sub_agent as _sa  # 数据源：运行中子代理的执行阶段（thinking / tool）
from core.todo import todo as _todo  # 数据源：todo_write 整体替换 CURRENT_TODOS，须经模块取最新引用
from core.tui.theme import _SPINNER_FRAMES

_RUNNING_STATUSES = ("in_progress", "running")  # 需要轮播字形的状态：todo / bg 各一


class _InfoRow(Static):
    """右栏条目行：点击在单行摘要（超限截断附 …）与多行缩进全文间切换。

    行首字形：in_progress / running 为轮播帧（_InfoPanel._tick 推进），
    其余为状态点；kind="bg" / "subagents" 的行额外显示任务 id，
    折叠时只显示工具名 / 阶段，展开后才以灰色补上参数列表 / description。"""

    _CLIP = {"todos": 72, "bg": 48, "subagents": 48}  # 折叠摘要单行字符上限

    def __init__(self, kind: str, data: dict, cursor: int) -> None:
        super().__init__(markup=False, classes="info-row -expandable")
        self._kind = kind
        self._data = data
        self._cursor = cursor
        self._expanded = False
        self.update(self._text())

    def is_running(self) -> bool:
        return self._data["status"] in _RUNNING_STATUSES

    def tick(self, cursor: int) -> None:
        """推进一帧轮播字形（仅进行中的行由调用方调用）"""
        self._cursor = cursor
        self.update(self._text())

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self.update(self._text())

    def _text(self) -> Text:
        frame = _SPINNER_FRAMES[self._cursor % len(_SPINNER_FRAMES)]
        it = self._data
        if self._kind == "todos":
            glyph, color, text_style = {
                "in_progress": (frame, "#facc15", "#facc15"),
                "completed": ("●", "#4ade80", "strike #4ade80"),
            }.get(it["status"], ("○", "#94a3b8", "#e2e8f0"))
        elif self._kind == "bg":  # 仅 running / completed 两态（见 core/background_task.py start_background_task）
            running = it["status"] == "running"
            glyph, color = (frame, "#facc15") if running else ("●", "#4ade80")
            text_style = "#facc15" if running else "strike #4ade80"
        else:  # subagents：条目只存在于运行期间，颜色区分阶段（thinking / tool）
            thinking = it["phase"] != "tool"
            glyph, color = frame, "#facc15" if thinking else "#60a5fa"
            text_style = color
        lines = it["text"].splitlines() or [""]
        clip = self._CLIP[self._kind]
        row = Text()
        row.append("▾ " if self._expanded else "▸ ", style="#64748b")  # 折叠态指示（同分区标题 ▼/▶）
        row.append(f"{glyph} {it['id']} " if self._kind != "todos" else f"{glyph} ", style=color)
        row.append(lines[0] if self._expanded else lines[0][:clip], style=text_style)
        if self._expanded:
            for line in lines[1:]:
                row.append(f"\n  {line}", style=text_style)
        elif len(lines[0]) > clip or len(lines) > 1:  # 折叠为单行摘要，其余展开可见
            row.append("…", style=text_style)
        if self._kind in ("bg", "subagents") and self._expanded and it.get("expand_text"):
            for line in it["expand_text"].splitlines():  # 折叠时完全不占位，展开才灰色补全文
                row.append(f"\n  {line}", style="#94a3b8")
        return row


class _InfoPanel(Vertical):
    """右栏各分区卡片：标题（▼/▶ + 计数，嵌在上边框左端）随边框点击独立折叠/展开
    （-collapsed 类见 app.css），条目行由数据源 1s 轮询重建（内容/状态/顺序签名未变则跳过）。"""

    SECTIONS = {"todos": "Todos", "bg": "Background Tasks", "subagents": "Subagents"}  # 顺序即右栏上下顺序
    EMPTY = {"todos": "No todos", "bg": "No background tasks", "subagents": "No subagents"}

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sig: dict[str, object] = {}  # 各区上次渲染的数据签名
        self._cursor = 0  # 条目行加载动画帧下标
        self._anim = None  # 加载动画 interval（出现进行中项时惰性启动）

    def compose(self) -> ComposeResult:
        for key, label in self.SECTIONS.items():
            section = Vertical(VerticalScroll(id=f"{key}-list"), id=f"{key}-section", classes="info-section")
            section.border_title = f"▼ {label}"  # 标题嵌在上边框左端（同弹窗；计数见 _head_text）
            yield section
        yield Static("Ctrl+←/→ resize panel", id="info-hint", markup=False)  # 调宽组合键提示（dock bottom，随面板折叠隐藏）

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(1.0, self._refresh)

    def on_click(self, event: events.Click) -> None:
        """条目行点击：摘要 ↔ 全文；分区边框（含标题）点击：折叠/展开该分区（就地处理，不冒泡到 App）。"""
        target = event.widget
        if target.has_class("-expandable"):
            event.stop()
            target.toggle_expand()
        elif target.has_class("info-section"):
            event.stop()
            kind = target.id.removesuffix("-section")
            target.set_class(not target.has_class("-collapsed"), "-collapsed")
            self._head_text(kind, len(self._items(kind)))

    # ---------- 数据同步 ----------

    def _refresh(self) -> None:
        """同步三个分区：条目行按签名按需重建，标题行更新计数，有进行中项时驱动轮播动画。"""
        running = False
        for kind in self.SECTIONS:
            items = self._sync_rows(kind)
            self._head_text(kind, len(items))
            running |= any(it["status"] in _RUNNING_STATUSES for it in items)
        self._sync_anim(running)

    def _items(self, kind: str) -> list[dict]:
        """分区条目快照 [{id, status, text}]。todo_write 整体替换 CURRENT_TODOS 引用、
        bg 线程持锁改状态，故一律取副本。"""
        if kind == "todos":
            return [{"id": t.content, "status": t.status, "text": t.content}
                    for t in list(_todo.CURRENT_TODOS)]
        if kind == "subagents":
            # 条目只在子代理运行期间存在（core/sub_agent.py 的 finally 会移除）；
            # 阶段拼进 text（thinking ↔ tool 切换即触发签名重建），description 缺省不展示
            items = []
            for aid, info in list(_sa.SUBAGENT_TASKS.items()):
                label = f"tool: {info['detail']}" if info.get("phase") == "tool" else "thinking"
                items.append({"id": aid, "status": "running", "phase": info.get("phase"),
                              "text": f"[{label}]", "expand_text": info.get("description", "")})
            return items
        with _bg.BACKGROUND_LOCK:
            # 折叠只留工具名，参数（tool_call 括号内）改为展开后灰色展示
            items = []
            for bid, info in _bg.BACKGROUND_TASKS.items():
                name, _, args = info.get("tool_call", "").partition("(")
                items.append({"id": bid, "status": info.get("status"), "text": name,
                              "expand_text": args.removesuffix(")")})
            return items

    def _sync_rows(self, kind: str) -> list[dict]:
        """按最新条目重建 #kind-list（id/状态/内容签名未变则跳过，避免 1s 轮询反复重建）。返回本次快照。"""
        items = self._items(kind)
        sig = tuple((it["id"], it["status"], it["text"]) for it in items)
        if sig == self._sig.get(kind):
            return items
        self._sig[kind] = sig
        holder = self.query_one(f"#{kind}-list", VerticalScroll)
        holder.remove_children()
        if not items:
            holder.mount(Static(self.EMPTY[kind], markup=False, classes="info-row"))  # 空列表占位（不可展开）
        else:
            holder.mount(*(_InfoRow(kind, it, self._cursor) for it in items))
        return items

    def _head_text(self, kind: str, count: int) -> None:
        """上边框左端标题 = 折叠箭头（随分区状态）+ 分区名 + dim 条目计数"""
        section = self.query_one(f"#{kind}-section", Vertical)
        glyph = "▶" if section.has_class("-collapsed") else "▼"
        section.border_title = f"{glyph} {self.SECTIONS[kind]} [dim #a7bf21]· {count}[/dim #a7bf21]"

    def _sync_anim(self, running: bool) -> None:
        """有进行中项时惰性启动 0.1s Braille spinner interval；全部结束即停并复位帧
        （避免空闲期空转重绘）"""
        if running and self._anim is None:
            self._anim = self.set_interval(0.1, self._tick)
        elif not running and self._anim is not None:
            self._anim.stop()
            self._anim = None
            self._cursor = 0  # 复位首帧，下次有进行中项时从头轮播

    def _tick(self) -> None:
        """轮播一帧：先刷新条目数据（顺带拾取两次 1s 轮询间 todo_write / bg 线程的写入），
        再重绘进行中的行；全部结束即停（见 _sync_anim）。"""
        self._cursor += 1
        self._refresh()
        for row in self.query(_InfoRow):
            if row.is_running():
                row.tick(self._cursor)
