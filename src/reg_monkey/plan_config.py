from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class TaskNodeConfig:
    section: str
    tags: List[str] = field(default_factory=list)
    note: str = ""
    task_spec: Dict[str, Any] = field(default_factory=dict)
    children: List["TaskNodeConfig"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section": self.section,
            "tags": list(self.tags or []),
            "note": self.note,
            "task_spec": dict(self.task_spec or {}),
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskNodeConfig":
        children = [
            cls.from_dict(item)
            for item in (data.get("children") or [])
        ]
        return cls(
            section=data.get("section") or "",
            tags=list(data.get("tags") or []),
            note=data.get("note") or "",
            task_spec=dict(data.get("task_spec") or {}),
            children=children,
        )


@dataclass
class PlanConfig:
    version: str = "1.0"
    roots: List[TaskNodeConfig] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "roots": [node.to_dict() for node in self.roots],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanConfig":
        roots = [
            TaskNodeConfig.from_dict(item)
            for item in (data.get("roots") or [])
        ]
        return cls(
            version=data.get("version") or "1.0",
            roots=roots,
        )


__all__ = ["PlanConfig", "TaskNodeConfig"]
