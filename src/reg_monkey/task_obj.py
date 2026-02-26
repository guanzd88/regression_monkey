from __future__ import annotations
from dataclasses import dataclass, field
from dataclasses import is_dataclass, fields as dc_fields
from typing import List, Dict, Any, Optional, Literal, Union, ClassVar
from copy import deepcopy
from reg_monkey.util import ConfigLoader
from reg_monkey.code_generator import CodeGenerator
from rpy2.robjects.vectors import StrVector
import re,json,hashlib,inspect
import pandas as pd
import numpy as np

_FIELD_PATTERN = re.compile(r"field\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")

# ============================================================
# 条件表达式 AST 节点定义
# ============================================================
@dataclass
class _Condition:
    """单个条件节点: group(sign) op pvalue"""
    group: str
    sign: str       # '+', '-', '*'
    comparator: str # '<', '<=', '>', '>=', '='
    pvalue: float

@dataclass
class _AndExpr:
    """AND 表达式节点"""
    left: Any  # _ExprNode
    right: Any # _ExprNode

@dataclass
class _OrExpr:
    """OR 表达式节点"""
    left: Any  # _ExprNode
    right: Any # _ExprNode

# _ExprNode = Union[_Condition, _AndExpr, _OrExpr]

# ============================================================
# 条件表达式词法分析器
# ============================================================
_TOKEN_PATTERNS = [
    ("GROUP",      r"[a-zA-Z_][a-zA-Z0-9_]*"),
    ("PVALUE",     r"\d+\.\d+|\d+"),
    ("COMPARATOR", r"<=|>=|<|>|="),
    ("SIGN",       r"[+\-*]"),
    ("LPAREN",     r"\("),
    ("RPAREN",     r"\)"),
    ("AND",        r"&"),
    ("OR",         r"\|"),
    ("WS",         r"\s+"),
]

def _tokenize_condition(expr: str) -> List[tuple]:
    """将条件表达式字符串转为 token 列表"""
    tokens = []
    pos = 0
    compiled = [(name, re.compile(pattern)) for name, pattern in _TOKEN_PATTERNS]
    while pos < len(expr):
        matched = False
        for token_type, regex in compiled:
            match = regex.match(expr, pos)
            if match:
                if token_type != "WS":
                    tokens.append((token_type, match.group()))
                pos = match.end()
                matched = True
                break
        if not matched:
            raise ValueError(f"Condition expression parse error at position {pos}: '{expr[pos:pos+10]}...'")
    return tokens

# ============================================================
# 条件表达式语法解析器
# ============================================================
class _ConditionParser:
    """递归下降解析器，将 token 流转为 AST"""

    def __init__(self, tokens: List[tuple]):
        self.tokens = tokens
        self.pos = 0

    def parse(self):
        """入口：解析整个表达式"""
        if not self.tokens:
            raise ValueError("Condition expression is empty")
        result = self._or_expr()
        if self.pos < len(self.tokens):
            raise ValueError(f"Unexpected tokens at end of expression: {self.tokens[self.pos:]}")
        return result

    def _or_expr(self):
        """or_expr = and_expr { '|' and_expr }"""
        left = self._and_expr()
        while self._match("OR"):
            right = self._and_expr()
            left = _OrExpr(left, right)
        return left

    def _and_expr(self):
        """and_expr = atom { '&' atom }"""
        left = self._atom()
        while self._match("AND"):
            right = self._atom()
            left = _AndExpr(left, right)
        return left

    def _atom(self):
        """atom = '(' or_expr ')' | condition"""
        if self._match("LPAREN"):
            expr = self._or_expr()
            self._expect("RPAREN")
            return expr
        return self._condition()

    def _condition(self):
        """condition = group '(' sign ')' comparator pvalue"""
        group = self._expect("GROUP")
        self._expect("LPAREN")
        sign = self._expect("SIGN")
        self._expect("RPAREN")
        comparator = self._expect("COMPARATOR")
        pvalue_str = self._expect("PVALUE")
        pvalue = float(pvalue_str)
        return _Condition(group, sign, comparator, pvalue)

    def _match(self, token_type: str) -> bool:
        """若当前 token 类型匹配则消费并返回 True"""
        if self.pos < len(self.tokens) and self.tokens[self.pos][0] == token_type:
            self.pos += 1
            return True
        return False

    def _expect(self, token_type: str) -> str:
        """期望当前 token 类型匹配，返回其值；否则抛错"""
        if self.pos >= len(self.tokens):
            raise ValueError(f"Expression ended early; expected {token_type}")
        if self.tokens[self.pos][0] != token_type:
            raise ValueError(
                f"Expected {token_type} but found {self.tokens[self.pos][0]}: '{self.tokens[self.pos][1]}'"
            )
        value = self.tokens[self.pos][1]
        self.pos += 1
        return value

    def _peek(self) -> Optional[tuple]:
        """查看当前 token 但不消费"""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None


def _dedup_preserve_order(items: List[str]) -> List[str]:
    """Remove duplicates while preserving order, keep only truthy str values."""
    seen = set()
    out: List[str] = []
    for x in items:
        if not x:
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

@dataclass
class StandardRegTask:
    """
    标准回归任务（StandardRegTask）

    概述
    ----
    作为“编排层”的最小回归任务单元，负责**参数规整**、**模板上下文构建**以及与外部
    代码生成/执行器的**衔接**（占位实现）。不负责数据清洗、缺失处理、特征工程，也不承担
    变量组合爆炸的搜索；大规模规格枚举应在类外完成（如 BaselineExplorer），再实例化本类。

    适用场景
    --------
    - 你已经确定了 y / X / controls / 固定效应 / 模型 / 子集条件等“单一规格”的要素；
    - 希望将该规格转成模板上下文 → 生成 R 代码 → 交给执行器跑回归；
    - 跑完后把 R 侧结构化结果转换为 Python 原生结构并做验收判断。

    关键字段（构造参数）
    --------------------
    - task_id: str | None            稳定短 ID，默认运行后由 `_generate_task_id()` 生成
    - name: str | None               任务名称（推荐含业务语义）
    - dataset: str | None            数据集标识
    - y: str                         因变量
    - X: list[str]                   自变量列表
    - controls: list[str]            控制变量
    - category_controls: list[str]   固定效应（分类变量名列表）
    - panel_ids: dict                面板索引 {'entity': ..., 'time': ...}
    - subset: dict                   子集配置（classification_field / operator / classification_conditions）
    - options: dict                  运行选项（由模板/执行器消费）
    - model: {'OLS','FE','RE'}       回归模型枚举，默认 'OLS'
    - note: str                      备注，默认 "baseline"
    - if_reprep: bool                是否二次预处理标志
    - active: bool                   是否启用该任务

    重要属性
    --------
    - exec_result (property)
        * setter 接收 R 端对象，自动转换为 Python 原生结构（dict / list / DataFrame→records）
        * getter 返回归一化后的结果字典：
          {
            "forward_res":  ...,
            "opposite_res": ...
          }

    常用方法
    --------
    - generate_task_context() -> dict
        产出模板上下文：y/X/controls/fixed effects/panel_ids/options 及 regression_model 子结构。
    - generate_code() -> str
        基于 CodeGenerator(self) 组装完整 R 脚本；设置 `self.code_text` 与 `prep_fingerprint`。
    - get_all_variables() -> list[str]
        汇总执行所需字段（含 subset/roles 展开），去重保序。
    - copy_with(**overrides) -> StandardRegTask
        仅拷贝“构造器可接收”的字段并应用覆盖，返回新实例（排除运行期/缓存字段）。
    - evaluate_acceptance(significance_level: float = 0.1) -> bool
        验收规则：
        * 若仅 `forward_res` 存在：检查所有自变量的 p 值是否 ≤ 给定显著性水平；
        * 若 `forward_res` 与 `opposite_res` 均存在：比较两组显著性与符号是否**完全一致**。
          若“显著性一致且符号全一致”为 False，则视为“存在差异”并返回 True。
    - to_json(as_str=False, include_runtime=False, indent=2)
        导出为可序列化结构/字符串；subset 展平到同级字段，必要时规范化条件表达式。
    - _generate_task_id() -> str
        依据“语义等价定义”（y/X/controls/FE/panel_ids/subset/options/model）生成稳定短 ID。

    约定与注意
    ----------
    - 本类专注于“单一规格”的封装与验收；枚举与打分请交给上层探索器/规划器。
    - `model` 仅允许 {'OLS','FE','RE'}；非法值会在 `__post_init__` 中抛错。
    - `evaluate_acceptance` 假定结果对象中包含 `coefficients`，并含 'Variable'、
      'P_Value'、'Estimate' 等列；请保证执行端返回结构遵循该约定。
    - 若 `exec_result` 为 None，或包含 rpy2 的 `StrVector`（提示缺结果），将直接判定为不通过。
    """

    # ===== 必备属性 =====
    task_id: Optional[str] = field(default=None, init=True, repr=True, compare=False)
    name: Optional[str] = field(default=None, init=True, repr=True, compare=False)
    dataset: Optional[str] = field(default=None, init=True, repr=False, compare=False)
    y: Optional[str] = field(default_factory=str)
    X: Union[List[str], Dict[str, Union[str, List[str]]]] = field(default_factory=dict)
    controls: List[str] = field(default_factory=list)
    category_controls: List[str] = field(default_factory=list)
    category_controls_mapping: Dict[str, str] = field(default_factory=dict)  # 效应名 → 字段名映射
    panel_ids: Dict[str, str] = field(default_factory=dict)  # {'entity': '...', 'time': '...'}
    subset: Dict[str, Optional[str]] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    model: Literal['OLS', 'FE', 'RE'] = 'OLS'
    note: str = "baseline"
    prep_fingerprint: str = ""
    if_reprep: Optional[str] = field(default=False, init=True, repr=False, compare=False)
    cg: Any = field(default=None, init=True, repr=False, compare=False)
    active: Optional[str] = field(default=True, init=True, repr=False, compare=False)
    parent_task_id: str = None
    interaction: bool = False
    interaction_map: Dict[str, tuple] = field(default_factory=dict)
    incremental_controls: bool = False

    # 用于 Plan 序列化的字段白名单
    _SERIALIZABLE_FIELDS: ClassVar[List[str]] = [
        "task_id",
        "name",
        "dataset",
        "y",
        "X",
        "controls",
        "category_controls",
        "category_controls_mapping",
        "panel_ids",
        "subset",
        "options",
        "model",
        "note",
        "prep_fingerprint",
        "if_reprep",
        "active",
        "parent_task_id",
        "interaction",
        "interaction_map",
        "incremental_controls",
        "code_text",
        "_code_segments",
    ]

    _FIELD_META_CACHE: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def _get_field_meta(cls) -> Dict[str, Any]:
        if not getattr(cls, "_FIELD_META_CACHE", None):
            cls._FIELD_META_CACHE = {f.name: f for f in dc_fields(cls)}
        return cls._FIELD_META_CACHE

    def to_spec(self) -> Dict[str, Any]:
        """导出可序列化的任务配置"""
        spec: Dict[str, Any] = {}
        for attr in self._SERIALIZABLE_FIELDS:
            if hasattr(self, attr):
                spec[attr] = deepcopy(getattr(self, attr))
        return spec

    @classmethod
    def from_spec(cls, spec: Dict[str, Any]) -> "StandardRegTask":
        """根据配置字典重建任务对象"""
        if not isinstance(spec, dict):
            raise ValueError("spec must be a dict when reconstructing StandardRegTask")
        field_meta = cls._get_field_meta()
        init_kwargs: Dict[str, Any] = {}
        post_assign: Dict[str, Any] = {}
        for key, value in (spec or {}).items():
            if key not in field_meta:
                continue
            target = deepcopy(value)
            if field_meta[key].init:
                init_kwargs[key] = target
            else:
                post_assign[key] = target
        task = cls(**init_kwargs)
        for key, value in post_assign.items():
            setattr(task, key, value)
        return task

    # ===== 第2部分：模板渲染与执行编排 =====
    # code_text: Optional[str] = field(default=None, repr=False)   # generate_code 的产出

    # ===== 第3部分：结果容器与验收 =====
    # exec_result: Optional[Any] = field(default=None, repr=False)

    @property
    def X_flat(self) -> List[str]:
        """将分组字典展平为变量列表"""
        X = getattr(self, 'X', None)
        if X is None:
            return []
        if isinstance(X, list):
            return list(X)
        if isinstance(X, dict):
            result = []
            for v in X.values():
                if isinstance(v, str):
                    result.append(v)
                elif isinstance(v, list):
                    result.extend(v)
            return result
        return []

    @property
    def exec_result(self):
        """访问时拿到已处理后的结果（Python 原生结构）。"""
        output = getattr(self, '_exec_result', None)
        return output

    @exec_result.setter
    def exec_result(self, value):
        """赋值时触发：把 rpy2 的 R 对象→Python 原生结构，并缓存。"""
        self._exec_result_raw = value
        self._exec_result = self._r_obj_to_native(value)

    def _r_obj_to_native(self,obj):
        try:
            import pandas as pd
            import rpy2.robjects as ro
            from rpy2.robjects import default_converter
            from rpy2.robjects.conversion import localconverter
            from rpy2.robjects import pandas2ri
            from rpy2.robjects.vectors import ListVector
            from rpy2.rinterface_lib.sexp import Sexp
            from rpy2.rlike.container import NamedList, TaggedList
        except Exception:
            return obj

        # 内部递归：R→Python
        def _r_to_py(x):
            with localconverter(default_converter + pandas2ri.converter):
                return ro.conversion.rpy2py(x)

        def _convert(x):
            # ① 先处理 Python 端容器：NamedList / TaggedList
            if isinstance(x, (NamedList, TaggedList)):
                names = list(x.names()) if x.names() is not None else [None] * len(x)
                out = {}
                for i, nm in enumerate(names):
                    key = str(nm) if nm is not None else str(i)
                    out[key] = _convert(x[i])
                return out
            # ② 再处理 R 端 Sexp（ListVector / data.frame / matrix / 标量等）
            if isinstance(x, Sexp):
                # data.frame / matrix / array
                try:
                    classes = set(map(str, ro.rclass(x)))
                except Exception:
                    classes = set()

                if {"data.frame", "matrix", "array"} & classes:
                    py = _r_to_py(x)
                    if isinstance(py, pd.DataFrame):
                        return py.to_dict(orient="records")
                    return py
                # R 的 ListVector（不是 Python 的 NamedList）
                if isinstance(x, ListVector):
                    names = list(x.names()) if x.names() is not None else [None] * len(x)
                    out = {}
                    for i, nm in enumerate(names):
                        key = str(nm) if nm is not None else str(i)
                        out[key] = _convert(x[i])
                    return out
                # 其它标量/向量 → 直接转换
                return _r_to_py(x)
            # ③ 既不是 NamedList/TaggedList，也不是 Sexp：可能已经是 Python 原生
            return x

        py_obj = _convert(obj)

        # 你的模板通常把两个结果放在 forward_res / opposite_res（注意不是 forward_result）
        forward = None
        opposite = None
        stepwise: List[Dict[str, Any]] = []

        def _to_scalar(value, default=None):
            try:
                import numpy as np
            except ImportError:  # pragma: no cover
                np = None

            if value is None:
                return default
            if isinstance(value, (list, tuple)) and value:
                return _to_scalar(value[0], default)
            if np is not None and isinstance(value, np.ndarray) and value.size == 1:
                try:
                    return value.reshape(-1)[0].item()
                except Exception:
                    pass
            if hasattr(value, "item") and not isinstance(value, (str, bytes)):
                try:
                    return value.item()
                except Exception:
                    pass
            return value

        def _to_list(value):
            if value is None:
                return []
            try:
                from rpy2.rinterface_lib.sexp import NULLType  # type: ignore
            except Exception:  # pragma: no cover - rpy2 not installed
                NULLType = None
            if NULLType is not None and isinstance(value, NULLType):
                return []
            if isinstance(value, list):
                return [str(v) for v in value]
            if isinstance(value, tuple):
                return [str(v) for v in value]
            try:
                from collections.abc import Iterable
                if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                    return [str(v) for v in list(value)]
            except Exception:
                pass
            return [str(value)]
        if isinstance(py_obj, dict):
            forward = py_obj.get("forward_res") or py_obj.get("forward_result")
            opposite = py_obj.get("opposite_res") or py_obj.get("opposite_result")
            raw_stepwise = py_obj.get("stepwise_results") or []

            def _iter_steps(container: Any):
                if isinstance(container, list):
                    return container
                if isinstance(container, dict):
                    try:
                        return [
                            container[key]
                            for key in sorted(
                                container.keys(),
                                key=lambda x: int(x) if str(x).isdigit() else str(x),
                            )
                        ]
                    except Exception:
                        return list(container.values())
                return []

            for entry in _iter_steps(raw_stepwise):
                if not isinstance(entry, dict):
                    continue
                normalized = dict(entry)
                mark_val = _to_scalar(normalized.get("export_marked"), False)
                normalized["export_marked"] = bool(mark_val)
                step_idx = _to_scalar(normalized.get("step"), len(stepwise))
                try:
                    normalized["step"] = int(step_idx)
                except Exception:
                    normalized["step"] = len(stepwise)
                label_value = _to_scalar(normalized.get("label"))
                if isinstance(label_value, bytes):
                    label_value = label_value.decode("utf-8", errors="ignore")
                normalized["label"] = str(label_value) if label_value is not None else f"Step {normalized['step']}"
                normalized["controls_included"] = _to_list(normalized.get("controls_included"))
                stepwise.append(normalized)

        # 组织一个稳定的返回结构（系数矩阵若是 list[dict]，可直接用）
        def _pack(block):
            if block is None:
                return None
            else:
                return block

        return {
            "forward_res":  _pack(forward),
            "opposite_res": _pack(opposite),
            "stepwise_results": stepwise,
        }

    # ===== 配置加载器（按需） =====
    config: Any | None = field(default=None, repr=False, compare=False)

    def _normalize_X(self) -> None:
        """将 X 规范化为分组字典格式"""
        X = getattr(self, 'X', None)
        if X is None or (isinstance(X, (dict, list)) and not X):
            # None、空字典、空列表都转换为默认空分组
            self.X = {'default': []}
        elif isinstance(X, str):
            self.X = {'default': [X]}
        elif isinstance(X, list):
            self.X = {'default': list(X)}
        elif isinstance(X, dict):
            # 确保每个 value 都是 list
            normalized = {}
            for k, v in X.items():
                if isinstance(v, str):
                    normalized[k] = [v]
                elif isinstance(v, list):
                    normalized[k] = list(v)
                else:
                    normalized[k] = [str(v)] if v else []
            self.X = normalized

    def __post_init__(self) -> None:
        # 轻量校验：模型枚举
        allowed = {'OLS', 'FE', 'RE', 'FMB'}
        if self.model not in allowed:
            raise ValueError(f"model must be one of {allowed}; received: {self.model}")
        if self.config is None:
            self.config = ConfigLoader()
        # 规范化 X 为分组字典格式
        self._normalize_X()
        # 交互项格式校验
        if self.interaction:
            self._validate_interaction_format()

    def _validate_interaction_format(self) -> None:
        """校验交互项所需的分组格式"""
        if not isinstance(self.X, dict):
            raise ValueError("When interaction=True, X must be provided as a grouped dictionary")

        # 排除已存在的 intersection 分组
        groups = {k: v for k, v in self.X.items() if k != "intersection"}

        if len(groups) != 2:
            raise ValueError(
                f"When interaction=True, X must contain exactly two groups (excluding 'intersection'); "
                f"current groups: {list(groups.keys())}"
            )

        for group_name, vars_list in groups.items():
            if not isinstance(vars_list, list) or len(vars_list) != 1:
                count = len(vars_list) if isinstance(vars_list, list) else 'non-list'
                raise ValueError(
                    f"When interaction=True, each group must contain exactly one variable; "
                    f"group '{group_name}' has {count}"
                )

    # ---------------- 数据集标识 -----------------
    def get_dataset_key(self) -> str:
        """
        获取当前任务的数据集标识（用于 R 变量命名）。
        若 self.dataset 存在则返回净化后的标识，否则返回 "main"。
        """
        if self.dataset:
            return self._sanitize_dataset_key(self.dataset)
        return "main"

    @staticmethod
    def _sanitize_dataset_key(name: str) -> str:
        """
        将数据集名称转换为合法的 R 变量名片段。
        - 非字母/数字/下划线的字符替换为下划线
        - 首字符若为数字则加前缀 'd_'
        - 转为小写
        """
        # 替换非法字符
        sanitized = re.sub(r'[^A-Za-z0-9_]', '_', name)
        # 首字符为数字则加前缀
        if sanitized and sanitized[0].isdigit():
            sanitized = 'd_' + sanitized
        return sanitized.lower()

    # ---------------- 参数规整与角色 -----------------
    def get_all_variables(self) -> List[str]:
        """
        汇总执行环境所需的字段： y + X + controls + category_controls + panel_ids(值) + subset 相关 + generate_roles().fields
        - y 需要先转换为单元素列表。
        - subset.classification_conditions 中若出现 field(<col>)，则把捕获到的列名加入；否则仅加入 classification_field。
        - 末尾拼接 generate_roles()['fields']。
        - 返回去重且保持原始顺序的 List[str]。
        """
        vars_ordered: List[str] = []

        # 1) 基础拼接
        vars_ordered.extend([self.y])
        vars_ordered.extend(self.X_flat)
        vars_ordered.extend(self.controls or [])
        vars_ordered.extend(self.category_controls or [])

        # 2) 面板设定（只加入值部分：实体ID列和时间列）
        if isinstance(self.panel_ids, dict):
            for key in ("entity", "time"):
                if key in self.panel_ids and self.panel_ids[key]:
                    vars_ordered.append(self.panel_ids[key])

        # 3) 子集条件：三件套
        subset = self.subset or {}
        cfield = subset.get("classification_field")
        cond = subset.get("classification_conditions")
        if cfield:
            # 查看 classification_conditions 是否包含 field(<col>) 引用；可能有多个
            cols_in_field = _FIELD_PATTERN.findall(str(cond) if cond is not None else "")
            if cols_in_field:
                vars_ordered.extend([cfield])
                vars_ordered.extend(cols_in_field)
            else:
                vars_ordered.extend([cfield])

        # 4) 角色（供子类扩展的专属变量）
        roles = self.generate_roles() or {}
        role_fields: List[str] = roles.get("fields", []) if isinstance(roles, dict) else []
        vars_ordered.extend(role_fields)

        # 5) 去重保序，排除交互项（R 端派生列，不在原始 df 中）
        interaction_cols = set((self.interaction_map or {}).keys())
        return [
            v for v in _dedup_preserve_order(vars_ordered)
            if v not in interaction_cols
        ]

    def generate_roles(self) -> Dict[str, List[str]]:
        """
        供特殊任务（如 2SLS/Heckman/PSM）覆写，声明其专属的字段角色。
        父类仅占位：返回空字段列表。
        约定：返回结构须包含 key 'fields' → List[str]
        """
        return {"fields": []}

    # ---------------- 模板上下文 -----------------
    def generate_task_context(self) -> Dict[str, Any]:
        """
        产出通用模板上下文（不含数据路径）：
        - 原子键：y, X, controls, fe, panel_ids, options
        - regression_model: 供 OLS/FE/RE 宏直接消费
            * dependent_var
            * independent_vars
            * control_vars
            * fixed_effects
            * subset 三件套（若提供）
        """
        fe_vars = list(self.category_controls or [])

        ctx: Dict[str, Any] = {
            "y": self.y,
            "X": self.X_flat,
            "controls": list(self.controls or []),
            "fe": fe_vars,
            "panel_ids": dict(self.panel_ids or {}),
            "options": dict(self.options or {}),
            "selected_model": self.model,
        }

        # regression_model for 宏
        regression_model: Dict[str, Any] = {
            "dependent_var": self.y,
            "independent_vars": self.X_flat,
            "control_vars": list(self.controls or []),
            "fixed_effects": fe_vars,
        }

        subset = self.subset or {}
        if subset.get("classification_field") and subset.get("operator") and subset.get("classification_conditions"):
            regression_model.update(
                {
                    "classification_field": subset["classification_field"],
                    "operator": subset["operator"],
                    "classification_conditions": subset["classification_conditions"],
                }
            )

        ctx["regression_model"] = regression_model
        return ctx

    def extra_context(self) -> Dict[str, Any]:
        """非模型语义的补充信息（可供模板渲染使用），默认空。子类可覆盖。"""
        return {}

    # ---------------- 交互效应 -----------------
    def _build_interaction_term(self) -> None:
        """
        当 interaction=True 时：
        1. 从两个分组各取一个变量
        2. 生成交互项变量名（变量名按字母排序）
        3. 记录到 interaction_map
        4. 将交互项放入 "intersection" 分组
        幂等：interaction_map 非空或已存在 intersection 分组则跳过。
        """
        if not self.interaction:
            return
        if self.interaction_map:
            return

        # 兜底幂等：已存在 intersection 分组
        if isinstance(self.X, dict) and "intersection" in self.X:
            existing_inter = self.X["intersection"]
            if existing_inter:
                # 重建 interaction_map
                groups = {k: v for k, v in self.X.items() if k != "intersection"}
                if len(groups) == 2:
                    group_names = list(groups.keys())
                    v1 = groups[group_names[0]][0] if groups[group_names[0]] else None
                    v2 = groups[group_names[1]][0] if groups[group_names[1]] else None
                    if v1 and v2:
                        for col in existing_inter:
                            self.interaction_map[col] = (v1, v2)
                return

        # 排除 intersection 分组，获取两个主分组
        groups = {k: v for k, v in self.X.items() if k != "intersection"}

        # 格式已在 __post_init__ 中校验，这里直接取值
        group_names = list(groups.keys())
        v1 = groups[group_names[0]][0]
        v2 = groups[group_names[1]][0]

        # 生成交互项名称（变量名按字母排序）
        pair = tuple(sorted([v1, v2]))
        candidate = f"inter_{pair[0]}_X_{pair[1]}"
        if len(candidate) > 50:
            h = hashlib.sha256(f"{pair[0]}|{pair[1]}".encode()).hexdigest()[:8]
            candidate = f"inter_{h}"

        self.interaction_map = {candidate: (v1, v2)}

        # 将交互项放入 "intersection" 分组
        self.X["intersection"] = [candidate]

    # ---------------- 代码生成与执行（占位） -----------------
    def generate_code(self) -> str:
        """
        生成并返回可执行的 R 代码文本：
        - 直接实例化 CodeGenerator(self)；
        - 调用 assembly() 获得完整脚本（含依赖安装段）；
        - 将结果写入 self.code_text 并返回。
        不负责执行。
        """
        self._build_interaction_term()
        self.cg = CodeGenerator(self)
        is_root_task = not bool(getattr(self, "parent_task_id", None))
        code_text = self.cg.assembly(is_root=is_root_task)
        self.code_text = code_text
        self._code_segments = code_text
        self.prep_fingerprint = code_text['prep_fingerprint']
        return code_text

    def copy_with(self, **overrides):
        """
        仅拷贝“构造器可接收”的字段，过滤掉运行期/缓存属性，合并 overrides 后构造新实例。
        不会删除 prep_fingerprint（允许覆盖）。
        """
        # 1) 收集可传入 __init__ 的字段
        if is_dataclass(self.__class__):
            # dataclass：只取 init=True 的字段
            init_field_names = [f.name for f in dc_fields(self.__class__) if getattr(f, "init", True)]
            kwargs = {name: getattr(self, name) for name in init_field_names if hasattr(self, name)}
        else:
            # 非 dataclass：用 __init__ 的签名兜底
            sig = inspect.signature(self.__class__)
            init_param_names = [
                p.name for p in sig.parameters.values()
                if p.kind in (inspect._ParameterKind.POSITIONAL_OR_KEYWORD, inspect._ParameterKind.KEYWORD_ONLY)
                and p.name != "self"
            ]
            kwargs = {name: getattr(self, name) for name in init_param_names if hasattr(self, name)}

        # 2) 合并外部覆盖
        kwargs.update(overrides)

        # 3) 过滤运行期/缓存字段（黑名单）
        RUNTIME_BLACKLIST = {
            "code_text", "cg", "exec_result", "_exec_result", "_exec_result_raw",
            "dependencies", "prepare_code", "execute_code", "post_regression_code",
            "combined", "prep_args", "_code_segments",
            "interaction_map",
        }
        for k in list(kwargs.keys()):
            if k in RUNTIME_BLACKLIST:
                kwargs.pop(k, None)

        # 4) 返回新实例
        return self.__class__(**kwargs)

    # ---------------- 结果验收（占位） -----------------
    def evaluate_acceptance(self, significance_level: Optional[float] = 0.1) -> bool:
        """
        根据规则判断任务结果是否被接受。
        1. 情况一，如果self.exec_result中只有self.exec_result['forward_res']为非空，则判断是否自变量的p值是否小于等于输入的significance_level
        2. 情况二， 如果self.exec_result['forward_res']和self.exec_result['opposite_res']均为非空，则判断两个结果的自变量（组）的显著性是否存在差异。（在这里True=有差异）
        判断是否差异的逻辑是：
        判断两组自变量是否显著性一致（以入参significance_level计），且所有自变量符号（正负性）全部都一致记为False，否则为True。
        """
        def _bool_function(res_df, X, significance_level: float = significance_level) -> bool:
            if set(X).issubset(set(res_df['Variable'])):
                return all((p <= significance_level for p in list(res_df[res_df['Variable'].isin(X)]['P_Value'])))
            else:
                return False
        def _sign_consistency_checker(res_obj, X, significance_level: float = significance_level):
            forward_df = res_obj['forward_res']['coefficients']
            opposite_df = res_obj['opposite_res']['coefficients']
            forward_df = forward_df[forward_df['Variable'].isin(X)][['Variable','P_Value','Estimate']]
            opposite_df = opposite_df[opposite_df['Variable'].isin(X)][['Variable','P_Value','Estimate']]
            temp = pd.merge(forward_df,opposite_df,on='Variable')
            temp["significance_consistency"] = (
                ((temp["P_Value_x"] > significance_level) & (temp["P_Value_y"] > significance_level)) |
                ((temp["P_Value_x"] <= significance_level) & (temp["P_Value_y"] <= significance_level))
            )
            temp["sign_consistency"] = (
                ((temp["Estimate_x"] > 0) & (temp["Estimate_y"] > 0)) |
                ((temp["Estimate_x"] < 0) & (temp["Estimate_y"] < 0))
            )
            print(f'temp df is {temp}')
            sign_consistency_bool = all(temp[temp['Variable'].isin(X)]['sign_consistency'])
            significance_consistency_bool = all(temp[temp['Variable'].isin(X)]['significance_consistency'])
            if set(X).issubset(set(forward_df['Variable'])) and set(X).issubset(set(opposite_df['Variable'])):
                return not(sign_consistency_bool and significance_consistency_bool)
            else: 
                return False
        res = getattr(self, "exec_result", None)
        if res is None: #无结果直接拒绝
            return False
        if any(isinstance(value,StrVector)for value in res.values()): # 如果两个里面至少有一个StrVector则直接拒绝，因为存在没结果的情况
            return False
        forward_res = res.get('forward_res',None)
        opposite_res = res.get('opposite_res',None)
        X_flat = self.X_flat
        if forward_res is not None:
            if set(X_flat).issubset(set(forward_res['coefficients']['Variable'])):
                forward_res_bool = _bool_function(forward_res['coefficients'], X_flat)
            else:
                return False
        else:
            forward_res_bool = False
        if forward_res is not None and opposite_res is not None:
            if set(X_flat).issubset(set(forward_res['coefficients']['Variable'])) and set(X_flat).issubset(set(opposite_res['coefficients']['Variable'])):
                opposite_res_bool = _sign_consistency_checker(res, X_flat) # 存在 opposite 时以对比为准
            else:
                return False
        else: 
            opposite_res_bool = False # forward_res 和opposite_res不都为 None则直接赋值False

        if res['opposite_res'] is not None: # 判断 是否不存在opposite_res 结果
            return opposite_res_bool
        else:
            return forward_res_bool

    def _generate_task_id(self) -> str:
        """
        依据任务“语义等价定义”生成稳定 task_id。
        纳入字段：
        - y, X, controls, category_controls, panel_ids(entity/time),
            subset(classification_field/operator/normalized classification_conditions),
            options(排序后), model
        返回：形如 "FE-ROA-1a2b3c4d5e6f" 的短ID，并写回 self.task_id
        """

        # subset 里的条件需要标准化，避免同义不同写法导致不稳定
        subset = dict(self.subset or {})
        cfield = subset.get("classification_field")
        op = subset.get("operator")
        cond_raw = subset.get("classification_conditions")
        cond_norm = None
        if cond_raw is not None:
            try:
                cond_norm = self._normalize_classification_condition(cond_raw)
            except Exception:
                # 无法规范化则退回原始字符串（保证不抛错）
                cond_norm = str(cond_raw)

        # 只取 panel_ids 中与面板声明相关的键
        panel_ids_norm = {}
        if isinstance(self.panel_ids, dict):
            for k in ("entity", "time"):
                if k in self.panel_ids and self.panel_ids[k]:
                    panel_ids_norm[k] = self.panel_ids[k]

        # 注意：为获得稳定性，options 要排序后序列化
        # X 保持原始分组结构用于哈希（不同分组应产生不同 ID）
        payload = {
            "y": self.y,
            "X": self.X,
            "controls": list(self.controls or []),
            "category_controls": list(self.category_controls or []),
            "panel_ids": panel_ids_norm,
            "subset": {
                "classification_field": cfield,
                "operator": op,
                "classification_conditions": cond_norm,
            } if (cfield or op or cond_norm is not None) else {},
            "options": dict(self.options or {}),   # json.dumps(sort_keys=True) 会排序键
            "model": self.model,
        }
        # 稳定序列化（去空格、按键排序，确保跨运行一致）
        s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]

        prefix = f"{self.model}-{self.y}"
        task_id = f"{prefix}-{digest}"
        self.task_id = task_id
        return task_id

    def _normalize_classification_condition(self, value: Any) -> str:
        """
        标准化 classification_conditions 字段：
        - field(<col_name>)  -> col_name
        - 数值常量 (int/float/或 '3.2', 'num(3.2)') -> 3.2
        - 字符串常量 str(xxx) -> "xxx"
        - 表达式 expr(x+y>0) -> x+y>0
        - 其他非法输入 -> ValueError
        """
        # Python 数值类型直接返回
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if not isinstance(value, str):
            raise ValueError(f"Unsupported classification_conditions type: {type(value)}")
        v = value.strip()
        # field(<col>)
        m = re.match(r"^field\(([A-Za-z_][A-Za-z0-9_\.]*)\)$", v)
        if m:
            return m.group(1)

        # 数字字符串或 num(...)
        m = re.match(r"^num\(([+-]?\d+(\.\d+)?)\)$", v)
        if m:
            return m.group(1)
        m = re.match(r"^[+-]?\d+(\.\d+)?$", v)
        if m:
            return v

        # 字符串
        m = re.match(r'^str\((.*)\)$', v)
        if m:
            inner = m.group(1)
            inner = inner.replace('"', '\\"')  # 转义双引号
            return f'"{inner}"'

        # R语言表达式
        m = re.match(r"^expr\((.*)\)$", v)
        if m:
            return m.group(1)

        raise ValueError(f"Unrecognized classification_conditions format: {value}")

    def to_json(self, *, as_str: bool = False, include_runtime: bool = False, indent: int = 2) -> Any:
        """
        导出任务为 JSON 可序列化对象（默认返回 dict；as_str=True 时返回 JSON 字符串）。
        - include_runtime: 是否包含运行期信息（如 code_text 的存在与否、简要执行结果标识）。
        - indent: 当 as_str=True 时用于 json.dumps 的缩进。
        注意：会排除不可序列化的对象（如 config/exec_result 的复杂类型）。
        """
        payload: Dict[str, Any] = {
            "name": self.name,
            "dataset": self.dataset,
            "y": self.y,
            "X": self.X_flat,  # 输出展平后的列表（兼容现有消费方）
            "X_groups": self.X if isinstance(self.X, dict) else {'default': self.X_flat},  # 分组信息
            "controls": list(self.controls or []),
            "category_controls": list(self.category_controls or []),
            "panel_ids": dict(self.panel_ids or {}),
            # 注意：此处不再直接放 subset
            "options": dict(self.options or {}),
            "model": self.model,
            "incremental_controls": bool(getattr(self, "incremental_controls", False)),
        }

        # 将 subset 展平到同级
        subset = dict(self.subset or {})
        for k in ("classification_field", "operator", "classification_conditions"):
            v = subset.get(k)
            if v is not None:
                payload[k] = v

        if include_runtime:
            payload["runtime"] = {
                "has_code_text": self.code_text is not None,
                "has_exec_result": self.exec_result is not None,
            }
        cond = self.subset.get("classification_conditions")
        if cond is not None:
            payload["classification_conditions"] = self._normalize_classification_condition(cond)
        if as_str:
            return json.dumps(payload, ensure_ascii=False, indent=indent)
        return payload

    # ---------------- 条件表达式验收 -----------------
    def check_condition(self, expr: str, target: str = "forward") -> bool:
        """
        判断 exec_result 是否满足给定的条件表达式。

        语法格式:
            <组名>(<符号要求>) <比较运算符> <p-value阈值>

        示例:
            - "treatment(+) < 0.05"           # treatment组系数为正且p<0.05
            - "policy(-) <= 0.1"              # policy组系数为负且p<=0.1
            - "treatment(*) < 0.05"           # treatment组p<0.05（不限符号）
            - "treatment(+) < 0.05 & policy(-) <= 0.1"  # AND组合
            - "treatment(+) < 0.05 | policy(-) <= 0.1"  # OR组合

        符号要求:
            - '+' : 系数为正 (Estimate > 0)
            - '-' : 系数为负 (Estimate < 0)
            - '*' : 不限符号

        参数:
            expr: 条件表达式字符串
            target: 结果目标，"forward" 或 "opposite"

        返回:
            bool: 组内所有变量是否均满足条件

        异常:
            ValueError: 表达式语法错误、引用的组名不存在、或 exec_result 为空
        """
        # 1. 检查 exec_result
        res = getattr(self, "exec_result", None)
        if res is None:
            raise ValueError("exec_result is empty; cannot evaluate acceptance conditions")

        target_key = f"{target}_res"
        target_res = res.get(target_key)
        if target_res is None:
            raise ValueError(f"exec_result does not contain '{target_key}'")

        coefficients = target_res.get("coefficients")
        if coefficients is None:
            raise ValueError(f"'{target_key}' does not contain 'coefficients'")

        # 2. 构建系数映射: {Variable: {P_Value: ..., Estimate: ...}}
        #    支持 DataFrame 或 List[dict] 两种格式
        if isinstance(coefficients, pd.DataFrame):
            # DataFrame 格式：转为 {Variable: {col: val, ...}}
            coef_map = coefficients.set_index("Variable").to_dict(orient="index")
        else:
            # List[dict] 格式
            coef_map = {row["Variable"]: row for row in coefficients}

        # 3. 解析表达式
        tokens = _tokenize_condition(expr)
        parser = _ConditionParser(tokens)
        ast = parser.parse()

        # 4. 求值
        return self._eval_condition_ast(ast, coef_map)

    def _eval_condition_ast(self, node, coef_map: Dict[str, Any]) -> bool:
        """递归求值 AST 节点"""
        if isinstance(node, _Condition):
            return self._eval_single_condition(node, coef_map)
        elif isinstance(node, _AndExpr):
            return self._eval_condition_ast(node.left, coef_map) and \
                   self._eval_condition_ast(node.right, coef_map)
        elif isinstance(node, _OrExpr):
            return self._eval_condition_ast(node.left, coef_map) or \
                   self._eval_condition_ast(node.right, coef_map)
        else:
            raise ValueError(f"Unknown AST node type: {type(node)}")

    def _eval_single_condition(self, cond: _Condition, coef_map: Dict[str, Any]) -> bool:
        """
        求值单个条件: group(sign) op pvalue

        逻辑:
        1. 从 self.X 获取 group 对应的变量列表
        2. 对组内每个变量检查 p-value 和符号条件
        3. 所有变量均满足时返回 True
        """
        X = self.X

        # 获取组内变量列表
        if cond.group in X:
            # 组名匹配
            vars_in_group = X[cond.group]
        elif cond.group in self.X_flat:
            # 直接使用变量名（兼容模式）
            vars_in_group = [cond.group]
        else:
            raise ValueError(f"Unknown group or variable name: '{cond.group}'. Available groups: {list(X.keys())}")

        # 确保是列表
        if isinstance(vars_in_group, str):
            vars_in_group = [vars_in_group]

        # 检查组内每个变量
        for var in vars_in_group:
            if var not in coef_map:
                # 变量在结果中不存在
                return False
            row = coef_map[var]
            p_val = row.get("P_Value")
            estimate = row.get("Estimate")

            if p_val is None or estimate is None:
                return False

            # 检查 p-value 条件
            if not self._compare_pvalue(p_val, cond.comparator, cond.pvalue):
                return False

            # 检查符号条件
            if cond.sign == "+" and estimate <= 0:
                return False
            if cond.sign == "-" and estimate >= 0:
                return False
            # sign == "*" 不检查符号

        return True

    @staticmethod
    def _compare_pvalue(val: float, op: str, threshold: float) -> bool:
        """比较 p-value 与阈值"""
        comparators = {
            "<":  lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            ">":  lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
            "=":  lambda a, b: abs(a - b) < 1e-9,
        }
        if op not in comparators:
            raise ValueError(f"Unknown comparator: '{op}'")
        return comparators[op](val, threshold)
