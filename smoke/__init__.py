"""ui_textual 的冒烟自检（顶层 smoke/ 包）：`python -m core.tui.ui_textual --smoke`。

用 Textual run_test 无头驱动 ChatApp，按功能分模块断言全流程（渲染 / 流式 / 补全 /
弹窗 / 权限 / 右栏数据源 / 页脚），失败即抛 AssertionError。仅 --smoke 分支按需导入，
正常运行与接入 Agent 不加载本模块。
"""

from __future__ import annotations

import asyncio

import core.tui.render as _render
from .demo import _demo_agent
from . import (
    chat,
    clarify,
    footer,
    login,
    mcp,
    mentions,
    models,
    panels,
    providers,
    sessions,
    settings,
    skills,
)
from core.tui.ui_textual import ChatApp

# 执行顺序即断言顺序（各段复用同一 App 实例的累积状态，勿随意调换）
_SECTIONS = (chat, providers, models, login, footer, mentions, sessions, panels, settings, skills,
             mcp, clarify)


async def _smoke() -> None:
    app = ChatApp(handle_query=_demo_agent)
    _render._APP = app  # 冒烟不经 run()：直接把实例挂到渲染桥接全局
    try:
        async with app.run_test() as pilot:
            for section in _SECTIONS:
                await section.run(app, pilot)
    finally:
        _render._APP = None


def main() -> None:
    """--smoke 入口：跑完整 UI 自检（阻塞至结束）。"""
    asyncio.run(_smoke())
