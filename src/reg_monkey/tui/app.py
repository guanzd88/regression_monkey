"""
OutputMappingApp - Output Mapping TUI 主应用
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

import pandas as pd
from textual.app import App
from textual.binding import Binding

from .state import SharedState
from .messages import (
    NavigateToTableList,
    NavigateToTableEditor,
    NavigateToResultBrowser,
    TableCreated,
    TableDeleted,
    ColumnsAdded,
    ConfigSaved,
    RequestQuit,
)
from .screens import (
    TableListScreen,
    TableEditorScreen,
    ResultBrowserScreen,
    HelpScreen,
    ConfirmQuitScreen,
)

if TYPE_CHECKING:
    from reg_monkey.output_mapping import OutputMapping
    from reg_monkey.code_executor import CodeExecutor


class OutputMappingApp(App):
    """
    Output Mapping TUI 主应用

    职责：
    1. 初始化共享状态（使用 SharedState.load() 恢复 TableMatrix）
    2. 管理 Screen 堆栈
    3. 处理全局消息
    4. 协调模块间通信
    """

    TITLE = "Output Mapping TUI"
    SUB_TITLE = "Manage regression output tables"

    CSS = """
    Screen {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True, priority=True),
        Binding("ctrl+q", "quit_app", "Quit", show=True, priority=True),
        Binding("f1", "help", "Help", show=True),
    ]

    def __init__(
        self,
        code_executor: Optional["CodeExecutor"] = None,
        results_df: Optional[pd.DataFrame] = None,
        config_path: str = "output_mapping.json",
        mapping: "OutputMapping | None" = None,
    ) -> None:
        """
        初始化应用

        Args:
            code_executor: CodeExecutor 实例（推荐），包含完整的执行上下文
            results_df: to_materialize() 返回的 DataFrame（向后兼容）
            config_path: 配置文件路径
            mapping: 可选的已有 OutputMapping 对象（已弃用，仅保留兼容性）
        """
        super().__init__()

        # 如果传入了 CodeExecutor，从中提取数据
        cached_plan = None
        cached_datasets: Dict[str, pd.DataFrame] = {}
        if code_executor is not None:
            results_df = code_executor.plan.to_materialize()
            datasets = getattr(code_executor, 'datasets', {})
            plan = code_executor.plan
        else:
            if results_df is None:
                results_df, cached_plan, cached_datasets = _load_cached_results_df()
            datasets = cached_datasets
            plan = cached_plan

        # 使用 SharedState.load() 初始化状态
        # 它会自动加载配置文件并恢复 TableMatrix 对象
        self.shared_state = SharedState.load(
            config_path=config_path,
            results_df=results_df if results_df is not None else pd.DataFrame(),
            datasets=datasets,
            plan=plan,
        )

    def on_mount(self) -> None:
        """应用启动时，显示表格列示界面"""
        self.push_screen(TableListScreen(self.shared_state))

    # ========== 消息处理 ==========

    def on_navigate_to_table_list(self, message: NavigateToTableList) -> None:
        """跳转到表格列示界面"""
        self.pop_screen()

    def on_navigate_to_table_editor(self, message: NavigateToTableEditor) -> None:
        """跳转到表格编辑界面"""
        self.shared_state.current_table_index = message.table_index
        self.push_screen(TableEditorScreen(self.shared_state))

    def on_navigate_to_result_browser(self, message: NavigateToResultBrowser) -> None:
        """跳转到结果浏览界面"""
        self.shared_state.current_table_index = message.table_index
        screen = ResultBrowserScreen(
            self.shared_state,
            section_filter=message.section_filter,
        )
        self.push_screen(screen)

    def on_columns_added(self, message: ColumnsAdded) -> None:
        """列已添加，返回到表格编辑界面"""
        self.shared_state.mark_dirty()
        self.pop_screen()  # 关闭结果浏览界面
        table = self.shared_state.get_table(message.table_index)
        table_name = table.title if table else "Unknown"
        self.notify(f"Added {len(message.task_ids)} column(s) to {table_name}")

    def on_table_created(self, message: TableCreated) -> None:
        """新表格已创建"""
        self.shared_state.mark_dirty()
        # 跳转到编辑界面由 TableListScreen 处理

    def on_table_deleted(self, message: TableDeleted) -> None:
        """表格已删除"""
        self.shared_state.mark_dirty()

    def on_config_saved(self, message: ConfigSaved) -> None:
        """配置已保存"""
        self.notify(f"Configuration saved to {message.path}")

    def on_request_quit(self, message: RequestQuit) -> None:
        """处理退出请求"""
        if self.shared_state.dirty:
            self.push_screen(
                ConfirmQuitScreen(self.shared_state),
                callback=self._on_quit_confirmed,
            )
        else:
            self.exit()

    def _on_quit_confirmed(self, should_quit: bool) -> None:
        """退出确认回调"""
        if should_quit:
            self.exit()

    # ========== 全局操作 ==========

    def action_save(self) -> None:
        """保存配置"""
        self.shared_state.save()
        self.notify(f"Saved to {self.shared_state.config_path}")

    def action_quit_app(self) -> None:
        """退出应用"""
        self.post_message(RequestQuit())

    def action_help(self) -> None:
        """显示帮助"""
        self.push_screen(HelpScreen())


def run_app(
    code_executor: Optional["CodeExecutor"] = None,
    results_df: Optional[pd.DataFrame] = None,
    config_path: str = "output_mapping.json",
    mapping: "OutputMapping | None" = None,
) -> None:
    """
    运行 TUI 应用的便捷函数

    Args:
        code_executor: CodeExecutor 实例（推荐），包含完整的执行上下文
        results_df: to_materialize() 返回的 DataFrame（向后兼容）
        config_path: 配置文件路径
        mapping: 可选的已有 OutputMapping 对象

    Usage:
        from reg_monkey.tui import run_app

        # 推荐方式：传入 CodeExecutor 实例
        ce = CodeExecutor(plan)
        ce.run()
        run_app(code_executor=ce)

        # 向后兼容：传入 DataFrame
        results_df = ce.plan.to_materialize()
        run_app(results_df=results_df)
    """
    if code_executor is None and results_df is None:
        results_df, cached_plan, cached_datasets = _load_cached_results_df()
        if results_df is None and cached_plan is None:
            raise ValueError("No CodeExecutor or cached results available for run_app")
        code_executor = None
        if cached_plan is not None:
            class _CachedExecutor:
                def __init__(self, plan, datasets):
                    self.plan = plan
                    self.datasets = datasets or {}

            code_executor = _CachedExecutor(cached_plan, cached_datasets)
            if results_df is None:
                try:
                    results_df = cached_plan.to_materialize()
                except Exception:
                    pass

    app = OutputMappingApp(
        code_executor=code_executor,
        results_df=results_df,
        config_path=config_path,
        mapping=mapping,
    )
    app.run()


def _load_cached_results_df() -> tuple[Optional[pd.DataFrame], Optional[Any], Dict[str, pd.DataFrame]]:
    try:
        from reg_monkey.code_executor import CodeExecutor
    except Exception:
        return None, None

    payload = CodeExecutor.load_cached_payload()
    if not payload:
        return None, None, {}

    plan = payload.get("plan")
    datasets = payload.get("datasets") or {}
    df: Optional[pd.DataFrame] = None
    if plan is not None and hasattr(plan, "to_materialize"):
        try:
            df = plan.to_materialize()
        except Exception:
            df = None

    if df is None:
        cache = payload.get("exec_results") or {}
        rows = [
            {"task_id": task_id, "exec_result": exec_result}
            for task_id, exec_result in cache.items()
        ]
        df = pd.DataFrame(rows) if rows else None

    return df, plan, datasets
