"""
对话框组件
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Center
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Input, Label, Select, Static, DataTable
from textual.binding import Binding

if TYPE_CHECKING:
    from reg_monkey.tui.state import SharedState


class NewTableDialog(ModalScreen[Optional[dict]]):
    """新建表格对话框"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    NewTableDialog {
        align: center middle;
    }

    #dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: 3;
        margin-bottom: 1;
    }

    .form-row Label {
        width: 15;
    }

    .form-row Input {
        width: 1fr;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .button-row Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("CREATE NEW TABLE", classes="dialog-title")
            with Horizontal(classes="form-row"):
                yield Label("Table Name:")
                yield Input(id="name_input", placeholder="Enter table name...")
            with Horizontal(classes="form-row"):
                yield Label("Description:")
                yield Input(id="desc_input", placeholder="Optional description...")
            with Horizontal(classes="form-row"):
                yield Label("Filename:")
                yield Input(id="file_input", value="table_new.xlsx")
            with Horizontal(classes="button-row"):
                yield Button("Create", id="btn_create", variant="primary")
                yield Button("Cancel", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create":
            name = self.query_one("#name_input", Input).value.strip()
            if name:
                self.dismiss(
                    {
                        "name": name,
                        "description": self.query_one("#desc_input", Input).value,
                        "filename": self.query_one("#file_input", Input).value
                        or "table.xlsx",
                    }
                )
            else:
                self.notify("Table name is required", severity="error")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    """确认对话框"""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmDialog {
        align: center middle;
    }

    #dialog {
        width: 50;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }

    .dialog-message {
        text-align: center;
        margin: 1 0;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .button-row Button {
        margin: 0 1;
    }
    """

    def __init__(
        self, title: str = "Confirm", message: str = "Are you sure?", **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.dialog_title = title
        self.dialog_message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.dialog_title, classes="dialog-title")
            yield Static(self.dialog_message, classes="dialog-message")
            with Horizontal(classes="button-row"):
                yield Button("[Y] Yes", id="btn_yes", variant="error")
                yield Button("[N] No", id="btn_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RenameDialog(ModalScreen[Optional[str]]):
    """重命名对话框"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    RenameDialog {
        align: center middle;
    }

    #dialog {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: 3;
        margin-bottom: 1;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .button-row Button {
        margin: 0 1;
    }
    """

    def __init__(self, current_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("RENAME", classes="dialog-title")
            yield Static(f"Current: {self.current_name}")
            with Horizontal(classes="form-row"):
                yield Label("New name:")
                yield Input(id="new_name", value=self.current_name)
            with Horizontal(classes="button-row"):
                yield Button("Confirm", id="btn_confirm", variant="primary")
                yield Button("Cancel", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_confirm":
            new_name = self.query_one("#new_name", Input).value.strip()
            if new_name and new_name != self.current_name:
                self.dismiss(new_name)
            else:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class EditDescriptionDialog(ModalScreen[Optional[str]]):
    """编辑描述对话框"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    EditDescriptionDialog {
        align: center middle;
    }

    #dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: 3;
        margin-bottom: 1;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, current_desc: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self.current_desc = current_desc

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("EDIT DESCRIPTION", classes="dialog-title")
            with Horizontal(classes="form-row"):
                yield Input(id="desc_input", value=self.current_desc)
            with Horizontal(classes="button-row"):
                yield Button("Save", id="btn_save", variant="primary")
                yield Button("Cancel", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_save":
            new_desc = self.query_one("#desc_input", Input).value
            self.dismiss(new_desc)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TaskDetailModal(ModalScreen):
    """任务详情弹出面板"""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("space", "toggle_select", "Toggle Select"),
        Binding("o", "preview_coefficients", "View Coefficients"),
    ]

    CSS = """
    TaskDetailModal {
        align: center middle;
    }

    #dialog {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .section-title {
        text-style: bold;
        color: $primary;
        margin-top: 1;
    }

    .detail-row {
        margin-left: 2;
    }
    """

    def __init__(self, task_row: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task_row = task_row

    def compose(self) -> ComposeResult:
        row = self.task_row
        with Vertical(id="dialog"):
            yield Static("TASK DETAIL", classes="dialog-title")

            yield Static(f"Task ID:      {row.get('task_id', 'N/A')}", classes="detail-row")
            yield Static(f"Parent Task:  {row.get('parent_task_id', 'N/A')}", classes="detail-row")
            yield Static(f"Name:         {row.get('name', 'N/A')}", classes="detail-row")
            yield Static(f"Section:      {row.get('section', 'N/A')}", classes="detail-row")

            yield Static("─── Model ───", classes="section-title")
            yield Static(f"Model:        {row.get('model', 'N/A')}", classes="detail-row")
            yield Static(f"Dataset:      {row.get('dataset', 'N/A')}", classes="detail-row")
            yield Static(f"Y:            {row.get('y', 'N/A')}", classes="detail-row")
            yield Static(f"X:            {row.get('X', 'N/A')}", classes="detail-row")
            yield Static(f"Controls:     {row.get('controls', 'N/A')}", classes="detail-row")
            yield Static(f"FE:           {row.get('category_controls', 'N/A')}", classes="detail-row")
            yield Static(f"Subset:       {row.get('subset', 'N/A')}", classes="detail-row")

            yield Static("─── Result ───", classes="section-title")
            yield Static(f"Mark:         {row.get('mark', 'N/A')}", classes="detail-row")

            yield Static("")
            yield Static("[Esc] Close    [Space] Toggle Select    [o] View Coefficients")

    def action_close(self) -> None:
        self.dismiss()


class StepwiseSelectModal(ModalScreen[bool]):
    """Stepwise 步骤选择对话框"""

    BINDINGS = [
        Binding("space", "toggle_mark", "Toggle"),
        Binding("a", "select_all", "All"),
        Binding("n", "clear_all", "None"),
        Binding("enter", "confirm", "Apply"),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    StepwiseSelectModal {
        align: center middle;
    }

    #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #step_table {
        height: 1fr;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .button-row Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        shared_state,
        task_id: str,
        task_name: str,
        exec_result: Optional[dict],
        auto_enabled: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.shared_state = shared_state
        self.task_id = task_id
        self.task_name = task_name or task_id
        self.exec_steps = list((exec_result or {}).get("stepwise_results") or [])
        self.show_columns = bool(shared_state.should_display_stepwise(task_id))
        self.auto_enabled = bool(auto_enabled)
        self.rows: List[dict] = []
        max_step = None
        for entry in self.exec_steps:
            if not isinstance(entry, dict):
                continue
            try:
                step_idx = int(entry.get("step", len(self.rows)))
            except Exception:
                continue
            if max_step is None or step_idx > max_step:
                max_step = step_idx

        for entry in self.exec_steps:
            if not isinstance(entry, dict):
                continue
            try:
                step_idx = int(entry.get("step", len(self.rows)))
            except Exception:
                continue
            if max_step is not None and step_idx == max_step:
                continue  # full controls 始终输出，不在选择窗口显示
            controls = entry.get("controls_included") or []
            controls_display = ", ".join(controls) if controls else "(none)"
            result_block = entry.get("result") or {}
            observations = result_block.get("Observations") or result_block.get("n_obs") or result_block.get("n")
            r2 = result_block.get("R-squared") or result_block.get("R_squared") or result_block.get("r_squared")
            if isinstance(r2, (int, float)):
                r2_display = f"{r2:.4f}"
            else:
                r2_display = str(r2) if r2 is not None else ""
            label_clean = self._clean_step_label(entry.get("label"), step_idx)
            self.rows.append(
                {
                    "index": step_idx,
                    "label": label_clean,
                    "controls": controls_display,
                    "observations": str(observations) if observations is not None else "",
                    "r_squared": r2_display,
                    "marked": bool(entry.get("export_marked", False)),
                }
            )

        if self.auto_enabled:
            self.show_columns = True

        if not self.show_columns or self.auto_enabled:
            for row in self.rows:
                row["marked"] = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"STEPWISE STEPS → {self.task_name}", classes="dialog-title")
            yield Static(self._display_status_text(), id="stepwise_status")
            if not self.rows:
                yield Static("[yellow]No stepwise results for this task.[/yellow]")
            else:
                yield DataTable(id="step_table")
                yield Static("[Space] Toggle  [A] All  [N] None  [Enter] Apply  [Esc] Cancel")
            with Horizontal(classes="button-row"):
                yield Button(self._toggle_button_label(), id="btn_toggle_stepwise")
                yield Button("Apply", id="btn_apply", variant="primary")
                yield Button("Cancel", id="btn_cancel")

    def on_mount(self) -> None:
        if not self.rows:
            self._update_status()
            return
        table = self.query_one("#step_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Sel", "Step", "Label", "Controls", "N", "R^2")
        self._refresh_table()
        table.focus()
        self._update_status()

    def _refresh_table(self) -> None:
        if not self.rows:
            return
        table = self.query_one("#step_table", DataTable)
        table.clear()
        for idx, row in enumerate(self.rows):
            prefix = "[*]" if row["marked"] else "[ ]"
            table.add_row(
                prefix,
                str(row["index"]),
                self._truncate(row["label"], 30),
                self._truncate(row["controls"], 30),
                row["observations"],
                row["r_squared"],
                key=str(idx),
            )

    def _get_cursor_index(self) -> Optional[int]:
        if not self.rows:
            return None
        table = self.query_one("#step_table", DataTable)
        if table.cursor_row is None:
            return None
        return min(table.cursor_row, len(self.rows) - 1)

    def action_toggle_mark(self) -> None:
        idx = self._get_cursor_index()
        if idx is None:
            return
        self.rows[idx]["marked"] = not self.rows[idx]["marked"]
        self._refresh_table()

    def action_select_all(self) -> None:
        for row in self.rows:
            row["marked"] = True
        self._refresh_table()

    def action_clear_all(self) -> None:
        for row in self.rows:
            row["marked"] = False
        self._refresh_table()

    def _apply_marks(self) -> None:
        self.shared_state.set_stepwise_display(self.task_id, self.show_columns)
        if not self.rows:
            return
        marks = {row["index"]: row["marked"] for row in self.rows}
        for step_idx, marked in marks.items():
            self.shared_state.set_stepwise_mark(self.task_id, step_idx, marked)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_apply":
            self.action_confirm()
        elif event.button.id == "btn_toggle_stepwise":
            self.show_columns = not self.show_columns
            self._update_status()
        else:
            self.action_cancel()

    def action_confirm(self) -> None:
        self._apply_marks()
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _truncate(self, text: str, width: int) -> str:
        text = text or ""
        if len(text) <= width:
            return text
        if width <= 3:
            return text[:width]
        return text[: width - 2] + ".."

    def _toggle_button_label(self) -> str:
        return "Disable Columns" if self.show_columns else "Enable Columns"

    def _display_status_text(self) -> str:
        state = "ON" if self.show_columns else "OFF"
        return f"Stepwise columns: [bold]{state}[/bold]"

    def _update_status(self) -> None:
        status = self.query_one("#stepwise_status", Static)
        status.update(self._display_status_text())
        btn = self.query_one("#btn_toggle_stepwise", Button)
        btn.label = self._toggle_button_label()

    def _clean_step_label(self, label: Any, step_idx: int) -> str:
        text = str(label or f"Step {step_idx}")
        text = text.strip().replace("\n", "")
        if text.startswith("[1]"):
            text = text[3:].strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        return text or f"Step {step_idx}"


class CoefficientsModal(ModalScreen):
    """系数预览弹出面板"""

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    CSS = """
    CoefficientsModal {
        align: center middle;
    }

    #dialog {
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #coef_table {
        height: 1fr;
    }
    """

    def __init__(self, task_id: str, exec_result: Any, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task_id = task_id
        self.exec_result = exec_result

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"COEFFICIENTS: {self.task_id}", classes="dialog-title")
            yield DataTable(id="coef_table")
            yield Static("[Esc] Close    [↑↓] Scroll")

    def _get_field(self, row, *keys, default=""):
        """尝试多个可能的字段名，返回第一个非 None 的值"""
        for k in keys:
            v = row.get(k) if isinstance(row, dict) else getattr(row, k, None)
            if v is not None:
                return v
        return default

    def _format_number(self, val, decimals=4):
        """格式化数字"""
        if isinstance(val, (int, float)):
            return f"{val:.{decimals}f}"
        return str(val) if val else ""

    def on_mount(self) -> None:
        table = self.query_one("#coef_table", DataTable)
        table.add_columns("Variable", "Estimate", "Std.Error", "t-value", "p-value", "Sig")

        if self.exec_result is None:
            table.add_row("No results", "", "", "", "", "")
            return

        # 解包 OpaqueExecResult
        result = self.exec_result
        if hasattr(result, "value"):
            result = result.value

        # 获取系数表
        coefficients = result.get("forward_res", {}).get("coefficients")
        if coefficients is None:
            table.add_row("No coefficients", "", "", "", "", "")
            return

        # 处理 DataFrame 或 List[dict]
        import pandas as pd

        if isinstance(coefficients, pd.DataFrame):
            for _, row in coefficients.iterrows():
                row_dict = row.to_dict()

                variable = self._get_field(row_dict, "Variable", "variable", "term", "name")
                estimate = self._get_field(row_dict, "Estimate", "estimate", "coef", "coefficient")
                std_error = self._get_field(row_dict, "Std_Error", "Std. Error", "Std.Error", "std.error", "se", "stderr")
                t_value = self._get_field(row_dict, "T_Value", "t value", "t-value", "tvalue", "t", "t_value")
                p_value = self._get_field(row_dict, "P_Value", "Pr(>|t|)", "p-value", "pvalue", "p", "p_value", "Pr(>|z|)")

                sig = self._get_significance(p_value)

                table.add_row(
                    str(variable),
                    self._format_number(estimate, 4),
                    self._format_number(std_error, 4),
                    self._format_number(t_value, 3),
                    self._format_number(p_value, 4),
                    sig,
                )
        elif isinstance(coefficients, list):
            for row in coefficients:
                variable = self._get_field(row, "Variable", "variable", "term", "name")
                estimate = self._get_field(row, "Estimate", "estimate", "coef", "coefficient")
                std_error = self._get_field(row, "Std_Error", "Std. Error", "Std.Error", "std.error", "se", "stderr")
                t_value = self._get_field(row, "T_Value", "t value", "t-value", "tvalue", "t", "t_value")
                p_value = self._get_field(row, "P_Value", "Pr(>|t|)", "p-value", "pvalue", "p", "p_value", "Pr(>|z|)")

                sig = self._get_significance(p_value)

                table.add_row(
                    str(variable),
                    self._format_number(estimate, 4),
                    self._format_number(std_error, 4),
                    self._format_number(t_value, 3),
                    self._format_number(p_value, 4),
                    sig,
                )

    def _get_significance(self, pval: Any) -> str:
        """获取显著性标记"""
        try:
            p = float(pval)
            if p < 0.001:
                return "***"
            elif p < 0.01:
                return "**"
            elif p < 0.05:
                return "*"
            elif p < 0.1:
                return "."
            return ""
        except (ValueError, TypeError):
            return ""

    def action_close(self) -> None:
        self.dismiss()


class HelpScreen(ModalScreen):
    """帮助界面"""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    CSS = """
    HelpScreen {
        align: center middle;
    }

    #dialog {
        width: 70%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .section-title {
        text-style: bold;
        color: $primary;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("HELP", classes="dialog-title")

            yield Static("─── Global ───", classes="section-title")
            yield Static("  Ctrl+S     Save configuration")
            yield Static("  Ctrl+Q     Quit application")
            yield Static("  F1 / ?     Show this help")
            yield Static("  Esc        Go back / Cancel")

            yield Static("─── Navigation ───", classes="section-title")
            yield Static("  ↑/k        Move up")
            yield Static("  ↓/j        Move down")
            yield Static("  gg         Go to first row")
            yield Static("  G          Go to last row")
            yield Static("  PgUp/PgDn  Page up/down")

            yield Static("─── Table List ───", classes="section-title")
            yield Static("  N          New table")
            yield Static("  Enter      Edit table")
            yield Static("  D          Delete table")
            yield Static("  R          Rename table")
            yield Static("  E          Edit description")

            yield Static("─── Table Editor ───", classes="section-title")
            yield Static("  A          Add columns from results")
            yield Static("  D          Delete column")
            yield Static("  Shift+↑/↓  Move column")

            yield Static("─── Result Browser ───", classes="section-title")
            yield Static("  Space      Toggle selection")
            yield Static("  Enter      Add selected to table")
            yield Static("  /          Search")
            yield Static("  F          Filter")
            yield Static("  i          View task detail")
            yield Static("  o          View coefficients")

            yield Static("")
            yield Static("[Esc] or [Q] to close")

    def action_close(self) -> None:
        self.dismiss()


class ConfirmQuitScreen(ModalScreen[bool]):
    """退出确认界面（有未保存修改时）"""

    BINDINGS = [
        Binding("y", "save_and_quit", "Save & Quit"),
        Binding("n", "quit_without_save", "Don't Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmQuitScreen {
        align: center middle;
    }

    #dialog {
        width: 50;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    .dialog-message {
        text-align: center;
        margin: 1 0;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .button-row Button {
        margin: 0 1;
    }
    """

    def __init__(self, shared_state: "SharedState", **kwargs) -> None:
        super().__init__(**kwargs)
        self.shared_state = shared_state

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("UNSAVED CHANGES", classes="dialog-title")
            yield Static(
                "You have unsaved changes.\nDo you want to save before quitting?",
                classes="dialog-message",
            )
            with Horizontal(classes="button-row"):
                yield Button("[Y] Save & Quit", id="btn_save", variant="primary")
                yield Button("[N] Don't Save", id="btn_nosave", variant="error")
                yield Button("[Esc] Cancel", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_save":
            self.shared_state.save()
            self.dismiss(True)
        elif event.button.id == "btn_nosave":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_save_and_quit(self) -> None:
        self.shared_state.save()
        self.dismiss(True)

    def action_quit_without_save(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ExportDialog(ModalScreen[Optional[dict]]):
    """导出对话框"""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ExportDialog {
        align: center middle;
    }

    #dialog {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    .form-row {
        height: 3;
        margin-bottom: 1;
    }

    .form-row Label {
        width: 22;
    }

    .form-row Input {
        width: 1fr;
    }

    .form-row Select {
        width: 1fr;
    }

    .button-row {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .button-row Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("EXPORT", classes="dialog-title")

            with Horizontal(classes="form-row"):
                yield Label("Table Output Path:")
                yield Input(id="table_path", value="./output/")

            with Horizontal(classes="form-row"):
                yield Label("Table Format:")
                yield Select(
                    id="table_format",
                    options=[
                        ("Excel (.xlsx)", "xlsx"),
                        ("Text (.txt)", "txt"),
                        ("LaTeX (.tex)", "latex"),
                    ],
                    value="xlsx",
                )

            with Horizontal(classes="form-row"):
                yield Label("Data & Code Path:")
                yield Input(id="data_path", value="./output/reproducibility/")

            with Horizontal(classes="button-row"):
                yield Button("Export Tables", id="btn_export_tables", variant="primary")
                yield Button("Export Data & Code", id="btn_export_data")
                yield Button("Cancel", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_export_tables":
            self.dismiss({
                "action": "export_tables",
                "table_path": self.query_one("#table_path", Input).value,
                "format": self.query_one("#table_format", Select).value,
            })
        elif event.button.id == "btn_export_data":
            self.dismiss({
                "action": "export_data",
                "data_path": self.query_one("#data_path", Input).value,
            })
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
