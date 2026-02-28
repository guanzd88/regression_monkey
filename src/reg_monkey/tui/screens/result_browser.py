"""ResultBrowserScreen - browse regression results."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import TYPE_CHECKING, Any, List, Optional, Set, Tuple

import pandas as pd
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Select, Static
from textual.widgets._input import Selection

from .base import BaseScreen
from .dialogs import TaskDetailModal, CoefficientsModal, StepwiseSelectModal
from ..messages import ColumnsAdded

if TYPE_CHECKING:
    from reg_monkey.tui.state import SharedState


@dataclass
class BrowserState:
    """Result browser runtime state."""

    df: pd.DataFrame  # original dataset
    filtered_df: pd.DataFrame  # filtered rows
    cursor_index: int = 0  # cursor position in filtered_df
    selected_indices: Set[int] = field(default_factory=set)  # selected row indices
    search_term: str = ""  # search keyword
    filters: dict = field(default_factory=dict)  # metadata filters (UI)
    command_filters: dict = field(default_factory=dict)  # metadata filters (:filter)
    result_conditions: List[str] = field(default_factory=list)  # result condition expressions
    result_condition_logic: str = "AND"  # combination logic for result filters


RESULT_CONDITION_PATTERN = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(\([+\-*]\))?\s*(<=|>=|=|<|>)\s*(.+)$")


class ResultBrowserScreen(BaseScreen):
    """Regression results browser screen."""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("g", "go_top", "Top", show=False),
        Binding("G", "go_bottom", "Bottom", show=False),
        Binding("ctrl+u", "page_up", "Page Up", show=False),
        Binding("ctrl+d", "page_down", "Page Down", show=False),
        Binding("space", "toggle_select", "Select"),
        Binding("c", "confirm_add", "Confirm Add"),
        Binding("a", "select_all", "Select All"),
        Binding("A", "deselect_all", "Deselect All", show=False),
        Binding("slash", "search", "Search"),
        Binding("f", "open_filter", "Filter"),
        Binding("i", "show_detail", "Detail"),
        Binding("o", "preview_coefficients", "Coefficients"),
        Binding("w", "open_stepwise", "Stepwise"),
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+shift+c", "copy_selection", "Copy"),
        Binding("escape", "go_back", "Back"),
        Binding("colon", "command_mode", "Command"),
        Binding("f1", "show_help", "Help"),
    ]

    CSS = """
    ResultBrowserScreen {
        layout: vertical;
    }

    #title_bar {
        height: 1;
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    #filter_bar {
        height: 3;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #filter_bar Input {
        width: 1fr;
    }

    #filter_bar Select {
        width: 15;
        margin-left: 1;
    }

    #result_table {
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

    def __init__(
        self, shared_state: "SharedState", section_filter: str = "", **kwargs
    ) -> None:
        super().__init__(shared_state, **kwargs)
        self.initial_section_filter = section_filter
        self.browser_state: Optional[BrowserState] = None

    def compose(self) -> ComposeResult:
        yield Header()
        matrix = self.shared_state.get_current_table()
        target = matrix.title if matrix else "Unknown"
        yield Static(f"RESULT BROWSER → {target}", id="title_bar")
        with Horizontal(id="filter_bar"):
            yield Input(id="search_input", placeholder="Filter...")
            yield Select(
                id="section_filter",
                options=[("All", "all")] + [(s, s) for s in self.shared_state.get_unique_sections()],
                value="all",
            )
        yield DataTable(id="result_table")
        yield Static(id="action_bar")
        yield Static(id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        # 初始化状态
        self.browser_state = BrowserState(
            df=self.results_df.copy(),
            filtered_df=self.results_df.copy(),
        )

        # 应用初始筛选
        if self.initial_section_filter:
            self.browser_state.filters["section"] = self.initial_section_filter
            section_select = self.query_one("#section_filter", Select)
            section_select.value = self.initial_section_filter

        # 设置表格
        table = self.query_one("#result_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("", "#", "parent_task_id", "task_id", "name", "section", "y", "X", "mark")

        self._apply_filters()
        self._update_action_bar()

        # 默认焦点放在结果表格
        table.focus()

    def _refresh_table(self) -> None:
        """刷新表格显示"""
        table = self.query_one("#result_table", DataTable)
        table.clear()

        if self.browser_state is None:
            return

        existing_pairs = self._build_existing_pairs()

        for display_idx, (orig_idx, row) in enumerate(self.browser_state.filtered_df.iterrows()):
            is_selected = display_idx in self.browser_state.selected_indices
            parent_id = row.get("parent_task_id") or ""
            task_id = row.get("task_id")
            source_id = row.get("source_task_id") or task_id
            has_existing = (parent_id, task_id) in existing_pairs or (parent_id, source_id) in existing_pairs
            select_marker = "[*]" if is_selected else "[ ]"
            prefix = f"{select_marker}{'*' if has_existing else ' '}"

            # 格式化 mark
            mark_val = row.get("mark", "")
            if mark_val is True or mark_val == "pass":
                mark_display = "pass"
            elif mark_val is False or mark_val == "fail":
                mark_display = "fail"
            else:
                mark_display = str(mark_val) if mark_val else "-"

            table.add_row(
                prefix,
                str(display_idx + 1),
                self._truncate(str(row.get("parent_task_id", "")), 15),
                self._truncate(str(row.get("task_id", "")), 20),
                str(row.get("name", "")),
                str(row.get("section", "")),
                str(row.get("y", "")),
                self._format_X(row.get("X", "")),
                mark_display,
                key=str(display_idx),
            )

        self._update_status()

    def _apply_filters(self) -> None:
        """应用所有筛选条件"""
        if self.browser_state is None:
            return

        df = self.browser_state.df.copy()

        # 1. 文本搜索
        if self.browser_state.search_term:
            term = self.browser_state.search_term.lower()
            mask = df.apply(
                lambda row: term in str(row.values).lower(),
                axis=1,
            )
            df = df[mask]

        # 2. 元数据下拉筛选
        combined_filters: dict[str, Any] = {}
        combined_filters.update(self.browser_state.command_filters)
        combined_filters.update(self.browser_state.filters)

        for col, value in combined_filters.items():
            if value and value != "all":
                if col not in df.columns:
                    continue
                series = df[col].fillna("").astype(str).str.strip()
                if isinstance(value, tuple) and value[0] == "contains":
                    # 模糊匹配
                    needle = str(value[1]).strip()
                    if not needle:
                        continue
                    df = df[series.str.contains(needle, case=False, na=False)]
                else:
                    # 精确匹配
                    target = str(value).strip()
                    df = df[series == target]

        # 3. 结果条件筛选
        if self.browser_state.result_conditions:
            mask = df.apply(
                lambda row: self._check_result_conditions(row),
                axis=1,
            )
            df = df[mask]

        self.browser_state.filtered_df = df.reset_index(drop=True)
        self.browser_state.selected_indices.clear()  # 清除选择
        self._refresh_table()

    def _check_result_conditions(self, row) -> bool:
        """检查单行是否满足所有结果条件"""
        if self.browser_state is None:
            return False

        exec_result = row.get("exec_result")
        if exec_result is None:
            return False

        # 解包 OpaqueExecResult
        if hasattr(exec_result, "value"):
            exec_result = exec_result.value

        # 使用 check_condition 方法
        try:
            from reg_monkey.task_obj import StandardRegTask

            task = StandardRegTask(
                y=row.get("y", ""),
                X=row.get("X", {}),
                model=row.get("model", "OLS"),
            )
            task._exec_result = exec_result

            results = []
            for condition in self.browser_state.result_conditions:
                try:
                    results.append(task.check_condition(condition))
                except Exception:
                    results.append(False)

            if self.browser_state.result_condition_logic == "AND":
                return all(results)
            else:
                return any(results)
        except Exception:
            return False

    def _parse_filter_command(self, cmd: str) -> None:
        """解析筛选命令"""
        if self.browser_state is None:
            return

        # 清除旧的筛选
        self.browser_state.command_filters.clear()
        self.browser_state.result_conditions.clear()
        self.browser_state.result_condition_logic = "AND"

        if cmd.strip().lower() == "clear":
            self._apply_filters()
            return

        # 检测 OR 逻辑
        if "||" in cmd:
            self.browser_state.result_condition_logic = "OR"
            parts = [p.strip() for p in cmd.split("||")]
        else:
            parts = [p.strip() for p in cmd.split("&&")]

        for part in parts:
            part = part.strip().strip("()")

            if part.startswith("result:"):
                # 结果条件
                condition = self._normalize_result_condition(part[7:].strip())
                self.browser_state.result_conditions.append(condition)

            elif "=" in part:
                # 精确匹配
                key, value = part.split("=", 1)
                self.browser_state.command_filters[key.strip()] = value.strip()

            elif "~" in part:
                # 模糊匹配
                key, value = part.split("~", 1)
                self.browser_state.command_filters[key.strip()] = (
                    "contains",
                    value.strip(),
                )

            elif ":" in part and not part.startswith("result"):
                # 包含
                key, value = part.split(":", 1)
                self.browser_state.command_filters[key.strip()] = (
                    "contains",
                    value.strip(),
                )

            elif part:
                # treat bare expressions as result conditions for convenience
                self.browser_state.result_conditions.append(part)

        self._apply_filters()

    def _normalize_result_condition(self, condition: str) -> str:
        """缺省省略符号时自动补成 (*)。"""
        match = RESULT_CONDITION_PATTERN.match(condition)
        if not match:
            return condition
        group, sign, comparator, value = match.groups()
        if sign:
            normalized = f"{group}{sign} {comparator} {value}"
        else:
            normalized = f"{group}(*) {comparator} {value}"
        return normalized.strip()

    def _truncate(self, text: str, max_len: int) -> str:
        if len(text) > max_len:
            return text[: max_len - 2] + ".."
        return text

    def _format_X(self, X) -> str:
        if isinstance(X, dict):
            groups = list(X.keys())
            if len(groups) == 1:
                return groups[0]
            return f"{groups[0]}+{len(groups)-1}"
        elif isinstance(X, list):
            if len(X) == 1:
                return X[0]
            return f"{X[0]}+{len(X)-1}"
        return str(X)[:15] if X else ""

    def _stringify_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except TypeError:
                return str(value)
        return str(value)

    def _update_action_bar(self) -> None:
        actions = (
            "[Space] Select  [C] Confirm Add  [/] Search  [F] Filter  [i] Detail  [o] Coef  [Ctrl+Shift+C] Copy  [Esc] Back"
        )
        self.query_one("#action_bar", Static).update(actions)

    def _update_status(self) -> None:
        if self.browser_state is None:
            return

        total = len(self.browser_state.df)
        filtered = len(self.browser_state.filtered_df)
        selected = len(self.browser_state.selected_indices)

        status = f"Selected: {selected}  |  Showing: {filtered}/{total}"
        if self.browser_state.result_conditions:
            cond_str = " & ".join(self.browser_state.result_conditions)
            status += f"  |  Result filter: {cond_str}"

        self.query_one("#status_bar", Static).update(status)

    def _build_existing_pairs(self, matrix: Optional[Any] = None) -> Set[Tuple[str, str]]:
        pairs: Set[Tuple[str, str]] = set()
        if matrix is None:
            matrix = self.shared_state.get_current_table()
        if not matrix:
            return pairs
        for col in matrix.columns:
            parent = getattr(col, "parent_task_id", None) or ""
            task_key = col.task_id
            source_key = getattr(col, "source_task_id", None) or task_key
            pairs.add((parent, task_key))
            pairs.add((parent, source_key))
        return pairs

    def _get_selected_row_index(self) -> Optional[int]:
        """获取当前选中行的索引（在 filtered_df 中）"""
        table = self.query_one("#result_table", DataTable)
        if self.browser_state is None:
            return None
        if table.cursor_row is not None:
            return min(table.cursor_row, len(self.browser_state.filtered_df) - 1)
        return None

    def _gather_rows_for_copy(self) -> List[pd.Series]:
        """按照展示顺序获取需要复制的行数据"""

        if self.browser_state is None:
            return []

        indices: List[int] = []
        if self.browser_state.selected_indices:
            indices = sorted(self.browser_state.selected_indices)

        cursor_idx = self._get_selected_row_index()
        if not indices and cursor_idx is not None:
            indices = [cursor_idx]

        rows: List[pd.Series] = []
        for idx in indices:
            if 0 <= idx < len(self.browser_state.filtered_df):
                rows.append(self.browser_state.filtered_df.iloc[idx])
        return rows

    def _format_row_for_copy(self, row: pd.Series) -> str:
        fields = [
            ("parent_task_id", row.get("parent_task_id")),
            ("task_id", row.get("task_id")),
            ("name", row.get("name")),
            ("section", row.get("section")),
            ("y", row.get("y")),
            ("X", row.get("X")),
            ("controls", row.get("controls")),
            ("model", row.get("model")),
            ("mark", row.get("mark")),
        ]
        return "\n".join(
            f"{key}: {self._stringify_value(value)}" for key, value in fields
        )

    # ========== 事件处理 ==========

    def on_input_changed(self, event: Input.Changed) -> None:
        """搜索框内容变化"""
        if event.input.id == "search_input" and self.browser_state:
            value = event.value.strip()

            # 如果以 :filter 开头，不实时更新（等待回车执行）
            if value.startswith(":filter"):
                return

            # 其他命令也不实时更新
            if value.startswith(":"):
                return

            # 普通文本搜索：实时更新
            self.browser_state.search_term = value
            self._apply_filters()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """回车时执行搜索/筛选"""
        if event.input.id == "search_input" and self.browser_state:
            value = event.value.strip()

            if value.startswith(":filter "):
                # 执行筛选命令
                cmd = value[8:]  # 移除 ":filter " 前缀
                self._parse_filter_command(cmd)
                self.notify_user(f"Filter applied: {cmd}")

            elif value == ":filter":
                # 空命令
                self.notify_user("Usage: :filter <conditions>", severity="information")

            elif value.startswith(":"):
                # 未知命令
                self.notify_user(f"Unknown command: {value}", severity="warning")

            else:
                # 普通搜索（回车确认）
                self.browser_state.search_term = value
                self._apply_filters()

    def on_select_changed(self, event: Select.Changed) -> None:
        """下拉筛选变化"""
        if self.browser_state is None:
            return

        if event.select.id == "section_filter":
            if event.value == "all":
                self.browser_state.filters.pop("section", None)
            else:
                self.browser_state.filters["section"] = event.value

        self._apply_filters()

    # ========== 导航操作 ==========

    def action_cursor_up(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.action_cursor_down()

    def action_go_top(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.move_cursor(row=0)

    def action_go_bottom(self) -> None:
        table = self.query_one("#result_table", DataTable)
        if self.browser_state:
            table.move_cursor(row=len(self.browser_state.filtered_df) - 1)

    def action_page_up(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.action_page_up()

    def action_page_down(self) -> None:
        table = self.query_one("#result_table", DataTable)
        table.action_page_down()

    # ========== 选择操作 ==========

    def action_toggle_select(self) -> None:
        """切换当前行选中状态"""
        idx = self._get_selected_row_index()
        if idx is None or self.browser_state is None:
            return

        if idx in self.browser_state.selected_indices:
            self.browser_state.selected_indices.remove(idx)
        else:
            row = self.browser_state.filtered_df.iloc[idx]
            parent_id = row.get("parent_task_id") or ""
            task_id = row.get("task_id")
            source_id = row.get("source_task_id") or task_id
            existing_pairs = self._build_existing_pairs()
            if (parent_id, task_id) in existing_pairs or (parent_id, source_id) in existing_pairs:
                self.notify_user("This result is already in the target table and cannot be selected again.", severity="information")
                return
            self.browser_state.selected_indices.add(idx)

        self._refresh_table()
        # 保持光标位置
        table = self.query_one("#result_table", DataTable)
        table.move_cursor(row=idx)

    def action_select_all(self) -> None:
        """全选"""
        if self.browser_state is None:
            return
        existing_pairs = self._build_existing_pairs()
        selectable = set()
        for idx, (_, row) in enumerate(self.browser_state.filtered_df.iterrows()):
            parent_id = row.get("parent_task_id") or ""
            task_id = row.get("task_id")
            source_id = row.get("source_task_id") or task_id
            if (parent_id, task_id) in existing_pairs or (parent_id, source_id) in existing_pairs:
                continue
            selectable.add(idx)
        self.browser_state.selected_indices = selectable
        self._refresh_table()

    def action_deselect_all(self) -> None:
        """取消全选"""
        if self.browser_state is None:
            return
        self.browser_state.selected_indices.clear()
        self._refresh_table()

    def action_confirm_add(self) -> None:
        """确认添加选中项到表格"""
        if self.browser_state is None:
            self.notify_user("Browser state not initialized", severity="error")
            return

        if not self.browser_state.selected_indices:
            self.notify_user("No items selected. Use [Space] to select rows first.", severity="warning")
            return

        # 获取当前表格
        table_index = self.shared_state.current_table_index
        if table_index is None:
            self.notify_user("No target table specified", severity="error")
            return

        matrix = self.shared_state.get_table(table_index)
        if not matrix:
            self.notify_user(f"Table not found", severity="error")
            return

        existing_pairs = self._build_existing_pairs(matrix)

        # 获取选中的行并添加到 TableMatrix
        selected_task_ids = []
        selected_parent_ids: List[Optional[str]] = []
        skipped_duplicates = 0
        refreshed_existing = 0
        for idx in sorted(self.browser_state.selected_indices):
            if idx < len(self.browser_state.filtered_df):
                row = self.browser_state.filtered_df.iloc[idx]
                task_id = row["task_id"]
                parent_id = row.get("parent_task_id") or ""
                pair_key = (parent_id, task_id)
                if pair_key in existing_pairs:
                    exec_result = self.shared_state.get_exec_result(task_id)
                    if exec_result is not None:
                        include_children = self.shared_state.should_display_stepwise(task_id)
                        matrix.refresh_stepwise_columns(task_id, exec_result, stepwise_enabled=include_children)
                        refreshed_existing += 1
                    skipped_duplicates += 1
                    continue
                selected_task_ids.append(task_id)
                selected_parent_ids.append(parent_id or None)

                # 获取 exec_result
                exec_result = self.shared_state.get_exec_result(task_id)

                # 添加列到 TableMatrix（包含完整 exec_result 和元数据）
                # 处理 category_controls：优先使用 mapping（效应名→字段名），否则从 list 构建
                cat_ctrl_mapping = row.get("category_controls_mapping")
                cat_ctrl_list = row.get("category_controls")

                # 优先使用 mapping（如果存在且非空）
                if isinstance(cat_ctrl_mapping, dict) and cat_ctrl_mapping:
                    cat_ctrl = cat_ctrl_mapping
                elif isinstance(cat_ctrl_list, list) and cat_ctrl_list:
                    # 列表格式：转换为 {field: field}（效应名=字段名）
                    cat_ctrl = {field: field for field in cat_ctrl_list}
                else:
                    cat_ctrl = {}

                matrix.add_column(
                    task_id=task_id,
                    label="",
                    exec_result=exec_result,
                    include_stepwise_children=self.shared_state.should_display_stepwise(task_id),
                    y=row.get("y", ""),
                    X=row.get("X", {}),
                    controls=row.get("controls", []),
                    model=row.get("model", "OLS"),
                    section=row.get("section", ""),
                    name=row.get("name", ""),
                    category_controls=cat_ctrl,
                    parent_task_id=row.get("parent_task_id"),
                )
                existing_pairs.add(pair_key)
                source_pair = (parent_id, row.get("source_task_id") or task_id)
                existing_pairs.add(source_pair)

        if not selected_task_ids:
            if refreshed_existing:
                self.notify_user("Updated existing columns with new stepwise settings.", severity="information")
            else:
                self.notify_user("No valid tasks selected", severity="warning")
            return

        self.notify_user(f"Adding {len(selected_task_ids)} column(s)...")
        self.post_message(ColumnsAdded(table_index, selected_task_ids, selected_parent_ids))
        if skipped_duplicates:
            note = f"Skipped {skipped_duplicates} duplicate selection(s)."
            if refreshed_existing:
                note += f" Refreshed {refreshed_existing} existing column(s)."
            self.notify_user(note, severity="information")

    # ========== 查看操作 ==========

    def action_show_detail(self) -> None:
        """显示任务详情"""
        idx = self._get_selected_row_index()
        if idx is None or self.browser_state is None:
            return

        if idx < len(self.browser_state.filtered_df):
            row = self.browser_state.filtered_df.iloc[idx].to_dict()
            self.app.push_screen(TaskDetailModal(task_row=row))

    def action_preview_coefficients(self) -> None:
        """预览系数表"""
        idx = self._get_selected_row_index()
        if idx is None or self.browser_state is None:
            return

        if idx < len(self.browser_state.filtered_df):
            row = self.browser_state.filtered_df.iloc[idx]
            task_id = row["task_id"]
            exec_result = self.shared_state.get_exec_result(task_id)
            self.app.push_screen(CoefficientsModal(task_id=task_id, exec_result=exec_result))

    def action_open_stepwise(self) -> None:
        """打开 Stepwise 步骤标记窗口"""
        idx = self._get_selected_row_index()
        if idx is None or self.browser_state is None:
            return

        if idx < len(self.browser_state.filtered_df):
            row = self.browser_state.filtered_df.iloc[idx]
            task_id = row.get("task_id")
            if not task_id:
                self.notify_user("Missing task_id for selected row.", severity="error")
                return
            exec_result = self.shared_state.get_exec_result(task_id)
            if exec_result is None:
                self.notify_user("No exec_result available for this task.", severity="warning")
                return
            if hasattr(exec_result, "value"):
                exec_result = exec_result.value
            steps = exec_result.get("stepwise_results") if isinstance(exec_result, dict) else None
            if not steps:
                self.notify_user("No stepwise results available for this task.", severity="warning")
                return
            task_name = row.get("name", task_id)
            previously_enabled = self.shared_state.should_display_stepwise(task_id)
            self.shared_state.set_stepwise_display(task_id, True)
            self.app.push_screen(
                StepwiseSelectModal(
                    shared_state=self.shared_state,
                    task_id=task_id,
                    task_name=task_name,
                    exec_result=exec_result,
                    auto_enabled=not previously_enabled,
                )
            )

    # ========== 其他操作 ==========

    def action_search(self) -> None:
        """聚焦搜索框"""
        self.query_one("#search_input", Input).focus()

    def action_open_filter(self) -> None:
        """激活筛选输入框并预填 :filter """
        search_input = self.query_one("#search_input", Input)
        search_input.value = ":filter "
        search_input.focus()
        cursor = len(search_input.value)

        def _position_cursor() -> None:
            search_input.cursor_position = cursor
            search_input.selection = Selection.cursor(cursor)

        self.call_after_refresh(_position_cursor)

    def action_refresh(self) -> None:
        """刷新数据"""
        if self.browser_state:
            self.browser_state.df = self.results_df.copy()
            self._apply_filters()
            self.notify_user("Refreshed")

    async def action_copy_selection(self) -> None:
        """复制选中行或输入框内容"""

        if await self._copy_input_if_focused():
            return

        if self.browser_state is None:
            self.notify_user("Browser state not initialized", severity="error")
            return

        rows = self._gather_rows_for_copy()
        if not rows:
            self.notify_user("No row is selected", severity="warning")
            return

        blocks = [self._format_row_for_copy(row) for row in rows]
        copy_text = "\n\n".join(blocks)
        await self._copy_text(
            copy_text,
            success_message=f"Copied {len(rows)} row(s) to clipboard.",
        )

    def action_go_back(self) -> None:
        """返回上一界面"""
        self.app.pop_screen()

    def action_show_help(self) -> None:
        """显示帮助"""
        from .dialogs import HelpScreen
        self.app.push_screen(HelpScreen())

    def action_command_mode(self) -> None:
        """命令模式（简化实现：通过 notify 提示）"""
        self.notify_user(
            "Command mode: Use search box with syntax like 'section=baseline && result:Size(+) < 0.05'",
            severity="information",
        )
