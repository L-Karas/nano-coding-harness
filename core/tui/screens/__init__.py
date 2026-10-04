"""TUI 弹窗集合：base（基础设施）+ session / provider / model / skills / mcp / login / fork。

core.tui.widgets 仍转出常用弹窗名，兼容历史导入。
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
