"""弹窗指令 / 会话历史 / 用户回合 / 压缩——ChatApp 的指令流 mixin。

/ 指令的打开方法表（_OPENERS）与分派（_dispatch_query）都在本模块；ui_textual.on_input_submitted
只做输入清理后转调。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from core.runtime_context import AgentInterrupted
from core.skill import skills as _skills
from core.tui.render import (
    render_background_notification,
    render_session_history,
    render_sessions,
    render_user_input,
)
from core.tui.screens import (
    EffortScreen,
    ForkScreen,
    LoginScreen,
    MCPServersScreen,
    ModelPickerScreen,
    ProviderScreen,
    SessionPickerScreen,
    SettingsScreen,
    SkillsScreen,
)
from core.tui.theme import _PLACEHOLDER


class _CommandFlow:
    """ChatApp 指令与回合 mixin。"""

    _OPENERS = {  # / 指令 → 打开方法名（方法见下）
        "/sessions": "_open_sessions", "/fork": "_open_fork", "/skills": "_open_skills",
        "/mcp": "_open_mcp", "/provider": "_open_providers", "/model": "_open_models",
        "/effort": "_open_effort", "/login": "_open_login", "/logout": "_open_login",
        "/settings": "_open_settings",
    }

    # ---------- / 弹窗指令 ----------

    def _client_call(self, getter: str, title: str) -> Any:
        """懒加载调用 core.client.<getter>()；缺依赖（演示/冒烟环境）时提示卡并返回 None。"""
        try:
            import core.client as client
            return getattr(client, getter)()
        except Exception as exc:
            render_background_notification(str(exc), title=title)
            return None

    def _open_sessions(self) -> None:
        if self._manager is None:
            render_background_notification("SessionManager not connected — /sessions unavailable",
                                           title="⚠️ Sessions")
            return
        if not self._manager.load_session_list():
            render_sessions()  # 空列表提示卡片
            return
        self.push_screen(SessionPickerScreen(self._manager, on_delete_current=self._clear_cards),
                         callback=self._on_session_picked)

    def _open_fork(self) -> None:
        """/fork：列出全部用户消息，选中后从该消息前分叉新会话，原文填回输入栏。"""
        if self._manager is None:
            render_background_notification("SessionManager not connected — /fork unavailable",
                                           title="⚠️ Fork")
            return
        if self._busy:  # 分叉会换当前会话，进行中的回合必须拒绝
            self._reject_busy()
            return
        messages = self._manager.load_user_messages()
        if not messages:
            render_background_notification("No user messages to fork from", title="⚠️ Fork")
            return
        self.push_screen(ForkScreen(messages),
                         callback=lambda message_id: self._on_fork_picked(message_id, messages))

    def _on_fork_picked(self, message_id: Optional[str], messages: list[Any]) -> None:
        """ForkScreen 回调：分叉并重放新会话历史（不含选中消息），原文填入输入条；
        None = Esc 取消，分叉失败只提示。"""
        if not message_id or self._manager is None:
            return
        forked = self._manager.fork_session(message_id)
        if forked is None:
            render_background_notification("Fork failed: message not found", title="⚠️ Fork")
            return
        if forked.messages:
            self._reload_history()
        else:
            self._clear_cards()  # 分叉到起点：新会话尚无历史
        content = next((m.content for m in messages if m.id == message_id), "")
        prompt = self._prompt()
        prompt.text = content
        prompt.cursor_location = prompt.document.end  # text setter 把光标归位开头，置于末尾
        prompt.focus()

    def _open_skills(self) -> None:
        """/skills：core.skill 扫描到的技能列表。"""
        rows = list(_skills.SKILL_REGISTRY.values())
        if not rows:
            render_background_notification(
                "No skills found: add SKILL.md manifests under the global (~/.agents/skills), "
                "user (.harness/skills) or project (.agents/skills) skills directory",
                title="⚠️ Skills")
            return
        self.push_screen(SkillsScreen(rows), callback=self._on_skill_picked)

    def _on_skill_picked(self, skill_name: Optional[str]) -> None:
        """SkillsScreen 回调：选中技能作为用户消息发出（busy 时拒绝）。"""
        if not skill_name:
            return
        if self._busy:
            self._reject_busy()
            return
        query = f"Invoke skill '{skill_name}'"
        self._prompt().text = query  # 填入后随发送清空（效果同手打回车）
        self._send_user_query(query)

    def _open_mcp(self) -> None:
        """/mcp：MCP server 列表（懒导入 + 异常兜底；servers 为空也打开，窗内可 Insert 配置）。"""
        try:
            from core.mcp.mcp_client import get_mcp_server_list
            servers = get_mcp_server_list()
        except Exception as exc:
            render_background_notification(str(exc), title="⚠️ MCP Servers")
            return
        self.push_screen(MCPServersScreen(servers))

    def _open_providers(self) -> None:
        """/provider：provider 配置列表；关闭后即时刷新页脚（2s 轮询兜底）。"""
        rows = self._client_call("get_provider_list", "⚠️ Provider List")
        if rows is not None:
            self.push_screen(ProviderScreen(rows), callback=lambda _: self._refresh_footer())

    def _open_models(self) -> None:
        """/model：可用模型列表，Enter 经 shared_model_client().set_model_client 切换。"""
        rows = self._client_call("get_model_list", "⚠️ Model List")
        if rows is None:
            return
        if not rows:
            render_background_notification("No models available: configure a provider via /provider first",
                                           title="⚠️ Model List")
            return
        self.push_screen(ModelPickerScreen(rows), callback=lambda _: self._refresh_footer())

    def _open_effort(self) -> None:
        """/effort：思考深度五档（minimal→max），Enter 经 set_thinking_level 生效。"""
        client = self._client_call("shared_model_client", "⚠️ Effort List")
        if client is None:
            return
        current = getattr(client, "current_thinking_level", "") or ""
        self.push_screen(EffortScreen(current), callback=lambda _: self._refresh_footer())

    def _open_login(self) -> None:
        """/login（别名 /logout）：自定义提供方 / 模型注册（注销）菜单。"""
        self.push_screen(LoginScreen(), callback=lambda _: self._refresh_footer())

    def _open_settings(self) -> None:
        """/settings：AgentConfig 参数表单；Enter 两段式（先编辑后保存，开关直接切换保存），Esc 关闭。"""
        self.push_screen(SettingsScreen())

    # ---------- 会话历史 ----------

    def _reload_history(self, session_id: str = "") -> None:
        """按会话最新消息重放聊板（省略 id = 当前会话；切换会话 / 压缩完成共用）。"""
        if self._manager is None:
            return
        session = self._manager.load_session(session_id)
        if session is not None:
            self._clear_cards()
            render_session_history(session)

    def _on_session_picked(self, result: tuple[Optional[str], bool]) -> None:
        session_id, was_empty = result
        if not session_id:
            if was_empty:
                render_sessions()
            return
        self._reload_history(session_id)

    # ---------- 用户回合 ----------

    def _dispatch_query(self, query: str) -> None:
        """/ 指令与普通消息分派：exit → opener → 忙时拒绝 → compact → new → 发送。"""
        cmd = query.lower()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif opener := self._OPENERS.get(cmd):
            getattr(self, opener)()
        elif self._busy:
            # 回合进行中拒绝 /compact /new 与普通消息：/new 清空会话指针后，本轮后续 add_message
            # 会把回话写进新建会话（见 session.py：current_session 为空时自动 new_session）
            self._reject_busy()
        elif cmd == "/compact":
            self._run_compact()
        elif cmd == "/new":
            if self._manager is not None:
                self._manager.reset()  # 延迟建会话：下条消息到达时自动创建
            self._clear_cards()
        else:
            self._send_user_query(query)

    def _reject_busy(self) -> None:
        render_background_notification("Previous turn is still running, please wait…", title="⏳ Busy")

    def _send_user_query(self, query: str) -> None:
        """把一条文本作为用户消息发出（输入条回车与 /skills 选中技能共用）。"""
        self._prompt().text = ""  # 输入已消费即清空
        self._busy = True
        self._prompt().disabled = True
        self._set_status_text("Working…", spin=True)
        self._progress_working()
        render_user_input(query)
        self._spawn_worker(lambda: self._turn_worker(query), "agent-turn", "⚠️ Agent Error")

    def _turn_worker(self, query: str) -> None:
        if self._manager is not None:
            try:
                self._manager.add_message({"role": "user", "content": query})
            except Exception as exc:
                render_background_notification(f"保存会话消息失败：{exc}", title="⚠️ Session Error")
        self._handle(query)

    def _spawn_worker(self, work: Callable[[], None], name: str, title: str) -> threading.Thread:
        """后台线程跑 work：线程内异常上屏，收尾回 App 线程复位忙碌态（App 已退出则忽略）。"""

        def _run() -> None:
            try:
                work()
            except Exception as exc:
                render_background_notification(f"{type(exc).__name__}: {exc}", title=title)
            finally:
                try:
                    self.call_from_thread(self._set_idle)
                except RuntimeError:
                    pass  # App 已退出

        thread = threading.Thread(target=_run, daemon=True, name=name)
        thread.start()
        return thread

    def _set_idle(self) -> None:
        """回合 / 压缩结束：复位忙碌态、收起权限与 clarify 列表、恢复输入条并收回焦点。"""
        self._busy = False
        self._progress_clear()
        self._reset_interactions()
        self._set_status_text("")
        prompt = self._prompt()
        prompt.placeholder = _PLACEHOLDER
        prompt.disabled = False
        prompt.focus()

    # ---------- 手动压缩（/compact） ----------

    def _run_compact(self) -> None:
        """后台线程调 AgentRuntime.run_compact（与 agent 回合串行）。"""
        if self._runtime is None:
            render_background_notification("AgentRuntime not connected — /compact unavailable",
                                           title="⚠️ Compact")
            return
        if self._manager and not self._manager.current_session:
            render_background_notification("Current session is empty — /compact unavailable",
                                           title="⚠️ Compact")
            return
        self._busy = True
        self._prompt().disabled = True
        self._set_status_text("Compacting…", spin=True)
        self._progress_working()
        self._spawn_worker(lambda: self._compact_worker(self._runtime), "agent-compact", "⚠️ Compact Error")

    def _compact_worker(self, runtime: Any) -> None:
        """压缩线程主体：run_compact() 在锁内提交并在 runtime 事件循环上跑完（阻塞）。
        Esc 可中断：中断则不落会话，历史保持原样。
        compact 返回 False（低于阈值 / 未选模型）时只提示未压缩，不重放历史。"""
        try:
            compacted = runtime.run_compact()
        except AgentInterrupted:  # 取消统一成 AgentInterrupted
            render_background_notification("Compaction interrupted — history unchanged.",
                                           title="⏹ Compact")
            return
        if not compacted:
            render_background_notification("Nothing to compact — session is below the compaction "
                                           "threshold (or no model is selected).", title="🗜 Compact")
            return
        self.call_from_thread(self._reload_history)
        render_background_notification("Context compacted — history re-rendered.", title="🗜 Compact")
