"""
Output Mapping TUI 模块

提供终端用户界面用于：
1. 浏览和筛选回归任务执行结果
2. 组合结果成多张输出表格
3. 编辑表格样式、调整列顺序
4. 导出配置供后续生成 Excel/LaTeX 表格

Usage:
    from reg_monkey.tui import OutputMappingApp, run_app

    # 方式一：使用便捷函数
    results_df = ce.plan.to_materialize()
    run_app(results_df)

    # 方式二：手动创建应用
    app = OutputMappingApp(results_df=results_df)
    app.run()
"""

from .app import OutputMappingApp, run_app
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

__all__ = [
    "OutputMappingApp",
    "run_app",
    "SharedState",
    "NavigateToTableList",
    "NavigateToTableEditor",
    "NavigateToResultBrowser",
    "TableCreated",
    "TableDeleted",
    "ColumnsAdded",
    "ConfigSaved",
    "RequestQuit",
]
