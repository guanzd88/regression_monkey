from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Iterable, Literal, Deque
from collections import deque
from reg_monkey.task_obj import StandardRegTask

# ---------------------------
# TaskNode - 任务节点
# ---------------------------
@dataclass(eq=False)
class TaskNode:
    task: 'StandardRegTask'
    section: str
    tags: List[str] = field(default_factory=list)
    note: str = ""
    children: List['TaskNode'] = field(default_factory=list)

    def add_child(self, child: 'TaskNode') -> 'TaskNode':
        # --- ADDED: ensure task_id on the child task at attach-time ---
        try:
            t = getattr(child, "task", None)
            if t is not None and (getattr(t, "task_id", None) in (None, "")):
                gen = getattr(t, "_generate_task_id", None)
                if callable(gen):
                    t.task_id = gen()
        except Exception:
            # 不让 ID 生成异常影响添加流程
            pass
        # --- ORIGINAL ---
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
# Plan 类 - 主控制类
# ---------------------------
class Plan:
    """
    任务规划器（Plan）

    概述
    ----
    将一个或一组 `StandardRegTask` 作为“baseline 根”挂成森林（每个 baseline 一棵树），
    提供统一的**构建派生任务**、**遍历与筛选**、**批量代码生成**、**结果验收**与**物化导出**
    能力。每个树节点为 `TaskNode`，承载 `task`（具体回归规格）、`section`（模块/阶段标签）、
    `tags` 与子节点等信息。初始化时会确保根任务具备 `task_id`。  # noqa

    快速上手
    -------
    >>> plan = Plan(baseline_task)                              # 以 baseline 为根建一棵树
    >>> plan = (plan
    ...         .baseline(incremental_controls=True)            # 增量控制变量序列
    ...         .post_perf(["ROA_post", "ROE_post"])            # 事后绩效
    ...         .mechanisms(["Cash","Inv"])                     # 机制检验
    ...         .robust_model(["OLS","FE","RE"])                # 模型稳健性
    ...        )
    >>> plan.render_code_in_batch()                              # 批量生成代码并处理指纹继承
    >>> print(plan.preview())                                    # 人类可读的任务预览
    >>> acc = plan.evaluate_acceptance_over_forest()             # True/False + 星标（*** / ** / *）
    >>> df  = plan.to_materialize()                              # 将整片森林物化为 DataFrame

    重要属性
    --------
    - roots : List[TaskNode]
        每个 baseline 构成一棵根；构造时会复制 baseline（name=“baseline”，note=“baseline”），并尽量生成 task_id。

    结构遍历与筛选
    --------------
    - iter_roots() -> Iterable[TaskNode]
        返回根节点列表的副本。
    - iter_tree(root, order="dfs") -> Iterable[TaskNode]
        遍历单棵树（深/广）。
    - _traverse_nodes(roots, order="dfs") -> List[TaskNode]
        内部通用遍历器，供多处复用。
    - flatten(order="dfs") -> List[StandardRegTask]
        将整个森林的节点 task 平铺为列表。
    - flatten_by_tree(order="dfs") -> List[List[StandardRegTask]]
        按树分组的平铺。
    - preview(order="dfs") -> str
        以 “section name: y ~ RHS [meta]” 形式生成可读预览。
    - only(pred_or_section) -> Plan
        仅保留满足谓词或给定 section 的节点及其祖先，返回新的 Plan（不改动原树结构顺序）。
    - skip(pred_or_section) -> Plan
        按谓词或 name 前缀**标记**节点 `active=False`，不改结构与顺序（未命中置为 True）。

    任务生成（派生规格）
    -------------------
    - baseline(incremental_controls: bool=False) -> Plan
        若开启 `incremental_controls=True`：在 baseline 下按控制变量“累进”生成子任务，并将根 baseline 标记为 inactive。
    - post_perf(ys: List[str], name_fmt="{base}_post_{y}", if_reprep=False) -> Plan
        按给定 Ys 复制生成“事后绩效”任务（section="post_acq_perf"）。
    - mechanisms(ys: List[str], name_fmt="{base}_mech_{y}", if_reprep=False) -> Plan
        机制检验（section="mechanism"）。
    - heterogeneity(splits: List[dict], if_reprep=False) -> Plan
        R 侧条件表达式的异质性拆分：将 `subset` 写入 task（section="heterogeneity"）。
    - robust_model(models: List[str]) -> Plan
        基于模型名（如 "OLS"/"FE"/"RE"）展开稳健性变体；内部“hub”节点在无子项时移除，有子项时被扁平化。
    - robust_measure(y_alternatives=None, x_sets=None, control_sets=None) -> Plan
        对 y / X / controls 的替代表列进行稳健性变体扩展；同样自动扁平化 hub。

    代码生成与输出
    --------------
    - render_code_in_batch(order="dfs", codegen=None, strict=True, logger=None) -> Plan
        批量调用外部 `codegen(task)` 或 `task.generate_code()`，写回 `task.code_text` 与
        `prep_fingerprint`，并在整棵树内执行**指纹继承与一致性校验**（非 `if_reprep` 节点必须等于根指纹）。
    - output_r_code_by_tree(include_prepare=True, with_headers=True,
                            collect_dependencies=True, on_missing_prepare="comment") -> List[dict]
        生成每棵树的 R 脚本（按规则决定何时渲染 `prepare_code`；`active=False` 时不输出执行/后处理代码），
        汇总 `deps` 并把节点文本写回 `task.code_text`。

    结果验收与物化
    --------------
    - evaluate_acceptance_over_forest(order="dfs") -> List[dict]
        遍历所有节点，调用 `task.evaluate_acceptance(alpha)`：
          * baseline（name 以 "baseline" 开头）使用 `self.cfg.significance_level`；
          * 非 baseline 依次用 0.01 / 0.05 / 0.10，命中即停；
          * 星标规则：0.01→"***"、0.05→"**"、0.10→"*"，未通过为 ""；
        返回包含 `task_id/name/section/accepted/used_alpha/mark` 的列表。
    - to_materialize() -> pd.DataFrame
        将森林平铺为表，列：`task_id/name/dataset/section/model/y/X/controls/category_controls/panel_ids/exec_result/subset/if_reprep/active/mark`，
        并把上面的验收结果（转为 `[['task_id','section','mark']]`）按 `task_id` 左连接。为避免 pandas 在打印时遍历
        可迭代对象触发 `StopIteration`，对“非基础类型”字段会安全地 `repr` 序列化。

    设计约定
    --------
    * 绝不隐式修改树结构的相对顺序；`skip()` 只改 `active` 标记。
    * 代码生成阶段若节点设置 `if_reprep=True`，其 `prep_fingerprint` 必须自洽；否则非 `if_reprep` 节点会继承根指纹。
    * `TaskNode.add_child()` 与 Plan 初始化阶段会尽力为新任务生成 `task_id`，即使上游未显式提供。
    * 内部辅助 `_map_roots(fn)` 用于对每棵树“原地”应用构建操作。

    依赖与类型
    ----------
    * 依赖：`StandardRegTask`, `TaskNode`, `dataclasses`, `typing`, `collections.deque`, `pandas`（仅在物化时）。
    * 遍历顺序：`order` 取值 `"dfs"` / `"bfs"`；未指明时默认 `"dfs"`。

    """
    def __init__(self, baseline: 'StandardRegTask | List[StandardRegTask]'):
        bases = baseline if isinstance(baseline, list) else [baseline]
        if not bases:
            raise ValueError("Plan() requires at least one StandardRegTask as baseline")
        self.roots: List[TaskNode] = [
            TaskNode(
                task=b.copy_with(name=f"baseline", note="baseline"),
                section="baseline",
                tags=["baseline"],
                note="baseline",
            )
            for b in bases
        ]
        # --- ADDED: ensure task_id for every root task right after creation ---
        try:
            for _node in self.roots:
                _t = getattr(_node, "task", None)
                if _t is not None and (getattr(_t, "task_id", None) in (None, "")):
                    _gen = getattr(_t, "_generate_task_id", None)
                    if callable(_gen):
                        _t.task_id = _gen()
        except Exception:
            # 不让 ID 生成异常影响 Plan 初始化
            pass


    def iter_roots(self) -> Iterable[TaskNode]:
        return list(self.roots)

    def iter_tree(self, root: TaskNode, order: Literal["dfs", "bfs"] = "dfs") -> Iterable[TaskNode]:
        return self._traverse_nodes([root], order=order)

    def flatten_by_tree(self, order: Literal["dfs", "bfs"] = "dfs") -> List[List['StandardRegTask']]:
        return [[n.task for n in self._traverse_nodes([r], order=order)] for r in self.roots]

    def _traverse_nodes(self, roots: Iterable[TaskNode], order: Literal["dfs","bfs"] = "dfs") -> List[TaskNode]:
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
    def only(self, pred_or_section) -> 'Plan':
        """Filter plan to only *one or more sections* or nodes matching a predicate.

        Usage:
          - only("mechanism")                  # keep only the 'mechanism' section (per tree), plus ancestors
          - only(["post_acq_perf","baseline"]) # keep multiple sections
          - only(lambda n: cond)               # advanced predicate (back-compat)
        """
        # Build predicate from section name(s) or callable
        if callable(pred_or_section):
            pred: Callable[[TaskNode], bool] = pred_or_section
        else:
            if isinstance(pred_or_section, str):
                sections = {pred_or_section}
            else:
                sections = set(pred_or_section)
            pred = lambda n: n.section in sections

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

    def skip(self, pred_or_section) -> 'Plan':
        """Mark tasks as inactive (active=False) without changing tree structure or order.

        New behavior:
        - If `pred_or_section` is callable: use it as predicate over TaskNode `n`.
        - Else: treat as str or List[str] of name prefixes; match if any prefix is a
            prefix of `StandardRegTask.name`.
        - For every task in every tree: if predicate matches -> task.active = False,
            else task.active = True.
        - Preserve tree and children order; do NOT remove or reorder nodes.
        """
        # 1) 归一化谓词
        if callable(pred_or_section):
            pred = pred_or_section  # expects lambda n: bool
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

        # 2) 遍历所有任务树，按原有顺序设置 active 标记；不修改结构
        for root in getattr(self, "roots", []) or []:
            queue = [root]  # BFS/顺序遍历：不改变结构与相对顺序
            while queue:
                node = queue.pop(0)
                try:
                    matched = bool(pred(node))
                except Exception:
                    matched = False

                task = getattr(node, "task", None)
                if task is not None:
                    # 命中则禁用；未命中则启用
                    try:
                        setattr(task, "active", not matched)
                    except Exception:
                        # 兜底：如果没有 active 字段，尝试兼容 enabled 语义
                        try:
                            setattr(task, "enabled", not matched)
                        except Exception:
                            pass

                children = getattr(node, "children", None) or []
                # 保序加入队列（不改变树与子节点顺序）
                for ch in children:
                    queue.append(ch)

        return self

    def flatten(self, order: str = "dfs") -> List['StandardRegTask']:
        return [n.task for n in self._traverse_nodes(self.roots, order=order)]

    def preview(self, order: str = "dfs") -> str:
        lines = []
        for idx, n in enumerate(self._traverse_nodes(self.roots, order=order), start=1):
            t = n.task
            rhs_bits = (getattr(t, 'X', []) or []) + (getattr(t, 'controls', []) or [])
            cats = getattr(t, 'category_controls', []) or []
            if cats:
                rhs_bits += [f"factor({c})" for c in cats]
            rhs = " + ".join(rhs_bits) if rhs_bits else "1"
            model = getattr(t, 'model', 'OLS')
            prep_fp = getattr(t, 'prep_fingerprint', None)
            mode = (getattr(t, 'options', {}) or {}).get('prep_mode')
            suffix = f" [{model}]"
            if prep_fp or mode:
                pieces = [model]
                if mode: pieces.append(f"mode={mode}")
                if prep_fp: pieces.append(f"fp={str(prep_fp)[:8]}…")
                suffix = " [" + ", ".join(pieces) + "]"
            lines.append(f"[{idx:02d}] {n.section} {t.name}: {t.y} ~ {rhs}{suffix}")
        return "\n".join(lines)

    # ---------------------------
    # 各种任务构建方法（baseline, post_perf, mechanisms等）
    # ---------------------------
    def baseline(self, *, incremental_controls: bool = False) -> 'Plan':
        """
        当 incremental_controls=True 时：
        1) 为每棵任务树在 baseline 根节点下增量添加 controls 子任务；
        2) 完成添加后，将该树的 root 节点对应的任务标记为 active=False（不执行根 baseline 任务）。
        """
        if not incremental_controls:
            return self

        def add_inc(r: TaskNode) -> None:
            base = r.task
            # 1) 逐步累积 controls 并添加子任务
            for i, ctrls in enumerate(_accumulate(getattr(base, 'controls', []) or []), start=1):
                t = base.copy_with(
                    name=f"{base.name}_inc_{i:02d}",
                    controls=ctrls,
                    note=f"controls += {ctrls[-1]}",
                )
                r.add_child(
                    TaskNode(
                        t,
                        section="baseline",
                        tags=["baseline", "inc"],
                        note=f"controls += {ctrls[-1]}",
                    )
                )
            # 2) 将 root 节点任务标记为不执行
            try:
                setattr(base, "active", False)
            except Exception:
                # 兜底：部分实现可能使用 enabled 语义
                try:
                    setattr(base, "enabled", False)
                except Exception:
                    pass
        return self._map_roots(add_inc)

    def post_perf(self, ys: List[str], name_fmt: str = "{base}_post_{y}", if_reprep: bool = False) -> 'Plan':
        def add_post(r: TaskNode) -> None:
            base = r.task
            for y in ys:
                t = base.copy_with(
                    name='post_perf',
                    y=y,
                    note=f"Y→{y}",
                    if_reprep=if_reprep  # Pass if_reprep to child task
                )
                r.add_child(TaskNode(t, section="post_acq_perf", tags=["post", "replace_y"], note=f"Y→{y}"))
        return self._map_roots(add_post)

    def mechanisms(self, ys: List[str], name_fmt: str = "{base}_mech_{y}", if_reprep: bool = False) -> 'Plan':
        def add_mech(r: TaskNode) -> None:
            base = r.task
            for y in ys:
                t = base.copy_with(
                    name='mechanisms',
                    y=y,
                    note=f"Y→{y}",
                    if_reprep=if_reprep  # Pass if_reprep to child task
                )
                r.add_child(TaskNode(t, section="mechanism", tags=["mechanism", "replace_y"], note=f"Y→{y}"))
        return self._map_roots(add_mech)

    def heterogeneity(self, splits: List[Dict[str, Any]], if_reprep: bool = False) -> 'Plan':
        """
        Generates tasks for heterogeneity analysis where splitting conditions
        are defined in R code instead of Python. Each task can optionally
        be flagged to independently clean the data by setting `if_reprep`.
        Args:
            splits: List of dictionaries defining the splitting conditions, 
                    where each dictionary should contain:
                        - field: The variable to split on
                        - op: The operator (e.g., '>=', '==')
                        - cond: The condition value (e.g., 'avg_ROCE')
            if_reprep: A boolean flag to determine if each generated task
                        should independently clean the data. Default is False.
        """
        def add_het(r: TaskNode) -> None:
            base = r.task
            for i, sp in enumerate(splits, start=1):
                subset = {
                    "classification_field": sp["field"],
                    "operator": sp["op"],
                    "classification_conditions": sp["cond"],
                }
                t = base.copy_with(
                    name="heterogeneity",
                    subset=subset,  # Directly pass the split condition
                    note=f"subset={subset}",
                    if_reprep=if_reprep  # Pass if_reprep to the child task
                )
                r.add_child(TaskNode(t, section="heterogeneity", tags=["heterogeneity", "subset"], note=f"subset={subset}"))
        
        return self._map_roots(add_het)

    def robust_model(self, models: List[str]) -> 'Plan':
        def add_model(r: TaskNode) -> None:
            base = r.task
            hub = r.add_child(TaskNode(base.copy_with(name=f"{base.model}_models_hub", note="model variants"),
                                    section="robust_model", tags=["robust", "hub"], note="model variants"))
            for m in models:
                tm = base.copy_with(name=f"robust_model_{m.lower()}", model=m, note=f"model→{m}")
                hub.add_child(TaskNode(tm, section="robust_model", tags=["robust", "model"], note=f"model→{m}"))
            # --- ADDED: flatten hub ---
            try:
                # 若未生成任何变体：直接移除 hub
                if not hub.children:
                    try:
                        r.children.remove(hub)
                    except ValueError:
                        pass
                else:
                    # 有子节点：将子节点提升到父节点 r 的 children 中，并移除 hub
                    try:
                        idx = r.children.index(hub)
                    except ValueError:
                        idx = len(r.children)
                    promoted = list(hub.children)  # 拷贝
                    # 先移除 hub
                    try:
                        r.children.pop(idx)
                    except Exception:
                        try:
                            r.children.remove(hub)
                        except Exception:
                            pass
                    # 在原位置插回其子节点（保持顺序）
                    for offset, ch in enumerate(promoted):
                        r.children.insert(idx + offset, ch)
            except Exception:
                # 任何异常不影响构建流程
                pass

        return self._map_roots(add_model)


    def robust_measure(
        self,
        *,
        y_alternatives: Optional[List[str]] = None,
        x_sets: Optional[List[List[str]]] = None,
        control_sets: Optional[List[List[str]]] = None,
    ) -> 'Plan':
        y_alternatives = y_alternatives or []
        x_sets = x_sets or []
        control_sets = control_sets or []

        def add_meas(r: TaskNode) -> None:
            base = r.task
            hub = r.add_child(TaskNode(base.copy_with(name=f"robust_measure_hub", note="measure variants"),
                                    section="robust_measure", tags=["robust", "measure"], note="measure variants"))
            for y in y_alternatives:
                hub.add_child(TaskNode(base.copy_with(name=f"robust_measure_y", y=y, note=f"Y→{y}"),
                                    section="robust_measure", tags=["robust", "replace_y"], note=f"Y→{y}"))
            for xs in x_sets:
                hub.add_child(TaskNode(base.copy_with(name=f"robust_measure_X", X=xs, note=f"X→{xs}"),
                                    section="robust_measure", tags=["robust", "replace_X"], note=f"X→{xs}"))
            for cs in control_sets:
                hub.add_child(TaskNode(base.copy_with(name=f"robust_measure_controls", controls=cs, note=f"controls→{cs}"),
                                    section="robust_measure", tags=["robust", "replace_controls"], note=f"controls→{cs}"))

            # --- ADDED: 扁平化 hub —— 如果没有子变体就删除 hub；如果有，把子节点提升到 r 下并移除 hub ---
            try:
                # 如果未添加任何变体：直接把 hub 从父节点移除
                if not hub.children:
                    try:
                        r.children.remove(hub)
                    except ValueError:
                        pass
                else:
                    # 有子节点：将子节点整体提升到父节点 r 的 children 中，放在 hub 原位置
                    try:
                        idx = r.children.index(hub)
                    except ValueError:
                        idx = len(r.children)
                    promoted = list(hub.children)  # 拷贝一份
                    # 移除 hub 自身
                    try:
                        r.children.pop(idx)
                    except Exception:
                        # 如果找不到索引，尝试直接移除
                        try:
                            r.children.remove(hub)
                        except Exception:
                            pass
                    # 在原位置按顺序插入其子节点
                    for offset, ch in enumerate(promoted):
                        r.children.insert(idx + offset, ch)
            except Exception:
                # 任何异常不影响构建流程
                pass

        return self._map_roots(add_meas)
    # ---------------------------
    # render_code_in_batch 方法
    # ---------------------------
    def render_code_in_batch(
        self,
        *,
        order: Literal["dfs", "bfs"] = "dfs",
        codegen: Optional[Callable[['StandardRegTask'], Dict[str, Any]]] = None,
        strict: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ) -> "Plan":
        """
        任务批量代码生成与指纹校验。

        步骤：
        1) 遍历所有节点（按树结构顺序），生成代码文本并更新 task 对象。
        2) 统一处理 prep_fingerprint 继承逻辑。
        3) 校验树内指纹一致性（若 strict=True）。
        """
        log = logger or (lambda msg: None)

        # ---------- Step 1: 遍历任务树，生成代码 ----------
        for root in self.roots:
            for node in self._traverse_nodes([root], order=order):
                t = node.task

                # 优先使用外部 codegen，否则调用 task 自带 generate_code()
                if codegen is not None:
                    try:
                        out = codegen(t)
                    except Exception as e:
                        raise RuntimeError(
                            f"codegen failed for task {getattr(t, 'name', '<unnamed>')}: {e}"
                        ) from e
                elif hasattr(t, "generate_code"):
                    try:
                        out = t.generate_code()
                    except Exception as e:
                        raise RuntimeError(
                            f"generate_code() failed for task {getattr(t, 'name', '<unnamed>')}: {e}"
                        ) from e
                else:
                    raise RuntimeError(
                        f"Task {getattr(t,'name','<unnamed>')} has neither external codegen nor generate_code()."
                    )

                if not isinstance(out, dict):
                    raise TypeError("codegen/generate_code must return a dict")

                # 提取生成结果字段
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

                # 写回
                setattr(t, "prep_fingerprint", fp)
                setattr(t, "code_text", code_text)
                node.task = t

        # ---------- Step 2: 处理指纹继承 ----------
        for root in self.roots:
            rt = root.task
            root_fp = getattr(rt, "prep_fingerprint", None)
            if root_fp is None:
                raise RuntimeError(
                    f"Root task {getattr(rt,'name','<unnamed>')} has no prep_fingerprint after code generation."
                )

            for node in self._traverse_nodes([root], order=order):
                t = node.task
                if_reprep = bool(getattr(t, "if_reprep", False))

                if if_reprep:
                    # 自行准备的节点必须自带 fingerprint
                    if getattr(t, "prep_fingerprint", None) is None:
                        raise RuntimeError(
                            f"Task {getattr(t,'name','<unnamed>')} is if_reprep=True but has no prep_fingerprint."
                        )
                    continue

                # 非 if_reprep：必须继承根指纹
                if getattr(t, "prep_fingerprint", None) != root_fp:
                    new_t = t.copy_with(prep_fingerprint=root_fp)
                    # 双保险：若 copy_with 过滤了字段，则手动写回
                    if getattr(new_t, "prep_fingerprint", None) != root_fp:
                        setattr(new_t, "prep_fingerprint", root_fp)
                    node.task = new_t

            # ---------- Step 3: 校验一致性 ----------
            if strict:
                bad = []
                for node in self._traverse_nodes([root], order=order):
                    t = node.task
                    if not getattr(t, "if_reprep", False):
                        if getattr(t, "prep_fingerprint", None) != root_fp:
                            bad.append(getattr(t, "name", "<unnamed>"))

                if bad:
                    # 再尝试修复一次
                    for node in self._traverse_nodes([root], order=order):
                        t = node.task
                        if getattr(t, "name", None) in bad:
                            setattr(t, "prep_fingerprint", root_fp)

                    # 再次校验
                    bad2 = [
                        getattr(t, "name", "<unnamed>")
                        for node in self._traverse_nodes([root], order=order)
                        for t in [node.task]
                        if (not getattr(t, "if_reprep", False))
                        and getattr(t, "prep_fingerprint", None) != root_fp
                    ]

                    if bad2:
                        raise RuntimeError(
                            f"Tree rooted at {getattr(rt,'name','<unnamed>')} has non-inherited fingerprints: "
                            + ", ".join(bad2)
                        )

        return self

    # ---------------------------
    # 按照树来生成代码
    # ---------------------------
    def output_r_code_by_tree(
            self,
            include_prepare: bool = True,      # 开关仍保留；若你始终需要按规则渲染，可传 True（默认）
            with_headers: bool = True,
            collect_dependencies: bool = True,
            on_missing_prepare: str = "comment",  # "comment" | "raise"
        ) -> List[Dict[str, Any]]:
        """
        生成每棵任务树的 R 代码脚本与节点代码（写回到 task.code_text）并返回汇总结构。

        规则更新：
        1) active 属性仅控制是否包含 execution_code：
        - active=True  -> 输出 execution_code（以及 post_regression_code）
        - active=False -> 不输出 execution_code（也不输出 post_regression_code）

        2) if_reprep 控制是否包含 prepare_code，以下三种情况需要渲染（均采用“当前任务”的参数来渲染）：
        - 当前任务为该任务树的第一个任务（无论 if_reprep / active）
        - 当前任务 if_reprep=True
        - 上一个任务 if_reprep=True（对“当前任务”重新渲染 prepare_code）
        其余情况不渲染 prepare_code。

        说明：
        - “采用当前任务的参数进行 prepare_code 渲染” => 使用当前 task.generate_code() 得到的 prepare_code 片段。
        """

        def _is_active(t: Any) -> bool:
            return bool(getattr(t, "active", getattr(t, "enabled", True)))

        def _need_prepare(idx: int, tasks: List[Any]) -> bool:
            if idx == 0:
                return True
            cur = bool(getattr(tasks[idx], "if_reprep", False))
            prev = bool(getattr(tasks[idx - 1], "if_reprep", False))
            return cur or prev

        results: List[Dict[str, Any]] = []
        trees: List[List[Any]] = self.flatten_by_tree() or []

        for ti, tasks in enumerate(trees):
            if not tasks:
                results.append({"tree_id": None, "r_script": "", "nodes": [], "deps": []})
                continue

            # 1) 预取并缓存所有节点的 generate_code() 结果（避免重复调用/副作用）
            code_cache: List[Dict[str, Any]] = []
            for t in tasks:
                obj = t.generate_code() or {}
                code_cache.append({
                    "prepare_code": (obj.get("prepare_code") or "").strip(),
                    "execute_code": (obj.get("execute_code") or "").strip(),
                    "post_regression_code": (obj.get("post_regression_code") or "").strip(),
                    "combined":     (obj.get("combined") or "").strip(),
                    "dependencies": list(obj.get("dependencies") or []),
                })

            first_task_id = getattr(tasks[0], "task_id", None)
            deps_set = set()
            script_parts: List[str] = []

            # 2) 逐节点装配（只使用缓存，不在此处再次 generate）
            for idx, task in enumerate(tasks):
                cached = code_cache[idx]
                prep   = cached["prepare_code"]
                body   = cached["execute_code"] or cached["combined"]
                post   = cached["post_regression_code"]

                # 收集依赖
                if collect_dependencies:
                    for d in cached["dependencies"]:
                        if d:
                            deps_set.add(d)

                node_chunks: List[str] = []
                # ---- prepare_code 条件渲染（永远使用“当前任务”的 prepare_code）----
                if include_prepare and _need_prepare(idx, tasks):
                    if prep:
                        if with_headers:
                            header = f"# ---- [prep] {getattr(task, 'name', f'task_{idx}')}"
                            model  = getattr(task, "model", "") or getattr(task, "note", "")
                            header = header + (f" ({model})" if model else "") + " ----\n"
                            node_chunks.append(header + prep)
                        else:
                            node_chunks.append(prep)
                    else:
                        msg = f"# MISSING prepare_code at task '{getattr(task,'name','')}' (idx={idx})"
                        if on_missing_prepare == "raise":
                            raise RuntimeError(msg)
                        node_chunks.append(msg)

                # ---- execution_code 受 active 控制 ----
                if _is_active(task):
                    if body:
                        if with_headers:
                            label = getattr(task, "name", "")
                            meta  = getattr(task, "model", "") or getattr(task, "note", "")
                            header = f"#----------------{label}" + (f" ({meta})" if meta else "") + " ----------------#\n"
                            node_chunks.append(header + body)
                        else:
                            node_chunks.append(body)
                    # post_regression_code 仅在 active=True 时输出
                    if post:
                        node_chunks.append(post)
                else:
                    # inactive：严格不输出 execution_code / post_regression_code
                    pass

                node_text = "\n".join([b for b in node_chunks if b and b.strip()]).strip()
                setattr(task, "code_text", node_text)
                if node_text:
                    script_parts.append(node_text)

            r_script = "\n\n".join(script_parts).strip()
            results.append({
                "tree_id": first_task_id,
                "r_script": r_script,
                "nodes": tasks,                               # 已将各节点 code_text 写回
                "deps": sorted(deps_set) if collect_dependencies else [],
            })

        return results

    # ---------------------------
    # 添加 _map_roots 方法
    # ---------------------------
    def _map_roots(self, fn: Callable[[TaskNode], None]) -> 'Plan':
        for root in self.roots:
            fn(root)
        return self
    
    # ---------------------------
    # 添加 evaluate_acceptance_over_forest 方法
    # ---------------------------
    def evaluate_acceptance_over_forest(
        self,
        *,
        order: Literal["dfs", "bfs"] = "dfs"
    ) -> List[Dict[str, Any]]:
        """
        遍历所有任务树，对每个节点调用 task.evaluate_acceptance(alpha) 进行“能否被接受”的判断，
        并根据显著性水平返回星标：
            - 传入 0.01 返回 True  -> "***"
            - 传入 0.05 返回 True  -> "**"
            - 传入 0.10 返回 True  -> "*"
        规则：
          1) 对所有 name 以 'baseline' 开头的 StandardRegTask，仅使用 self.cfg.significance_level 判定一次；
             若通过，则按该 alpha 映射星标（若 alpha 不在 {0.01,0.05,0.1}，则按阈值：<=0.01→***，<=0.05→**，<=0.1→*）。
          2) 其余任务依次尝试 alpha ∈ {0.01, 0.05, 0.10}，取最严格（最小 alpha）即刻通过的结果并返回相应星标。
          3) 若均不通过，则标记为空字符串 ""，accepted=False，used_alpha=None。

        返回：
          List[dict]，每个元素包含：
            {
              "task_id": str | None,
              "name": str,
              "section": str,
              "accepted": bool,
              "used_alpha": float | None,
              "mark": str,        # "***" / "**" / "*" / ""(未通过)
            }
        """
        def _alpha_to_mark(alpha: float) -> str:
            # 精确匹配优先；否则按阈值就近映射
            exact = {0.01: "***", 0.05: "**", 0.1: "*"}
            if alpha in exact:
                return exact[alpha]
            if alpha <= 0.01:
                return "***"
            if alpha <= 0.05:
                return "**"
            if alpha <= 0.10:
                return "*"
            return ""  # 超过 0.10 不给星

        results: List[Dict[str, Any]] = []

        # 遍历每棵树
        for root in self.roots:
            for node in self._traverse_nodes([root], order=order):
                t = getattr(node, "task", None)
                if t is None:
                    continue

                eval_fn = getattr(t, "evaluate_acceptance", None)
                if not callable(eval_fn):
                    # 没有该接口：视为不可判定
                    results.append({
                        "task_id": getattr(t, "task_id", None),
                        "name": getattr(t, "name", "<unnamed>"),
                        "section": getattr(node, "section", ""),
                        "accepted": False,
                        "used_alpha": None,
                        "mark": "",
                    })
                    continue

                name = str(getattr(t, "name", "")) if getattr(t, "name", None) is not None else ""
                is_baseline = name.startswith("baseline")

                used_alpha: Optional[float] = None
                accepted: bool = False
                mark: str = ""

                if is_baseline:
                    # 1) baseline：仅用 self.cfg.significance_level
                    cfg = getattr(self, "cfg", None)
                    alpha = getattr(cfg, "significance_level", None) if cfg is not None else None

                    # 若缺失 cfg 或 significance_level，就保守地不通过并返回空标记
                    if isinstance(alpha, (int, float)):
                        try:
                            ok = bool(eval_fn(float(alpha)))
                        except Exception:
                            ok = False
                        used_alpha = float(alpha)
                        accepted = ok
                        mark = _alpha_to_mark(used_alpha) if ok else ""
                    else:
                        used_alpha = None
                        accepted = False
                        mark = ""
                else:
                    # 2) 非 baseline：依次试 0.01、0.05、0.1，取最先通过者
                    for a in (0.01, 0.05, 0.10):
                        try:
                            ok = bool(eval_fn(a))
                        except Exception:
                            ok = False
                        if ok:
                            used_alpha = a
                            accepted = True
                            mark = _alpha_to_mark(a)  # 题述中三档的直接映射
                            break

                results.append({
                    "task_id": getattr(t, "task_id", None),
                    "name": name or "<unnamed>",
                    "section": getattr(node, "section", ""),
                    "accepted": accepted,
                    "used_alpha": used_alpha,
                    "mark": mark,
                })
        return results

    def to_materialize(self):
        """
        将所有任务树平铺成一张 pandas.DataFrame，并与 evaluate_acceptance_over_forest 的结果左连接。
        返回列顺序：
        ['task_id','name','dataset','section','model','y','X','controls','category_controls',
         'panel_ids','exec_result','subset','if_reprep','active','mark']
        """
        import pandas as pd

        # 需要抽取的任务属性
        task_cols = [
            'task_id', 'name', 'dataset', 'model', 'y', 'X',
            'controls', 'category_controls', 'panel_ids',
            'exec_result', 'subset', 'if_reprep', 'active'
        ]

        # 某些字段里可能塞了生成器/迭代器/自定义可迭代对象，pandas 在 pretty print 时会尝试遍历它们，导致 StopIteration。
        # 为避免打印阶段崩溃，把“非基础类型”的值序列化为 repr 字符串。
        def _sanitize(v):
            if v is None or isinstance(v, (str, int, float, bool)):
                return v
            try:
                return repr(v)
            except Exception:
                return f"<unrepr {type(v).__name__}>"

        rows = []

        # 统一遍历节点（优先用类里已有的遍历器）
        def _iter_nodes():
            if hasattr(self, "_traverse_nodes"):
                for r in getattr(self, "roots", []):
                    for n in self._traverse_nodes([r], order="dfs"):
                        yield n
            else:
                stack = list(getattr(self, "roots", []))[::-1]
                while stack:
                    n = stack.pop()
                    yield n
                    children = getattr(n, "children", []) or []
                    stack.extend(reversed(children))
        for node in _iter_nodes():
            t = getattr(node, "task", None)
            if t is None:
                continue
            rec = {}
            for c in task_cols:
                rec[c] = _sanitize(getattr(t, c, None))
            rec['section'] = _sanitize(getattr(node, 'section', None))
            rows.append(rec)
        base_cols = [
            'task_id', 'name', 'dataset', 'model', 'y', 'X',
            'controls', 'category_controls', 'panel_ids',
            'exec_result', 'subset', 'if_reprep', 'active'
        ]
        final_cols = base_cols + ['mark']
        if not rows:
            return pd.DataFrame(columns=final_cols)
        df = pd.DataFrame(rows)
        # 评估并左连接（仅按 task_id）
        if not hasattr(self, 'evaluate_acceptance_over_forest'):
            raise AttributeError("evaluate_acceptance_over_forest 方法不存在，请先实现。")
        acc_list = self.evaluate_acceptance_over_forest(order="dfs")
        acc_df = pd.DataFrame(acc_list) if acc_list else pd.DataFrame(columns=['task_id', 'section', 'mark'])
        for need in ['task_id', 'section', 'mark']:
            if need not in acc_df.columns:
                acc_df[need] = pd.NA
        acc_df = acc_df[['task_id', 'section', 'mark']]
        merged = df.merge(acc_df, on=['task_id'], how='left', suffixes=('', '_acc'))
        # 如果评估结果里有 section，可用其覆盖（仅在评估有值时）
        if 'section_acc' in merged.columns:
            merged['section'] = merged['section_acc'].combine_first(merged['section'])
            merged = merged.drop(columns=['section_acc'])
        # 补全并按顺序返回
        for c in base_cols:
            if c not in merged.columns:
                merged[c] = pd.NA
        if 'mark' not in merged.columns:
            merged['mark'] = pd.NA
        return merged[final_cols]
__all__ = ['Plan']