"""
TUI 界面模块
"""

from .base import BaseScreen
from .table_list import TableListScreen
from .table_editor import TableEditorScreen
from .result_browser import ResultBrowserScreen
from .dialogs import (
    NewTableDialog,
    ConfirmDialog,
    RenameDialog,
    EditDescriptionDialog,
    TaskDetailModal,
    CoefficientsModal,
    HelpScreen,
    ConfirmQuitScreen,
)

__all__ = [
    "BaseScreen",
    "TableListScreen",
    "TableEditorScreen",
    "ResultBrowserScreen",
    "NewTableDialog",
    "ConfirmDialog",
    "RenameDialog",
    "EditDescriptionDialog",
    "TaskDetailModal",
    "CoefficientsModal",
    "HelpScreen",
    "ConfirmQuitScreen",
]
