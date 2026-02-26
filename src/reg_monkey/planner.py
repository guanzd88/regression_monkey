# planner_withNameBracketBidir.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Iterable, Literal, Deque, Union
from collections import deque
import re

from reg_monkey.task_obj import StandardRegTask
from reg_monkey.util import name_bracket_bidir
from reg_monkey.plan_config import PlanConfig, TaskNodeConfig


# =========================
# 用于安全装载 exec_result（保留原结构，但避免 DataFrame 打印时递归崩掉）
# =========================
class OpaqueExecResult:
    """
    包装 exec_result（dict: {'forward_res': DataFrame, 'opposite_res': DataFrame|None}）
    - .value 保留原始结构（你要用原 dict 就取 .value）
    - repr/str 不展开 DataFrame，避免 pandas.pretty 触发 StopIteration
    """
    __slots__ = ("value",)

    def __init__(self, value: Any):
        self.value = value

    def __repr__(self) -> str:
        v = self.value
        if isinstance(v, dict):
            keys = list(v.keys())
            return f"<exec_result dict keys={keys}>"
        return f"<exec_result {type(v).__name__}>"

    __str__ = __repr__


# ---------------------------
# TaskNode - 任务节点
# ---------------------------
@dataclass(eq=False)
class TaskNode:
    task: "StandardRegTask"
    section: str
    tags: List[str] = field(default_factory=list)
    note: str = ""
    children: List["TaskNode"] = field(default_factory=list)

    def add_child(self, child: "TaskNode") -> "TaskNode":
        """
        双保险：
        1) child.task.task_id 为空 -> 生成
        2) child.task.task_id == parent.task.task_id（继承父 id）-> 重新生成
        同时兜底：挂载时做变量名 name_bracket_bidir 规范化
        """
        try:
            parent_task = getattr(self, "task", None)
            parent_id = getattr(parent_task, "task_id", None) if parent_task is not None else None

            t = getattr(child, "task", None)
            if t is not None:
                # 1) 变量名规范化兜底
                try:
                    _normalize_task_varnames_inplace(t)
                except Exception:
                    pass

                # 2) task_id 生成/重置
                cur_id = getattr(t, "task_id", None)
                need_regen = (cur_id in (None, "")) or (parent_id not in (None, "") and cur_id == parent_id)
                if need_regen:
                    gen = getattr(t, "_generate_task_id", None)
                    if callable(gen):
                        t.task_id = gen()
        except Exception:
            pass

        self.children.append(child)
        return child


# ---------------------------
# 辅助函数：累积
# ---------------------------
def _accumulate(lst: List[str]) -> List[List[str]]:
    acc, cur = [], []
    for x in lst:
        cur = cur + [x]
        acc.append(cur)
    return acc


# ---------------------------
# 变量名规范化：对 StandardRegTask 做就地处理
# ---------------------------
_FIELD_CALL_PAT = re.compile(r"field\(\s*([^)]+?)\s*\)")


def _rewrite_field_calls(expr: Any) -> Any:
    """
    把 subset.classification_conditions 里出现的 field(<col>) 的 <col> 也做 name_bracket_bidir。
    """
    if not isinstance(expr, str) or not expr:
        return expr

    def _repl(m):
        inner = (m.group(1) or "").strip()
        try:
            inner2 = name_bracket_bidir(inner)
        except Exception:
            inner2 = inner
        return f"field({inner2})"

    return _FIELD_CALL_PAT.sub(_repl, expr)


def _wrap_generate_roles_with_bidir(task: StandardRegTask) -> None:
    """
    roles.fields 也要走 name_bracket_bidir。
    roles 是动态生成（generate_roles），这里包装 generate_roles：
    - 返回 dict 且含 fields(list[str]) 时，转换后再返回
    """
    if getattr(task, "_name_bidir_roles_wrapped", False):
        return

    orig = getattr(task, "generate_roles", None)
    if not callable(orig):
        return

    def _wrapped_generate_roles(*args, **kwargs):
        roles = orig(*args, **kwargs)
        if isinstance(roles, dict) and isinstance(roles.get("fields"), list):
            try:
                roles = dict(roles)
                roles["fields"] = name_bracket_bidir(list(roles["fields"]))
            except Exception:
                pass
        return roles

    try:
        setattr(task, "_orig_generate_roles", orig)
        setattr(task, "generate_roles", _wrapped_generate_roles)
        setattr(task, "_name_bidir_roles_wrapped", True)
    except Exception:
        pass


def _normalize_task_varnames_inplace(task: StandardRegTask) -> StandardRegTask:
    """
    按你的清单就地处理这些位置：
    y / X / controls / category_controls / panel_ids(entity/time) / subset(field(...)) / roles.fields
    """
    # y
    if isinstance(getattr(task, "y", None), str):
        task.y = name_bracket_bidir(task.y)

    # X / controls / category_controls
    X0 = getattr(task, "X", None)
    C0 = getattr(task, "controls", None)
    CC0 = getattr(task, "category_controls", None)

    if isinstance(X0, str):
        task.X = name_bracket_bidir(X0)
    elif isinstance(X0, list):
        task.X = name_bracket_bidir(X0)
    elif isinstance(X0, dict):
        # 处理分组字典
        normalized = {}
        for k, v in X0.items():
            if isinstance(v, str):
                normalized[k] = name_bracket_bidir(v)
            elif isinstance(v, list):
                normalized[k] = name_bracket_bidir(v)
            else:
                normalized[k] = v
        task.X = normalized

    if isinstance(C0, list):
        task.controls = name_bracket_bidir(C0)
    elif isinstance(C0, str):
        task.controls = name_bracket_bidir(C0)

    if isinstance(CC0, list):
        task.category_controls = name_bracket_bidir(CC0)
    elif isinstance(CC0, str):
        task.category_controls = name_bracket_bidir(CC0)

    # panel_ids(entity/time)
    pids = getattr(task, "panel_ids", None)
    if isinstance(pids, dict):
        pids2 = dict(pids)
        for k in ("entity", "time"):
            v = pids2.get(k)
            if isinstance(v, str) and v:
                try:
                    pids2[k] = name_bracket_bidir(v)
                except Exception:
                    pass
        task.panel_ids = pids2

    # subset(field(...))
    subset = getattr(task, "subset", None)
    if isinstance(subset, dict):
        subset2 = dict(subset)
        cf = subset2.get("classification_field")
        if isinstance(cf, str) and cf:
            subset2["classification_field"] = name_bracket_bidir(cf)

        cond = subset2.get("classification_conditions")
        subset2["classification_conditions"] = _rewrite_field_calls(cond)

        task.subset = subset2

    # roles.fields（通过包装 generate_roles 来实现）
    _wrap_generate_roles_with_bidir(task)

    return task


# ---------------------------
# Plan 类 - 主控制类
# ---------------------------
class Plan:
    """
    任务规划器（Plan）
    """

    def __init__(self, baseline: "StandardRegTask | List[StandardRegTask]"):
        bases = baseline if isinstance(baseline, list) else [baseline]
        if not bases:
            raise ValueError("Plan() requires at least one StandardRegTask as baseline")

        self.roots: List[TaskNode] = []
        for b in bases:
            t = b.copy_with(name="baseline", note="baseline")
            try:
                _normalize_task_varnames_inplace(t)
            except Exception:
                pass
            self.roots.append(TaskNode(task=t, section="baseline", tags=["baseline"], note="baseline"))

        # ensure task_id for roots
        try:
            for node in self.roots:
                t = getattr(node, "task", None)
                if t is not None and (getattr(t, "task_id", None) in (None, "")):
                    gen = getattr(t, "_generate_task_id", None)
                    if callable(gen):
                        t.task_id = gen()
        except Exception:
            pass

    def to_config(self) -> PlanConfig:
        roots = [self._node_to_config(node) for node in self.roots]
        return PlanConfig(version="1.0", roots=roots)

    @classmethod
    def from_config(cls, config: "PlanConfig | Dict[str, Any]") -> "Plan":
        if isinstance(config, dict):
            cfg = PlanConfig.from_dict(config)
        else:
            cfg = config
        plan = object.__new__(cls)
        plan.roots = [cls._node_from_config(node_cfg) for node_cfg in getattr(cfg, "roots", [])]
        return plan

    @staticmethod
    def _node_to_config(node: TaskNode) -> TaskNodeConfig:
        task = getattr(node, "task", None)
        spec = task.to_spec() if hasattr(task, "to_spec") and task is not None else {}
        children = [Plan._node_to_config(child) for child in getattr(node, "children", [])]
        return TaskNodeConfig(
            section=getattr(node, "section", ""),
            tags=list(getattr(node, "tags", []) or []),
            note=getattr(node, "note", ""),
            task_spec=spec,
            children=children,
        )

    @classmethod
    def _node_from_config(cls, node_cfg: "TaskNodeConfig | Dict[str, Any]") -> TaskNode:
        if isinstance(node_cfg, dict):
            config = TaskNodeConfig.from_dict(node_cfg)
        else:
            config = node_cfg
        task_spec = getattr(config, "task_spec", {}) or {}
        task = StandardRegTask.from_spec(task_spec) if task_spec else None
        node = TaskNode(
            task=task,
            section=config.section,
            tags=list(config.tags or []),
            note=config.note,
            children=[],
        )
        for child_cfg in getattr(config, "children", []) or []:
            node.children.append(cls._node_from_config(child_cfg))
        return node

    # ---------------------------
    # 数据上下文分配
    # ---------------------------
    def assign_data_contexts(self) -> "Plan":
        """在任务树初始化阶段为每个任务分配数据变量上下文。"""
        for root in self.roots:
            root_task = getattr(root, "task", None)
            if root_task is None:
                continue

            baseline_ds = self._get_task_dataset_key(root_task)
            root_task._data_context = {
                "input_data_variable": f"df_raw_{baseline_ds}",
                "output_data_variable": f"df_prep_{baseline_ds}_baseline",
                "regression_data_variable": f"df_prep_{baseline_ds}_baseline",
            }

            for node in self._traverse_nodes([root], order="dfs"):
                if node is root:
                    continue
                task = getattr(node, "task", None)
                if task is None:
                    continue
                self._assign_task_context(task, baseline_ds)

        return self

    def _assign_task_context(self, task: Any, baseline_ds: str) -> None:
        ds = self._get_task_dataset_key(task) or baseline_ds
        if_reprep = bool(getattr(task, "if_reprep", False))

        if if_reprep or ds != baseline_ds:
            suffix = self._get_safe_task_suffix(task)
            ctx = {
                "input_data_variable": f"df_raw_{ds}",
                "output_data_variable": f"df_prep_{ds}_{suffix}",
                "regression_data_variable": f"df_prep_{ds}_{suffix}",
            }
        else:
            ctx = {
                "input_data_variable": f"df_prep_{baseline_ds}_baseline",
                "output_data_variable": None,
                "regression_data_variable": f"df_prep_{baseline_ds}_baseline",
            }

        setattr(task, "_data_context", ctx)

    def _get_safe_task_suffix(self, task: Any) -> str:
        candidate = getattr(task, "task_id", None) or getattr(task, "name", None) or "task"
        candidate = re.sub(r"[^A-Za-z0-9_]", "_", str(candidate))
        if candidate and candidate[0].isdigit():
            candidate = "_" + candidate
        candidate = candidate.lower()
        return candidate or "task"

    def _get_task_dataset_key(self, task: Any) -> str:
        if hasattr(task, "get_dataset_key") and callable(getattr(task, "get_dataset_key")):
            try:
                key = task.get_dataset_key()
                if key:
                    return key
            except Exception:
                pass
        dataset = getattr(task, "dataset", None)
        if dataset:
            safe = re.sub(r"[^A-Za-z0-9_]", "_", str(dataset))
            if safe and safe[0].isdigit():
                safe = "_" + safe
            return safe or "main"
        return "main"

    # =========================
    # ✅ 核心修复：统一派生任务（强制 task_id=None）
    # =========================
    def _spawn(self, base: StandardRegTask, **overrides) -> StandardRegTask:
        """
        所有派生任务都必须用这个：
        - 强制 task_id=None，防止继承 baseline 的 id（导致 baseline 重复 / 缓存污染 / merge 笛卡尔积）
        - 做变量名规范化，确保和 R 环境列名一致（car[-30,0] 等）
        - 数据集继承：子任务默认继承父任务 dataset，可通过 overrides 显式覆盖
        - 跨数据集校验：若子任务使用不同 dataset，自动设置 if_reprep=True
        """
        overrides = dict(overrides or {})
        overrides["task_id"] = None
        if getattr(base, "task_id", None) in (None, ""):
            gen = getattr(base, "_generate_task_id", None)
            if callable(gen):
                base.task_id = gen()
        overrides["parent_task_id"] = base.task_id
        overrides["task_id"] = None

        # 数据集继承逻辑
        base_dataset = getattr(base, "dataset", None)
        child_dataset = overrides.get("dataset")
        if child_dataset is None:
            # 子任务未显式指定 dataset，继承父任务
            overrides["dataset"] = base_dataset
        elif child_dataset != base_dataset:
            # 跨数据集：强制 if_reprep=True（跨数据集必须重新准备数据，不可覆盖）
            overrides["if_reprep"] = True

        t = base.copy_with(**overrides)
        try:
            _normalize_task_varnames_inplace(t)
        except Exception:
            pass
        return t

    def iter_roots(self) -> Iterable[TaskNode]:
        return list(self.roots)

    def iter_tree(self, root: TaskNode, order: Literal["dfs", "bfs"] = "dfs") -> Iterable[TaskNode]:
        return self._traverse_nodes([root], order=order)

    def flatten_by_tree(self, order: Literal["dfs", "bfs"] = "dfs") -> List[List["StandardRegTask"]]:
        return [[n.task for n in self._traverse_nodes([r], order=order)] for r in self.roots]

    def _traverse_nodes(self, roots: Iterable[TaskNode], order: Literal["dfs", "bfs"] = "dfs") -> List[TaskNode]:
        out: List[TaskNode] = []
        if order == "bfs":
            q: Deque[TaskNode] = deque(roots)
            while q:
                n = q.popleft()
                out.append(n)
                q.extend(n.children)
        else:
            def dfs(n: TaskNode):
                out.append(n)
                for c in n.children:
                    dfs(c)
            for r in roots:
                dfs(r)
        return out

    def only(self, pred_or_section) -> "Plan":
        """Filter plan to only *one or more sections* or nodes matching a predicate."""
        if callable(pred_or_section):
            pred: Callable[[TaskNode], bool] = pred_or_section
        else:
            if isinstance(pred_or_section, str):
                sections = {pred_or_section}
            else:
                sections = set(pred_or_section)
            pred = lambda n: n.section in sections  # noqa: E731

        keep: set[TaskNode] = set()
        parent: Dict[TaskNode, Optional[TaskNode]] = {}

        for r in self.roots:
            def dfs(n: TaskNode, p: Optional[TaskNode] = None) -> None:
                if p is not None:
                    parent[n] = p
                if pred(n):
                    cur: Optional[TaskNode] = n
                    while cur is not None:
                        if cur in keep:
                            break
                        keep.add(cur)
                        cur = parent.get(cur)
                for c in n.children:
                    dfs(c, n)
            dfs(r)

        def filter_tree(n: TaskNode) -> Optional[TaskNode]:
            if n not in keep:
                return None
            nn = TaskNode(n.task, n.section, list(n.tags), n.note, [])
            for c in n.children:
                cc = filter_tree(c)
                if cc is not None:
                    nn.children.append(cc)
            return nn

        new_plan = object.__new__(Plan)
        new_plan.roots = [x for x in (filter_tree(r) for r in self.roots) if x is not None]
        return new_plan

    def skip(self, pred_or_section) -> "Plan":
        """Mark tasks as inactive (active=False) without changing tree structure or order."""
        if callable(pred_or_section):
            pred = pred_or_section
        else:
            preds = pred_or_section
            if isinstance(preds, str):
                preds = [preds]
            else:
                preds = list(preds or [])
            preds = [p for p in preds if isinstance(p, str) and p != ""]

            def pred(n):
                t = getattr(n, "task", None)
                name = getattr(t, "name", None)
                return isinstance(name, str) and any(name.startswith(p) for p in preds)

        for root in getattr(self, "roots", []) or []:
            queue = [root]
            while queue:
                node = queue.pop(0)
                try:
                    matched = bool(pred(node))
                except Exception:
                    matched = False

                task = getattr(node, "task", None)
                if task is not None:
                    try:
                        setattr(task, "active", not matched)
                    except Exception:
                        try:
                            setattr(task, "enabled", not matched)
                        except Exception:
                            pass

                children = getattr(node, "children", None) or []
                for ch in children:
                    queue.append(ch)

        return self

    def flatten(self, order: str = "dfs") -> List["StandardRegTask"]:
        return [n.task for n in self._traverse_nodes(self.roots, order=order)]

    def preview(self, order: str = "dfs") -> str:
        lines = []
        for idx, n in enumerate(self._traverse_nodes(self.roots, order=order), start=1):
            t = n.task
            rhs_bits = (getattr(t, "X", []) or []) + (getattr(t, "controls", []) or [])
            cats = getattr(t, "category_controls", []) or []
            if cats:
                rhs_bits += [f"factor({c})" for c in cats]
            rhs = " + ".join(rhs_bits) if rhs_bits else "1"
            model = getattr(t, "model", "OLS")
            prep_fp = getattr(t, "prep_fingerprint", None)
            mode = (getattr(t, "options", {}) or {}).get("prep_mode")
            suffix = f" [{model}]"
            if prep_fp or mode:
                pieces = [model]
                if mode:
                    pieces.append(f"mode={mode}")
                if prep_fp:
                    pieces.append(f"fp={str(prep_fp)[:8]}…")
                suffix = " [" + ", ".join(pieces) + "]"
            lines.append(f"[{idx:02d}] {n.section} {t.name}: {t.y} ~ {rhs}{suffix}")
        return "\n".join(lines)

    # ---------------------------
    # 任务构建方法（全部改为 self._spawn）
    # ---------------------------
    def baseline(
        self,
        *,
        incremental_controls: bool = False,
        include_interaction: bool = False,
        dataset: str = None,
    ) -> "Plan":
        for r in self.roots:
            t = r.task
            if t is None:
                continue
            if include_interaction:
                t.interaction = True
            if dataset is not None:
                t.dataset = dataset
            t.incremental_controls = bool(incremental_controls)
        return self

    def post_perf(
        self,
        ys: List[str],
        name_fmt: str = "{base}_post_{y}",
        if_reprep: bool = False,
        include_interaction: bool = False,
        dataset: str = None,
        incremental_controls: bool = False,
    ) -> "Plan":
        def add_post(r: TaskNode) -> None:
            base = r.task
            spawn_kwargs = {
                "name": "post_perf",
                "note": f"Y→{{y}}",
                "if_reprep": if_reprep,
                "interaction": include_interaction,
                "incremental_controls": incremental_controls,
            }
            if dataset is not None:
                spawn_kwargs["dataset"] = dataset
            for y in ys:
                spawn_kwargs["y"] = y
                spawn_kwargs["note"] = f"Y→{y}"
                t = self._spawn(base, **spawn_kwargs)
                r.add_child(TaskNode(t, section="post_acq_perf", tags=["post", "replace_y"], note=f"Y→{y}"))

        return self._map_roots(add_post)

    def mechanisms(
        self,
        ys: List[str],
        name_fmt: str = "{base}_mech_{y}",
        if_reprep: bool = False,
        include_interaction: bool = False,
        dataset: str = None,
        incremental_controls: bool = False,
    ) -> "Plan":
        def add_mech(r: TaskNode) -> None:
            base = r.task
            spawn_kwargs = {
                "name": "mechanism",
                "if_reprep": if_reprep,
                "interaction": include_interaction,
                "incremental_controls": incremental_controls,
            }
            if dataset is not None:
                spawn_kwargs["dataset"] = dataset
            for y in ys:
                spawn_kwargs["y"] = y
                spawn_kwargs["note"] = f"Y→{y}"
                t = self._spawn(base, **spawn_kwargs)
                r.add_child(TaskNode(t, section="mechanism", tags=["mechanism", "replace_y"], note=f"Y→{y}"))

        return self._map_roots(add_mech)

    def heterogeneity(
        self,
        splits: List[Dict[str, Any]],
        if_reprep: bool = False,
        include_interaction: bool = False,
        dataset: str = None,
        incremental_controls: bool = False,
    ) -> "Plan":
        def add_het(r: TaskNode) -> None:
            base = r.task
            spawn_kwargs = {
                "name": "heterogeneity",
                "if_reprep": if_reprep,
                "interaction": include_interaction,
                "incremental_controls": incremental_controls,
            }
            if dataset is not None:
                spawn_kwargs["dataset"] = dataset
            for i, sp in enumerate(splits, start=1):
                subset = {
                    "classification_field": sp["field"],
                    "operator": sp["op"],
                    "classification_conditions": sp["cond"],
                }
                spawn_kwargs["subset"] = subset
                spawn_kwargs["note"] = f"subset={subset}"
                t = self._spawn(base, **spawn_kwargs)
                r.add_child(TaskNode(t, section="heterogeneity", tags=["heterogeneity", "subset"], note=f"subset={subset}"))

        return self._map_roots(add_het)

    def robust_model(
        self,
        models: List[str],
        include_interaction: bool = False,
        dataset: str = None,
        incremental_controls: bool = False,
    ) -> "Plan":
        def add_model(r: TaskNode) -> None:
            base = r.task
            spawn_kwargs = {
                "name": f"{base.model}_models_hub",
                "note": "model variants",
                "interaction": include_interaction,
                "incremental_controls": incremental_controls,
            }
            if dataset is not None:
                spawn_kwargs["dataset"] = dataset
            hub_task = self._spawn(base, **spawn_kwargs)
            hub = r.add_child(TaskNode(hub_task, section="robust_model", tags=["robust", "hub"], note="model variants"))

            for m in models:
                model_kwargs = {
                    "name": f"robust_model_{m.lower()}",
                    "model": m,
                    "note": f"model→{m}",
                    "interaction": include_interaction,
                    "incremental_controls": incremental_controls,
                }
                if dataset is not None:
                    model_kwargs["dataset"] = dataset
                tm = self._spawn(base, **model_kwargs)
                hub.add_child(TaskNode(tm, section="robust_model", tags=["robust", "model"], note=f"model→{m}"))

            # flatten hub
            try:
                if not hub.children:
                    try:
                        r.children.remove(hub)
                    except ValueError:
                        pass
                else:
                    try:
                        idx = r.children.index(hub)
                    except ValueError:
                        idx = len(r.children)
                    promoted = list(hub.children)
                    try:
                        r.children.pop(idx)
                    except Exception:
                        try:
                            r.children.remove(hub)
                        except Exception:
                            pass
                    for offset, ch in enumerate(promoted):
                        r.children.insert(idx + offset, ch)
            except Exception:
                pass

        return self._map_roots(add_model)

    def robust_measure(
        self,
        *,
        y_alternatives: Optional[List[str]] = None,
        x_sets: Optional[List[Union[List[str], Dict[str, Union[str, List[str]]]]]] = None,
        if_reprep: bool = False,
        control_sets: Optional[List[List[str]]] = None,
        include_interaction: bool = False,
        dataset: str = None,
        incremental_controls: bool = False,
    ) -> "Plan":
        y_alternatives = y_alternatives or []
        x_sets = x_sets or []
        control_sets = control_sets or []

        def add_meas(r: TaskNode) -> None:
            base = r.task
            hub_kwargs = {
                "name": "robust_measure_hub",
                "note": "measure variants",
                "if_reprep": if_reprep,
                "interaction": include_interaction,
                "incremental_controls": incremental_controls,
            }
            if dataset is not None:
                hub_kwargs["dataset"] = dataset
            hub_task = self._spawn(base, **hub_kwargs)
            hub = r.add_child(TaskNode(hub_task, section="robust_measure", tags=["robust", "measure"], note="measure variants"))

            base_kwargs = {
                "interaction": include_interaction,
                "if_reprep": if_reprep,
                "incremental_controls": incremental_controls,
            }
            if dataset is not None:
                base_kwargs["dataset"] = dataset

            for y in y_alternatives:
                t = self._spawn(base, name="robust_measure_y", y=y, note=f"Y→{y}", **base_kwargs)
                hub.add_child(TaskNode(t, section="robust_measure", tags=["robust", "replace_y"], note=f"Y→{y}"))

            for xs in x_sets:
                t = self._spawn(base, name="robust_measure_X", X=xs, note=f"X→{xs}", **base_kwargs)
                hub.add_child(TaskNode(t, section="robust_measure", tags=["robust", "replace_X"], note=f"X→{xs}"))

            for cs in control_sets:
                t = self._spawn(base, name="robust_measure_controls", controls=cs, note=f"controls→{cs}", **base_kwargs)
                hub.add_child(TaskNode(t, section="robust_measure", tags=["robust", "replace_controls"], note=f"controls→{cs}"))

            # flatten hub
            try:
                if not hub.children:
                    try:
                        r.children.remove(hub)
                    except ValueError:
                        pass
                else:
                    try:
                        idx = r.children.index(hub)
                    except ValueError:
                        idx = len(r.children)
                    promoted = list(hub.children)
                    try:
                        r.children.pop(idx)
                    except Exception:
                        try:
                            r.children.remove(hub)
                        except Exception:
                            pass
                    for offset, ch in enumerate(promoted):
                        r.children.insert(idx + offset, ch)
            except Exception:
                pass

        return self._map_roots(add_meas)

    # ---------------------------
    # render_code_in_batch 方法（保留+增加兜底规范化）
    # ---------------------------
    def render_code_in_batch(
        self,
        *,
        order: Literal["dfs", "bfs"] = "dfs",
        codegen: Optional[Callable[["StandardRegTask"], Dict[str, Any]]] = None,
        strict: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ) -> "Plan":
        log = logger or (lambda msg: None)

        try:
            self.assign_data_contexts()
        except Exception:
            pass

        # Step 0: 兜底规范化一遍
        for node in self._traverse_nodes(self.roots, order=order):
            t = getattr(node, "task", None)
            if t is None:
                continue
            try:
                _normalize_task_varnames_inplace(t)
            except Exception:
                pass

        # Step 1: 生成代码
        for root in self.roots:
            for node in self._traverse_nodes([root], order=order):
                t = node.task

                if codegen is not None:
                    try:
                        out = codegen(t)
                    except Exception as e:
                        raise RuntimeError(f"codegen failed for task {getattr(t, 'name', '<unnamed>')}: {e}") from e
                elif hasattr(t, "generate_code"):
                    try:
                        out = t.generate_code()
                    except Exception as e:
                        raise RuntimeError(f"generate_code() failed for task {getattr(t, 'name', '<unnamed>')}: {e}") from e
                else:
                    raise RuntimeError(f"Task {getattr(t,'name','<unnamed>')} has neither external codegen nor generate_code().")

                if not isinstance(out, dict):
                    raise TypeError("codegen/generate_code must return a dict")

                fp = out.get("prep_fingerprint", getattr(t, "prep_fingerprint", None))
                code_text = (
                    out.get("code_text")
                    or out.get("combined")
                    or "\n".join(
                        filter(
                            None,
                            [
                                out.get("dependencies"),
                                out.get("prepare_code"),
                                out.get("execute_code"),
                            ],
                        )
                    )
                    or None
                )

                setattr(t, "prep_fingerprint", fp)
                setattr(t, "code_text", code_text)
                setattr(t, "_code_segments", out)
                node.task = t

        # Step 2: 指纹继承
        for root in self.roots:
            rt = root.task
            root_fp = getattr(rt, "prep_fingerprint", None)
            if root_fp is None:
                raise RuntimeError(f"Root task {getattr(rt,'name','<unnamed>')} has no prep_fingerprint after code generation.")

            for node in self._traverse_nodes([root], order=order):
                t = node.task
                if_reprep = bool(getattr(t, "if_reprep", False))

                if if_reprep:
                    if getattr(t, "prep_fingerprint", None) is None:
                        raise RuntimeError(f"Task {getattr(t,'name','<unnamed>')} is if_reprep=True but has no prep_fingerprint.")
                    continue

                if getattr(t, "prep_fingerprint", None) != root_fp:
                    new_t = t.copy_with(prep_fingerprint=root_fp)
                    if getattr(new_t, "prep_fingerprint", None) != root_fp:
                        setattr(new_t, "prep_fingerprint", root_fp)
                    node.task = new_t

            if strict:
                bad = []
                for node in self._traverse_nodes([root], order=order):
                    t = node.task
                    if not getattr(t, "if_reprep", False):
                        if getattr(t, "prep_fingerprint", None) != root_fp:
                            bad.append(getattr(t, "name", "<unnamed>"))

                if bad:
                    for node in self._traverse_nodes([root], order=order):
                        t = node.task
                        if getattr(t, "name", None) in bad:
                            setattr(t, "prep_fingerprint", root_fp)

                    bad2 = [
                        getattr(t, "name", "<unnamed>")
                        for node in self._traverse_nodes([root], order=order)
                        for t in [node.task]
                        if (not getattr(t, "if_reprep", False))
                        and getattr(t, "prep_fingerprint", None) != root_fp
                    ]

                    if bad2:
                        raise RuntimeError(
                            f"Tree rooted at {getattr(rt,'name','<unnamed>')} has non-inherited fingerprints: " + ", ".join(bad2)
                        )

        return self

    # ---------------------------
    # output_r_code_by_tree（CodeExecutor.run 依赖这个，必须存在）
    # ---------------------------

    def output_r_code_by_tree(
        self,
        include_prepare: bool = True,
        with_headers: bool = True,
        collect_dependencies: bool = True,
        on_missing_prepare: str = "comment",
        *,
        selection_excel: str = "reg_monkey_task_selection.xlsx",
        selection_mark_col: str = "emit_r_code",
    ) -> List[Dict[str, Any]]:
        """
        按任务树输出 R 代码（仅输出 active=True 的任务）。

        Excel 选择逻辑（修复版）：
        - 优先使用复合键 (parent_task_id, task_id) 来精确点选任务，避免同 task_id 在不同树被误激活。
        - 若 Excel 缺少 parent_task_id 列，则降级为仅 task_id 匹配（兼容旧表）。
        """

        try:
            self.assign_data_contexts()
        except Exception:
            pass

        def _is_active(t: Any) -> bool:
            return bool(getattr(t, "active", getattr(t, "enabled", True)))

        def _need_prepare_between(prev_task: Any | None, cur_task: Any) -> bool:
            ctx = getattr(cur_task, "_data_context", None)
            if isinstance(ctx, dict):
                return bool(ctx.get("output_data_variable"))
            if prev_task is None:
                return True
            cur = bool(getattr(cur_task, "if_reprep", False))
            prev = bool(getattr(prev_task, "if_reprep", False))
            return cur or prev

        def _norm_id(v: Any) -> str:
            """
            把 None/NaN/空白统一成 ''；其他转成 str 并 strip。
            """
            try:
                # pandas NaN
                import pandas as pd  # type: ignore
                if v is None or (isinstance(v, float) and v != v) or (hasattr(pd, "isna") and pd.isna(v)):
                    return ""
            except Exception:
                if v is None:
                    return ""
            s = str(v)
            return s.strip()

        def _apply_excel_selection_if_any() -> None:
            """
            读取选择表并就地更新各 task.active：
            - 如果表中存在 parent_task_id + task_id + emit_r_code：使用复合键匹配
            - 否则如果只有 task_id + emit_r_code：降级用 task_id 匹配
            - 如果没有任何选中行：不覆盖现有 active
            """
            try:
                import pandas as pd
                from pathlib import Path

                fp = Path(selection_excel)
                if not fp.is_absolute():
                    fp = Path.cwd() / fp
                if not fp.exists():
                    return

                df = pd.read_excel(fp)

                # 必要字段检查：task_id + mark
                if "task_id" not in df.columns or selection_mark_col not in df.columns:
                    return

                # 选中：mark 非空（NaN/None/空白字符串都算未选）
                mark = df[selection_mark_col]
                mark_non_empty = mark.notna() & (mark.astype(str).str.strip() != "")
                df_sel = df.loc[mark_non_empty].copy()
                if df_sel.empty:
                    return

                has_parent = "parent_task_id" in df_sel.columns

                # 生成选中集合：优先复合键，否则降级为 task_id
                if has_parent:
                    active_pairs = set(
                        (
                            _norm_id(p),
                            _norm_id(tid),
                        )
                        for p, tid in zip(df_sel["parent_task_id"], df_sel["task_id"])
                    )
                    # 若表里 parent_task_id 都是空，active_pairs 可能退化；这种情况也没必要覆盖
                    if not active_pairs:
                        return
                else:
                    active_task_ids = set(_norm_id(tid) for tid in df_sel["task_id"])
                    if not active_task_ids:
                        return

                # 强制覆盖：先全体 inactive，再激活选中项
                for root in getattr(self, "roots", []) or []:
                    for node in self._traverse_nodes([root], order="dfs"):
                        t = getattr(node, "task", None)
                        if t is None:
                            continue

                        tid = _norm_id(getattr(t, "task_id", None))
                        pid = _norm_id(getattr(t, "parent_task_id", None))

                        if has_parent:
                            is_on = (pid, tid) in active_pairs
                        else:
                            is_on = tid in active_task_ids

                        try:
                            setattr(t, "active", is_on)
                        except Exception:
                            try:
                                setattr(t, "enabled", is_on)
                            except Exception:
                                pass

            except Exception:
                # 读取/解析失败时，不影响原逻辑
                return

        # 1) 先按 Excel 点选覆盖 active（如果有）
        _apply_excel_selection_if_any()

        results: List[Dict[str, Any]] = []
        trees: List[List[Any]] = self.flatten_by_tree() or []

        for ti, tasks in enumerate(trees):
            if not tasks:
                results.append({"tree_id": None, "r_script": "", "nodes": [], "deps": []})
                continue

            # 输出前再兜底规范化一次
            for t in tasks:
                try:
                    _normalize_task_varnames_inplace(t)
                except Exception:
                    pass

            # 2) 只保留 active 任务（执行/输出都只针对它们）
            active_tasks = [t for t in tasks if _is_active(t)]

            if not active_tasks:
                first_task_id = getattr(tasks[0], "task_id", None)
                results.append({"tree_id": first_task_id, "r_script": "", "nodes": [], "deps": []})
                continue

            # 3) 缓存每个 active task 的 codegen 结果（避免重复 generate_code）
            code_cache: List[Dict[str, Any]] = []
            for t in active_tasks:
                obj = t.generate_code() or {}
                setattr(t, "_code_segments", obj)
                code_cache.append(
                    {
                        "prepare_code": (obj.get("prepare_code") or "").strip(),
                        "execute_code": (obj.get("execute_code") or "").strip(),
                        "post_regression_code": (obj.get("post_regression_code") or "").strip(),
                        "combined": (obj.get("combined") or "").strip(),
                        "dependencies": list(obj.get("dependencies") or []),
                    }
                )

            first_task_id = getattr(tasks[0], "task_id", None)
            deps_set = set()
            script_parts: List[str] = []

            prev_active: Any | None = None
            for idx, task in enumerate(active_tasks):
                cached = code_cache[idx]
                prep = cached["prepare_code"]
                body = cached["execute_code"] or cached["combined"]
                post = cached["post_regression_code"]

                if collect_dependencies:
                    for d in cached["dependencies"]:
                        if d:
                            deps_set.add(d)

                node_chunks: List[str] = []

                # prepare：仅在 active 任务之间需要时输出
                if include_prepare and _need_prepare_between(prev_active, task):
                    if prep:
                        if with_headers:
                            header = f"# ---- [prep] {getattr(task, 'name', f'task_{idx}')}"
                            model = getattr(task, "model", "") or getattr(task, "note", "")
                            header = header + (f" ({model})" if model else "") + " ----\n"
                            node_chunks.append(header + prep)
                        else:
                            node_chunks.append(prep)
                    else:
                        msg = f"# MISSING prepare_code at task '{getattr(task,'name','')}' (idx={idx})"
                        if on_missing_prepare == "raise":
                            raise RuntimeError(msg)
                        node_chunks.append(msg)

                # execute + post
                if body:
                    if with_headers:
                        label = getattr(task, "name", "")
                        meta = getattr(task, "model", "") or getattr(task, "note", "")
                        header = f"#----------------{label}" + (f" ({meta})" if meta else "") + " ----------------#\n"
                        node_chunks.append(header + body)
                    else:
                        node_chunks.append(body)
                if post:
                    node_chunks.append(post)

                node_text = "\n".join([b for b in node_chunks if b and b.strip()]).strip()
                setattr(task, "code_text", node_text)
                if node_text:
                    script_parts.append(node_text)

                prev_active = task

            r_script = "\n\n".join(script_parts).strip()
            results.append(
                {
                    "tree_id": first_task_id,
                    "r_script": r_script,
                    "nodes": active_tasks,
                    "deps": sorted(deps_set) if collect_dependencies else [],
                }
            )

        return results
        
    # ---------------------------
    # _map_roots
    # ---------------------------
    def _map_roots(self, fn: Callable[[TaskNode], None]) -> "Plan":
        for root in self.roots:
            fn(root)
        return self

    # ---------------------------
    # evaluate_acceptance_over_forest（保留）
    # ---------------------------
    def evaluate_acceptance_over_forest(
            self,
            *,
            order: Literal["dfs", "bfs"] = "dfs",
        ) -> List[Dict[str, Any]]:

            def _alpha_to_mark(alpha: float) -> int:
                """根据p-value返回显著性等级"""
                if alpha <= 0.01:
                    return 3
                if alpha <= 0.05:
                    return 2
                if alpha <= 0.10:
                    return 1
                return 0

            def _evaluate_single_result(exec_result_dict: Dict, X_vars: List[str]) -> Dict[str, int]:
                """
                评估单个回归结果（forward_res或opposite_res）
                
                参数:
                    exec_result_dict: {'coefficients': DataFrame, ...}
                    X_vars: 自变量名称列表
                
                返回:
                    {变量名: 显著性等级（带符号）}
                """
                if exec_result_dict is None:
                    return None
                
                if not isinstance(exec_result_dict, dict):
                    return None
                
                coefficients_df = exec_result_dict.get('coefficients')
                if coefficients_df is None:
                    return None
                
                result = {}
                
                for var in X_vars:
                    # 在coefficients DataFrame中查找该变量
                    mask = coefficients_df['Variable'] == var
                    if not mask.any():
                        # 变量不在结果中，跳过
                        continue
                    
                    row = coefficients_df[mask].iloc[0]
                    coefficient = row['Estimate']
                    p_value = row['P_Value']
                    
                    # 计算显著性等级
                    significance = _alpha_to_mark(p_value)
                    
                    # 根据系数符号调整
                    if coefficient < 0:
                        significance = -significance
                    
                    result[var] = significance
                
                return result

            results: List[Dict[str, Any]] = []

            for root in self.roots:
                for node in self._traverse_nodes([root], order=order):
                    t = getattr(node, "task", None)
                    if t is None:
                        continue

                    # 获取exec_result和X字段
                    exec_result = getattr(t, "exec_result", None)
                    X = getattr(t, "X", None)
                    
                    # 确保X是列表格式
                    if isinstance(X, str):
                        X_vars = [X]
                    elif isinstance(X, list):
                        X_vars = X
                    else:
                        X_vars = []
                    
                    # 初始化评估结果
                    evaluation_result = {
                        "forward_res": None,
                        "opposite_res": None
                    }
                    
                    # 如果有exec_result，进行评估
                    if exec_result is not None:
                        # 处理OpaqueExecResult包装
                        if hasattr(exec_result, 'value'):
                            exec_result = exec_result.value
                        
                        if isinstance(exec_result, dict):
                            # 评估forward_res
                            forward_res = exec_result.get('forward_res')
                            if forward_res is not None:
                                evaluation_result['forward_res'] = _evaluate_single_result(forward_res, X_vars)
                            
                            # 评估opposite_res
                            opposite_res = exec_result.get('opposite_res')
                            if opposite_res is not None:
                                evaluation_result['opposite_res'] = _evaluate_single_result(opposite_res, X_vars)

                    results.append({
                        "parent_task_id": getattr(t, "parent_task_id", None),
                        "task_id": getattr(t, "task_id", None),
                        "name": getattr(t, "name", "<unnamed>"),
                        "section": getattr(node, "section", ""),
                        "accepted": evaluation_result.get('forward_res') is not None,  # 如果有forward结果就算accepted
                        "mark": evaluation_result,  # 将完整的评估结果存在mark字段
                    })
            
            return results
        # ---------------------------
        # to_materialize（✅ merge key = task_id + section；✅ exec_result 保留结构但可安全打印）
        # ---------------------------

    def to_materialize(self,output_file_name='reg_monkey_task_selection.xlsx',output_to_file=False):
            import pandas as pd

            task_cols = [
                "parent_task_id",
                "task_id",
                "name",
                "dataset",
                "model",
                "y",
                "X",
                "controls",
                "category_controls",
                "category_controls_mapping",
                "panel_ids",
                "exec_result",
                "code_text",
                "subset",
                "if_reprep",
                "active",
                "emit_r_code"
            ]

            def _safe_cell(v: Any) -> Any:
                # exec_result：保留结构但包装一下，避免 pandas pretty 崩
                if isinstance(v, dict) and ("forward_res" in v or "opposite_res" in v):
                    return OpaqueExecResult(v)
                return v

            base_cols = [
                "parent_task_id",
                "task_id",
                "name",
                "dataset",
                "model",
                "y",
                "X",
                "controls",
                "category_controls",
                "category_controls_mapping",
                "panel_ids",
                "exec_result",
                "subset",
                "if_reprep",
                "active",
                "emit_r_code"
            ]
            final_cols = base_cols + ["section", "mark"]

            # ✅ 新方案：每棵树单独处理，然后纵向合并
            tree_dfs = []
            
            for root in getattr(self, "roots", []):
                rows = []
                # 1. 遍历当前树收集任务数据
                for node in self._traverse_nodes([root], order="dfs"):
                    t = getattr(node, "task", None)
                    if t is None:
                        continue
                    rec = {}
                    for c in task_cols:
                        rec[c] = _safe_cell(getattr(t, c, None))
                    rec["section"] = getattr(node, "section", None)
                    rows.append(rec)
                
                if not rows:
                    continue
                
                # 当前树的DataFrame
                tree_df = pd.DataFrame(rows)
                
                # 2. 调用evaluate_acceptance_over_forest获取当前树的acceptance结果
                # 创建临时Plan对象，只包含当前树
                temp_plan = object.__new__(Plan)
                temp_plan.roots = [root]
                temp_plan.cfg = getattr(self, "cfg", None)
                
                tree_acc_list = temp_plan.evaluate_acceptance_over_forest(order="dfs")
                
                # 3. 当前树内部merge（不会产生重复）
                tree_acc_df = pd.DataFrame(tree_acc_list) if tree_acc_list else pd.DataFrame(columns=["parent_task_id", "task_id", "section", "mark"])
                for need in ["parent_task_id", "task_id", "section", "mark"]:
                    if need not in tree_acc_df.columns:
                        tree_acc_df[need] = pd.NA
                tree_acc_df = tree_acc_df[["parent_task_id", "task_id", "section", "mark"]]
                
                tree_merged = tree_df.merge(tree_acc_df, on=["parent_task_id", "task_id", "section"], how="left", suffixes=("", "_acc"))
                tree_dfs.append(tree_merged)
            
            # 4. 纵向合并所有树的结果
            if not tree_dfs:
                return pd.DataFrame(columns=final_cols)
            
            merged = pd.concat(tree_dfs, ignore_index=True)
            
            for c in final_cols:
                if c not in merged.columns:
                    merged[c] = pd.NA
            if output_to_file: # 如果这个入参被标记为True则输出到文件，默认为False
                merged.drop('exec_result',errors='ignore').to_excel(output_file_name)
            return merged[final_cols]
__all__ = ["Plan"]
