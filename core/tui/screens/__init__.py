"""TUI 弹窗集合（自 widgets.py 按功能拆出）。

- base：弹窗基础设施——原地确认 / OptionList 弹窗骨架 / 会话行 / 通用小工具；
- session / provider / model / skills / mcp / login：各功能弹窗。

导入面保持稳定：core.tui.widgets 仍转出常用弹窗名（历史导入不破）。
"""

from core.tui.screens.base import (
    _InlineConfirm,
    _InstantTabs,
    _ListPickerScreen,
    _SessionRow,
    _entry_row,
    _error_text,
    _model_row_text,
    _rebuild_options,
)
from core.tui.screens.fork import ForkScreen
from core.tui.screens.login import (
    LoginScreen,
    RegisterModelScreen,
    RegisterProviderScreen,
    UnregisterModelScreen,
    UnregisterProviderScreen,
)
from core.tui.screens.mcp import MCPConfigScreen, MCPServersScreen, MCPToolsScreen
from core.tui.screens.model import EffortScreen, ModelPickerScreen
from core.tui.screens.provider import ApiKeyScreen, ProviderScreen
from core.tui.screens.session import SessionPickerScreen
from core.tui.screens.skills import SkillsScreen

__all__ = [
    "ApiKeyScreen", "EffortScreen", "ForkScreen", "LoginScreen", "MCPConfigScreen", "MCPServersScreen",
    "MCPToolsScreen", "ModelPickerScreen", "ProviderScreen", "RegisterModelScreen",
    "RegisterProviderScreen", "SessionPickerScreen", "SkillsScreen", "UnregisterModelScreen",
    "UnregisterProviderScreen",
    "_InlineConfirm", "_InstantTabs", "_ListPickerScreen", "_SessionRow", "_entry_row",
    "_error_text", "_model_row_text", "_rebuild_options",
]
