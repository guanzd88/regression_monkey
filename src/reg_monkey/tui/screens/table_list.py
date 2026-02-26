"""
TableListScreen - 表格列示界面（默认入口）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from .base import BaseScreen
from .dialogs import ConfirmDialog, NewTableDialog, RenameDialog, EditDescriptionDialog
from ..messages import NavigateToTableEditor, TableCreated

if TYPE_CHECKING:
    from reg_monkey.tui.state import SharedState
    from reg_monkey.table_matrix import TableMatrix


class TableListScreen(BaseScreen):
    """表格列示界面 - TUI 默认入口"""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("g", "go_top", "Top", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("n", "new_table", "New Table"),
        Binding("o", "edit_table", "Open/Edit"),
        Binding("d", "delete_table", "Delete"),
        Binding("r", "rename_table", "Rename"),
        Binding("c", "copy_table", "Copy"),
        Binding("e", "edit_description", "Description"),
        Binding("colon", "command_mode", "Command"),
        Binding("s", "save", "Save"),
        Binding("q", "quit_app", "Quit"),
        Binding("x", "export", "Export"),
        Binding("f1", "show_help", "Help"),
        Binding("question_mark", "show_help", "Help", show=False),
    ]

    CSS = """
    TableListScreen {
        layout: vertical;
    }

    #title_bar {
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    #table_list {
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

    #empty_message {
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self, shared_state: "SharedState", **kwargs) -> None:
        super().__init__(shared_state, **kwargs)
        self.cursor_index = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("OUTPUT TABLE MANAGER", id="title_bar")
        yield DataTable(id="table_list")
        yield Static(id="action_bar")
        yield Static(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table_list", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "Table Name", "Description", "Columns")
        self._refresh_table()
        self._update_action_bar()

    def _refresh_table(self) -> None:
        """刷新表格显示"""
        table = self.query_one("#table_list", DataTable)
        table.clear()

        if not self.tables:
            self.query_one("#status_bar", Static).update(
                "No tables defined. Press [N] to create one."
            )
            return

        for idx, matrix in enumerate(self.tables):
            name_display = (
                matrix.title[:28] + ".." if len(matrix.title) > 30 else matrix.title
            )
            desc_display = (
                matrix.description[:38] + ".."
                if len(matrix.description) > 40
                else matrix.description
            )
            table.add_row(
                str(idx + 1),
                name_display,
                desc_display,
                str(matrix.num_columns),
                key=str(idx),
            )

        self._update_status()

    def _update_status(self) -> None:
        """更新状态栏"""
        count = len(self.tables)
        dirty = " [modified]" if self.shared_state.dirty else ""
        status = f"Tables: {count}{dirty}  |  {self.shared_state.config_path}"
        self.query_one("#status_bar", Static).update(status)

    def _update_action_bar(self) -> None:
        """更新操作提示栏"""
        actions = "[N] New  [O] Open  [D] Delete  [R] Rename  [X] Export  [S] Save  [Q] Quit"
        self.query_one("#action_bar", Static).update(actions)

    def _get_selected_index(self) -> Optional[int]:
        """获取当前选中的表格索引"""
        table = self.query_one("#table_list", DataTable)
        if table.cursor_row is not None and self.tables:
            return min(table.cursor_row, len(self.tables) - 1)
        return None

    def _get_selected_table(self) -> Optional["TableMatrix"]:
        """获取当前选中的表格"""
        idx = self._get_selected_index()
        if idx is not None:
            return self.shared_state.get_table(idx)
        return None

    # ========== 导航操作 ==========

    def action_cursor_up(self) -> None:
        table = self.query_one("#table_list", DataTable)
        table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#table_list", DataTable)
        table.action_cursor_down()

    def action_go_top(self) -> None:
        table = self.query_one("#table_list", DataTable)
        table.move_cursor(row=0)

    def action_go_bottom(self) -> None:
        table = self.query_one("#table_list", DataTable)
        if self.tables:
            table.move_cursor(row=len(self.tables) - 1)

    # ========== 表格操作 ==========

    def action_new_table(self) -> None:
        """新建表格"""
        self.app.push_screen(NewTableDialog(), self._on_new_table_created)

    def _on_new_table_created(self, result: Optional[dict]) -> None:
        if result:
            # 使用 SharedState.create_table() 创建 TableMatrix
            self.shared_state.create_table(
                title=result["name"],
                filename=result["filename"],
                description=result.get("description", ""),
            )
            self._refresh_table()
            # 跳转到编辑界面（使用新创建的表格索引）
            new_index = len(self.tables) - 1
            self.post_message(NavigateToTableEditor(new_index))

    def action_edit_table(self) -> None:
        """编辑选中的表格"""
        idx = self._get_selected_index()
        if idx is not None:
            self.post_message(NavigateToTableEditor(idx))

    def action_delete_table(self) -> None:
        """删除选中的表格"""
        idx = self._get_selected_index()
        if idx is None:
            return

        matrix = self.tables[idx]
        self.app.push_screen(
            ConfirmDialog(
                title="DELETE TABLE",
                message=f'Delete "{matrix.title}"?\n({matrix.num_columns} columns)\n\nThis cannot be undone.',
            ),
            self._on_delete_confirmed,
        )

    def _on_delete_confirmed(self, confirmed: bool) -> None:
        if confirmed:
            idx = self._get_selected_index()
            if idx is not None:
                self.shared_state.delete_table(idx)
                self._refresh_table()
                self.notify_user("Table deleted")

    def action_rename_table(self) -> None:
        """重命名表格"""
        matrix = self._get_selected_table()
        if matrix:
            self.app.push_screen(
                RenameDialog(current_name=matrix.title),
                self._on_rename_confirmed,
            )

    def _on_rename_confirmed(self, new_name: Optional[str]) -> None:
        if new_name:
            idx = self._get_selected_index()
            if idx is not None:
                self.tables[idx].title = new_name
                self.shared_state.mark_dirty()
                self._refresh_table()
                self.notify_user(f"Renamed to: {new_name}")

    def action_copy_table(self) -> None:
        """复制表格"""
        idx = self._get_selected_index()
        if idx is None:
            return

        matrix = self.tables[idx]
        new_name = f"{matrix.title}_copy"

        # 创建新表格
        new_matrix = self.shared_state.create_table(
            title=new_name,
            filename=f"{matrix.filename.rsplit('.', 1)[0]}_copy.{matrix.filename.rsplit('.', 1)[-1]}",
            description=matrix.description,
        )

        # 复制列
        for col in matrix.columns:
            new_matrix.add_column(
                task_id=col.task_id,
                label=col.label,
                exec_result=col.exec_result,
                flatten_heterogeneity=False,
                result_target=col.result_target,
                group_label=col.group_label,
                stepwise_index=col.stepwise_index,
                stepwise_label=col.stepwise_label,
                source_task_id=col.source_task_id,
                include_stepwise_children=col.stepwise_enabled,
                y=col.y,
                X=col.X,
                model=col.model,
                section=col.section,
                name=col.name,
                category_controls=col.category_controls,
            )

        self._refresh_table()
        self.notify_user(f"Copied as: {new_name}")

    def action_edit_description(self) -> None:
        """编辑表格描述"""
        idx = self._get_selected_index()
        if idx is None:
            return

        matrix = self.tables[idx]
        self.app.push_screen(
            EditDescriptionDialog(current_desc=matrix.description),
            self._on_description_edited,
        )

    def _on_description_edited(self, new_desc: Optional[str]) -> None:
        if new_desc is not None:
            idx = self._get_selected_index()
            if idx is not None:
                self.tables[idx].description = new_desc
                self.shared_state.mark_dirty()
                self._refresh_table()

    # ========== 其他操作 ==========

    def action_save(self) -> None:
        """保存配置"""
        self.shared_state.save()
        self._update_status()
        self.notify_user(f"Saved to {self.shared_state.config_path}")

    def action_quit_app(self) -> None:
        """退出应用"""
        from ..messages import RequestQuit
        self.post_message(RequestQuit())

    def action_show_help(self) -> None:
        """显示帮助"""
        from .dialogs import HelpScreen
        self.app.push_screen(HelpScreen())

    def on_screen_resume(self) -> None:
        """当从其他界面返回时刷新显示"""
        self._refresh_table()

    def action_export(self) -> None:
        """打开导出对话框"""
        from .dialogs import ExportDialog
        self.app.push_screen(ExportDialog(), self._on_export_result)

    def _on_export_result(self, result: Optional[dict]) -> None:
        """处理导出对话框返回结果"""
        if result is None:
            return

        try:
            from reg_monkey.export_service import ExportService
            from pathlib import Path

            # 创建 ExportService 实例（直接使用 SharedState 中的数据）
            service = ExportService(
                config_path=self.shared_state.config_path,
                results_df=self.shared_state.results_df,
                exec_results=self.shared_state.exec_results,
                tables=self.shared_state.tables,
                datasets=self.shared_state.datasets,
                plan=self.shared_state.plan,
            )

            if result["action"] == "export_tables":
                # 确保目录存在
                output_dir = Path(result["table_path"])
                output_dir.mkdir(parents=True, exist_ok=True)

                exported = service.export_tables(
                    output_dir=result["table_path"],
                    format=result["format"],
                )
                self.notify_user(f"Exported {len(exported)} table(s) to {result['table_path']}")

            elif result["action"] == "export_data":
                # 确保目录存在
                output_dir = Path(result["data_path"])
                output_dir.mkdir(parents=True, exist_ok=True)

                exported = service.export_data_and_code(output_dir=result["data_path"])
                dataset_count = len(exported.get("datasets", []))
                task_count = exported.get("total_tasks", 0)
                has_main_r = len(exported.get("code", [])) > 0

                if has_main_r:
                    msg = f"Exported {dataset_count} dataset(s), main.R with {task_count} regression(s)"
                else:
                    msg = f"Exported {dataset_count} dataset(s)"
                    if "message" in exported:
                        msg += f" | {exported['message']}"
                self.notify_user(msg)

        except Exception as e:
            import traceback
            self.notify_user(f"Export failed: {e}\n{traceback.format_exc()[:200]}", severity="error")

    def action_command_mode(self) -> None:
        """命令模式（暂未实现）"""
        self.notify_user("Command mode not implemented yet", severity="warning")
