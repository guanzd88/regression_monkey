"""
TableEditorScreen - 表格编辑界面
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from .base import BaseScreen
from .dialogs import ConfirmDialog, RenameDialog
from ..messages import NavigateToTableList, NavigateToResultBrowser, ColumnDeleted

if TYPE_CHECKING:
    from reg_monkey.tui.state import SharedState
    from reg_monkey.table_matrix import TableMatrix


class TableEditorScreen(BaseScreen):
    """表格编辑界面"""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("a", "add_columns", "Add Columns"),
        Binding("d", "delete_column", "Delete"),
        Binding("r", "rename_label", "Rename Label"),
        Binding("shift+up", "move_up", "Move Up"),
        Binding("shift+down", "move_down", "Move Down"),
        Binding("K", "move_up", "Move Up", show=False),
        Binding("J", "move_down", "Move Down", show=False),
        Binding("escape", "go_back", "Back"),
        Binding("colon", "command_mode", "Command"),
        Binding("s", "save", "Save"),
        Binding("f1", "show_help", "Help"),
        Binding("f2", "show_debug", "Debug", show=False),
    ]

    CSS = """
    TableEditorScreen {
        layout: vertical;
    }

    #title_bar {
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    #preview_container {
        height: 50%;
        border: solid $primary;
        margin: 1;
        padding: 0;
        overflow-y: auto;
        overflow-x: auto;
    }

    #preview_title {
        text-style: bold;
        margin-bottom: 0;
        padding: 0 1;
        background: $surface;
    }

    #preview_content {
        padding: 0 1;
        overflow-x: auto;
    }

    #column_list {
        height: 1fr;
    }

    #action_bar {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
    }

    #status_bar {
        height: 1;
        background: $surface-darken-2;
        padding: 0 1;
    }
    """

    def __init__(self, shared_state: "SharedState", **kwargs) -> None:
        super().__init__(shared_state, **kwargs)

    @property
    def current_table(self) -> Optional["TableMatrix"]:
        """获取当前正在编辑的 TableMatrix"""
        return self.shared_state.get_current_table()

    def compose(self) -> ComposeResult:
        yield Header()
        matrix = self.current_table
        table_name = matrix.title if matrix else "Unknown"
        yield Static(f"TABLE EDITOR: {table_name}", id="title_bar")
        with Vertical(id="preview_container"):
            yield Static("Preview", id="preview_title")
            yield Static(id="preview_content")
        yield DataTable(id="column_list")
        yield Static(id="action_bar")
        yield Static(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#column_list", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "Label", "Parent", "Task ID", "Y", "X", "Model")
        self._refresh_columns()
        self._refresh_preview()
        self._update_action_bar()
        self._update_status()

    def on_screen_resume(self) -> None:
        """当从其他界面返回时刷新显示"""
        self._refresh_columns()
        self._refresh_preview()
        self._update_status()

    def _refresh_columns(self) -> None:
        """刷新列列表"""
        table = self.query_one("#column_list", DataTable)
        table.clear()

        matrix = self.current_table
        if not matrix:
            return

        for idx, col in enumerate(matrix.columns):
            # MatrixColumn 已经包含元数据
            table.add_row(
                str(idx + 1),
                col.label or f"({idx + 1})",
                self._truncate(col.parent_task_id or "", 15),
                self._truncate(col.task_id, 20),
                col.y,
                self._format_X(col.X),
                col.model,
                key=str(idx),
            )

    def _refresh_preview(self) -> None:
        """
        刷新表格预览

        使用 TableMatrix.to_txt() 进行实时渲染
        """
        matrix = self.current_table
        preview_widget = self.query_one("#preview_content", Static)

        if not matrix:
            preview_widget.update("[dim]No table selected[/dim]")
            return

        if matrix.is_empty:
            preview_widget.update(
                "[dim]No columns defined. Press [bold][A][/bold] to add columns.[/dim]"
            )
            return

        try:
            # 获取预览区域的可用宽度
            preview_container = self.query_one("#preview_container")
            available_width = preview_container.size.width - 4  # 减去边框和padding

            # 使用 TableMatrix.to_txt() 进行渲染
            config = self._get_table_config()
            rendered_text = matrix.to_txt(
                config=config,
                max_width=max(80, available_width),
                col_width=14,
            )

            preview_widget.update(rendered_text)

            # 根据文本宽度决定是否显示横向滚动条
            max_line_len = max((len(line) for line in rendered_text.splitlines()), default=0)
            if max_line_len > available_width:
                preview_widget.styles.width = max_line_len + 4
                preview_widget.styles.overflow_x = "scroll"
            else:
                preview_widget.styles.width = "auto"
                preview_widget.styles.overflow_x = "hidden"

        except Exception as e:
            # 渲染失败时显示错误信息和调试信息
            import traceback
            debug_info = ""
            if matrix and hasattr(matrix, 'debug_columns'):
                try:
                    debug_info = f"\n\n[dim]Debug info:\n{matrix.debug_columns()}[/dim]"
                except Exception:
                    pass
            preview_widget.update(f"[red]Preview error: {e}[/red]{debug_info}")

    def _get_table_config(self):
        """
        获取表格配置

        优先从 SharedState 获取配置路径，否则使用默认配置
        """
        from reg_monkey.table_config import TableConfig

        # 尝试从 shared_state 获取配置路径
        config_path = getattr(self.shared_state, 'config_path', None)
        if config_path:
            try:
                return TableConfig.load(config_path)
            except Exception:
                pass

        return TableConfig()

    def _truncate(self, text: str, max_len: int) -> str:
        """截断文本"""
        if len(text) > max_len:
            return text[: max_len - 2] + ".."
        return text

    def _format_X(self, X) -> str:
        """格式化 X 字段"""
        if isinstance(X, dict):
            groups = list(X.keys())
            if not groups:
                return ""
            if len(groups) == 1:
                return groups[0]
            return f"{groups[0]}+{len(groups)-1}"
        elif isinstance(X, list):
            if not X:
                return ""
            if len(X) == 1:
                return X[0]
            return f"{X[0]}+{len(X)-1}"
        return str(X)[:15] if X else ""

    def _update_action_bar(self) -> None:
        actions = "[A] Add Columns  [D] Delete  [R] Rename  [Shift+↑↓] Reorder  [Esc] Back"
        self.query_one("#action_bar", Static).update(actions)

    def _update_status(self) -> None:
        matrix = self.current_table
        if matrix:
            count = matrix.num_columns
            dirty = " [modified]" if self.shared_state.dirty else ""
            status = f"Columns: {count}  |  File: {matrix.filename}{dirty}"
        else:
            status = "No table selected"
        self.query_one("#status_bar", Static).update(status)

    def _get_selected_index(self) -> Optional[int]:
        """获取当前选中的列索引"""
        table = self.query_one("#column_list", DataTable)
        matrix = self.current_table
        if table.cursor_row is not None and matrix and matrix.columns:
            return min(table.cursor_row, len(matrix.columns) - 1)
        return None

    # ========== 导航操作 ==========

    def action_cursor_up(self) -> None:
        table = self.query_one("#column_list", DataTable)
        table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#column_list", DataTable)
        table.action_cursor_down()

    # ========== 列操作 ==========

    def action_add_columns(self) -> None:
        """跳转到结果浏览界面添加列"""
        if self.shared_state.current_table_index is not None:
            self.post_message(
                NavigateToResultBrowser(
                    table_index=self.shared_state.current_table_index
                )
            )

    def action_delete_column(self) -> None:
        """删除选中的列"""
        idx = self._get_selected_index()
        matrix = self.current_table
        if idx is None or not matrix:
            return

        col = matrix.columns[idx]
        self.app.push_screen(
            ConfirmDialog(
                title="DELETE COLUMN",
                message=f'Delete column "{col.label or col.task_id}"?',
            ),
            self._on_delete_confirmed,
        )

    def _on_delete_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            idx = self._get_selected_index()
            matrix = self.current_table
            table_index = self.shared_state.current_table_index
            if idx is not None and matrix and table_index is not None:
                col = matrix.columns[idx]
                matrix.remove_column_at(idx)
                self.shared_state.mark_dirty()
                self._refresh_columns()
                self._refresh_preview()
                self._update_status()
                self.post_message(
                    ColumnDeleted(table_index, col.task_id, col.parent_task_id)
                )
                self.notify_user("Column deleted")

    def action_rename_label(self) -> None:
        """重命名列标签"""
        idx = self._get_selected_index()
        matrix = self.current_table
        if idx is None or not matrix:
            return

        col = matrix.columns[idx]
        self.app.push_screen(
            RenameDialog(current_name=col.label or f"({idx + 1})"),
            self._on_label_renamed,
        )

    def _on_label_renamed(self, new_label: Optional[str]) -> None:
        if new_label:
            idx = self._get_selected_index()
            matrix = self.current_table
            if idx is not None and matrix:
                col = matrix.columns[idx]
                matrix.rename_column(col.task_id, new_label, col.parent_task_id)
                self.shared_state.mark_dirty()
                self._refresh_columns()
                self._refresh_preview()

    def action_move_up(self) -> None:
        """上移列"""
        idx = self._get_selected_index()
        matrix = self.current_table
        if idx is None or not matrix or idx == 0:
            return

        matrix.move_column(idx, idx - 1)
        self.shared_state.mark_dirty()
        self._refresh_columns()
        self._refresh_preview()
        # 移动光标
        table = self.query_one("#column_list", DataTable)
        table.move_cursor(row=idx - 1)

    def action_move_down(self) -> None:
        """下移列"""
        idx = self._get_selected_index()
        matrix = self.current_table
        if idx is None or not matrix or idx >= len(matrix.columns) - 1:
            return

        matrix.move_column(idx, idx + 1)
        self.shared_state.mark_dirty()
        self._refresh_columns()
        self._refresh_preview()
        # 移动光标
        table = self.query_one("#column_list", DataTable)
        table.move_cursor(row=idx + 1)

    # ========== 其他操作 ==========

    def action_go_back(self) -> None:
        """返回表格列示界面"""
        self.post_message(NavigateToTableList())

    def action_save(self) -> None:
        """保存配置"""
        self.shared_state.save()
        self._update_status()
        self.notify_user(f"Saved to {self.shared_state.config_path}")

    def action_show_help(self) -> None:
        """显示帮助"""
        from .dialogs import HelpScreen
        self.app.push_screen(HelpScreen())

    def action_command_mode(self) -> None:
        """命令模式"""
        self.notify_user("Command mode not implemented yet", severity="warning")

    def action_show_debug(self) -> None:
        """显示调试信息"""
        matrix = self.current_table
        if matrix and hasattr(matrix, 'debug_columns'):
            preview_widget = self.query_one("#preview_content", Static)
            preview_widget.update(f"[dim]{matrix.debug_columns()}[/dim]")
