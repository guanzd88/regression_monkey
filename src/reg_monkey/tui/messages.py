"""
消息类型定义 - 用于界面间通信
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from textual.message import Message


# ========== 界面跳转消息 ==========


class NavigateToTableList(Message):
    """跳转到表格列示界面"""

    pass


@dataclass
class NavigateToTableEditor(Message):
    """跳转到表格编辑界面"""

    table_index: int

    def __init__(self, table_index: int) -> None:
        super().__init__()
        self.table_index = table_index


@dataclass
class NavigateToResultBrowser(Message):
    """跳转到结果浏览界面"""

    table_index: int  # 选中后添加到哪个表格的索引
    section_filter: str = ""  # 预设的 section 筛选

    def __init__(self, table_index: int, section_filter: str = "") -> None:
        super().__init__()
        self.table_index = table_index
        self.section_filter = section_filter


# ========== 数据操作消息 ==========


@dataclass
class TableCreated(Message):
    """新表格已创建"""

    table_index: int

    def __init__(self, table_index: int) -> None:
        super().__init__()
        self.table_index = table_index


@dataclass
class TableDeleted(Message):
    """表格已删除"""

    table_index: int

    def __init__(self, table_index: int) -> None:
        super().__init__()
        self.table_index = table_index


@dataclass
class TableRenamed(Message):
    """表格已重命名"""

    table_index: int
    new_name: str

    def __init__(self, table_index: int, new_name: str) -> None:
        super().__init__()
        self.table_index = table_index
        self.new_name = new_name


@dataclass
class ColumnsAdded(Message):
    """列已添加到表格"""

    table_index: int
    task_ids: List[str]
    parent_task_ids: List[Optional[str]]

    def __init__(
        self,
        table_index: int,
        task_ids: List[str],
        parent_task_ids: Optional[List[Optional[str]]] = None,
    ) -> None:
        super().__init__()
        self.table_index = table_index
        self.task_ids = task_ids
        if parent_task_ids is None:
            parent_task_ids = [None] * len(task_ids)
        self.parent_task_ids = parent_task_ids


@dataclass
class ColumnDeleted(Message):
    """列已从表格删除"""

    table_index: int
    task_id: str
    parent_task_id: Optional[str]

    def __init__(
        self,
        table_index: int,
        task_id: str,
        parent_task_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.table_index = table_index
        self.task_id = task_id
        self.parent_task_id = parent_task_id


@dataclass
class ConfigSaved(Message):
    """配置已保存"""

    path: str

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path


class RequestQuit(Message):
    """请求退出（可能需要确认保存）"""

    pass


class RequestRefresh(Message):
    """请求刷新当前界面"""

    pass
