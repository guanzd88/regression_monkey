from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from reg_monkey.util import ConfigLoader,PublicConfig  # 由外部提供
import json, hashlib, re

class CodeGenerator:
    """
    代码生成器：把 `StandardRegTask` 渲染为可执行的 **R** 代码（基于 Jinja2 模板）。

    概要
    ----
    - 依据任务配置与外部公共配置（语言、模板映射、依赖清单），选择模板并渲染出三个层次的代码：
      ① 数据准备（清洗/设面板/Winsorize/因子化）、② 主回归执行、③（可选）回归后标准化输出。
    - 自动收集并去重依赖，在脚本前部生成一次性的安装/加载段。
    - 对“准备阶段入参”做**稳定规范化**并计算指纹（sha256），便于跨环境复现实验。

    依赖
    ----
    - `jinja2`（Environment、FileSystemLoader）
    - 外部提供的 `ConfigLoader` 与 `PublicConfig`
      * `PublicConfig.TEMPLATE_MAP`：语言→模板文件名映射
      * `PublicConfig.LIBRARY_REQUIREMENTS`：各片段对应的 R 包清单
      * `PublicConfig.MODEL_MAPPING`：模型名→模板宏名映射

    构造
    ----
    __init__(task_obj: Any)
        - `task_obj`：一个 `StandardRegTask` 实例（需提供 `.to_json()`、`.get_all_variables()` 等接口）。
        - 从 `ConfigLoader()` 读取语言/模板根目录；根据 `PublicConfig.TEMPLATE_MAP` 解析模板文件。
        - 若语言不受支持 → `ValueError`；若模板不存在 → `FileNotFoundError`。

    主要属性
    --------
    - task_obj: Any                      任务对象
    - config: ConfigLoader               外部配置
    - language: str                      渲染语言（目前仅 "r"）
    - template_root: str                 模板根目录（可通过配置覆写）
    - _template_path: pathlib.Path       选中的模板文件路径
    - jinja_env: jinja2.Environment      Jinja2 环境
    - code_text: Optional[str]           生成的最终代码（由 `assembly()` 产出）
    - prep_fingerprint: str              准备阶段参数的稳定指纹（sha256）
    - _last_segments: Dict[str, Any]     最近一次组装的分段产物（调试用）

    常用公开方法
    ------------
    - get_required_package(task_req_lib: List[str]) -> List[str]
        去重并保序返回依赖列表。
    - render_required_libraries(deps: List[str]) -> str
        调用模板宏 `install_required_packages` 渲染安装/加载段。
    - render_clean_sample(input_data_variable="input_df", output_data_variable="r_df") -> Dict[str, Any]
        产出清洗 + `extract_coefficients` 函数注入段，并返回所需依赖。
    - render_set_panel() -> Dict[str, Any]
        基于 `panel_ids` 渲染面板数据设定代码；若缺少必要键则返回空段。
    - render_winsorize() -> Dict[str, Any]
        按 `options.winsorize_percent/fields` 生成 Winsorize 与分类因子化代码。
    - render_post_regression(include_opposite=True) -> str
        渲染回归后固定格式的 Python 回传对象构建段（forward/opposite 两份结果）。
    - render_main_task(include_opposite=False) -> Dict[str, Any]
        依据模型（OLS/FE/RE）选择相应模板宏并渲染主回归代码；返回依赖与 code_block。
    - assembly(internal_output: bool = True, include_opposite=True) -> Dict[str, Any]
        **组装入口**。把准备层与执行层拼接（必要时附加回归后导出段），合并依赖并计算
        `prep_fingerprint`，返回结构：
        {
          "prepare_code", "execute_code", "post_regression_code",
          "dependencies", "combined", "prep_args", "prep_fingerprint"
        }

    关键内部实现要点
    ----------------
    - _get_macro(name): 从模板中取宏，缺失时抛 `AttributeError`。
    - _infer_plm_model_effect(tj): 将任务 JSON 规范化为 plm 兼容的 `model/effect` 组合：
        * OLS/pooling → model="pooling", effect=None
        * FE/within → 根据 `panel_ids.entity/time` 与（固定效应 ∪ `category_controls`）的重合关系推断
          individual/time/twoways。
    - _normalize_for_fingerprint(obj): 递归规范化 dict/list/set 等结构，以获得稳定 JSON 串用于哈希。

    异常与边界
    ----------
    - 读取模板或宏失败：`FileNotFoundError` / `AttributeError`。
    - 不支持的语言：`ValueError`。
    - 指纹计算失败不会中断，`prep_fingerprint` 会被置为 `'异常'`，但仍返回代码。

    最简示例
    -------
    >>> task = StandardRegTask(...)                 # 已配置好的任务
    >>> gen = CodeGenerator(task)
    >>> out = gen.assembly(internal_output=True)    # 组装各段并计算指纹
    >>> print(out["combined"])                      # 打印完整脚本
    >>> print(out["prep_fingerprint"])              # 记录准备阶段参数指纹以溯源
    """

    def __init__(self, task_obj: Any):
        """
        初始化 CodeGenerator。

        - `task_obj` 为 StandardRegTask 实例。
        - 通过外部提供的 `ConfigLoader` 加载配置（不在此类中实现）。
        - 根据配置语言选择模板文件，构建 Jinja2 环境。
        """
        self.task_obj = task_obj
        self.config = ConfigLoader()
        # 语言统一小写
        self.language: str = str(getattr(self.config, "language", "r")).lower()
        if self.language not in PublicConfig.TEMPLATE_MAP:
            raise ValueError(
                f"Unsupported language '{self.language}'. Supported values: {list(PublicConfig.TEMPLATE_MAP)}"
            )

        # 确定模板路径
        default_root = Path(__file__).parent
        template_root = getattr(self.config, "template_root", None)
        self.template_root: str = str(Path(template_root) if template_root else default_root)
        template_filename = PublicConfig.TEMPLATE_MAP[self.language]
        self._template_path = Path(self.template_root) / template_filename

        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Template file not found: {self._template_path}. Set config.template_root to override the path."
            )

        self.jinja_env: Environment = Environment(
            loader=FileSystemLoader(self.template_root),
            autoescape=False,  # 生成代码无需转义
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._requirements: List[str] = []  # 收集的依赖包
        self.code_text: Optional[str] = None  # 最终生成的代码
        self._stepwise_result_var: Optional[str] = None

    # ---------------------------
    # 内部工具
    # ---------------------------
    def _get_macro(self, macro_name: str):
        """从模板中获取指定宏，不存在时抛出异常。"""
        try:
            tmpl = self.jinja_env.get_template(self._template_path.name)
        except TemplateNotFound as e:
            raise FileNotFoundError(f"Template not found: {self._template_path}") from e

        module = tmpl.make_module(vars={})
        if not hasattr(module, macro_name):
            raise AttributeError(
                f"Template '{self._template_path.name}' does not define macro '{macro_name}'."
            )
        return getattr(module, macro_name)

    @staticmethod
    def _dedup_preserve_order(items: List[str]) -> List[str]:
        """去重并保持原有顺序。"""
        seen = set()
        out: List[str] = []
        for x in items:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return out

    # ---------------------------
    # 多数据集支持：上下文变量名分配
    # ---------------------------
    def get_context(self, is_root: bool = True) -> Dict[str, Optional[str]]:
        """
        根据任务在树中的位置，返回统一的 R 变量名配置。

        参数:
            is_root: 是否为根节点（Baseline 任务）

        返回:
            {
                "input_data_variable": 输入数据变量名（原始层）,
                "output_data_variable": 输出数据变量名（处理层），子任务为 None,
                "regression_data_variable": 回归使用的数据变量名（处理层）,
            }
        """
        ctx = getattr(self.task_obj, "_data_context", None)
        if isinstance(ctx, dict):
            return ctx

        ds = self._get_dataset_key()

        if is_root:
            # Root 任务：输入是原始注入的数据，输出是 Baseline 准备好的数据
            return {
                "input_data_variable": f"df_raw_{ds}",
                "output_data_variable": f"df_prep_{ds}_baseline",
                "regression_data_variable": f"df_prep_{ds}_baseline",
            }
        else:
            # 子任务：直接从 Baseline 准备好的数据读取，禁止写入
            return {
                "input_data_variable": f"df_prep_{ds}_baseline",
                "output_data_variable": None,  # 子任务禁写！
                "regression_data_variable": f"df_prep_{ds}_baseline",
            }

    def _get_dataset_key(self) -> str:
        """
        获取任务的数据集键名，用于 R 变量命名。
        优先使用 task_obj.get_dataset_key()，否则回退到 dataset 字段或 "main"。
        """
        import re
        # 优先调用 get_dataset_key 方法
        if hasattr(self.task_obj, "get_dataset_key") and callable(getattr(self.task_obj, "get_dataset_key")):
            return self.task_obj.get_dataset_key()
        # 回退：从 dataset 字段提取
        dataset = getattr(self.task_obj, "dataset", None)
        if dataset:
            safe = re.sub(r'[^A-Za-z0-9_]', '_', str(dataset))
            if safe and safe[0].isdigit():
                safe = '_' + safe
            return safe or "main"
        return "main"

    @staticmethod
    def _sanitize_identifier(value: Optional[str], fallback: str = "task") -> str:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", str(value) if value else "")
        if not safe:
            safe = fallback
        if safe[0].isdigit():
            safe = f"_{safe}"
        return safe.lower()

    @staticmethod
    def _collect_stepwise_variables(y: str, x_vars: List[str], controls: List[str], fe_controls: List[str]) -> List[str]:
        ordered: List[str] = []
        seen: set[str] = set()
        for value in [y] + list(x_vars or []) + list(controls or []) + list(fe_controls or []):
            if not value:
                continue
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

    def _get_panel_ids(self) -> Dict[str, Optional[str]]:
        panel_ids = getattr(self.task_obj, "panel_ids", None)
        if callable(panel_ids):
            panel_ids = panel_ids()
        if not isinstance(panel_ids, dict):
            panel_ids = getattr(self.task_obj, "__dict__", {}).get("panel_ids") or {}
        if not isinstance(panel_ids, dict):
            panel_ids = {}
        entity = (
            panel_ids.get("entity")
            or panel_ids.get("Entity")
            or panel_ids.get("id")
            or panel_ids.get("ID")
            or panel_ids.get("individual")
        )
        time = (
            panel_ids.get("time")
            or panel_ids.get("Time")
            or panel_ids.get("year")
            or panel_ids.get("Year")
            or panel_ids.get("t")
            or panel_ids.get("T")
        )
        out: Dict[str, Optional[str]] = {}
        if entity:
            out["entity"] = str(entity)
        if time:
            out["time"] = str(time)
        return out

    def _resolve_fmb_options(self) -> Dict[str, str]:
        opts = dict(getattr(self.task_obj, "options", {}) or {})
        nested = opts.get("fmb") if isinstance(opts.get("fmb"), dict) else {}

        def pick(key: str, fallback_key: Optional[str] = None):
            if isinstance(nested, dict) and key in nested:
                return nested.get(key)
            if fallback_key and fallback_key in opts:
                return opts.get(fallback_key)
            return opts.get(key)

        nw_lag_val = pick("nw_lag", "fmb_nw_lag")
        t_type_val = pick("t_type", "fmb_t_type") or "NW"
        shanken_val = pick("shanken", "fmb_shanken")
        shanken_list = pick("shanken_use_factors", "fmb_shanken_use_factors")

        def format_int(value: Any) -> str:
            if value is None:
                return "NULL"
            try:
                return f"as.integer({int(value)})"
            except Exception:
                return "NULL"

        def format_bool(value: Any, default: bool = True) -> str:
            if value is None:
                return "TRUE" if default else "FALSE"
            return "TRUE" if bool(value) else "FALSE"

        def format_t_type(value: Any) -> str:
            import json as _json
            text = str(value or "NW").upper()
            return _json.dumps(text)

        def format_vector(values: Any) -> str:
            import json as _json
            if values is None:
                return "NULL"
            if isinstance(values, str):
                values = [values]
            try:
                seq = [str(v) for v in values if v is not None and str(v) != ""]
            except TypeError:
                seq = []
            if not seq:
                return "NULL"
            return "c(" + ", ".join(_json.dumps(s) for s in seq) + ")"

        return {
            "nw_lag": format_int(nw_lag_val),
            "t_type": format_t_type(t_type_val),
            "shanken": format_bool(shanken_val, default=True),
            "shanken_factors": format_vector(shanken_list),
        }

    @staticmethod
    def _build_subset_strings(regression_model: Dict[str, Any]) -> Dict[str, Optional[str]]:
        field = regression_model.get("classification_field") or None
        operator = regression_model.get("operator") or None
        cond = regression_model.get("classification_conditions") or None
        if field and operator and (cond is not None and str(cond).strip() != ""):
            expr = f"({field} {operator} {cond})"
            return {"subset": expr, "subset_neg": f"(!{expr})"}
        return {"subset": None, "subset_neg": None}

    @staticmethod
    def _infer_plm_model_effect(tj) -> dict:
        """
        将用户入参映射为 plm 的 model/effect（只覆盖 OLS + FE）。
        规则：
          * model='OLS' or 'pooling' → model='pooling', effect=None
          * model in {'FE','within'} → 
              - 与 time 重合且不与 id 重合 → effect='time'
              - 与 id 重合且不与 time 重合 → effect='individual'
              - 其它（无重合 / 全重合 / 信息不足） → effect='twoways'
        其中“固定效应变量列表”= fixed_effects ∪ category_controls（名称大小写不敏感）。
        """
        # tj = task.to_json()
        out = dict(tj)

        # 读取 & 归一化模型名
        in_model = (tj.get("model") or "").strip().lower()

        # 面板索引，兼容多种命名，统一小写
        pid = tj.get("panel_ids") or {}
        id_name   = (pid.get("entity") or pid.get("id") or pid.get("individual") or "").strip()
        time_name = (pid.get("time")   or pid.get("year") or pid.get("t")          or "").strip()
        id_l, time_l = id_name.lower(), time_name.lower()

        # 回归方程“固定效应变量列表” = fixed_effects ∪ category_controls
        fe_raw = tj.get("fixed_effects")
        if fe_raw is True:
            fix_from_fixed = set()   # 未指明具体维度 -> 不据此判定
        elif fe_raw in (False, None):
            fix_from_fixed = set()
        elif isinstance(fe_raw, str):
            fix_from_fixed = {fe_raw.strip().lower()}
        else:
            fix_from_fixed = {str(x).strip().lower() for x in list(fe_raw or [])}

        cats = {str(x).strip().lower() for x in (tj.get("category_controls") or [])}

        fe_set = fix_from_fixed | cats  # ← 关键修复点：把 category_controls 也纳入 FE 列表

        # OLS / pooling：直接映射
        if in_model in ("ols", "pooling"):
            out["model"] = "pooling"
            out["effect"] = None
            return out

        # FE / within：按重合规则判定
        if in_model in ("fe", "within"):
            id_match = bool(id_l) and (id_l in fe_set)
            tm_match = bool(time_l) and (time_l in fe_set)

            if tm_match and not id_match:
                effect = "time"
            elif id_match and not tm_match:
                effect = "individual"
            else:
                # 无重合 / 全重合 / 信息不足 → twoways
                effect = "twoways"

            out["model"] = "within"
            out["effect"] = effect
            return out

        # 其他模型不在本规则范围：原样返回
        return out


    def _normalize_for_fingerprint(self,obj):
        """
        规范化为可 JSON 序列化的结构：
        - dict：键排序（通过 json.dumps(sort_keys=True) 实现）
        - list/tuple：保留顺序；tuple 转 list
        - set：转为按字符串排序后的 list（避免无序）
        - 其余原样返回
        """
        from typing import Mapping, Iterable
        if isinstance(obj, dict):
            return {k: self._normalize_for_fingerprint(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [ self._normalize_for_fingerprint(x) for x in obj ]
        if isinstance(obj, set):
            # 将元素先规范化为字符串用于排序，再反规范化为原值的规范形式
            normed = [ self._normalize_for_fingerprint(x) for x in obj ]
            # 为了稳定排序，把转成字符串比较（但保持元素的规范形态作为返回）
            return [x for _, x in sorted(((repr(y), y) for y in normed), key=lambda z: z[0])]
        return obj

    # ---------------------------
    # 依赖处理
    # ---------------------------
    def get_required_package(self, task_req_lib: List[str]) -> List[str]:
        """去重后返回当前任务阶段所需依赖包列表。"""
        return self._dedup_preserve_order(task_req_lib)

    def render_required_libraries(self, deps: List[str]) -> str:
        """使用 `install_required_packages` 宏渲染安装/加载依赖段。"""
        deps = self._dedup_preserve_order(deps)
        if not deps:
            return ""

        macro = self._get_macro("install_required_packages")
        try:
            return str(macro(required_libraries=deps))
        except TypeError:
            return str(macro(deps))

    # ---------------------------
    # 数据清洗
    # ---------------------------
    def render_clean_sample(
        self,
        input_data_variable: str = None,
        output_data_variable: str = None,
        is_root: bool = True,
    ) -> Dict[str, Any]:
        """
        1) 渲染 extract_coefficients 宏，得到 code_block_1（定义 R 侧提取系数的函数）
        2) 收集任务涉及字段，渲染 clean_sample 宏为 code_block_2（列裁剪 + 去 NA）
        3) 返回 required_libraries 与合并后的 code_block

        参数:
            is_root: 是否为根节点。子任务 (is_root=False) 跳过数据准备。
        """
        ctx = self.get_context(is_root=is_root)
        if input_data_variable is None:
            input_data_variable = ctx.get("input_data_variable")
        if output_data_variable is None:
            output_data_variable = ctx.get("output_data_variable")

        if not input_data_variable or not output_data_variable:
            return {"required_libraries": [], "code_block": ""}

        # 1) 提前注入 R 函数：extract_coefficients
        macro_extract = self._get_macro("extract_coefficients")
        code_block_1 = str(macro_extract())

        # 2) 收集字段并渲染 clean_sample
        if hasattr(self.task_obj, "get_all_variables"):
            fields = list(self.task_obj.get_all_variables() or [])
        else:
            tj = self.task_obj.to_json()
            fields = list(
                set(
                    [tj.get("y")]
                    + (tj.get("X") or [])
                    + (tj.get("controls") or [])
                    + (tj.get("category_controls") or [])
                    + list((tj.get("panel_ids") or {}).values())
                    + ([tj.get("subset", {}).get("classification_field")] if tj.get("subset") else [])
                )
            )
            # 如果 classification_conditions 里是 field(col)，也尝试加入 col 名
            cc = (tj.get("subset") or {}).get("classification_conditions")
            if isinstance(cc, str) and cc.startswith("field(") and cc.endswith(")"):
                col = cc[6:-1].strip()
                if col:
                    fields.append(col)

        macro_clean = self._get_macro("clean_sample")
        code_block_2 = str(
            macro_clean(
                fields=fields,
                input_data_variable=input_data_variable,
                output_data_variable=output_data_variable,
            )
        )
        reqs = PublicConfig.LIBRARY_REQUIREMENTS.get(self.language.lower(), {}).get("clean_sample", [])
        return {
            "required_libraries": reqs,
            "code_block": "\n".join([code_block_1, code_block_2]),
        }

    # ---------------------------
    # 面板数据设置
    # ---------------------------
    def render_set_panel(self, is_root: bool = True) -> Dict[str, Any]:
        """使用 `set_panel` 宏渲染面板数据设定代码。"""
        ctx = self.get_context(is_root=is_root)
        data_variable = ctx.get("output_data_variable")
        if not data_variable:
            return {"required_libraries": [], "code_block": ""}

        model_upper = (getattr(self.task_obj, "model", "") or "").upper()
        if model_upper == "FMB":
            # FMB 的 fmb_two_pass 期望使用普通 data.frame
            return {"required_libraries": [], "code_block": ""}

        panel_ids: Dict[str, str] = {}
        if hasattr(self.task_obj, "panel_ids") and callable(getattr(self.task_obj, "panel_ids")):
            panel_ids = dict(self.task_obj.panel_ids() or {})
        else:
            panel_ids = dict(self.task_obj.to_json().get("panel_ids") or {})

        if not panel_ids or not panel_ids.get("entity") or not panel_ids.get("time"):
            return {"required_libraries": [], "code_block": "\n"}

        macro = self._get_macro("set_panel")
        code_block = str(
            macro(
                set_panel_fields={
                    "entity": panel_ids["entity"],
                    "time": panel_ids["time"],
                },
                input_data_variable=data_variable,
                output_data_variable=data_variable,
            )
        )
        reqs = PublicConfig.LIBRARY_REQUIREMENTS.get(self.language.lower(), {}).get("set_panel", [])
        return {"required_libraries": reqs, "code_block": code_block}

    # ---------------------------
    # Winsorize + 分类变量因子化
    # ---------------------------
    def render_winsorize(self, is_root: bool = True) -> Dict[str, Any]:
        """根据任务配置生成 Winsorize 和分类变量因子化代码。"""
        ctx = self.get_context(is_root=is_root)
        data_variable = ctx.get("output_data_variable")
        if not data_variable:
            return {"required_libraries": [], "code_block": ""}

        tj = self.task_obj.to_json()
        options = dict(tj.get("options") or {})
        winsorize_percent = options.get("winsorize_percent")
        winsorize_fields = options.get("winsorize_fields") or []

        code_blocks = []

        if winsorize_percent is not None and isinstance(winsorize_fields, list) and len(winsorize_fields) > 0:
            macro_w = self._get_macro("winsorize")
            code_block_1 = str(
                macro_w(
                    winsorize_percent=winsorize_percent,
                    winsorize_fields=winsorize_fields,
                    input_data_variable=data_variable,
                    output_data_variable=data_variable,
                )
            )
            code_blocks.append(code_block_1)

        category_controls = tj.get("category_controls") or []
        if category_controls:
            macro_cat = self._get_macro("set_category_controls")
            code_block_2 = str(
                macro_cat(
                    fields=category_controls,
                    input_data_variable=data_variable,
                    output_data_variable=data_variable,
                )
            )
            code_blocks.append(code_block_2)

        reqs = []
        reqs.extend(PublicConfig.LIBRARY_REQUIREMENTS.get(self.language.lower(), {}).get("winsorize", []))
        reqs.extend(PublicConfig.LIBRARY_REQUIREMENTS.get(self.language.lower(), {}).get("set_panel", []))

        joined = "\n".join([b for b in code_blocks if b])
        return {"required_libraries": self._dedup_preserve_order(reqs), "code_block": joined}

    # ---------------------------
    # 交互项计算
    # ---------------------------
    def render_interaction_terms(self, is_root: bool = True) -> Dict[str, Any]:
        """
        若任务有交互项（interaction_map 非空），
        生成 R 端交互列计算代码。
        """
        ctx = self.get_context(is_root=is_root)
        data_variable = ctx.get("output_data_variable")
        if not data_variable:
            return {"required_libraries": [], "code_block": ""}

        imap = getattr(self.task_obj, "interaction_map", {})
        if not imap:
            return {"required_libraries": [], "code_block": ""}

        lines = ["# ---- interaction terms ----"]
        for col_name, (v1, v2) in imap.items():
            lines.append(
                f"{data_variable}[['{col_name}']] <- {data_variable}[['{v1}']] * {data_variable}[['{v2}']]"
            )
        return {"required_libraries": [], "code_block": "\n".join(lines)}
    
    # ---------------------------
    # 回归后结果导出
    # ---------------------------
    def render_post_regression(self,include_opposite=True) -> Dict[str]:
        """渲染回归结束之后的标准化代码"""
        macro = self._get_macro(f'EXTRACT_COEFS') # 获取OLS的输出模板
        model_upper = (getattr(self.task_obj, "model", "") or "").upper()
        result_var = f"reg_{self.task_obj.name.lower()}_result"
        # 主回归段里所有模型都命名为 reg_<name>_result，反向子集会追加
        # "_opposite" 后缀而不是插入在 "_result" 前，因此这里需要保持一致。
        if include_opposite is False:
            output_condition={
                'forward_res': result_var,
                'opposite_res': None,
            }
        else:
            output_condition={
                'forward_res': result_var,
                'opposite_res': f"{result_var}_opposite",
            }
        output = []
        for key,value in output_condition.items():
            if value is not None:
                if model_upper == "FMB":
                    output.append(f"{key} <- {value}")
                else:
                    parsed_marco = macro(
                        parsed_name = key,
                        regression_results_name = value,
                        model = self.task_obj.model
                    )
                    output.append(parsed_marco)
        if output_condition['opposite_res'] is None:
            output.append('''opposite_res <- NULL''')
        stepwise_var = self._stepwise_result_var
        if stepwise_var:
            output.append(f"stepwise_res <- {stepwise_var}")
        else:
            output.append("stepwise_res <- NULL")
        output.append('''python_output <- list(\n  "forward_res" = forward_res,\n  "opposite_res" = opposite_res,\n  "stepwise_results" = stepwise_res\n) ''')
        return '\n'.join(output)
    # ---------------------------
    # 主回归任务
    # ---------------------------
    def render_main_task(self, include_opposite=False, is_root: bool = True) -> Dict[str, Any]:
        """根据模型类型（OLS/FE/RE）渲染主回归代码。"""
        tj = self._infer_plm_model_effect(self.task_obj.to_json())
        model = getattr(self.task_obj, 'model', None)
        name = getattr(self.task_obj, 'name', None)
        model_upper = (model or "").upper()
        panel_ids = self._get_panel_ids()
        entity_var = panel_ids.get("entity")
        time_var = panel_ids.get("time")

        # 动态获取变量名
        ctx = self.get_context(is_root=is_root)
        regression_data_variable = ctx["regression_data_variable"]

        if model_upper == "FMB":
            if not entity_var or not time_var:
                raise ValueError("FMB model requires panel_ids['entity'/'time'] to be specified.")
            skip = {str(entity_var), str(time_var)}

            def _strip_terms(values: Optional[List[str]]) -> List[str]:
                if not values:
                    return []
                return [v for v in values if v and str(v) not in skip]

            tj["category_controls"] = _strip_terms(tj.get("category_controls"))
            tj["controls"] = _strip_terms(tj.get("controls"))
            tj["X"] = _strip_terms(tj.get("X"))
            subset_info = self._build_subset_strings(tj)
            fmb_opts = self._resolve_fmb_options()
            macro = self._get_macro("fmb_regression")
            code_block = str(
                macro(
                    regression_model=tj,
                    regression_data_variable=regression_data_variable,
                    output_data_variable=f"{name.lower()}_result",
                    include_opposite=include_opposite,
                    entity_col=entity_var,
                    time_col=time_var,
                    fmb_options=fmb_opts,
                    subset_condition=subset_info.get("subset"),
                    subset_condition_neg=subset_info.get("subset_neg"),
                )
            )
        else:
            if model in PublicConfig.MODEL_MAPPING:
                macro = self._get_macro(PublicConfig.MODEL_MAPPING[model])
            else:
                macro = self._get_macro(model)
            code_block = str(
                macro(
                    regression_model=tj,
                    regression_data_variable=regression_data_variable,
                    output_data_variable=f"{name.lower()}_result",
                    include_opposite=include_opposite
                )
            )
        reqs = PublicConfig.LIBRARY_REQUIREMENTS.get(self.language.lower(), {}).get(model, [])
        return {"required_libraries": reqs, "code_block": code_block}

    def render_stepwise_regressions(self, is_root: bool = True) -> Dict[str, Any]:
        task = self.task_obj
        self._stepwise_result_var = None
        if not getattr(task, "incremental_controls", False):
            return {"required_libraries": [], "code_block": ""}

        controls = list(getattr(task, "controls", []) or [])
        if not controls:
            return {"required_libraries": [], "code_block": ""}

        ctx = self.get_context(is_root=is_root)
        data_var = ctx.get("regression_data_variable")
        if not data_var:
            return {"required_libraries": [], "code_block": ""}

        task_ctx = task.generate_task_context()
        reg_model = task_ctx.get("regression_model", {})
        y_var = reg_model.get("dependent_var") or getattr(task, "y", "y")
        x_vars = reg_model.get("independent_vars") or list(getattr(task, "X_flat", []) or [])
        fe_controls = reg_model.get("fixed_effects") or list(getattr(task, "category_controls", []) or [])
        subset_info = self._build_subset_strings(self.task_obj.to_json())
        panel_ids = self._get_panel_ids()
        panel_time = panel_ids.get("time")
        panel_entity = panel_ids.get("entity")
        if panel_time or panel_entity:
            skip_values = {str(v) for v in (panel_time, panel_entity) if v}
            fe_controls = [c for c in fe_controls if c and str(c) not in skip_values]

        steps: List[Dict[str, Any]] = []
        steps.append({"index": 0, "label": "No Controls", "controls": [], "controls_expr": ""})
        cumulative: List[str] = []
        for idx, ctrl in enumerate(controls, start=1):
            cumulative.append(ctrl)
            label = "+" + "+".join(cumulative)
            if len(cumulative) == len(controls):
                label = f"{label} (Full)"
            controls_expr = ", ".join(f'"{c}"' for c in cumulative)
            steps.append({"index": idx, "label": label, "controls": list(cumulative), "controls_expr": controls_expr})

        base_terms = list(x_vars)
        fe_terms: List[str] = []

        model_type = (getattr(task, "model", "OLS") or "OLS").upper()
        engine_fun = "stats::lm" if model_type == "OLS" else "plm"
        extract_fn = "extract_coefficients_OLS" if model_type == "OLS" else "extract_coefficients_plm"
        model_arg = None
        effect_arg = None
        fmb_options: Dict[str, str] = {}
        if model_type != "OLS":
            inferred = self._infer_plm_model_effect(task.to_json())
            if model_type == "RE":
                model_arg = "random"
            else:
                model_arg = inferred.get("model") or "within"
                effect_arg = inferred.get("effect") if model_arg == "within" else None
            if model_arg is None:
                model_arg = "within"

        engine_name = "lm" if model_type == "OLS" else "plm"
        if model_type == "OLS":
            fe_terms = [f"factor({fe})" for fe in fe_controls if fe]
        elif model_type == "FMB":
            if not panel_entity or not panel_time:
                raise ValueError("Stepwise FMB regressions require panel_ids['entity'/'time'] to be specified.")
            engine_fun = "fmb_two_pass"
            extract_fn = "identity"
            engine_name = "fmb"
            fe_controls = [c for c in fe_controls if c]
            fe_terms = list(fe_controls)
            fmb_options = self._resolve_fmb_options()
        else:
            fe_terms = [f"factor({fe})" for fe in fe_controls if fe]

        all_vars = self._collect_stepwise_variables(y_var, base_terms, controls, fe_controls)
        if panel_entity and panel_entity not in all_vars:
            all_vars.append(panel_entity)
        if panel_time and panel_time not in all_vars:
            all_vars.append(panel_time)
        safe_id = self._sanitize_identifier(getattr(task, "task_id", None) or getattr(task, "name", None), "stepwise")
        self._stepwise_result_var = f"stepwise_results_{safe_id}"

        macro_cfg = {
            "safe_id": safe_id,
            "data_var": data_var,
            "y_var": y_var,
            "base_terms": base_terms,
            "fe_terms": fe_terms,
            "steps": steps,
            "engine_fun": engine_fun,
            "engine": engine_name,
            "model": model_arg,
            "effect": effect_arg,
            "extract_fn": extract_fn,
            "all_vars": all_vars,
            "panel_entity": panel_entity or "",
            "panel_time": panel_time or "",
            "fmb_options": fmb_options,
            "subset_condition": subset_info.get("subset"),
        }

        macro = self._get_macro("stepwise_regression")
        code_block = str(macro(config=macro_cfg))
        reqs = []
        reqs.extend(PublicConfig.LIBRARY_REQUIREMENTS.get(self.language.lower(), {}).get(model_type, []) or [])
        return {"required_libraries": self._dedup_preserve_order(reqs), "code_block": code_block}

    # ---------------------------
    # 组装完整代码
    # ---------------------------
    def assembly(self, internal_output: bool = True, include_opposite=True, is_root: bool = True) -> str:
        """
        # 入参：
        - internal_output: 控制是否是外部输出模式，如果为True则默认添加R-Python必备代码。
        - include_opposite: 控制是否输出条件取反的回归结果，默认为True。
        - is_root: 是否为根节点（Baseline 任务）。子任务跳过准备阶段。
        # 代码组装：
        1) 调用 _render_preparation() 与 _render_execution() 获取标准化产物
        2) 以换行拼接 prepare_code 与 execute_code，空段跳过
        3) 调用render_post_regression渲染内部输出代码。并判断是否要附加到execution产物后面
        4) 合并依赖并用 _render_dependencies() 渲染安装段，置于脚本最前
        5) 计算 prep_args 的稳定指纹并缓存
        6) 将最终代码写入 self.code_text 并返回

        # 标准化输出字段
        - prepare_code: 数据准备段（清洗/设面板/Winsorize/因子化）
        - execute_code: 分析执行段（主回归）
        - dependencies: 依赖安装段
        - combined:     完整串联（安装段 + 准备段 + 执行段）
        - prep_args:    准备阶段的规范化入参快照
        - prep_fingerprint: 上述 prep_args 的指纹（sha256 hex）
        """

        needs_prep = self._needs_data_preparation(is_root=is_root)
        prep_res = self._render_preparation(should_prepare=needs_prep, is_root=is_root)
        exec_res = self._render_execution(include_opposite, is_root=is_root)

        # 2) 提取与拼接主体（准备段 → 执行段）
        prep_block = (prep_res or {}).get("code_block", {}) or {}
        prepare_code = prep_block.get("prepare_code", "") or ""
        execute_code = (exec_res or {}).get("code_block", "") or ""

        if needs_prep:
            body_parts = [prepare_code, execute_code]
        else:
            body_parts = [execute_code]
        body = "\n".join([p for p in body_parts if p and p.strip()])

        # 3) 调用render_post_regression渲染内部输出代码
        if 'opposite' in execute_code:
            include_opposite = True
        else:
            include_opposite = False
        post_regression_code = self.render_post_regression(include_opposite)

        # 4) 依赖合并并渲染安装段
        reqs = []
        reqs.extend((prep_res or {}).get("required_libraries", []) or [])
        reqs.extend((exec_res or {}).get("required_libraries", []) or [])
        dedup_reqs = self.get_required_package(reqs)
        full_parts = [body]
        if internal_output:
            full_parts = [body,post_regression_code]
        combined = "\n".join([p for p in full_parts if p and p.strip()])
        if combined:
            combined += "\n"

        # 5) 计算 prep_args 指纹（稳定/可复现）
        prep_args = (prep_res or {}).get("prep_args", {}) or {}

        try:
            canonical = self._normalize_for_fingerprint(prep_args)
            canonical.pop('subset')
            canonical = json.dumps(
                canonical,
                ensure_ascii=False,
                sort_keys=True,           # 仅 dict 键排序；列表顺序保持
                separators=(",", ":"),
            )
            # print(f"生成指纹参数对象：{canonical}\n\n")
            prep_fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except Exception:
            # Fallback: never block the main flow if fingerprinting fails
            prep_fingerprint = 'exception'

        # 6) 落盘与返回（同时缓存段落，便于调试/复用）
        self.prep_fingerprint = prep_fingerprint
        output = {
            "prepare_code": prepare_code,
            "execute_code": execute_code,
            "post_regression_code":post_regression_code,
            "dependencies": dedup_reqs,
            "combined": combined,
            "prep_args": prep_args,
            "prep_fingerprint": prep_fingerprint,
        }
        self._last_segments = output
        return output

    def _needs_data_preparation(self, is_root: bool) -> bool:
        ctx = getattr(self.task_obj, "_data_context", None)
        if isinstance(ctx, dict):
            return bool(ctx.get("output_data_variable"))
        if getattr(self.task_obj, "if_reprep", False):
            return True
        return bool(is_root)

    def _render_preparation(self, should_prepare: bool, is_root: bool = True) -> Dict[str, Any]:
        """
        产出"数据准备层"代码 + 依赖 + 规范化的 prep_args。
        返回结构：
        {
        "code_block": {
            "dependencies": "<install_required_packages 段>",
            "prepare_code": "<数据准备段>",
            "execute_code": "",
            "combined": "<安装段 + 准备段，仅供调试>"
        },
        "required_libraries": List[str],
        "prep_args": Dict[str, Any],
        }

        参数:
            should_prepare: 是否需要生成准备代码。
            is_root: 回退判断使用的根节点标记。
        """
        if not should_prepare:
            return {
                "code_block": {"dependencies": "", "prepare_code": "", "execute_code": "", "combined": ""},
                "required_libraries": [],
                "prep_args": {},
            }

        # 1) 三段准备渲染（清洗 / 面板 / Winsorize）+ 交互项
        clean = self.render_clean_sample(is_root=is_root)
        panel = self.render_set_panel(is_root=is_root)
        winz  = self.render_winsorize(is_root=is_root)
        interaction = self.render_interaction_terms(is_root=is_root)
        prepare_pieces = [clean.get("code_block", ""), panel.get("code_block", ""), winz.get("code_block", ""), interaction.get("code_block", "")]
        prepare_code = "\n".join([p for p in prepare_pieces if p and p.strip()])

        # 2) 依赖合并（准备层）
        reqs = []
        reqs.extend(clean.get("required_libraries", []) or [])
        reqs.extend(panel.get("required_libraries", []) or [])
        reqs.extend(winz.get("required_libraries", []) or [])
        reqs.extend(interaction.get("required_libraries", []) or [])
        dedup_reqs = self.get_required_package(reqs)

        # 3) 安装段（准备层单独的，方便调试）
        install_block = self._render_dependencies(dedup_reqs)  # 可能为空
        prep_combined = "\n".join([p for p in [install_block, prepare_code] if p and p.strip()])
        if prep_combined:
            prep_combined += "\n"

        # 4) 构造规范化 prep_args
        tj = self.task_obj.to_json()
        dataset = str(tj.get("dataset") or "").strip()

        # 尽可能稳定地收集字段闭包
        if hasattr(self.task_obj, "get_all_variables"):
            required_columns = list(self.task_obj.get_all_variables() or [])
        else:
            required_columns = list(
                filter(
                    None,
                    [tj.get("y")]
                    + (tj.get("X") or [])
                    + (tj.get("controls") or [])
                    + (tj.get("category_controls") or [])
                    + list((tj.get("panel_ids") or {}).values())
                    + [((tj.get("subset") or {}).get("classification_field"))],
                ),
            )
            # 若右侧是 field(col)，也加入 col
            cc = (tj.get("subset") or {}).get("classification_conditions")
            if isinstance(cc, str) and cc.startswith("field(") and cc.endswith(")"):
                col = cc[6:-1].strip()
                if col:
                    required_columns.append(col)
        # 去重保序
        seen = set(); req_cols_norm = []
        for c in required_columns:
            c2 = str(c).strip()
            if c2 and c2 not in seen:
                req_cols_norm.append(c2); seen.add(c2)

        # panel_ids 规范化
        pid = tj.get("panel_ids") or {}
        panel_ids_norm = {
            "entity": (pid.get("entity") or None),
            "time":   (pid.get("time") or None),
        }

        # winsorize 规范化
        opts = tj.get("options") or {}
        wins_norm = {
            "percent": (opts.get("winsorize_percent") if opts.get("winsorize_fields") else None),
            "fields":  list(opts.get("winsorize_fields") or []),
        }

        # 分类因子字段
        cat_norm = list(tj.get("category_controls") or [])

        # 子集条件规范化（轻量实现：数值/field/expr/str 四类）
        def _norm_cond(v: Any) -> Any:
            if isinstance(v, bool):
                return {"type": "expr", "value": "TRUE" if v else "FALSE"}
            if isinstance(v, (int, float)):
                return {"type": "number", "value": v}
            if v is None:
                return None
            s = str(v).strip()
            if s.startswith("field(") and s.endswith(")"):
                return {"type": "field", "value": s[6:-1].strip()}
            if s.startswith("expr(") and s.endswith(")"):
                return {"type": "expr", "value": s[5:-1].strip()}
            if s.startswith("str(") and s.endswith(")"):
                return {"type": "string", "value": s[4:-1]}
            # 纯数字字符串也作为 number
            import re
            if re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
                return {"type": "number", "value": float(s)}
            # 其它情况按原字面串保存
            return {"type": "string", "value": s}

        subset = tj.get("subset") or {}
        def _read_subset_anywhere(tj: dict) -> dict:
            """
            向后兼容读取子集三件套：
            1) 优先 tj['subset']
            2) 次选 tj 顶层扁平字段
            3) 次选 tj['regression_model'] 内的同名字段
            返回统一 dict（缺失项为 None）
            """
            s = dict(tj.get("subset") or {})

            # 顶层扁平字段兜底
            for key in ("classification_field", "operator", "classification_conditions"):
                if s.get(key) is None and tj.get(key) is not None:
                    s[key] = tj.get(key)

            # regression_model 兜底（有些实现把三件套塞到这里）
            rm = tj.get("regression_model") or {}
            for key in ("classification_field", "operator", "classification_conditions"):
                if s.get(key) is None and rm.get(key) is not None:
                    s[key] = rm.get(key)

            # 统一空值
            return {
                "classification_field": s.get("classification_field") or None,
                "operator": s.get("operator") or None,
                "classification_conditions": s.get("classification_conditions"),
            }
        any_subset = _read_subset_anywhere(tj)
        subset_norm = {
            "classification_field": any_subset["classification_field"],
            "operator": any_subset["operator"],
            "classification_conditions": _norm_cond(any_subset["classification_conditions"]),
        }

        # 执行层对准备的要求（FE/RE 需要 panel）
        model = str(tj.get("model") or "").upper()
        model_prep_needs = {"needs_panel": (model in {"FE", "RE"})}

        generator_ver = getattr(self, "GENERATOR_VERSION", "1.0")
        interaction_map = getattr(self.task_obj, "interaction_map", {})
        prep_args = {
            "dataset": dataset,
            "required_columns": req_cols_norm,
            "panel_ids": panel_ids_norm,
            "category_controls": cat_norm,
            "winsorize": wins_norm,
            "subset": subset_norm,
            "model_prep_needs": model_prep_needs,
            "interaction_map": {k: list(v) for k, v in interaction_map.items()},
            "version": {"template": self._template_path.name, "generator": f"CodeGenerator/{generator_ver}"},
        }

        return {
            "code_block": {
                "dependencies": install_block,
                "prepare_code": prepare_code,
                "execute_code": "",
                "combined": prep_combined,
            },
            "required_libraries": dedup_reqs,
            "prep_args": prep_args,
        }


    def _render_execution(self, include_opposite, is_root: bool = True) -> Dict[str, Any]:
        """
        产出执行层（主回归）代码与依赖。
        返回：
        {"code_block": <str>, "required_libraries": <List[str]>}
        """
        main = self.render_main_task(include_opposite, is_root=is_root)
        stepwise = self.render_stepwise_regressions(is_root=is_root)

        blocks = [main.get("code_block", ""), stepwise.get("code_block", "")]
        combined_code = "\n\n".join([b.strip() for b in blocks if b and b.strip()])

        reqs = []
        reqs.extend(main.get("required_libraries", []) or [])
        reqs.extend(stepwise.get("required_libraries", []) or [])

        return {
            "code_block": combined_code,
            "required_libraries": self._dedup_preserve_order(reqs),
        }


    def _render_dependencies(self, required_libs: List[str]) -> str:
        """
        将依赖包列表渲染为安装/加载段（可为空）。
        """
        dedup = self.get_required_package(required_libs or [])
        if not dedup:
            return ""
        return self.render_required_libraries(dedup)

__all__ = ["CodeGenerator"]
