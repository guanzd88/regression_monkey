from __future__ import annotations
from dataclasses import dataclass, field
from dataclasses import is_dataclass, fields as dc_fields
from typing import List, Dict, Any, Optional, Literal
from reg_monkey.util import ConfigLoader
from reg_monkey.code_generator import CodeGenerator
from rpy2.robjects.vectors import StrVector
import re,json,hashlib,inspect
import pandas as pd
import numpy as np

_FIELD_PATTERN = re.compile(r"field\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")

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
    X: List[str] = field(default_factory=list)
    controls: List[str] = field(default_factory=list)
    category_controls: List[str] = field(default_factory=list)
    panel_ids: Dict[str, str] = field(default_factory=dict)  # {'entity': '...', 'time': '...'}
    subset: Dict[str, Optional[str]] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    model: Literal['OLS', 'FE', 'RE'] = 'OLS'
    note: str = "baseline"
    prep_fingerprint: str = ""
    if_reprep: Optional[str] = field(default=False, init=True, repr=False, compare=False)
    cg: Any = field(default=None, init=True, repr=False, compare=False)
    active: Optional[str] = field(default=True, init=True, repr=False, compare=False)

    # ===== 第2部分：模板渲染与执行编排 =====
    # code_text: Optional[str] = field(default=None, repr=False)   # generate_code 的产出

    # ===== 第3部分：结果容器与验收 =====
    # exec_result: Optional[Any] = field(default=None, repr=False)
    @property
    def exec_result(self):
        """访问时拿到已处理后的结果（Python 原生结构）。"""
        return self._exec_result

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
        if isinstance(py_obj, dict):
            forward = py_obj.get("forward_res") or py_obj.get("forward_result")
            opposite = py_obj.get("opposite_res") or py_obj.get("opposite_result")

        # 组织一个稳定的返回结构（系数矩阵若是 list[dict]，可直接用）
        def _pack(block):
            if block is None:
                return None
            else:
                return block

        return {
            "forward_res":  _pack(forward),
            "opposite_res": _pack(opposite),
        }

    # ===== 配置加载器（按需） =====
    config: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # 轻量校验：模型枚举
        allowed = {'OLS', 'FE', 'RE'}
        if self.model not in allowed:
            raise ValueError(f"model 必须是 {allowed} 之一，收到: {self.model}")
        if self.config is None:
            self.config = ConfigLoader()

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
        vars_ordered.extend(list(self.X or []) if isinstance(self.X,list) else [self.X])
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
            cols_in_field = _FIELD_PATTERN.findall(cond or "")
            if cols_in_field:
                vars_ordered.extend([cfield])
                vars_ordered.extend(cols_in_field)
            else:
                vars_ordered.extend([cfield])

        # 4) 角色（供子类扩展的专属变量）
        roles = self.generate_roles() or {}
        role_fields: List[str] = roles.get("fields", []) if isinstance(roles, dict) else []
        vars_ordered.extend(role_fields)

        # 5) 去重保序
        return _dedup_preserve_order(vars_ordered)

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
            "X": list(self.X or []),
            "controls": list(self.controls or []),
            "fe": fe_vars,
            "panel_ids": dict(self.panel_ids or {}),
            "options": dict(self.options or {}),
            "selected_model": self.model,
        }

        # regression_model for 宏
        regression_model: Dict[str, Any] = {
            "dependent_var": self.y,
            "independent_vars": list(self.X or []),
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

    # ---------------- 代码生成与执行（占位） -----------------
    def generate_code(self) -> str:
        """
        生成并返回可执行的 R 代码文本：
        - 直接实例化 CodeGenerator(self)；
        - 调用 assembly() 获得完整脚本（含依赖安装段）；
        - 将结果写入 self.code_text 并返回。
        不负责执行。
        """
        self.cg = CodeGenerator(self)
        code_text = self.cg.assembly()
        self.code_text = code_text
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
            "combined", "prep_args",
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
        if forward_res is not None:
            if set(self.X).issubset(set(forward_res['coefficients']['Variable'])):
                forward_res_bool = _bool_function(forward_res['coefficients'],self.X)
            else:
                return False
        else: 
            forward_res_bool = False
        if forward_res is not None and opposite_res is not None:
            if set(self.X).issubset(set(forward_res['coefficients']['Variable'])) and set(self.X).issubset(set(opposite_res['coefficients']['Variable'])):
                opposite_res_bool = _sign_consistency_checker(res,self.X) # 存在 opposite 时以对比为准
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
        payload = {
            "y": self.y,
            "X": list(self.X or []),
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
            raise ValueError(f"classification_conditions 类型不支持: {type(value)}")
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

        raise ValueError(f"无法识别的 classification_conditions 格式: {value}")

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
            "X": list(self.X or []) if isinstance(self.X,list) else [self.X], # 如果X输入的不是列表则强制转换为列表
            "controls": list(self.controls or []),
            "category_controls": list(self.category_controls or []),
            "panel_ids": dict(self.panel_ids or {}),
            # 注意：此处不再直接放 subset
            "options": dict(self.options or {}),
            "model": self.model,
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