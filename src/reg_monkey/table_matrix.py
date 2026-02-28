"""
table_matrix.py

TableMatrix - TUI 运行时的表格数据对象

设计理念：
- TableMatrix 是运行时核心对象，存储完整的 exec_result 数据
- 保存到磁盘时转换为轻量级 TableSpec（只存 task_id 引用）
- 从磁盘加载时结合 exec_results 恢复完整 TableMatrix

数据流：
    TUI 运行时: TableMatrix（含完整 exec_result）
                    │
                    │ to_spec() ── 保存时
                    ↓
    磁盘: output_mapping.json（轻量级，只存引用）
                    │
                    │ from_spec() + exec_results ── 加载时
                    ↓
    TUI 运行时: TableMatrix（恢复完整数据）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import pandas as pd

if TYPE_CHECKING:
    from reg_monkey.output_mapping import TableSpec
    from reg_monkey.table_config import TableConfig


@dataclass
class MatrixColumn:
    """TableMatrix 中的一列，封装单个展示列需要的所有信息。"""

    task_id: str
    label: str
    exec_result: Optional[Dict[str, Any]] = None

    # 元数据（从 results_df 行提取，方便 TUI 显示）
    y: str = ""
    X: Dict[str, Any] = field(default_factory=dict)
    model: str = "OLS"
    section: str = ""
    name: str = ""
    category_controls: Dict[str, str] = field(default_factory=dict)  # 固定效应: {效应名: 字段名}
    controls: List[str] = field(default_factory=list)
    parent_task_id: Optional[str] = None

    # 列来源信息
    source_task_id: Optional[str] = None  # 原始 task_id（stepwise/派生列使用）
    result_target: str = "forward"      # forward / opposite / stepwise
    group_label: str = ""               # 分组名称，如 "SOE=1"
    stepwise_index: Optional[int] = None
    stepwise_label: str = ""
    stepwise_enabled: bool = False

    @property
    def is_heterogeneity(self) -> bool:
        return self.result_target in ("forward", "opposite") and bool(self.group_label)

    @property
    def is_stepwise(self) -> bool:
        return self.result_target == "stepwise"

    def _get_stepwise_entry(self) -> Optional[Dict[str, Any]]:
        if not self.is_stepwise or self.exec_result is None:
            return None
        steps = (self.exec_result or {}).get("stepwise_results") or []
        for entry in steps:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("step")
            try:
                idx_val = int(idx)
            except Exception:
                continue
            if idx_val == self.stepwise_index:
                return entry
        return None

    def get_coefficients(self) -> Optional[List[Dict]]:
        if self.exec_result is None:
            return None
        if self.is_stepwise:
            entry = self._get_stepwise_entry()
            if not entry:
                return None
            result_block = entry.get("result") or {}
            coeffs = result_block.get("coefficients")
        else:
            target_key = f"{self.result_target}_res"
            target_res = self.exec_result.get(target_key)
            if target_res is None:
                return None
            coeffs = target_res.get("coefficients")
        if coeffs is None:
            return None
        if isinstance(coeffs, pd.DataFrame):
            if coeffs.empty:
                return None
            return coeffs.to_dict("records")
        return coeffs

    def get_statistics(self) -> Optional[Dict]:
        if self.exec_result is None:
            return None
        if self.is_stepwise:
            entry = self._get_stepwise_entry()
            if not entry:
                return None
            result_block = entry.get("result")
            if isinstance(result_block, dict):
                return result_block
            return None
        target_key = f"{self.result_target}_res"
        target_res = self.exec_result.get(target_key)
        if target_res is None or not isinstance(target_res, dict):
            return None
        return target_res


@dataclass
class TableMatrix:
    """
    TUI 运行时的表格数据对象

    生命周期:
    1. 创建表格 → TableMatrix(title, description, filename)，columns 为空
    2. 添加列   → add_column(task_id, label, exec_result, metadata)
    3. 编辑     → reorder/remove/rename 等操作
    4. 保存     → to_spec() 转为轻量级 TableSpec 写入 JSON
    5. 加载     → from_spec() + exec_results 恢复完整 TableMatrix
    """

    title: str
    description: str = ""
    filename: str = ""

    columns: List[MatrixColumn] = field(default_factory=list)

    # ==================== 列操作 ====================

    def add_column(
        self,
        task_id: str,
        label: str = "",
        exec_result: Optional[Dict] = None,
        flatten_heterogeneity: bool = True,
        *,
        result_target: str = "forward",
        group_label: str = "",
        stepwise_index: Optional[int] = None,
        stepwise_label: str = "",
        source_task_id: Optional[str] = None,
        include_stepwise_children: bool = False,
        renumber: bool = True,
        **metadata,
    ) -> "TableMatrix":
        """
        添加列（TUI 从 Result Browser 选中后调用）

        如果 flatten_heterogeneity=True 且 exec_result 中同时存在
        forward_res 和 opposite_res，则自动平铺为两列。
        """
        # 检查是否需要平铺分组回归结果
        if (
            flatten_heterogeneity
            and exec_result is not None
            and exec_result.get("opposite_res") is not None
            and bool(exec_result.get("opposite_res"))
            and result_target == "forward"
        ):

            # 平铺为两列：forward 在前，opposite 在后
            base_label = label if label else self._generate_default_label("forward")

            # Forward 列
            forward_col = MatrixColumn(
                task_id=f"{task_id}_forward",
                label=f"{base_label}a",
                exec_result=exec_result,
                result_target="forward",
                group_label=metadata.get("forward_group_label", "Group 1"),
                source_task_id=task_id,
                stepwise_enabled=bool(include_stepwise_children),
                y=metadata.get("y", ""),
                X=metadata.get("X", {}),
                model=metadata.get("model", "OLS"),
                section=metadata.get("section", ""),
                name=metadata.get("name", ""),
                category_controls=metadata.get("category_controls", {}),
                controls=list(metadata.get("controls", []) or []),
                parent_task_id=metadata.get("parent_task_id"),
            )
            self.columns.append(forward_col)

            if include_stepwise_children:
                meta = {
                    "y": metadata.get("y", ""),
                    "X": metadata.get("X", {}),
                    "model": metadata.get("model", "OLS"),
                    "section": metadata.get("section", ""),
                    "name": metadata.get("name", ""),
                    "category_controls": metadata.get("category_controls", {}),
                    "controls": list(metadata.get("controls", []) or []),
                    "parent_task_id": metadata.get("parent_task_id"),
                }
                self._append_stepwise_columns(
                    base_column=forward_col,
                    exec_result=exec_result,
                    metadata=meta,
                    before_base=True,
                )

            # Opposite 列
            opposite_col = MatrixColumn(
                task_id=f"{task_id}_opposite",
                label=f"{base_label}b",
                exec_result=exec_result,
                result_target="opposite",
                group_label=metadata.get("opposite_group_label", "Group 2"),
                source_task_id=task_id,
                stepwise_enabled=False,
                y=metadata.get("y", ""),
                X=metadata.get("X", {}),
                model=metadata.get("model", "OLS"),
                section=metadata.get("section", ""),
                name=metadata.get("name", ""),
                category_controls=metadata.get("category_controls", {}),
                controls=list(metadata.get("controls", []) or []),
                parent_task_id=metadata.get("parent_task_id"),
            )
            self.columns.append(opposite_col)
        else:
            # 单一回归结果
            if not label:
                label = self._generate_default_label(result_target)

            col = MatrixColumn(
                task_id=task_id,
                label=label,
                exec_result=exec_result,
                result_target=result_target,
                group_label=group_label,
                source_task_id=source_task_id or task_id,
                stepwise_index=stepwise_index,
                stepwise_label=stepwise_label,
                stepwise_enabled=bool(include_stepwise_children) if result_target == "forward" else False,
                y=metadata.get("y", ""),
                X=metadata.get("X", {}),
                model=metadata.get("model", "OLS"),
                section=metadata.get("section", ""),
                name=metadata.get("name", ""),
                category_controls=metadata.get("category_controls", {}),
                controls=list(metadata.get("controls", []) or []),
                parent_task_id=metadata.get("parent_task_id"),
            )
            self.columns.append(col)

            if include_stepwise_children and result_target == "forward":
                self._append_stepwise_columns(
                    base_column=col,
                    exec_result=exec_result,
                    metadata=metadata,
                    before_base=True,
                )

        if renumber:
            self._renumber_labels()
        return self

    def _append_stepwise_columns(
        self,
        *,
        base_column: MatrixColumn,
        exec_result: Optional[Dict[str, Any]],
        metadata: Dict[str, Any],
        before_base: bool = True,
    ) -> None:
        if not exec_result or not getattr(base_column, "stepwise_enabled", False):
            return
        steps = exec_result.get("stepwise_results") or []
        if not steps:
            return

        entries: List[tuple[int, Dict[str, Any]]] = []
        max_step_idx: Optional[int] = None
        for entry in steps:
            if not isinstance(entry, dict):
                continue
            try:
                step_idx = int(entry.get("step", len(entries)))
            except Exception:
                continue
            max_step_idx = step_idx if max_step_idx is None else max(max_step_idx, step_idx)
            entries.append((step_idx, entry))

        marked_steps = []
        for step_idx, entry in entries:
            if max_step_idx is not None and step_idx == max_step_idx:
                continue  # full controls shown via base column
            if not entry.get("export_marked"):
                continue
            label_raw = entry.get("label") or f"Step {step_idx}"
            label = self._clean_step_label(label_raw, default=f"Step {step_idx}")
            marked_steps.append((step_idx, label))

        if not marked_steps:
            return
        marked_steps.sort(key=lambda item: item[0])

        base_index = self.columns.index(base_column)
        insert_at = base_index if before_base else base_index + 1
        source_task_id = base_column.source_task_id or base_column.task_id

        for offset, (step_idx, label) in enumerate(marked_steps):
            col_label = f"{base_column.label}-{label}"
            step_task_id = f"{source_task_id}__step{step_idx:02d}"
            step_col = MatrixColumn(
                task_id=step_task_id,
                label=col_label,
                exec_result=exec_result,
                result_target="stepwise",
                stepwise_index=step_idx,
                stepwise_label=label,
                source_task_id=source_task_id,
                y=metadata.get("y", base_column.y),
                X=metadata.get("X", base_column.X),
                model=metadata.get("model", base_column.model),
                section=metadata.get("section", base_column.section),
                name=metadata.get("name", base_column.name),
                category_controls=metadata.get("category_controls", base_column.category_controls),
                controls=list(entry.get("controls_included") or []),
                parent_task_id=base_column.parent_task_id,
            )
            self.columns.insert(insert_at + offset, step_col)
        self._renumber_labels()

    def _clean_step_label(self, label: Any, default: str) -> str:
        text = str(label or default)
        text = text.strip().replace("\n", "")
        if text.startswith("[1]"):
            text = text[3:].strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        return text or default

    def _generate_default_label(self, result_target: str) -> str:
        if result_target == "forward":
            base_count = sum(1 for c in self.columns if c.result_target == "forward")
            return f"({base_count + 1})"
        return f"({len(self.columns) + 1})"

    def insert_column(
        self,
        index: int,
        task_id: str,
        label: str = "",
        exec_result: Optional[Dict] = None,
        **metadata,
    ) -> "TableMatrix":
        """在指定位置插入列"""
        if not label:
            label = f"({index + 1})"

        col = MatrixColumn(
            task_id=task_id,
            label=label,
            exec_result=exec_result,
            result_target="forward",
            y=metadata.get("y", ""),
            X=metadata.get("X", {}),
            model=metadata.get("model", "OLS"),
            section=metadata.get("section", ""),
            name=metadata.get("name", ""),
            category_controls=metadata.get("category_controls", {}),
            controls=list(metadata.get("controls", []) or []),
            parent_task_id=metadata.get("parent_task_id"),
        )
        self.columns.insert(index, col)
        self._renumber_labels()
        return self

    def remove_column(self, task_id: str) -> "TableMatrix":
        """移除指定列"""
        self.columns = [c for c in self.columns if c.task_id != task_id]
        self._renumber_labels()
        return self

    def remove_column_at(self, index: int) -> "TableMatrix":
        """移除指定位置的列"""
        if 0 <= index < len(self.columns):
            self.columns.pop(index)
        self._renumber_labels()
        return self

    def move_column(self, from_idx: int, to_idx: int) -> "TableMatrix":
        """移动列位置"""
        if 0 <= from_idx < len(self.columns) and 0 <= to_idx < len(self.columns):
            col = self.columns.pop(from_idx)
            self.columns.insert(to_idx, col)
            self._renumber_labels()
        return self

    def reorder_columns(self, task_ids: List[str]) -> "TableMatrix":
        """按 task_id 列表重排列 (保持向后兼容)."""
        by_id = {c.task_id: c for c in self.columns}
        ordered = [by_id[tid] for tid in task_ids if tid in by_id]
        remaining = [c for c in self.columns if c.task_id not in set(task_ids)]
        self.columns = ordered + remaining
        self._renumber_labels()
        return self

    def rename_column(
        self,
        task_id: str,
        new_label: str,
        parent_task_id: Optional[str] = None,
    ) -> "TableMatrix":
        """重命名列标签"""
        col = self.get_column(task_id, parent_task_id)
        if col is not None:
            col.label = new_label
        return self

    def _renumber_labels(self) -> None:
        """重新编号默认标签"""
        for i, col in enumerate(self.columns):
            col.label = f"({i + 1})"

    # ==================== 属性访问 ====================

    @property
    def task_ids(self) -> List[str]:
        return [c.task_id for c in self.columns]

    @property
    def num_columns(self) -> int:
        return len(self.columns)

    @property
    def is_empty(self) -> bool:
        return len(self.columns) == 0

    def get_column(
        self,
        task_id: str,
        parent_task_id: Optional[str] = None,
    ) -> Optional[MatrixColumn]:
        """按 task_id/parent_task_id 获取列"""
        normalized_parent = (parent_task_id or None)
        for col in self.columns:
            if col.task_id != task_id:
                continue
            if normalized_parent is None:
                return col
            col_parent = col.parent_task_id or None
            if col_parent == normalized_parent:
                return col
        return None

    def refresh_stepwise_columns(
        self,
        source_task_id: str,
        exec_result: Optional[Dict[str, Any]],
        *,
        stepwise_enabled: Optional[bool] = None,
    ) -> None:
        """根据最新的 stepwise 标记刷新指定任务的派生列。"""
        if source_task_id is None or exec_result is None:
            return

        # 1) 移除旧的 stepwise 列
        self.columns = [
            col for col in self.columns
            if not (col.source_task_id == source_task_id and col.result_target == "stepwise")
        ]

        # 2) 为每个 forward 列重新附加 stepwise 列
        snapshot = list(self.columns)
        for col in snapshot:
            if col.source_task_id == source_task_id and col.result_target == "forward":
                if stepwise_enabled is not None:
                    col.stepwise_enabled = bool(stepwise_enabled)
                metadata = {
                    "y": col.y,
                    "X": col.X,
                    "model": col.model,
                    "section": col.section,
                    "name": col.name,
                    "category_controls": col.category_controls,
                    "controls": col.controls,
                    "parent_task_id": col.parent_task_id,
                }
                self._append_stepwise_columns(
                    base_column=col,
                    exec_result=exec_result,
                    metadata=metadata,
                )
        self._renumber_labels()

    def get_column_at(self, index: int) -> Optional[MatrixColumn]:
        """按索引获取列"""
        if 0 <= index < len(self.columns):
            return self.columns[index]
        return None

    # ==================== 序列化/反序列化 ====================

    def to_spec(self) -> "TableSpec":
        """
        转换为 TableSpec（轻量级，用于持久化）

        只保存 task_id 和 label，不保存 exec_result
        """
        from reg_monkey.output_mapping import TableColumn, TableSpec

        spec = TableSpec(
            name=self.title,
            filename=self.filename,
            description=self.description,
        )
        for col in self.columns:
            extras: Dict[str, Any] = {}
            source_id = col.source_task_id or col.task_id
            if source_id != col.task_id:
                extras["source_task_id"] = source_id
            if col.result_target != "forward":
                extras["result_target"] = col.result_target
            if col.group_label:
                extras["group_label"] = col.group_label
            if col.stepwise_index is not None:
                extras["stepwise_index"] = col.stepwise_index
            if col.stepwise_label:
                extras["stepwise_label"] = col.stepwise_label
            if col.result_target == "forward" and col.stepwise_enabled:
                extras["stepwise_enabled"] = True
            if col.controls:
                extras["controls"] = list(col.controls)
            if col.parent_task_id:
                extras["parent_task_id"] = col.parent_task_id

            spec.columns.append(
                TableColumn(
                    task_id=col.task_id,
                    label=col.label,
                    extras=extras,
                )
            )
        return spec

    @classmethod
    def from_spec(
        cls,
        spec: "TableSpec",
        exec_results: Dict[str, Any],
        results_df: Optional[pd.DataFrame] = None,
    ) -> "TableMatrix":
        """
        从 TableSpec 恢复 TableMatrix（加载时）

        Args:
            spec: 从 JSON 加载的 TableSpec
            exec_results: task_id -> exec_result 的映射
            results_df: 可选，用于提取元数据 (y, X, model 等)
        """
        matrix = cls(
            title=spec.name,
            description=spec.description,
            filename=spec.filename,
        )

        sources_need_step: Set[str] = set()
        sources_has_step: Set[str] = set()

        # 预扫描：检测哪些 source_task_id 在配置中已经有 stepwise 子列
        # 如果已有，则恢复时不再自动生成（避免重复）
        sources_with_stepwise_in_spec: set[str] = set()
        for col_spec in spec.columns:
            extras = col_spec.extras if hasattr(col_spec, "extras") else {}
            if extras.get("result_target") == "stepwise":
                src_id = extras.get("source_task_id", col_spec.task_id)
                sources_with_stepwise_in_spec.add(src_id)

        for col_spec in spec.columns:
            extras = col_spec.extras if hasattr(col_spec, "extras") else {}
            source_task_id = extras.get("source_task_id", col_spec.task_id)
            exec_result = exec_results.get(source_task_id)

            # 从 results_df 提取元数据
            metadata = {}
            if results_df is not None:
                row = results_df[results_df["task_id"] == col_spec.task_id]
                if not row.empty:
                    r = row.iloc[0]
                    metadata = {
                        "y": r.get("y", ""),
                        "X": r.get("X", {}),
                        "model": r.get("model", "OLS"),
                        "section": r.get("section", ""),
                        "name": r.get("name", ""),
                        "category_controls": r.get("category_controls", {}),
                        "controls": r.get("controls", []),
                        "parent_task_id": r.get("parent_task_id"),
                    }

            # 使用 flatten_heterogeneity=False 避免重复平铺
            derived = extras.get("result_target") == "stepwise"
            # 如果配置中已经显式保存了 stepwise 子列，则不再自动生成（避免重复）
            already_has_stepwise = source_task_id in sources_with_stepwise_in_spec
            include_children = bool(extras.get("stepwise_enabled", False)) and not derived and not already_has_stepwise

            if "controls" in extras and extras.get("controls") is not None:
                metadata["controls"] = extras.get("controls")
            if "parent_task_id" in extras and extras.get("parent_task_id") is not None:
                metadata["parent_task_id"] = extras.get("parent_task_id")

            matrix.add_column(
                task_id=col_spec.task_id,
                label=col_spec.label,
                exec_result=exec_result,
                flatten_heterogeneity=False,
                result_target=extras.get("result_target", "forward"),
                group_label=extras.get("group_label", ""),
                stepwise_index=extras.get("stepwise_index"),
                stepwise_label=extras.get("stepwise_label", ""),
                source_task_id=source_task_id,
                include_stepwise_children=include_children,
                renumber=False,
                **metadata,
            )

            if derived:
                sources_has_step.add(source_task_id)
            if extras.get("stepwise_enabled"):
                sources_need_step.add(source_task_id)

        for src in sources_need_step - sources_has_step:
            exec_result = exec_results.get(src)
            if exec_result is not None:
                try:
                    matrix.refresh_stepwise_columns(src, exec_result, stepwise_enabled=True)
                except Exception:
                    pass

        matrix._renumber_labels()

        return matrix

    # ==================== 数据提取 ====================

    def get_all_variables(self) -> List[str]:
        """获取所有列中出现的变量名（保持顺序）"""
        all_vars = []
        for col in self.columns:
            coeffs = col.get_coefficients()
            if coeffs is None:
                continue
            for coef in coeffs:
                var = coef.get("Variable", "")
                if var and var not in all_vars:
                    all_vars.append(var)
        return all_vars

    def get_coefficient(
        self, task_id: str, variable: str, target: str = "forward"
    ) -> Optional[Dict]:
        """获取指定列、指定变量的系数信息"""
        col = self.get_column(task_id)
        if col is None or col.exec_result is None:
            return None

        if col.result_target == "stepwise":
            entry = col._get_stepwise_entry()
            if not entry:
                return None
            block = (entry.get("result") or {}).get("coefficients")
            coeffs = block
        else:
            target_key = f"{target}_res"
            coeffs = col.exec_result.get(target_key, {}).get("coefficients")

        if coeffs is None:
            return None

        # 处理 DataFrame 类型的系数数据
        if isinstance(coeffs, pd.DataFrame):
            if coeffs.empty:
                return None
            coeffs = coeffs.to_dict("records")

        for coef in coeffs:
            if coef.get("Variable") == variable:
                return coef
        return None

    def get_statistic(
        self, task_id: str, stat_name: str, target: str = "forward"
    ) -> Optional[Any]:
        """获取指定列的统计量（N, R², F-stat 等）"""
        col = self.get_column(task_id)
        if col is None or col.exec_result is None:
            return None

        if col.result_target == "stepwise":
            entry = col._get_stepwise_entry()
            if not entry:
                return None
            result_block = entry.get("result") or {}
            return result_block.get(stat_name)

        target_key = f"{target}_res"
        stats = col.exec_result.get(target_key, {}).get("statistics", {})
        return stats.get(stat_name)

    # ==================== 渲染接口 ====================

    def to_dataframe(self, include_se: bool = True, decimal: int = 3) -> pd.DataFrame:
        """
        转换为 pandas DataFrame

        行: 变量名 (可选：标准误行)
        列: 各回归结果
        """
        all_vars = self.get_all_variables()

        data = {"Variable": []}
        for col in self.columns:
            data[col.label] = []

        for var in all_vars:
            # 系数行
            data["Variable"].append(var)
            for col in self.columns:
                coef = self.get_coefficient(col.task_id, var)
                if coef is not None:
                    est = coef.get("Estimate")
                    p_val = coef.get("P_Value")
                    stars = self._get_stars(p_val)
                    data[col.label].append(
                        f"{est:.{decimal}f}{stars}" if est is not None else ""
                    )
                else:
                    data[col.label].append("")

            # 标准误行
            if include_se:
                data["Variable"].append("")
                for col in self.columns:
                    coef = self.get_coefficient(col.task_id, var)
                    if coef is not None and coef.get("Std_Error") is not None:
                        se = coef["Std_Error"]
                        data[col.label].append(f"({se:.{decimal}f})")
                    else:
                        data[col.label].append("")

        return pd.DataFrame(data)

    def _get_stars(self, p_value: Optional[float]) -> str:
        """根据 p 值返回显著性星号"""
        if p_value is None:
            return ""
        if p_value < 0.01:
            return "***"
        if p_value < 0.05:
            return "**"
        if p_value < 0.1:
            return "*"
        return ""

    # ==================== TUI 纯文本渲染 ====================

    def to_txt(
        self,
        config: Optional["TableConfig"] = None,
        max_width: int = 120,
        col_width: int = 14,
    ) -> str:
        """
        渲染为纯文本格式（用于 TUI 预览）

        Args:
            config: 表格配置，None 时使用默认配置
            max_width: 最大宽度限制
            col_width: 每列宽度

        Returns:
            格式化的纯文本表格字符串
        """
        from reg_monkey.table_config import TableConfig

        cfg = config or TableConfig()

        if self.is_empty:
            return "(No columns defined)"

        lines = []
        n_cols = len(self.columns)
        var_col_width = 16  # 变量名列宽度

        # 计算实际列宽（确保不超过 max_width）
        total_width = var_col_width + n_cols * col_width + n_cols + 1
        if total_width > max_width and n_cols > 0:
            col_width = max(8, (max_width - var_col_width - n_cols - 1) // n_cols)

        # 辅助函数
        def make_row(cells: list, align: str = "center") -> str:
            """生成一行"""
            formatted = []
            for i, cell in enumerate(cells):
                w = var_col_width if i == 0 else col_width
                cell_str = str(cell)[:w]  # 截断过长内容
                if align == "left":
                    formatted.append(f"{cell_str:<{w}}")
                elif align == "right":
                    formatted.append(f"{cell_str:>{w}}")
                else:
                    formatted.append(f"{cell_str:^{w}}")
            return "│" + "│".join(formatted) + "│"

        def make_separator(char: str = "─", joints: tuple = ("├", "┼", "┤")) -> str:
            """生成分隔线"""
            parts = [char * var_col_width]
            for _ in range(n_cols):
                parts.append(char * col_width)
            return joints[0] + joints[1].join(parts) + joints[2]

        # 表头
        header_width = var_col_width + n_cols * col_width + n_cols
        lines.append("┌" + "─" * header_width + "┐")

        # 表格标题（合并单元格效果）
        title = self.title or "Regression Results"
        title_display = title[:header_width] if len(title) > header_width else title
        lines.append("│" + f"{title_display:^{header_width}}" + "│")

        # 表格描述（如果有）
        if self.description:
            desc = self.description[:header_width - 2] if len(self.description) > header_width - 2 else self.description
            lines.append("│" + f"{desc:<{header_width}}" + "│")

        lines.append(make_separator("─", ("├", "┬", "┤")))

        # 列标签行
        labels = [""] + [col.label or f"({i+1})" for i, col in enumerate(self.columns)]
        lines.append(make_row(labels))

        # 因变量行
        depvars = ["VARIABLES"] + [col.y for col in self.columns]
        lines.append(make_row(depvars))

        lines.append(make_separator())

        # 收集所有变量并排序
        all_vars = self._collect_sorted_variables(cfg)

        # 空白行
        if cfg.show_blank_rows:
            blank = [""] * (n_cols + 1)
            lines.append(make_row(blank))

        # 系数行
        for var in all_vars:
            # 系数值行
            row = [var]
            for col in self.columns:
                coef = self._get_column_coefficient_data(col, var)
                if coef is not None:
                    est, p_val = coef.get("Estimate"), coef.get("P_Value")
                    row.append(cfg.format_coefficient(est, p_val))
                else:
                    row.append("")
            lines.append(make_row(row, align="left" if row[0] else "center"))

            # 标准误行
            se_row = [""]
            for col in self.columns:
                coef = self._get_column_coefficient_data(col, var)
                if coef is not None:
                    se = coef.get("Std_Error")
                    t_stat = coef.get("t_value") or coef.get("T_Stat")
                    se_row.append(cfg.format_second_row(se, t_stat))
                else:
                    se_row.append("")
            lines.append(make_row(se_row))

        # 空白行
        if cfg.show_blank_rows:
            blank = [""] * (n_cols + 1)
            lines.append(make_row(blank))

        lines.append(make_separator())

        # Observations
        obs_row = ["Observations"]
        for col in self.columns:
            n = self._get_column_observations(col)
            obs_row.append(cfg.format_observations(n) if n else "")
        lines.append(make_row(obs_row, align="left"))

        # R-squared
        r2_row = ["R-squared"]
        for col in self.columns:
            r2 = self._get_column_r_squared(col)
            r2_row.append(f"{r2:.{cfg.decimal_places}f}" if r2 is not None else "")
        lines.append(make_row(r2_row, align="left"))

        # 固定效应控制行
        all_effects = self._collect_all_effects()
        for effect_name in all_effects:
            fe_row = [f"{effect_name} effect"]
            for col in self.columns:
                controlled = self._check_effect_controlled(col, effect_name)
                fe_row.append(cfg.fixed_effect_labels.yes if controlled else cfg.fixed_effect_labels.no)
            lines.append(make_row(fe_row, align="left"))

        # 底部
        lines.append("└" + "─" * header_width + "┘")

        # 显著性说明
        sl = cfg.significance_levels
        note = f"*** p<{sl.three_star}, ** p<{sl.two_star}, * p<{sl.one_star}"
        lines.append(note)

        return "\n".join(lines)

    def _build_control_priority_order(self) -> List[str]:
        """根据首个全控制列确定控制变量优先顺序。"""
        max_len = 0
        cleaned_controls: List[List[str]] = []
        for col in self.columns:
            controls = [str(c) for c in getattr(col, "controls", []) if c]
            cleaned_controls.append(controls)
            if controls:
                max_len = max(max_len, len(controls))
        if max_len == 0:
            return []

        order: List[str] = []
        seen: Set[str] = set()
        base_found = False

        for controls in cleaned_controls:
            if not controls:
                continue
            if not base_found:
                if len(controls) == max_len:
                    for ctrl in controls:
                        if ctrl not in seen:
                            seen.add(ctrl)
                            order.append(ctrl)
                    base_found = True
                continue
            for ctrl in controls:
                if ctrl not in seen:
                    seen.add(ctrl)
                    order.append(ctrl)

        if not base_found:
            return []
        return order

    def _collect_sorted_variables(self, cfg: "TableConfig") -> list:
        """收集并排序所有变量（排除固定效应展开变量）"""
        all_vars = set()
        independent_vars = set()

        # 需要排除的变量前缀（固定效应展开后的系数）
        exclude_prefixes = ("factor(", "as.factor(", "C(")

        for col in self.columns:
            coeffs = col.get_coefficients()
            if coeffs is not None:
                for coef in coeffs:
                    var = coef.get("Variable", "")
                    # 排除固定效应展开变量
                    if var and not var.startswith(exclude_prefixes):
                        all_vars.add(var)

            # 收集自变量
            if isinstance(col.X, dict):
                for group_vars in col.X.values():
                    if isinstance(group_vars, list):
                        independent_vars.update(group_vars)
                    elif isinstance(group_vars, str):
                        independent_vars.add(group_vars)
            elif isinstance(col.X, list):
                independent_vars.update(col.X)

        # 排序：自变量 -> 控制变量 -> Intercept
        intercept_names = {"Intercept", "(Intercept)", "Constant", "_cons"}
        independent = []
        controls = []
        intercept = None

        for var in all_vars:
            if var in intercept_names:
                intercept = var
            elif var in independent_vars:
                independent.append(var)
            else:
                controls.append(var)

        independent.sort()
        priority_order = self._build_control_priority_order()
        if priority_order:
            priority_index = {name: idx for idx, name in enumerate(priority_order)}
            controls.sort(key=lambda var: (priority_index.get(var, len(priority_order)), var))
        else:
            controls.sort()

        result = []
        if cfg.coefficient_sort.independent_vars_first:
            result.extend(independent)
            result.extend(controls)
        else:
            result.extend(sorted(independent + controls))

        if cfg.coefficient_sort.intercept_last and intercept:
            result.append(intercept)
        elif intercept:
            result.insert(0, intercept)

        return result

    def _get_column_coefficient_data(self, col: MatrixColumn, variable: str) -> Optional[dict]:
        """获取列中指定变量的系数数据"""
        coeffs = col.get_coefficients()
        if coeffs is None:
            return None
        for coef in coeffs:
            if coef.get("Variable") == variable:
                return coef
        return None

    def _extract_scalar(self, val: Any) -> Any:
        """从数组/列表中提取标量值（处理 numpy array、list、tuple）"""
        # 如果是字符串，直接返回
        if isinstance(val, str):
            return val
        # 尝试提取第一个元素（适用于 list, tuple, ndarray 等）
        try:
            if hasattr(val, '__len__') and len(val) > 0:
                return val[0]
        except (TypeError, IndexError):
            pass
        return val

    def _get_column_observations(self, col: MatrixColumn) -> Optional[int]:
        """获取列的观测数量"""
        stats = col.get_statistics()
        if stats is None:
            return None
        # R 模板使用 "Observations" 作为键名，同时兼容其他可能的键名
        for key in ["Observations", "N", "nobs", "n", "obs", "Obs"]:
            val = stats.get(key)
            if val is not None:
                # 处理数组类型（R/numpy 可能返回单元素数组）
                val = self._extract_scalar(val)
                try:
                    return int(float(val))  # 处理可能的浮点数格式
                except (ValueError, TypeError):
                    continue
        return None

    def _get_column_r_squared(self, col: MatrixColumn) -> Optional[float]:
        """获取列的 R² 值"""
        stats = col.get_statistics()
        if stats is None:
            return None
        # R 模板使用 "R-squared" 作为键名
        for key in ["R-squared", "R_squared", "r.squared", "R2", "adj.r.squared"]:
            val = stats.get(key)
            if val is not None:
                # 处理数组类型（R/numpy 可能返回单元素数组）
                val = self._extract_scalar(val)
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
        return None

    def _collect_all_effects(self) -> list:
        """收集所有列中的固定效应名称"""
        effects = set()
        for col in self.columns:
            if isinstance(col.category_controls, dict):
                # 字典格式：{效应名: 字段名}
                effects.update(col.category_controls.keys())
            elif isinstance(col.category_controls, (list, tuple)):
                # 列表格式：[字段名1, 字段名2, ...]，字段名即效应名
                effects.update(col.category_controls)
        return sorted(effects)

    def _check_effect_controlled(self, col: MatrixColumn, effect_name: str) -> bool:
        """检查列是否控制了指定的固定效应"""
        if isinstance(col.category_controls, dict):
            # 字典格式：检查 key
            return effect_name in col.category_controls
        elif isinstance(col.category_controls, (list, tuple)):
            # 列表格式：直接检查元素
            return effect_name in col.category_controls
        return False

    def __repr__(self) -> str:
        return f"TableMatrix(title={self.title!r}, columns={self.num_columns})"

    def debug_columns(self) -> str:
        """调试方法：输出所有列的数据结构信息"""
        lines = [f"TableMatrix: {self.title}", f"Columns: {self.num_columns}", ""]
        for i, col in enumerate(self.columns):
            lines.append(f"=== Column {i}: {col.label} ===")
            lines.append(f"  task_id: {col.task_id}")
            lines.append(f"  result_target: {col.result_target}")
            lines.append(f"  category_controls: {col.category_controls}")
            lines.append(f"  exec_result is None: {col.exec_result is None}")
            if col.exec_result:
                lines.append(f"  exec_result keys: {list(col.exec_result.keys())}")
                fwd = col.exec_result.get("forward_res")
                if fwd is None:
                    lines.append("  forward_res: None")
                elif isinstance(fwd, str):
                    lines.append(f"  forward_res: (string) {fwd[:100]}")
                elif isinstance(fwd, dict):
                    lines.append(f"  forward_res keys: {list(fwd.keys())}")
                    for k in ["Observations", "R-squared", "N", "nobs"]:
                        if k in fwd:
                            lines.append(f"    {k}: {fwd[k]} (type: {type(fwd[k]).__name__})")
                else:
                    lines.append(f"  forward_res type: {type(fwd)}")
            stats = col.get_statistics()
            lines.append(f"  get_statistics(): {type(stats).__name__ if stats else None}")

            # 测试渲染方法的返回值
            obs = self._get_column_observations(col)
            r2 = self._get_column_r_squared(col)
            lines.append(f"  _get_column_observations(): {obs}")
            lines.append(f"  _get_column_r_squared(): {r2}")
            lines.append("")
        return "\n".join(lines)
