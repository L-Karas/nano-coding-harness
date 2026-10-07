"""TUI 弹窗集合：session / provider / model / skills / mcp / login / fork / settings。

公共入口只有各屏类；base 的骨架 / 混入 / 行渲染按需从 core.tui.screens.base 直接导入。
"""

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
from core.tui.screens.settings import SettingsScreen
from core.tui.screens.skills import SkillsScreen

__all__ = [
    "ApiKeyScreen", "EffortScreen", "ForkScreen", "LoginScreen", "MCPConfigScreen", "MCPServersScreen",
    "MCPToolsScreen", "ModelPickerScreen", "ProviderScreen", "RegisterModelScreen",
    "RegisterProviderScreen", "SessionPickerScreen", "SettingsScreen", "SkillsScreen",
    "UnregisterModelScreen", "UnregisterProviderScreen",
]
