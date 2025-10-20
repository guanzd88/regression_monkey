# -*- coding: utf-8 -*-
"""
CodeExecutor: R 任务树执行编排器（rpy2 新版 API 适配）
- 不再使用 pandas2ri.activate()/deactivate()（已弃用）
- 统一通过 conversion.localconverter 上下文做 py<->R 转换
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from pathlib import Path
import os
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

import pandas as pd

# 外部提供（保持与设计文档一致）
from reg_monkey.util import ConfigLoader,PublicConfig
from reg_monkey.code_generator import CodeGenerator


# =========================
# 异常
# =========================

class DependencyError(RuntimeError):
    pass


class DataPrepMismatchError(RuntimeError):
    pass


class ExecutionRuntimeError(RuntimeError):
    pass


class ResultNotFoundError(RuntimeError):
    pass

class TemplateInstallSnippetMissing(DependencyError):
    pass

# =========================
# 接口：ILanguageExecutor
# =========================
class ILanguageExecutor(ABC):
    @abstractmethod
    def new_env(self) -> Any: ...
    @abstractmethod
    def inject_df(self, env: Any, df: pd.DataFrame, as_name: str) -> None: ...
    @abstractmethod
    def exec(self, code: str, env: Any) -> None: ...
    @abstractmethod
    def get(self, env: Any, name: str) -> Any: ...
    @abstractmethod
    def remove(self, env: Any, name: str) -> None: ...
    @abstractmethod
    def to_python(self, obj: Any) -> Any: ...
    @abstractmethod
    def close(self) -> None: ...


# =========================
# 语言适配层：RExecutor（已升级 rpy2 转换 API）
# =========================
class RExecutor(ILanguageExecutor):
    """
    rpy2 3.5+ / 3.6+ 兼容：
    - 使用 `robjects.conversion.localconverter` 与 `pandas2ri.converter`
    - 不再使用 pandas2ri.activate()/deactivate()（已弃用）
    """

    def __init__(self) -> None:
        try:
            import rpy2.robjects as ro  # type: ignore
            from rpy2.robjects import pandas2ri  # type: ignore
            from rpy2.robjects import conversion  # type: ignore
        except Exception as e:
            raise ImportError(
                "RExecutor 需要 rpy2：请先 `pip install rpy2` 并确认系统可用 R。"
            ) from e

        self.ro = ro
        self.r = ro.r
        self.conversion = conversion
        self.pandas2ri = pandas2ri

        # === R helpers（使用默认环境 .GlobalEnv）===
        # 如需“局部环境但可访问全局”，可改为：
        # self._new_env = self.r("function() new.env(parent=.GlobalEnv)")
        self._new_env = self.r("function() .GlobalEnv")

        self._assign_in_env = self.r(
            "function(env, name, value) assign(name, value, envir = env)"
        )
        self._get_in_env = self.r(
            "function(env, name) { "
            " if (exists(name, envir=env, inherits=FALSE)) get(name, envir=env, inherits=FALSE) "
            " else NULL }"
        )
        self._rm_in_env = self.r(
            "function(env, name) if (exists(name, envir=env, inherits=FALSE)) rm(list = name, envir = env)"
        )
        self._eval_in_env = self.r(
            "function(env, code) { eval(parse(text = code), envir = env); invisible(NULL) }"
        )

    # ---------- 转换工具（新版 API） ----------
    def _py2r(self, obj: Any):
        with self.conversion.localconverter(self.ro.default_converter + self.pandas2ri.converter):
            return self.ro.conversion.py2rpy(obj)

    def _r2py(self, obj: Any):
        with self.conversion.localconverter(self.ro.default_converter + self.pandas2ri.converter):
            return self.ro.conversion.rpy2py(obj)

    # ---------- ILanguageExecutor 实现 ----------
    def new_env(self) -> Any:
        return self._new_env()

    def inject_df(self, env: Any, df: pd.DataFrame, as_name: str = "input_df") -> None:
        r_df = self._py2r(df)
        self._assign_in_env(env, as_name, r_df)

    def exec(self, code: str, env: Any) -> None:
        try:
            self._eval_in_env(env, code)
        except Exception as e:
            raise ExecutionRuntimeError(str(e)) from e

    def get(self, env: Any, name: str) -> Any:
        try:
            return self._get_in_env(env, name)
        except Exception:
            return None

    def remove(self, env: Any, name: str) -> None:
        try:
            self._rm_in_env(env, name)
        except Exception:
            pass

    def to_python(self, obj: Any) -> Any:
        try:
            return self._r2py(obj)
        except Exception:
            return obj

    def close(self) -> None:
        return


# =========================
# 执行编排层：CodeExecutor
# =========================
@dataclass
class CodeExecutor:
    """
    代码执行编排器（CodeExecutor）

    概述
    ----
    将一片由 `Plan` 产出的“任务森林”（每棵树是一组相关的 `StandardRegTask`）批量
    渲染为 **R** 代码并在同一 R 运行环境中顺序执行，自动安装依赖、注入数据、收集回传
    结果，并把结果写回到各个任务的 `exec_result` 字段。当前仅支持语言 `"r"`，通过
    `RExecutor` 适配 rpy2 的 **新版转换 API**（`conversion.localconverter`），避免使用
    已弃用的 `pandas2ri.activate/deactivate`。

    预期输入（来自 Plan）
    ---------------------
    - `plan.output_r_code_by_tree()` 必须返回一个可迭代对象，其中每个元素是:
      `{ "nodes": [task0, task1, ...], "deps": ["pkgA","pkgB", ...] }`
      * `nodes[0]` 视为 baseline 任务，其 `code_text` 中通常包含 `prepare_code` 与
        `prep_fingerprint`（准备阶段参数指纹）。
      * `deps` 为该树所有节点聚合去重后的 R 包依赖。

    工作流（run）
    ------------
    1) 为每棵树创建一个独立 R 环境，并将 `df` 注入为 `input_df`；
    2) 基于模板宏 `install_required_packages` 渲染并执行依赖安装代码（仅当 `deps` 非空）；
    3) 遍历该树的每个任务：
       - 若任务标记 `active=False`（或 `enabled=False`），跳过；
       - 先执行“准备阶段”（见“准备代码复用策略”），再执行主代码；
       - 从 R 环境读取 `python_output`，转换为 Python 对象并设置到 `task.exec_result`；
       - 记录执行日志到 `self.logs`。

    准备代码复用策略
    ----------------
    - 默认所有非 `if_reprep` 的任务**共用 baseline 的准备指纹**：
      * 当 baseline 的 `prep_fingerprint` 与 `current_prep_fingerprint` 不一致时，执行
        baseline 的 `prepare_code`，之后同步指纹并复用；
      * 标记了 `if_reprep=True` 的任务会使用**自身**的指纹与准备代码；
      * 若没有任何 `prepare_code`，不会报错，直接同步指纹以避免重复检查。

    模板与依赖安装
    --------------
    - 在 `__post_init__` 中根据 `PublicConfig.TEMPLATE_MAP` 与 `ConfigLoader.template_root`
      定位模板文件，并初始化 Jinja 环境；
    - `_install_dependencies` 会从树级 `deps` 生成安装/加载代码，并调用后端执行；
    - 若无法从第一条任务定位模板或找不到宏 `install_required_packages`，抛出
      `TemplateInstallSnippetMissing`。

    语言后端（RExecutor）
    --------------------
    - 使用 rpy2 的 `conversion.localconverter` 与 `pandas2ri.converter` 完成
      `DataFrame` 的 `py <-> R` 转换；
    - 在 `.GlobalEnv` 执行代码，提供 `inject_df / exec / get / remove / to_python` 等抽象；
    - 执行阶段异常包装为 `ExecutionRuntimeError`。

    关键属性
    --------
    - plan: Any
        提供 `output_r_code_by_tree()` 的对象（通常是 `Plan`）。
    - df: pd.DataFrame
        要注入 R 端的输入数据（变量名固定为 `input_df`）。
    - language: Optional[str]
        目标语言（默认从 `ConfigLoader().language` 读取；当前仅支持 "r"）。
    - backend: ILanguageExecutor
        语言后端实例（目前为 `RExecutor`）。
    - current_prep_fingerprint: Optional[str]
        最近一次在该 R 环境中成功执行的准备阶段指纹，用于控制是否重复执行准备代码。
    - logs: Dict[str, Any]
        执行日志，按树分组：`{"tree[i]": [{"where": "...", "level": "ok|skip|error", "msg": "..."}]}`。
    - template_root / _template_path / _jinja_env
        模板目录、选中模板路径、Jinja2 环境。

    常用方法
    --------
    - run() -> None
        逐树逐任务执行，错误会记录日志；依赖安装失败会抛 `DependencyError` 并中止该树。
    - _render_install_from_template_file(first_task, deps) -> str
        通过模板宏渲染依赖安装代码。
    - _prepare_data(task, env, baseline_task, baseline_fp) -> None
        按指纹策略决定是否执行 `prepare_code`。
    - _unpack_code_text(ct) -> Dict[str, Any]
        兼容 `task.code_text` 为 `str`（视为仅执行段）或 `dict`（含分段与依赖）。

    可能抛出的异常
    --------------
    - DependencyError / TemplateInstallSnippetMissing
        依赖安装阶段失败或无法生成安装段。
    - ExecutionRuntimeError
        R 代码执行报错（由后端捕获并包装）。
    - ResultNotFoundError
        主代码运行后未在 R 环境中找到 `python_output`。
    - NotImplementedError
        传入了未支持的 `language`。
    - FileNotFoundError
        无法找到模板文件。

    最简示例
    -------
    >>> with CodeExecutor(plan=my_plan, df=my_df) as ex:
    ...     ex.run()
    ...     # 执行完成后，可检查 ex.logs 或逐任务访问 task.exec_result
    """
    plan: Any
    df: pd.DataFrame
    language: Optional[str] = ConfigLoader().language
    backend: ILanguageExecutor = field(init=False)
    current_prep_fingerprint: Optional[str] = field(default=None, init=False)
    logs: Dict[str, Any] = field(default_factory=dict, init=False)
    cfg: Any = field(default=None, init=False)
    _jinja_env: Environment = field(init=False, repr=False)
    def __post_init__(self) -> None:
        self.cfg = ConfigLoader()
        lang = (self.language or getattr(self.cfg, "language", "R")).strip()
        self.language = lang
        self.config = ConfigLoader()
        # 1) 初始化语言后端（保持原样）
        if lang.lower() == "r":
            self.backend = RExecutor()
        else:
            raise NotImplementedError(f"暂不支持语言：{lang}")

        # 2) 初始化 Jinja2 环境（模板目录 = 本文件同目录）
        # 确定模板路径
        default_root = Path(__file__).parent
        template_root = getattr(self.config, "template_root", None)
        self.template_root: str = str(Path(template_root) if template_root else default_root)
        template_filename = PublicConfig.TEMPLATE_MAP[self.language.lower()]
        self._template_path = Path(self.template_root) / template_filename

        if not self._template_path.exists():
            raise FileNotFoundError(
                f"模板文件不存在: {self._template_path}. 可通过 config.template_root 指定模板目录。"
            )
        # here = os.path.dirname(os.path.abspath(__file__))
        self._jinja_env = Environment(
            loader=FileSystemLoader(self.template_root),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,  # 缺变量直接报错
        )

    def __enter__(self) -> "CodeExecutor":
        return self
    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.backend.close()
        finally:
            return False
        
    def run(self) -> None:
        trees: Iterable[List[Any]] = self.plan.output_r_code_by_tree()  # type: ignore
        for tree_idx, tree_obj in enumerate(trees):
            tree = tree_obj['nodes']
            if not tree:
                continue
            env = self.backend.new_env()
            self.backend.inject_df(env, self.df, "input_df")
            self.backend.exec('base::require("utils", quietly=TRUE, character.only=TRUE)', env)
            self.current_prep_fingerprint = None
            try:
                self._install_dependencies(tree_obj, env)
            except Exception as e:
                self._log(tree_idx, "_install_dependencies", "error", str(e))
                raise
            baseline_task = tree[0]
            baseline_fp = getattr(baseline_task, "prep_fingerprint", None)
            for task_idx, task in enumerate(tree):
                # --- ADDED: active/enabled 开关检查（False 则直接跳过，不做任何操作） ---
                try:
                    active_flag = getattr(task, "active", getattr(task, "enabled", True))
                    if not bool(active_flag):
                        self._log(tree_idx, f"task[{task_idx}]", "skip", "inactive (active=False)")
                        continue
                except Exception:
                    # 出现异常也不阻断执行，按 active=True 继续
                    pass
                # --------------------------------------------------------------------
                try:
                    self._execute_task(task, env, baseline_task, baseline_fp)
                    self._log(tree_idx, f"task[{task_idx}]", "ok", "done")
                except Exception as e:
                    self._log(tree_idx, f"task[{task_idx}]", "error", str(e))
                    # 若想遇错中断，请改成 raise
                    continue

    def _get_macro(self, macro_name: str):
        """从模板中获取指定宏，不存在时抛出异常。"""
        try:
            tmpl = self._jinja_env.get_template(self._template_path.name)
        except TemplateNotFound as e:
            raise FileNotFoundError(f"找不到模板: {self._template_path}") from e

        module = tmpl.make_module(vars={})
        if not hasattr(module, macro_name):
            raise AttributeError(
                f"模板 '{self._template_path.name}' 中缺少宏 '{macro_name}'。"
            )
        return getattr(module, macro_name)
    
    # --- 覆盖安装依赖逻辑（树级聚合 → 用第一条任务模板渲染 → 执行） ---
    def _install_dependencies(self, tree: List[Any], env: Any) -> None:
        deps: List[str] = []
        # print(f'tree is {tree}')
        first_task = tree['nodes'][0]
        deps = tree['deps']
        if len(deps) > 0:
            install_code = self._render_install_from_template_file(first_task, deps)
            try:
                self.backend.exec(install_code, env)
            except ExecutionRuntimeError as e:
                raise DependencyError(f"依赖安装失败：{e}") from e

    # --- 新增：从第一条任务声明的模板文件渲染依赖安装代码 ---
    def _render_install_from_template_file(self, first_task: Any, deps: List[str]) -> str:

        lang_key = (self.language or "r").lower()
        cg = getattr(first_task, "cg", None)
        if cg is None:
            raise TemplateInstallSnippetMissing("first_task 没有 cg，无法定位模板。")

        tmpl_map = getattr(PublicConfig, "TEMPLATE_MAP", {}) or {}
        tmpl_name = tmpl_map.get(lang_key)
        if not isinstance(tmpl_name, str) or not tmpl_name.strip():
            raise TemplateInstallSnippetMissing(
                f"模板缺失：first_task.cg.TEMPLATE_MAP['{lang_key}'] 未找到。"
            )

        # 确认模板存在（模板目录=code_executor.py 同目录；Jinja env 在 __post_init__ 中初始化）
        try:
            _ = self._jinja_env.get_template(tmpl_name)
        except Exception as e:
            here = os.path.dirname(os.path.abspath(__file__))
            raise TemplateInstallSnippetMissing(
                f"未在 {here} 找到模板文件 '{tmpl_name}'。"
            ) from e

        # 渲染上下文：同时提供多种别名，便于不同模板复用
        pkgs_vec = ", ".join([f"'{p}'" for p in deps])
        ctx = {
            "required_libraries": deps,  # 你的模板用的名字
        }
        # 1) 优先尝试用“桥接模板”以各种方式调用宏（关键字/位置参数）
        macro = self._get_macro("install_required_packages")
        code = str(macro(required_libraries=deps))
        return code

    def _execute_task(self, task: Any, env: Any, baseline_task: Any, baseline_fp: Optional[str]) -> None:
        # 先保证准备阶段（会根据 fingerprint 决定是否执行）
        self._prepare_data(task, env, baseline_task, baseline_fp)

        # 统一解包 code_text（兼容 str / dict）
        ct_raw = getattr(task, "code_text", None)
        if ct_raw is not None:
            ct = self._unpack_code_text(ct_raw)

            code_text = ct.get("combined") or ct.get("execute_code")
            if not code_text or not isinstance(code_text, str):
                raise ExecutionRuntimeError("任务缺少可执行代码：未提供 'combined' 或 'execute_code'。")

            self.backend.exec(code_text, env)

            r_obj = self.backend.get(env, "python_output")
            if r_obj is None:
                raise ResultNotFoundError("R 环境未找到 `python_output`。")

            py_obj = self.backend.to_python(r_obj)
            setattr(task, "exec_result", py_obj)
            self.backend.remove(env, "python_output")

    def _prepare_data(self, task: Any, env: Any, baseline_task: Any, baseline_fp: Optional[str]) -> None:
        task_fp = getattr(task, "prep_fingerprint", None)
        if_reprep = bool(getattr(task, "if_reprep", False))
        # 决定“准备阶段”的绑定对象与目标指纹
        if if_reprep:
            target_task = task
            target_fp = task_fp
        else:
            target_task = baseline_task
            target_fp = baseline_fp
        # 只有当目标指纹变化时，才考虑执行准备代码
        if self.current_prep_fingerprint != target_fp:
            ct_raw = getattr(target_task, "code_text", None)
            ct = self._unpack_code_text(ct_raw)
            prep_code = ct.get("prepare_code", "") or ""

            if prep_code.strip():
                self.backend.exec(prep_code, env)
                # 准备代码成功后，同步指纹
                self.current_prep_fingerprint = target_fp
            else:
                # 无准备代码：不再报错，直接跳过，并同步指纹，避免后续重复检查
                self.current_prep_fingerprint = target_fp

    def _log(self, tree_idx: int, key: str, level: str, msg: str) -> None:
        k = f"tree[{tree_idx}]"
        self.logs.setdefault(k, []).append({"where": key, "level": level, "msg": msg})

    # --- 兼容工具：把 code_text（str 或 dict）统一“解包”为 dict ---
    def _unpack_code_text(self, ct: Any) -> Dict[str, Any]:
        """
        允许 task.code_text 为:
        - str: 视为仅有执行代码；prepare_code 为空；dependencies 为空
        - dict: 原样返回（空用 {} 兜底）
        """
        if isinstance(ct, str):
            s = ct
            return {
                "dependencies": [],
                "prepare_code": "",
                "execute_code": s,
                "combined": s,
            }
        return ct or {}
__all__ = ['CodeExecutor']