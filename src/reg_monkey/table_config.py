"""
table_config.py

表格输出配置模块

提供回归结果表格输出的配置选项：
- 显著性水平（三星、两星、一星）
- 小数位数
- 系数排序规则
- 固定效应标签
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union


@dataclass
class SignificanceLevels:
    """显著性水平配置"""
    three_star: float = 0.01  # ***
    two_star: float = 0.05    # **
    one_star: float = 0.10    # *


@dataclass
class CoefficientSortConfig:
    """系数排序配置"""
    independent_vars_first: bool = True   # 自变量排在前面
    intercept_last: bool = True           # 截距项放最后
    alphabetical: bool = True             # 其他变量按字母排序


@dataclass
class FixedEffectLabels:
    """固定效应控制标签"""
    yes: str = "YES"
    no: str = "NO"


@dataclass
class TableConfig:
    """
    表格输出配置

    从 config.json 的 table_output 节读取配置

    示例 config.json:
    {
        "table_output": {
            "significance_levels": {
                "three_star": 0.01,
                "two_star": 0.05,
                "one_star": 0.10
            },
            "decimal_places": 3,
            "second_row_type": "std_error",
            "coefficient_sort": {
                "independent_vars_first": true,
                "intercept_last": true,
                "alphabetical": true
            },
            "fixed_effect_labels": {
                "yes": "YES",
                "no": "NO"
            },
            "thousands_separator": true,
            "show_blank_rows": true
        }
    }
    """

    significance_levels: SignificanceLevels = field(default_factory=SignificanceLevels)
    decimal_places: int = 3
    second_row_type: Literal["std_error", "t_stat"] = "std_error"
    coefficient_sort: CoefficientSortConfig = field(default_factory=CoefficientSortConfig)
    fixed_effect_labels: FixedEffectLabels = field(default_factory=FixedEffectLabels)

    # 格式选项
    thousands_separator: bool = True      # 观测数量添加千分位
    show_blank_rows: bool = True          # 结果区域前后显示空白行

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TableConfig":
        """从字典构建配置"""
        config = cls()

        if "significance_levels" in d:
            sl = d["significance_levels"]
            config.significance_levels = SignificanceLevels(
                three_star=sl.get("three_star", 0.01),
                two_star=sl.get("two_star", 0.05),
                one_star=sl.get("one_star", 0.10),
            )

        if "decimal_places" in d:
            config.decimal_places = d["decimal_places"]

        if "second_row_type" in d:
            config.second_row_type = d["second_row_type"]

        if "coefficient_sort" in d:
            cs = d["coefficient_sort"]
            config.coefficient_sort = CoefficientSortConfig(
                independent_vars_first=cs.get("independent_vars_first", True),
                intercept_last=cs.get("intercept_last", True),
                alphabetical=cs.get("alphabetical", True),
            )

        if "fixed_effect_labels" in d:
            fel = d["fixed_effect_labels"]
            config.fixed_effect_labels = FixedEffectLabels(
                yes=fel.get("yes", "YES"),
                no=fel.get("no", "NO"),
            )

        if "thousands_separator" in d:
            config.thousands_separator = d["thousands_separator"]

        if "show_blank_rows" in d:
            config.show_blank_rows = d["show_blank_rows"]

        return config

    @classmethod
    def load(cls, config_path: Union[str, Path]) -> "TableConfig":
        """从 config.json 加载配置"""
        path = Path(config_path)
        if not path.exists():
            return cls()  # 返回默认配置

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return cls()  # 解析失败返回默认配置

        table_output = data.get("table_output", {})
        return cls.from_dict(table_output)

    def get_stars(self, p_value: Optional[float]) -> str:
        """根据 p 值返回显著性星号"""
        if p_value is None:
            return ""
        sl = self.significance_levels
        if p_value < sl.three_star:
            return "***"
        if p_value < sl.two_star:
            return "**"
        if p_value < sl.one_star:
            return "*"
        return ""

    def format_coefficient(self, estimate: Optional[float], p_value: Optional[float]) -> str:
        """格式化系数（带显著性星号）"""
        if estimate is None:
            return ""
        stars = self.get_stars(p_value)
        return f"{estimate:.{self.decimal_places}f}{stars}"

    def format_second_row(self, std_error: Optional[float], t_stat: Optional[float]) -> str:
        """格式化第二行（标准误或t统计量）"""
        value = std_error if self.second_row_type == "std_error" else t_stat
        if value is None:
            return ""
        return f"({value:.{self.decimal_places}f})"

    def format_observations(self, n: Optional[int]) -> str:
        """格式化观测数量"""
        if n is None:
            return ""
        if self.thousands_separator:
            return f"{n:,}"
        return str(n)

    def format_r_squared(self, r2: Optional[float]) -> str:
        """格式化 R² 值"""
        if r2 is None:
            return ""
        return f"{r2:.{self.decimal_places}f}"
