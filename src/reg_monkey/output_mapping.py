"""
output_mapping.py
独立的输出映射配置对象，描述：
  - 哪些表格需要输出
  - 每张表格包含哪些任务结果（按顺序）
  - 哪些任务需要输出 R 代码 + 数据集（评审复现）

JSON 持久化，与 Plan 解耦。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any


# ------------------------------------------------------------------
# 子结构：表格中的一列
# ------------------------------------------------------------------
@dataclass
class TableColumn:
    """表格中的一列，对应一个任务的回归结果。"""

    task_id: str                          # 精确 task_id
    label: str = ""                       # 列标题，如 "(1)"、"OLS基准"；空则自动编号
    extras: Dict[str, Any] = field(default_factory=dict)  # 额外元数据（如 stepwise 设置）

    def to_dict(self) -> dict:
        data: Dict[str, Any] = {"task_id": self.task_id}
        if self.label:
            data["label"] = self.label
        if self.extras:
            data["extras"] = self.extras
        return data

    @classmethod
    def from_dict(cls, d: dict) -> "TableColumn":
        return cls(
            task_id=d["task_id"],
            label=d.get("label", ""),
            extras=d.get("extras", {}) or {},
        )


# ------------------------------------------------------------------
# 子结构：一张表格
# ------------------------------------------------------------------
@dataclass
class TableSpec:
    """一张输出表格的完整描述。"""
    name: str                             # 表格标题 / 标识
    filename: str                         # 输出文件名（后缀决定格式）
    columns: List[TableColumn] = field(default_factory=list)
    description: str = ""                 # 可选描述

    # ---- 列操作 ----
    def add_column(self, task_id: str, label: str = "") -> "TableSpec":
        self.columns.append(TableColumn(task_id=task_id, label=label))
        return self

    def insert_column(self, index: int, task_id: str, label: str = "") -> "TableSpec":
        self.columns.insert(index, TableColumn(task_id=task_id, label=label))
        return self

    def remove_column(self, task_id: str) -> "TableSpec":
        self.columns = [c for c in self.columns if c.task_id != task_id]
        return self

    def reorder_columns(self, task_ids: List[str]) -> "TableSpec":
        """按给定的 task_id 顺序重排列；不在列表中的列追加到末尾。"""
        by_id = {c.task_id: c for c in self.columns}
        ordered = [by_id[tid] for tid in task_ids if tid in by_id]
        remaining = [c for c in self.columns if c.task_id not in set(task_ids)]
        self.columns = ordered + remaining
        return self

    @property
    def task_ids(self) -> List[str]:
        return [c.task_id for c in self.columns]

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "name": self.name,
            "filename": self.filename,
            "columns": [c.to_dict() for c in self.columns],
        }
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TableSpec":
        return cls(
            name=d["name"],
            filename=d["filename"],
            columns=[TableColumn.from_dict(c) for c in d.get("columns", [])],
            description=d.get("description", ""),
        )


# ------------------------------------------------------------------
# 子结构：复现输出条目
# ------------------------------------------------------------------
@dataclass
class ReproducibilityEntry:
    """一个需要输出 R 代码 + 数据集的任务。"""
    task_id: str
    emit_r_code: bool = True
    emit_data: bool = True
    data_format: str = "csv"              # csv / dta / rds

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "emit_r_code": self.emit_r_code,
            "emit_data": self.emit_data,
            "data_format": self.data_format,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReproducibilityEntry":
        if isinstance(d, str):
            return cls(task_id=d)
        return cls(
            task_id=d["task_id"],
            emit_r_code=d.get("emit_r_code", True),
            emit_data=d.get("emit_data", True),
            data_format=d.get("data_format", "csv"),
        )


# ------------------------------------------------------------------
# 主对象：OutputMapping
# ------------------------------------------------------------------
@dataclass
class OutputMapping:
    """
    输出映射配置。描述需要输出的表格和复现材料。

    用法:
        # 创建
        mapping = OutputMapping()
        t1 = mapping.add_table("主回归", "table_main.xlsx")
        t1.add_column("OLS-ma_dummy-badf00878f2d", label="(1)")
        t1.add_column("OLS-ma_count-28c67d01305d", label="(2)")

        mapping.add_reproducibility("OLS-ma_dummy-badf00878f2d")
        mapping.add_reproducibility("OLS-ma_count-28c67d01305d")

        # 保存
        mapping.save("output_mapping.json")

        # 加载
        mapping = OutputMapping.load("output_mapping.json")
    """

    tables: List[TableSpec] = field(default_factory=list)
    reproducibility: List[ReproducibilityEntry] = field(default_factory=list)
    output_dir: str = "./output/"

    # ===================== 表格操作 =====================

    def add_table(self, name: str, filename: str, description: str = "") -> TableSpec:
        """添加一张表格，返回 TableSpec 供链式操作。"""
        spec = TableSpec(name=name, filename=filename, description=description)
        self.tables.append(spec)
        return spec

    def get_table(self, name: str) -> Optional[TableSpec]:
        """按名称查找表格。"""
        for t in self.tables:
            if t.name == name:
                return t
        return None

    def remove_table(self, name: str) -> "OutputMapping":
        self.tables = [t for t in self.tables if t.name != name]
        return self

    def reorder_tables(self, names: List[str]) -> "OutputMapping":
        """按给定名称顺序重排表格；不在列表中的追加到末尾。"""
        by_name = {t.name: t for t in self.tables}
        ordered = [by_name[n] for n in names if n in by_name]
        remaining = [t for t in self.tables if t.name not in set(names)]
        self.tables = ordered + remaining
        return self

    # ===================== 复现材料操作 =====================

    def add_reproducibility(self, task_id: str, *,
                            emit_r_code: bool = True,
                            emit_data: bool = True,
                            data_format: str = "csv") -> "OutputMapping":
        """添加一个需要输出复现材料的任务。"""
        if any(r.task_id == task_id for r in self.reproducibility):
            return self  # 已存在则跳过
        self.reproducibility.append(ReproducibilityEntry(
            task_id=task_id,
            emit_r_code=emit_r_code,
            emit_data=emit_data,
            data_format=data_format,
        ))
        return self

    def remove_reproducibility(self, task_id: str) -> "OutputMapping":
        self.reproducibility = [r for r in self.reproducibility if r.task_id != task_id]
        return self

    # ===================== 汇总查询 =====================

    def all_task_ids(self) -> List[str]:
        """所有被引用的 task_id（去重，保序）。"""
        seen = set()
        ids = []
        for t in self.tables:
            for tid in t.task_ids:
                if tid not in seen:
                    seen.add(tid)
                    ids.append(tid)
        for r in self.reproducibility:
            if r.task_id not in seen:
                seen.add(r.task_id)
                ids.append(r.task_id)
        return ids

    # ===================== JSON 持久化 =====================

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"output_dir": self.output_dir}
        if self.tables:
            d["tables"] = [t.to_dict() for t in self.tables]
        if self.reproducibility:
            d["reproducibility"] = [r.to_dict() for r in self.reproducibility]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OutputMapping":
        return cls(
            output_dir=d.get("output_dir", "./output/"),
            tables=[TableSpec.from_dict(t) for t in d.get("tables", [])],
            reproducibility=[ReproducibilityEntry.from_dict(r) for r in d.get("reproducibility", [])],
        )

    def save(self, path: str | Path) -> None:
        """保存为 JSON 文件。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> "OutputMapping":
        """从 JSON 文件加载。"""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    # ===================== 校验 =====================

    def validate(self, available_task_ids: Optional[set] = None) -> List[str]:
        """
        校验配置合法性，返回错误信息列表（空列表 = 通过）。

        参数:
            available_task_ids: 可选，Plan 中实际存在的 task_id 集合。
                              传入时会检查引用的 task_id 是否存在。
        """
        errors = []

        # 表格名唯一
        table_names = [t.name for t in self.tables]
        dupes = [n for n in table_names if table_names.count(n) > 1]
        if dupes:
            errors.append(f"Duplicate table names detected: {set(dupes)}")

        # 每张表至少一列
        for t in self.tables:
            if not t.columns:
                errors.append(f"Table '{t.name}' does not define any columns")

        # task_id 存在性检查
        if available_task_ids is not None:
            for tid in self.all_task_ids():
                if tid not in available_task_ids:
                    errors.append(f"task_id '{tid}' does not exist in the Plan")

        return errors

    def __repr__(self) -> str:
        t_count = len(self.tables)
        r_count = len(self.reproducibility)
        return f"OutputMapping(tables={t_count}, reproducibility={r_count})"
