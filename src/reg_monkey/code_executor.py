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
import re
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound
from datetime import datetime, timezone

import pandas as pd

# 外部提供（保持与设计文档一致）
from reg_monkey.util import ConfigLoader, PublicConfig
from reg_monkey.code_generator import CodeGenerator
from reg_monkey.plan_config import PlanConfig
from reg_monkey.planner import Plan

# ✅ 新增：用于变量名重命名
from reg_monkey.util import name_bracket_bidir


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
    def exists(self, env: Any, name: str) -> bool: ...
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
                "RExecutor requires rpy2. Please `pip install rpy2` and ensure a working R runtime is available."
            ) from e

        self.ro = ro
        self.r = ro.r
        self.conversion = conversion
        self.pandas2ri = pandas2ri

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

    def _py2r(self, obj: Any):
        with self.conversion.localconverter(self.ro.default_converter + self.pandas2ri.converter):
            return self.ro.conversion.py2rpy(obj)

    def _r2py(self, obj: Any):
        with self.conversion.localconverter(self.ro.default_converter + self.pandas2ri.converter):
            return self.ro.conversion.rpy2py(obj)

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

    def exists(self, env: Any, name: str) -> bool:
        """检查 R 环境中是否存在指定变量。"""
        try:
            check_code = f'exists("{name}", envir = .GlobalEnv)'
            result = self.r(check_code)
            return bool(result[0])
        except Exception:
            return False

    def close(self) -> None:
        return


# =========================
# 执行编排层：CodeExecutor
# =========================
@dataclass
class CodeExecutor:
    plan: Any
    df: pd.DataFrame = None  # 改为可选，兼容旧用法
    datasets: Dict[str, pd.DataFrame] = field(default_factory=dict)  # 新增：多数据集支持
    language: Optional[str] = ConfigLoader().language
    backend: ILanguageExecutor = field(init=False)
    current_prep_fingerprint: Optional[str] = field(default=None, init=False)
    logs: Dict[str, Any] = field(default_factory=dict, init=False)
    cfg: Any = field(default=None, init=False)
    _jinja_env: Environment = field(init=False, repr=False)
    _prepared_datasets: set = field(default_factory=set, init=False)  # 跟踪已准备的数据集

    # 仅正向识别 raw bracket 的 pattern（避免双向函数把编码名反解）
    _RAW_BRACKET_COL_PAT = re.compile(r"^[A-Za-z0-9_]+\[\s*[+-]?\d+\s*,\s*\d+\s*\]$")

    def __post_init__(self) -> None:
        self.cfg = ConfigLoader()
        lang = (self.language or getattr(self.cfg, "language", "R")).strip()
        self.language = lang
        self.config = ConfigLoader()

        # 兼容旧用法：如果传入单个 df 且 datasets 为空，作为 "main" 数据集
        if self.df is not None and not self.datasets:
            self.datasets["main"] = self.df

        if lang.lower() == "r":
            self.backend = RExecutor()
        else:
            raise NotImplementedError(f"Unsupported language: {lang}")


        default_root = Path(__file__).parent
        template_root = getattr(self.config, "template_root", None)
        self.template_root: str = str(Path(template_root) if template_root else default_root)
        template_filename = PublicConfig.TEMPLATE_MAP[self.language.lower()]
        self._template_path = Path(self.template_root) / template_filename

        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Template file not found: {self._template_path}. Set config.template_root to override the search path."
            )

        self._jinja_env = Environment(
            loader=FileSystemLoader(self.template_root),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )

    def __enter__(self) -> "CodeExecutor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.backend.close()
        finally:
            return False

    # ✅ 新增：灌入 R 前对 df 列名做 encode-only 处理
    def _sanitize_df_for_r(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        仅对 raw bracket 列名（形如 name[a,b]）做 name_bracket_bidir 正向编码。
        已经编码过的 name__a_b_ / name_a_b_ 以及普通列名，不做任何改动。
        返回一个 copy，避免污染外部 df。
        """
        if df is None or not isinstance(df, pd.DataFrame):
            return df

        cols = list(df.columns)
        rename_dict: Dict[Any, Any] = {}

        for c in cols:
            if not isinstance(c, str):
                continue
            if self._RAW_BRACKET_COL_PAT.match(c):
                # 只在 raw bracket 形态下调用双向函数，确保不会反解
                new_c = name_bracket_bidir(c)
                if new_c != c:
                    rename_dict[c] = new_c

        if not rename_dict:
            return df

        df2 = df.copy()
        df2.rename(columns=rename_dict, inplace=True)
        return df2

    # ---------------------------
    # 多数据集支持
    # ---------------------------
    def _inject_raw_datasets(self, tree: List[Any], env: Any) -> None:
        """
        根据任务树中的数据集需求，注入原始数据集到 R 环境。
        使用 df_raw_{dataset} 命名。
        """
        from typing import Set
        required_datasets: Set[str] = set()

        for task in tree:
            ds = self._get_task_dataset_key(task)
            required_datasets.add(ds)

        for ds in required_datasets:
            raw_var_name = f"df_raw_{ds}"

            # 检查是否已注入
            if self.backend.exists(env, raw_var_name):
                continue

            # 获取数据源
            df = self._get_dataset_for_key(ds)
            if df is None:
                raise ValueError(f"Dataset '{ds}' not found. Check the datasets parameter.")

            # 注入到 R 环境
            sanitized_df = self._sanitize_df_for_r(df)
            self.backend.inject_df(env, sanitized_df, raw_var_name)

    def _get_task_dataset_key(self, task: Any) -> str:
        """
        获取任务的数据集键名。
        优先使用 task.get_dataset_key()，否则回退到 dataset 字段或 "main"。
        """
        if hasattr(task, "get_dataset_key") and callable(getattr(task, "get_dataset_key")):
            return task.get_dataset_key()
        dataset = getattr(task, "dataset", None)
        if dataset:
            safe = re.sub(r'[^A-Za-z0-9_]', '_', str(dataset))
            if safe and safe[0].isdigit():
                safe = '_' + safe
            return safe or "main"
        return "main"

    def _get_dataset_for_key(self, key: str) -> Optional[pd.DataFrame]:
        """
        根据键获取对应的数据集。
        查找顺序：
        1. datasets 字典中精确匹配
        2. datasets 字典中 "main"
        3. self.df (兼容旧用法)
        """
        if key in self.datasets:
            return self.datasets[key]
        if "main" in self.datasets:
            return self.datasets["main"]
        return self.df

    def _load_latest_cache(self) -> Optional[Dict[str, Any]]:
        base_dir = Path(getattr(self.cfg, "exec_cache_dir", "./cache_exec_results")).expanduser()
        if not base_dir.exists():
            return None
        pkl_files = sorted(base_dir.glob("exec_results_*.pkl"))
        if not pkl_files:
            return None
        latest = pkl_files[-1]
        try:
            import pickle

            return pickle.loads(latest.read_bytes())
        except Exception:
            return None

    def run(self) -> None:
        plan_obj = getattr(self, "plan", None)
        if plan_obj is not None and hasattr(plan_obj, "assign_data_contexts"):
            try:
                plan_obj.assign_data_contexts()
            except Exception:
                pass

        trees: Iterable[List[Any]] = self.plan.output_r_code_by_tree()  # type: ignore
        cache_dump_path = self._prepare_exec_cache()
        collected_results: Dict[str, Any] = {}
        for tree_idx, tree_obj in enumerate(trees):
            tree = tree_obj["nodes"]
            if not tree:
                continue

            env = self.backend.new_env()
            self._prepared_datasets = set()  # 每棵树重置

            # 注入所有需要的原始数据集（使用三段式命名：df_raw_{ds}）
            self._inject_raw_datasets(tree, env)

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
                try:
                    active_flag = getattr(task, "active", getattr(task, "enabled", True))
                    if not bool(active_flag):
                        self._log(tree_idx, f"task[{task_idx}]", "skip", "inactive (active=False)")
                        continue
                except Exception:
                    pass

                try:
                    info = {
                        "parent_task_id": getattr(task, "parent_task_id", None),
                        "task_id": getattr(task, "task_id", None),
                    }
                    print(info)
                    self._execute_task(task, env, baseline_task, baseline_fp)
                    # 使用 task_id，若不存在则用自动生成的键名
                    task_id = getattr(task, "task_id", None) or f"tree{tree_idx}_task{task_idx}"
                    if cache_dump_path is not None:
                        collected_results[task_id] = getattr(task, "exec_result", None)
                    self._log(tree_idx, f"task[{task_idx}]", "ok", "done")
                except Exception as e:
                    self._log(tree_idx, f"task[{task_idx}]", "error", str(e))
                    continue

        plan_config_dict = None
        plan_obj = getattr(self, "plan", None)
        if plan_obj is not None and hasattr(plan_obj, "to_config"):
            try:
                cfg = plan_obj.to_config()
                if cfg is not None:
                    plan_config_dict = cfg.to_dict()
            except Exception:
                plan_config_dict = None

        dataset_snapshot = self._snapshot_datasets()

        if cache_dump_path is not None and collected_results:
            try:
                import pickle

                payload = {
                    "exec_results": collected_results,
                }
                if plan_config_dict:
                    payload["plan_config"] = plan_config_dict
                if dataset_snapshot:
                    payload["datasets"] = dataset_snapshot
                cache_dump_path.write_bytes(pickle.dumps(payload))
            except Exception:
                pass

    def _get_macro(self, macro_name: str):
        try:
            tmpl = self._jinja_env.get_template(self._template_path.name)
        except TemplateNotFound as e:
            raise FileNotFoundError(f"Template not found: {self._template_path}") from e

        module = tmpl.make_module(vars={})
        if not hasattr(module, macro_name):
            raise AttributeError(
                f"Template '{self._template_path.name}' does not define macro '{macro_name}'."
            )
        return getattr(module, macro_name)

    def _install_dependencies(self, tree: List[Any], env: Any) -> None:
        first_task = tree["nodes"][0]
        deps = tree["deps"]
        if len(deps) > 0:
            install_code = self._render_install_from_template_file(first_task, deps)
            try:
                self.backend.exec(install_code, env)
            except ExecutionRuntimeError as e:
                raise DependencyError(f"Dependency installation failed: {e}, install_code is:\n\n {install_code}") from e

    def _render_install_from_template_file(self, first_task: Any, deps: List[str]) -> str:
        lang_key = (self.language or "r").lower()
        cg = getattr(first_task, "cg", None)
        if cg is None:
            raise TemplateInstallSnippetMissing("first_task has no code generator (cg); cannot locate the template.")

        tmpl_map = getattr(PublicConfig, "TEMPLATE_MAP", {}) or {}
        tmpl_name = tmpl_map.get(lang_key)
        if not isinstance(tmpl_name, str) or not tmpl_name.strip():
            raise TemplateInstallSnippetMissing(
                f"Template missing: first_task.cg.TEMPLATE_MAP['{lang_key}'] is not defined."
            )

        try:
            _ = self._jinja_env.get_template(tmpl_name)
        except Exception as e:
            here = os.path.dirname(os.path.abspath(__file__))
            raise TemplateInstallSnippetMissing(
                f"Template file '{tmpl_name}' was not found under {here}."
            ) from e

        macro = self._get_macro("install_required_packages")
        code = str(macro(required_libraries=deps))
        return code

    def _execute_task(self, task: Any, env: Any, baseline_task: Any, baseline_fp: Optional[str]) -> None:
        self._prepare_data(task, env, baseline_task, baseline_fp)

        segments = self._get_code_segments(task)
        code_text = segments.get("combined") or segments.get("execute_code")
        if not code_text or not isinstance(code_text, str):
            raise ExecutionRuntimeError("Task lacks executable code: provide either 'combined' or 'execute_code'.")

        self.backend.exec(code_text, env)

        r_obj = self.backend.get(env, "python_output")
        if r_obj is None:
            raise ResultNotFoundError("`python_output` was not created in the R environment.")

        py_obj = self.backend.to_python(r_obj)
        setattr(task, "exec_result", py_obj)
        self.backend.remove(env, "python_output")

    def _prepare_data(self, task: Any, env: Any, baseline_task: Any, baseline_fp: Optional[str]) -> None:
        ctx = getattr(task, "_data_context", None)
        if isinstance(ctx, dict):
            output_var = ctx.get("output_data_variable")
            if not output_var:
                return
            if output_var in self._prepared_datasets:
                return
            self._execute_prepare_code(task, env)
            self._prepared_datasets.add(output_var)
            return

        # 回退到旧 fingerprint 逻辑（兼容未分配数据上下文的场景）
        task_fp = getattr(task, "prep_fingerprint", None)
        if_reprep = bool(getattr(task, "if_reprep", False))

        if if_reprep:
            target_task = task
            target_fp = task_fp
        else:
            target_task = baseline_task
            target_fp = baseline_fp

        if self.current_prep_fingerprint != target_fp:
            segments = self._get_code_segments(target_task)
            prep_code = segments.get("prepare_code", "") or ""

            if prep_code.strip():
                self.backend.exec(prep_code, env)
            self.current_prep_fingerprint = target_fp

    def _log(self, tree_idx: int, key: str, level: str, msg: str) -> None:
        k = f"tree[{tree_idx}]"
        self.logs.setdefault(k, []).append({"where": key, "level": level, "msg": msg})

    def _execute_prepare_code(self, task: Any, env: Any) -> None:
        segments = self._get_code_segments(task)
        prep_code = segments.get("prepare_code", "") or ""
        if prep_code.strip():
            self.backend.exec(prep_code, env)

    def _prepare_exec_cache(self) -> Optional[Path]:
        dir_path = getattr(self.cfg, "exec_cache_dir", None)
        if not dir_path:
            dir_path = "./cache_exec_results"
        cache_dir = Path(dir_path).expanduser()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return cache_dir / f"exec_results_{timestamp}.pkl"

    @classmethod
    def load_cached_payload(cls, cache_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        dir_path = cache_dir or getattr(ConfigLoader(), "exec_cache_dir", "./cache_exec_results")
        cache_dir = Path(dir_path).expanduser()
        if not cache_dir.exists():
            return None
        pkl_files = sorted(cache_dir.glob("exec_results_*.pkl"))
        if not pkl_files:
            return None
        latest = pkl_files[-1]
        try:
            import pickle
            data = pickle.loads(latest.read_bytes())
        except Exception:
            return None

        if isinstance(data, dict):
            payload: Dict[str, Any] = dict(data)
        else:
            payload = {"exec_results": data}

        exec_results = payload.get("exec_results") or {}
        plan_cfg_dict = payload.get("plan_config")
        plan_obj = None
        if plan_cfg_dict:
            try:
                if isinstance(plan_cfg_dict, PlanConfig):
                    plan_obj = Plan.from_config(plan_cfg_dict)
                    payload["plan_config"] = plan_cfg_dict.to_dict()
                elif isinstance(plan_cfg_dict, dict):
                    plan_obj = Plan.from_config(PlanConfig.from_dict(plan_cfg_dict))
                else:
                    plan_obj = Plan.from_config(plan_cfg_dict)
            except Exception:
                plan_obj = None
        if plan_obj is not None and exec_results:
            cls._attach_exec_results_to_plan(plan_obj, exec_results)
        datasets = payload.get("datasets")
        if isinstance(datasets, dict):
            payload["datasets"] = datasets
        else:
            payload["datasets"] = {}
        payload["plan"] = plan_obj
        return payload

    @classmethod
    def load_cached_results(cls, cache_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        payload = cls.load_cached_payload(cache_dir)
        if not payload:
            return None
        return payload.get("exec_results")

    def _get_code_segments(self, task: Any) -> Dict[str, Any]:
        if task is None:
            return {}
        segments = getattr(task, "_code_segments", None)
        if isinstance(segments, dict):
            return segments
        ct_raw = getattr(task, "code_text", None)
        return self._unpack_code_text(ct_raw)

    def _unpack_code_text(self, ct: Any) -> Dict[str, Any]:
        if isinstance(ct, str):
            s = ct
            return {
                "dependencies": [],
                "prepare_code": "",
                "execute_code": s,
                "combined": s,
            }
        return ct or {}

    def _snapshot_datasets(self) -> Dict[str, pd.DataFrame]:
        snapshot: Dict[str, pd.DataFrame] = {}
        for key, df in (self.datasets or {}).items():
            if isinstance(df, pd.DataFrame):
                try:
                    snapshot[key] = df.copy()
                except Exception:
                    snapshot[key] = df
        return snapshot

    @staticmethod
    def _attach_exec_results_to_plan(plan_obj: Any, exec_results: Dict[str, Any]) -> None:
        if plan_obj is None or not exec_results:
            return
        roots = getattr(plan_obj, "roots", None)
        traverse = getattr(plan_obj, "_traverse_nodes", None)
        if not roots or not callable(traverse):
            return
        try:
            nodes = traverse(plan_obj.roots, order="dfs")  # type: ignore[attr-defined]
        except Exception:
            try:
                nodes = traverse(plan_obj.roots)  # type: ignore[attr-defined]
            except Exception:
                nodes = []
        for node in nodes or []:
            task = getattr(node, "task", None)
            if task is None:
                continue
            task_id = getattr(task, "task_id", None)
            if task_id and task_id in exec_results:
                try:
                    setattr(task, "exec_result", exec_results[task_id])
                except Exception:
                    continue


__all__ = ["CodeExecutor"]
