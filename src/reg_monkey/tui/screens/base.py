"""
BaseScreen - 子界面基类
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

import pandas as pd
from textual.screen import Screen
from textual.binding import Binding

if TYPE_CHECKING:
    from reg_monkey.table_matrix import TableMatrix
    from reg_monkey.tui.state import SharedState

from ..clipboard import ClipboardMixin

class BaseScreen(ClipboardMixin, Screen):
    """
    子界面基类
    - 持有共享状态引用
    - 提供通用方法
    """

    BINDINGS = [Binding("ctrl+shift+c", "copy_selection", "Copy", show=False)]

    def __init__(self, shared_state: "SharedState", **kwargs) -> None:
        super().__init__(**kwargs)
        self.shared_state = shared_state

    @property
    def tables(self) -> List["TableMatrix"]:
        """获取表格列表"""
        return self.shared_state.tables

    @property
    def results_df(self) -> pd.DataFrame:
        """获取结果 DataFrame"""
        return self.shared_state.results_df

    def notify_user(self, message: str, severity: str = "information") -> None:
        """显示通知"""
        self.app.notify(message, severity=severity)

    def go_back(self) -> None:
        """返回上一界面"""
        self.app.pop_screen()

    async def action_copy_selection(self) -> None:  # pragma: no cover - UI only
        handled = await self._copy_input_if_focused()
        if not handled:
            self.notify_user("Nothing to copy in the current widget", severity="warning")
