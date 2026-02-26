"""
SharedState - 所有子模块共享的状态

核心设计：
- tables: List[TableMatrix] 是运行时核心对象，存储完整数据
- 保存时通过 to_spec() 转为轻量级 TableSpec 写入 JSON
- 加载时通过 from_spec() 恢复完整 TableMatrix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import pandas as pd

if TYPE_CHECKING:
    from reg_monkey.output_mapping import OutputMapping
    from reg_monkey.table_matrix import TableMatrix


@dataclass
class SharedState:
    """
    所有子模块共享的状态
    - 由主应用创建并传递给各 Screen
    - 子模块可读写，主应用监听变更

    核心设计：
    - tables: List[TableMatrix] 是运行时核心对象，存储完整数据
    - 保存时通过 to_spec() 转为轻量级 TableSpec 写入 JSON
    - 加载时通过 from_spec() 恢复完整 TableMatrix
    """

    # 数据源
    tables: List["TableMatrix"] = field(default_factory=list)  # 运行时表格对象（核心）
    results_df: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())  # to_materialize() 返回的 DataFrame
    exec_results: Dict[str, Any] = field(default_factory=dict)  # task_id -> exec_result 缓存
    datasets: Dict[str, pd.DataFrame] = field(default_factory=dict)  # 数据集: name -> DataFrame
    plan: Any = None  # Plan 对象引用（用于访问任务的 code_text）
    stepwise_display: Dict[str, bool] = field(default_factory=dict)

    # 运行时状态
    current_table_index: Optional[int] = None  # 当前正在编辑的表格索引
    dirty: bool = False  # 是否有未保存的修改

    # 配置
    config_path: str = "output_mapping.json"  # 配置文件路径

    def mark_dirty(self) -> None:
        """标记有未保存的修改"""
        self.dirty = True

    def save(self) -> None:
        """
        保存配置
        - 将 List[TableMatrix] 转换为 OutputMapping (轻量级)
        - 写入 JSON 文件
        """
        from reg_monkey.output_mapping import OutputMapping

        mapping = OutputMapping()
        for matrix in self.tables:
            spec = matrix.to_spec()
            mapping.tables.append(spec)
        mapping.save(self.config_path)
        self.dirty = False

    @classmethod
    def load(
        cls,
        config_path: str,
        results_df: pd.DataFrame,
        datasets: Optional[Dict[str, pd.DataFrame]] = None,
        plan: Any = None,
    ) -> "SharedState":
        """
        从配置文件加载
        - 读取 JSON 文件中的 TableSpec 列表
        - 结合 exec_results 恢复为完整的 TableMatrix

        Args:
            config_path: 配置文件路径
            results_df: to_materialize() 返回的 DataFrame
            datasets: 数据集字典（来自 CodeExecutor.datasets）
            plan: Plan 对象引用
        """
        from reg_monkey.output_mapping import OutputMapping
        from reg_monkey.table_matrix import TableMatrix

        state = cls(config_path=config_path)
        state.results_df = results_df
        state.datasets = datasets or {}
        state.plan = plan

        # 预加载 exec_results
        state._preload_exec_results()

        # 加载配置文件
        try:
            mapping = OutputMapping.load(config_path)
            for spec in mapping.tables:
                matrix = TableMatrix.from_spec(spec, state.exec_results, results_df)
                state.tables.append(matrix)
                for col in matrix.columns:
                    if (
                        col.result_target == "forward"
                        and getattr(col, "stepwise_enabled", False)
                    ):
                        source_id = col.source_task_id or col.task_id
                        state.stepwise_display[source_id] = True
        except FileNotFoundError:
            pass  # 配置文件不存在，使用空列表

        return state

    def _preload_exec_results(self) -> None:
        """预加载所有任务的执行结果到缓存"""
        if self.results_df is None or self.results_df.empty:
            return
        for _, row in self.results_df.iterrows():
            task_id = row.get("task_id")
            exec_result = row.get("exec_result")
            if task_id and exec_result is not None:
                if hasattr(exec_result, "value"):
                    exec_result = exec_result.value
                self.exec_results[task_id] = exec_result

    def get_table(self, index: int) -> Optional["TableMatrix"]:
        """获取指定索引的表格"""
        if 0 <= index < len(self.tables):
            return self.tables[index]
        return None

    def get_current_table(self) -> Optional["TableMatrix"]:
        """获取当前正在编辑的表格"""
        if self.current_table_index is not None:
            return self.get_table(self.current_table_index)
        return None

    def create_table(
        self,
        title: str,
        filename: str,
        description: str = "",
    ) -> "TableMatrix":
        """
        创建新表格
        - 生成一个空的 TableMatrix
        - 添加到 tables 列表
        """
        from reg_monkey.table_matrix import TableMatrix

        matrix = TableMatrix(title=title, filename=filename, description=description)
        self.tables.append(matrix)
        self.mark_dirty()
        return matrix

    def delete_table(self, index: int) -> bool:
        """删除指定索引的表格"""
        if 0 <= index < len(self.tables):
            self.tables.pop(index)
            self.mark_dirty()
            return True
        return False

    def get_exec_result(self, task_id: str) -> Optional[dict]:
        """获取指定任务的执行结果"""
        if task_id in self.exec_results:
            return self.exec_results[task_id]
        # 从 DataFrame 中提取
        if self.results_df is not None and not self.results_df.empty:
            row = self.results_df[self.results_df["task_id"] == task_id]
            if not row.empty:
                exec_result = row.iloc[0].get("exec_result")
                if hasattr(exec_result, "value"):
                    exec_result = exec_result.value
                self.exec_results[task_id] = exec_result
                return exec_result
        return None

    def should_display_stepwise(self, task_id: str) -> bool:
        return bool(self.stepwise_display.get(task_id, False))

    def set_stepwise_display(self, task_id: str, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled:
            self.stepwise_display[task_id] = True
        else:
            self.stepwise_display.pop(task_id, None)
        self._refresh_tables_stepwise(task_id, stepwise_enabled=enabled)

    def get_task_metadata(self, task_id: str) -> dict:
        """从 results_df 获取任务的元数据"""
        if self.results_df is None or self.results_df.empty:
            return {}
        row = self.results_df[self.results_df["task_id"] == task_id]
        if row.empty:
            return {}
        r = row.iloc[0]
        return {
            "y": r.get("y", ""),
            "X": r.get("X", {}),
            "model": r.get("model", "OLS"),
            "section": r.get("section", ""),
            "name": r.get("name", ""),
        }

    def get_unique_sections(self) -> list:
        """获取所有唯一的 section 值"""
        if self.results_df is not None and "section" in self.results_df.columns:
            return sorted(self.results_df["section"].dropna().unique().tolist())
        return []

    def get_unique_names(self) -> list:
        """获取所有唯一的 name 值"""
        if self.results_df is not None and "name" in self.results_df.columns:
            return sorted(self.results_df["name"].dropna().unique().tolist())
        return []

    def get_unique_marks(self) -> list:
        """获取所有唯一的 mark 值"""
        if self.results_df is not None and "mark" in self.results_df.columns:
            return sorted(
                str(m) for m in self.results_df["mark"].dropna().unique().tolist()
            )
        return []

    def set_stepwise_mark(self, task_id: str, step_index: int, marked: bool) -> None:
        exec_result = self.get_exec_result(task_id)
        if not exec_result or not isinstance(exec_result, dict):
            return
        steps = exec_result.get("stepwise_results") or []
        for step in steps:
            try:
                if int(step.get("step", -1)) == int(step_index):
                    step["export_marked"] = bool(marked)
                    break
            except Exception:
                continue

        task = self.get_task_object(task_id)
        if task and isinstance(getattr(task, "exec_result", None), dict):
            task_steps = task.exec_result.get("stepwise_results") or []
            for step in task_steps:
                try:
                    if int(step.get("step", -1)) == int(step_index):
                        step["export_marked"] = bool(marked)
                        break
                except Exception:
                    continue

        self._refresh_tables_stepwise(task_id)

    def _refresh_tables_stepwise(self, task_id: str, stepwise_enabled: Optional[bool] = None) -> None:
        exec_result = self.exec_results.get(task_id)
        if exec_result is None:
            return
        if stepwise_enabled is None:
            stepwise_enabled = self.should_display_stepwise(task_id)
        for matrix in self.tables:
            matrix.refresh_stepwise_columns(task_id, exec_result, stepwise_enabled=stepwise_enabled)

    # ---------- Plan 辅助 ----------

    def refresh_task_object_cache(self) -> None:
        self._task_obj_map: Dict[str, Any] = {}
        plan = getattr(self, "plan", None)
        if not plan:
            return
        try:
            for root in getattr(plan, "roots", []) or []:
                self._collect_plan_tasks(root)
        except Exception:
            self._task_obj_map = {}

    def _collect_plan_tasks(self, node: Any) -> None:
        task = getattr(node, "task", None)
        task_id = getattr(task, "task_id", None) if task is not None else None
        if task is not None and task_id:
            self._task_obj_map[task_id] = task
        for child in getattr(node, "children", []) or []:
            self._collect_plan_tasks(child)

    def get_task_object(self, task_id: str) -> Optional[Any]:
        if not task_id:
            return None
        cache = getattr(self, "_task_obj_map", None)
        if not cache:
            self.refresh_task_object_cache()
            cache = getattr(self, "_task_obj_map", {})
        if not cache:
            return None
        task = cache.get(task_id)
        if task is not None:
            return task
        if task_id.endswith("_forward"):
            return cache.get(task_id[:-8])
        if task_id.endswith("_opposite"):
            return cache.get(task_id[:-9])
        if "__step" in task_id:
            base_id = task_id.split("__step", 1)[0]
            return cache.get(base_id)
        return None
