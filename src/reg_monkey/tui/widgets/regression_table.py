"""
RegressionTableWidget - 回归结果表格渲染组件
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from rich.console import Console, ConsoleOptions, RenderResult
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

if TYPE_CHECKING:
    from reg_monkey.output_mapping import TableSpec
    from reg_monkey.tui.state import SharedState


class RegressionTableWidget(Static):
    """
    回归结果表格渲染组件

    用于在 TUI 中以学术论文风格渲染回归结果表格。
    """

    def __init__(
        self,
        table_spec: "TableSpec",
        shared_state: "SharedState",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.table_spec = table_spec
        self.shared_state = shared_state

    def render(self) -> RenderResult:
        """渲染表格"""
        return self._build_table()

    def _build_table(self) -> Table:
        """构建 Rich 表格"""
        table = Table(
            title=self.table_spec.name,
            show_header=True,
            header_style="bold",
            border_style="dim",
        )

        # 添加变量列
        table.add_column("Variable", style="bold")

        # 添加各回归结果列
        for i, col in enumerate(self.table_spec.columns):
            label = col.label or f"({i + 1})"
            table.add_column(label, justify="center")

        # 获取所有变量
        variables = self._collect_variables()

        # 填充数据
        for var in variables:
            row_data = [var]
            for col in self.table_spec.columns:
                coef_str = self._get_coefficient_string(col.task_id, var)
                row_data.append(coef_str)
            table.add_row(*row_data)

        # 添加统计信息行
        table.add_section()
        for stat_name in ["N", "R²", "Adj. R²"]:
            row_data = [stat_name]
            for col in self.table_spec.columns:
                stat_val = self._get_statistic(col.task_id, stat_name)
                row_data.append(stat_val)
            table.add_row(*row_data)

        return table

    def _collect_variables(self) -> List[str]:
        """收集所有列中出现的变量"""
        variables = []
        seen = set()

        for col in self.table_spec.columns:
            exec_result = self.shared_state.get_exec_result(col.task_id)
            if exec_result is None:
                continue

            coefficients = exec_result.get("forward_res", {}).get("coefficients")
            if coefficients is None:
                continue

            # 处理 DataFrame 或 List[dict]
            import pandas as pd

            if isinstance(coefficients, pd.DataFrame):
                for _, row in coefficients.iterrows():
                    var = row.get("Variable", "")
                    if var and var not in seen:
                        seen.add(var)
                        variables.append(var)
            elif isinstance(coefficients, list):
                for row in coefficients:
                    var = row.get("Variable", "")
                    if var and var not in seen:
                        seen.add(var)
                        variables.append(var)

        return variables

    def _get_coefficient_string(self, task_id: str, variable: str) -> str:
        """获取指定任务和变量的系数字符串"""
        exec_result = self.shared_state.get_exec_result(task_id)
        if exec_result is None:
            return ""

        coefficients = exec_result.get("forward_res", {}).get("coefficients")
        if coefficients is None:
            return ""

        # 查找变量
        import pandas as pd

        coef_row = None
        if isinstance(coefficients, pd.DataFrame):
            matches = coefficients[coefficients["Variable"] == variable]
            if not matches.empty:
                coef_row = matches.iloc[0].to_dict()
        elif isinstance(coefficients, list):
            for row in coefficients:
                if row.get("Variable") == variable:
                    coef_row = row
                    break

        if coef_row is None:
            return ""

        # 格式化系数
        estimate = coef_row.get("Estimate", "")
        std_error = coef_row.get("Std. Error", "")
        pval = coef_row.get("Pr(>|t|)", coef_row.get("p-value", ""))

        if not isinstance(estimate, (int, float)):
            return ""

        sig = self._get_significance(pval)
        coef_str = f"{estimate:.3f}{sig}"

        if isinstance(std_error, (int, float)):
            coef_str += f"\n({std_error:.3f})"

        return coef_str

    def _get_statistic(self, task_id: str, stat_name: str) -> str:
        """获取指定任务的统计量"""
        exec_result = self.shared_state.get_exec_result(task_id)
        if exec_result is None:
            return ""

        forward_res = exec_result.get("forward_res", {})

        # 映射统计量名称
        stat_map = {
            "N": "nobs",
            "R²": "r_squared",
            "Adj. R²": "adj_r_squared",
        }

        key = stat_map.get(stat_name, stat_name.lower())
        value = forward_res.get(key)

        if value is None:
            return ""

        if isinstance(value, (int, float)):
            if stat_name == "N":
                return f"{int(value):,}"
            return f"{value:.3f}"

        return str(value)

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
                return "+"
            return ""
        except (ValueError, TypeError):
            return ""

    def refresh_data(self) -> None:
        """刷新数据"""
        self.refresh()
