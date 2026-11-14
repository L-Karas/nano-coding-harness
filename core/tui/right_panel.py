"""右栏折叠 / 宽度状态：折叠态、宽度百分比与「用户手动展开」意图的唯一真值。

CSS 只认 `#right.-collapsed` 与内联宽度；本模块负责让两份表示始终由同一份状态派生，
并把三区活动状态的「全空 ↔ 非空」跳变翻译成自动展开 / 收回。
"""

from __future__ import annotations

from collections.abc import Callable

from core.tui.parts import Parts


class RightPanel:
    """右栏状态机：折叠 / 宽度 / 手动展开意图。"""

    DEFAULT_PCT = 20  # 未调整时的默认宽度
    STEP = 10  # Ctrl+←/→ 每档
    MIN_PCT = 20  # 低于此值即折叠
    MAX_PCT = 40  # 加宽封顶

    def __init__(self, parts: Parts, *, on_layout_changed: Callable[[], None]) -> None:
        """初始化右栏状态。

        Args:
            parts: 部件取件门面。
            on_layout_changed: 宽度变化后的回调（布局完成后重算页脚对齐）。
        """
        self._parts = parts
        self._on_layout_changed = on_layout_changed
        self._pct: int | None = None  # None = 未调整（走 CSS 默认 1fr）
        self._manual_expand = False  # 用户手动展开后，三区清空也不自动收回
        self._had_content = False  # 上次的三区活动快照（只在跳变时动作）

    @property
    def pct(self) -> int | None:
        """当前宽度百分比。

        Returns:
            Ctrl+←/→ 调整过的百分比；从未调整时为 None。
        """
        return self._pct

    @property
    def collapsed(self) -> bool:
        """右栏是否折叠。

        Returns:
            `#right` 带 `-collapsed` 类时为 True。
        """
        return self._parts.right.has_class("-collapsed")

    def set_collapsed(self, collapsed: bool, *, manual: bool = False) -> None:
        """折叠或展开右栏。

        Args:
            collapsed: 是否折叠；折叠只留左缘 »/« 标签一列。
            manual: 是否记录用户展开意图，使三区清空后不再自动收回。
        """
        right = self._parts.right
        right.set_class(collapsed, "-collapsed")
        glyph = self._parts.info_tab
        glyph.update("«" if collapsed else "»")
        glyph.tooltip = "Expand info panel" if collapsed else "Collapse info panel"
        if self._pct is not None:  # 内联宽度优先于 CSS 折叠规则，折叠/展开须同步设宽
            right.styles.width = 1 if collapsed else f"{self._pct}%"
        if manual:
            self._manual_expand = True
        self._on_layout_changed()

    def sync(self, non_empty: bool) -> None:
        """按三区活动状态自动展开 / 收回右栏。

        Args:
            non_empty: 三区是否有内容。

        仅在「全空 ↔ 非空」跳变时动作；非空跳变时折叠则自动展开（手动折叠只保持到这次跳变）；
        全空跳变时仅当用户没手动展开过才自动收回。
        """
        if non_empty == self._had_content:
            return
        self._had_content = non_empty
        if non_empty:
            if self.collapsed:
                self.set_collapsed(False)
        elif not self._manual_expand:
            self.set_collapsed(True)

    def widen(self) -> None:
        """加宽一档（折叠态先展开到上次宽度）。"""
        if self.collapsed:
            self._pct = self._pct or self.DEFAULT_PCT
            self.set_collapsed(False, manual=True)
            return
        self._resize(self.STEP)

    def narrow(self) -> None:
        """收窄一档；低于最小宽度即折叠。"""
        self._resize(-self.STEP)

    def _resize(self, delta: int) -> None:
        """按档位调整宽度。

        Args:
            delta: 宽度增量（百分比）。

        默认 20%，夹在 MIN_PCT–MAX_PCT；低于 MIN_PCT 即收成折叠态。
        """
        if self.collapsed:
            return  # 折叠态已是最低宽度
        pct = (self._pct or self.DEFAULT_PCT) + delta
        if pct < self.MIN_PCT:
            self.set_collapsed(True)
            return
        self._pct = min(self.MAX_PCT, pct)
        self._parts.right.styles.width = f"{self._pct}%"
        self._on_layout_changed()
