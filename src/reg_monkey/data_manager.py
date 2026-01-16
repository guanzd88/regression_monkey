import os
import time
import json
import hashlib
import importlib.util
import ast
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

import pandas as pd
from arcticdb import Arctic
from reg_monkey.util import ConfigLoader

class DataManager:
    """
    DataManager：统一的数据加载 / 重算 / 持久化控制器
    概览
    ----
    面向以 *symbol* 为单位管理数据资产的项目，提供：
    1) **三源加载优先级**与回退：ArcticDB ↔ DataLoader(动态导入) ↔ PKL；
    2) **一次性强制刷新**与**依赖传播**：按语义变化与手动指令，沿反向依赖闭包重算；
    3) **语义指纹**：对 DataLoader.clean_data 的 AST + 依赖列表做稳定哈希，变更即判定为“语义变更”；
    4) **预算与交互决策**：按历史耗时估计链路成本，在阈值内/外分别走自动或确认策略；
    5) **ArcticDB 元数据写入**：记录 semantic_hash / runtime_seconds / dependencies / run_at 等；
    6) **PKL 读入与回灌**：命中 PKL 时可选择自动写回 Arctic（ingest-on-load）；
    7) **多把键缓存**：原名 / 规范名(去 .pkl) / 安全属性名 / 安全持久化名 同步写入内存缓存。
    数据来源优先级
    --------------
    - 默认（非强制）：**Arctic → DataLoader → PKL**
    - 强制（被点名或依赖传播）：**DataLoader → Arctic → PKL**
    *任一路径失败会自动回退；三者皆不可用则抛错。*
    生命周期
    --------
    - 构造时会：
      1) 初始化/复用 Arctic 本地库（LMDB，按 `cfg.project_name` 建库）；
      2) 做一次“缺失目标的首次补齐”（仅对 *目标本体* 且存在对应 `data_loader/<symbol>.py`）；
      3) 依据刷新策略进行加载或重算（含依赖图、阈值评估与用户/非交互决策）。
    - `__enter__/__exit__/close()` 管理 Arctic 句柄的**引用计数**，最后一个引用释放时尽力关闭。
    关键参数
    --------
    target_symbols : List[str]
        目标 symbol 列表（允许带“.pkl”后缀；内部会统一规范化）。
    project_path : str | None
        项目根目录；默认 `os.getcwd()`。
    budget_minutes : float
        刷新预算阈值（分钟），用于估算链路耗时是否需要确认/强制。
    default_node_seconds : int
        缺历史耗时时的保守估计（秒）。
    auto_approve_short : bool
        当估算总时长 ≤ 阈值时是否自动执行（非交互）。
    auto_decline_long : bool
        当估算总时长 > 阈值且未强制时是否默认拒绝（非交互）。
    force_when_long : bool
        当估算总时长 > 阈值是否直接强制执行（非交互）。
    confirm_decision_provider : Callable[[List[str], float], bool] | None
        自定义确认回调；若提供则优先使用其结果（True 执行 / False 跳过）。
    force_refresh_symbols : List[str] | None
        一次性强刷入口（等价于在环境或清单文件中声明）。
    ingest_pkl_on_load : bool
        命中 PKL 时是否把 DataFrame 回灌至 Arctic（默认 True）。
    相关环境变量
    ------------
    DM_REFRESH_THRESHOLD_MINUTES : float
        覆盖 `budget_minutes`。
    DM_DEFAULT_NODE_SEC : int
        覆盖 `default_node_seconds`。
    DM_AUTO_APPROVE_SHORT : {0,1}
        覆盖 `auto_approve_short`。
    DM_AUTO_DECLINE_LONG : {0,1}
        覆盖 `auto_decline_long`。
    DM_FORCE : {0,1}
        覆盖 `force_when_long`。
    DM_INGEST_PKL_ON_LOAD : {0,1}
        覆盖 `ingest_pkl_on_load`。
    DM_REFRESH : str
        一次性强刷目标，`"*" | "a,b,c"`；也可在 `data_loader/.refresh_once.list` 中声明并用完即删。
    DM_NONINTERACTIVE : {0,1}
        置 1 时走非交互兜底策略。
    目录与命名约定
    --------------
    - DataLoader 文件：`<project>/data_loader/<symbol>.py`，需定义类 `DataLoader` 与方法 `clean_data()`。
      *支持“老写法”：`__init__` 不接收 `output_pkl_name`；也支持“新写法”：`__init__(..., output_pkl_name=...)`。*
    - 源数据 PKL：`<project>/source_data/<symbol>.pkl`。
    - Arctic 本地库：`<cwd>/data`（LMDB）。
    - 安全命名：所有持久化/注入属性名使用**去 .pkl + 非法字符替换**后的“安全名”（数字开头会加前缀）。
    公开属性
    --------
    loaded_data : Dict[str, pd.DataFrame]
        内存缓存（含原名/规范名/安全名三把键）。
    library : Arctic.Library
        当前 Arctic 库句柄。
    graph : Dict[str, List[str]]
        DataLoader 的依赖图（仅模块依赖；`.pkl` 依赖单独注入，不入图）。
    常用方法
    --------
    get_loaded(symbol) -> pd.DataFrame | None
        依次尝试多把键返回已加载的 DataFrame。
    list_arctic_symbols() -> List[str]
        列出 Arctic 库中所有 symbol（安全名）。
    close() -> None
        释放当前实例对 Arctic 的引用（引用计数归零时尝试关闭底层句柄）。
    语义与依赖解析
    --------------
    - `_collect_all_dependencies()` 仅把 **模块依赖** 建图；`.pkl` 依赖在注入阶段处理，
      避免自环（如 A 依赖 A.pkl）。
    - `_compute_semantic_hash()` 对 `DataLoader.clean_data` 的 AST（去掉首行 docstring）与
      依赖列表做稳定哈希；若无法解析，则退化为源码文本哈希。
    刷新与执行策略（简述）
    ----------------------
    1) 解析依赖图与反向图；2) 收集一次性强刷集合 `F_manual`；
    3) 对比语义哈希求 `F_semantic`；4) 联合得 `F0` 并沿反向依赖闭包扩展成 `F`；
    5) 对 `F` 估算成本并按策略（交互或非交互）决定是否执行；
    6) 执行则按拓扑序 `_load_symbol()` 重算，否则只读加载目标本体（优先 Arctic，回退 PKL；可选回灌）。
    使用示例
    --------
    >>> with DataManager(target_symbols=["prices", "features"], project_path="/path/to/project") as dm:
    ...     df = dm.get_loaded("prices")          # 取缓存
    ...     print(dm.list_arctic_symbols()[:5])   # 查看库内已有表
    ...     # 非交互模式下，可通过环境变量控制：DM_NONINTERACTIVE=1 DM_AUTO_APPROVE_SHORT=1 等
    异常
    ----
    - `ValueError`：依赖图存在环；
    - `RuntimeError`：三源均不可用或都加载失败；
    - `FileNotFoundError`：声明了自依赖 PKL 但文件不存在；
    - 其它：动态导入/执行 `clean_data()` 失败、类型不匹配等。

    实现提示
    --------
    - DataFrame 的回写元数据包含：`semantic_hash`、`runtime_seconds`、`dependencies`、
      `trigger_reason`、`run_at` 等；便于溯源与预算估计。
    - 为避免 pandas 在 pretty-print 阶段遍历自定义可迭代对象导致异常，内部对外暴露的数据应保持
      朴素类型（DataFrame/标量/字典/列表）或在上层展示时进行安全序列化。
    """
    _ARCTIC_CACHE: dict[str, Arctic] = {}
    _ARCTIC_REFCOUNT: dict[str, int] = {}
    # ----------------------------- 构造与配置 -----------------------------
    def __init__(
        self,
        target_symbols: List[str],
        project_path: Optional[str] = None,
        # 固定阈值简化版
        budget_minutes: float = 10,                 # 总阈值（分钟）
        default_node_seconds: int = 60,           # 无历史估时缺省（秒）
        auto_approve_short: bool = False,         # 当 ≤ 阈值时，是否自动执行
        auto_decline_long: bool = True,           # 当 > 阈值时，是否默认拒绝
        force_when_long: bool = False,            # 当 > 阈值是否强制执行
        # 提示模式回调：形如 (symbols: List[str], estimated_total_sec: float) -> bool
        confirm_decision_provider: Optional[Callable[[List[str], float], bool]] = None,
        # 一次性强刷入口（也可用环境变量/清单文件）
        force_refresh_symbols: Optional[List[str]] = None,
        # ★ 新增：命中 PKL 时是否写回 Arctic（默认 True，可用环境变量覆盖）
        ingest_pkl_on_load: bool = True,
    ):
        self.project_path = project_path or os.getcwd()
        self.data_loader_folder = os.path.join(self.project_path, "data_loader")
        self.arctic_local_dir = os.path.join(os.getcwd(), "data")  # CWD/data

        # config / arctic
        self.cfg = ConfigLoader()
        self.store: Optional[Arctic] = None
        self.library = None

        # 内存缓存
        self.loaded_data: Dict[str, pd.DataFrame] = {}
        self.pkl_deps_map: Dict[str, List[str]] = {}

        # 统一规范化（去掉 .pkl 后缀）
        norm = lambda s: s[:-4] if isinstance(s, str) and s.endswith(".pkl") else s
        # self.target_symbols: List[str] = [norm(s) for s in (target_symbols or [])]
        self.target_symbols = [self._normalize_symbol(s) for s in (target_symbols or [])]
        # self.force_refresh_symbols_cfg: Set[str] = set(norm(s) for s in (force_refresh_symbols or []))
        self.force_refresh_symbols_cfg = set(self._normalize_symbol(s) for s in (force_refresh_symbols or []))

        # 判定与回调（可被环境变量覆盖）
        self.budget_minutes = float(os.getenv("DM_REFRESH_THRESHOLD_MINUTES", str(budget_minutes)))
        self.default_node_seconds = int(os.getenv("DM_DEFAULT_NODE_SEC", str(default_node_seconds)))
        self.auto_approve_short = bool(int(os.getenv("DM_AUTO_APPROVE_SHORT", "1" if auto_approve_short else "0")))
        self.auto_decline_long = bool(int(os.getenv("DM_AUTO_DECLINE_LONG", "1" if auto_decline_long else "0")))
        self.force_when_long = bool(int(os.getenv("DM_FORCE", "1" if force_when_long else "0")))
        self.confirm_decision_provider = confirm_decision_provider

        # ★ 命中 PKL 时是否写回 Arctic（ingest-on-load）
        self.ingest_pkl_on_load = bool(int(os.getenv(
            "DM_INGEST_PKL_ON_LOAD",
            "1" if ingest_pkl_on_load else "0"
        )))

        # 初始化并执行
        self._ensure_arctic_ready()
        self._initial_fill_when_normal()
        self._process_with_refresh_policy()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def __del__(self):
        # 兜底：如果用户忘了手动 close，析构时尝试降计数
        try:
            self.close()
        except Exception:
            pass

    # ----------------------------- Arctic 初始化 -----------------------------
    def _is_dir_empty(self, path: str) -> bool:
        return (not os.path.exists(path)) or (os.path.isdir(path) and len(os.listdir(path)) == 0)

    def _ensure_arctic_ready(self):
        os.makedirs(self.arctic_local_dir, exist_ok=True)
        uri = f"lmdb://{self.arctic_local_dir}"

        # 复用缓存的 Arctic；避免同进程重复打开同一路径
        if self.arctic_local_dir in DataManager._ARCTIC_CACHE:
            self.store = DataManager._ARCTIC_CACHE[self.arctic_local_dir]
            DataManager._ARCTIC_REFCOUNT[self.arctic_local_dir] += 1
        else:
            self.store = Arctic(uri)
            DataManager._ARCTIC_CACHE[self.arctic_local_dir] = self.store
            DataManager._ARCTIC_REFCOUNT[self.arctic_local_dir] = 1

        lib_name = self.cfg.project_name.replace(" ", "_")
        if not self.store.has_library(lib_name):
            self.store.create_library(lib_name)
        self.library = self.store[lib_name]

    # --------------------------- DataLoader 导入工具 --------------------------
    def _iter_loader_files(self) -> List[str]:
        if not os.path.exists(self.data_loader_folder):
            return []
        return [
            os.path.join(self.data_loader_folder, f)
            for f in os.listdir(self.data_loader_folder)
            if f.endswith('.py')
        ]

    def _symbol_from_path(self, filepath: str) -> str:
        return os.path.splitext(os.path.basename(filepath))[0]

    def _dynamic_import_loader(self, filepath: str):
        import os, importlib.util, inspect

        module_name = os.path.splitext(os.path.basename(filepath))[0]
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader, f"Failed to import {filepath}"
        spec.loader.exec_module(mod)
        dl_cls = getattr(mod, 'DataLoader')

        # 1) 不调用 __init__ 先创建实例
        dl = dl_cls.__new__(dl_cls)

        # 2) 预植入 output_pkl_name（不存在/为 None/空串时用模块名）
        try:
            val = getattr(dl, 'output_pkl_name', None)
        except Exception:
            val = None
        if val in (None, ""):
            try:
                setattr(dl, 'output_pkl_name', module_name)
            except Exception:
                # 极端情况下兜底
                object.__setattr__(dl, 'output_pkl_name', module_name)

        # 3) 再安全地调用 __init__：
        #    - 若子类 __init__ 接受 output_pkl_name，就把值也传进去（满足“规范做法”）
        #    - 若不接受，就直接无参调用（满足“老写法”）
        try:
            sig = inspect.signature(dl_cls)
        except (TypeError, ValueError):
            sig = None

        if sig and 'output_pkl_name' in sig.parameters:
            dl_cls.__init__(dl, output_pkl_name=getattr(dl, 'output_pkl_name'))
        else:
            dl_cls.__init__(dl)

        return dl


    # ------------------------- ArcticDB 读写与存在性 -------------------------
    def _list_symbols(self) -> Set[str]:
        try:
            return set(self.library.list_symbols())
        except Exception:
            return set()

    def _symbol_exists(self, symbol: str) -> bool:
        """优先查安全名；为兼容旧库，若安全名不存在则尝试原名。"""
        syms = self._list_symbols()
        safe = self._canonical_symbol(symbol)
        return safe in syms or symbol in syms

    def _get_latest_item(self, symbol: str):
        """优先读安全名；失败再回退原名。"""
        try:
            return self.library.read(self._canonical_symbol(symbol))
        except Exception:
            try:
                return self.library.read(symbol)  # 兼容历史不安全名
            except Exception:
                return None

    def _get_latest_metadata(self, symbol: str) -> Dict:
        item = self._get_latest_item(symbol)
        if item is None:
            return {}
        try:
            return dict(item.metadata or {})
        except Exception:
            return {}

    def _write_df(self, symbol: str, df: pd.DataFrame, metadata: Optional[Dict] = None) -> None:
        """所有写入一律写到安全名 symbol。"""
        self.library.write(self._canonical_symbol(symbol), df, metadata=(metadata or {}), prune_previous_versions=False)

    def _read_df(self, symbol: str) -> pd.DataFrame:
        """所有读取优先从安全名读取。"""
        item = self.library.read(self._canonical_symbol(symbol))
        return item.data


    # ------------------------------ 依赖解析/指纹 -----------------------------
    def _collect_all_dependencies(self) -> Dict[str, List[str]]:
        """
        构建“模块依赖图”：只把 data_loader/*.py 中的依赖（非 .pkl）纳入 DAG，
        .pkl 依赖不进图，改由注入阶段处理（避免 A 依赖 A.pkl 导致自环）。
        同时填充 self.pkl_deps_map[symbol] 供注入阶段使用。
        """
        graph: Dict[str, List[str]] = {}
        self.pkl_deps_map = {}

        for path in self._iter_loader_files():
            symbol = self._symbol_from_path(path)
            try:
                dl = self._dynamic_import_loader(path)
                deps_raw = getattr(dl, 'dependency', []) or []

                module_deps: List[str] = []
                pkl_deps: List[str] = []

                for d in deps_raw:
                    if not isinstance(d, str):
                        continue
                    if d.endswith('.pkl'):
                        name = d[:-4]  # 去掉后缀，仅注入阶段使用，不入图
                        if name:
                            pkl_deps.append(name)
                    else:
                        if d:
                            module_deps.append(d)

                # 模块依赖：去重、排序，并去掉自依赖（A -> A）
                module_deps = sorted(set(x for x in module_deps if x and x != symbol))
                graph[symbol] = module_deps

                # 记录 pkl 依赖（注入阶段使用；允许出现与 symbol 同名的自依赖 pkl）
                self.pkl_deps_map[symbol] = sorted(set(pkl_deps))

            except Exception:
                graph[symbol] = []
                self.pkl_deps_map[symbol] = []

        return graph


    def _reverse_graph(self, graph: Dict[str, List[str]]) -> Dict[str, List[str]]:
        rev: Dict[str, List[str]] = {k: [] for k in graph.keys()}
        for u, vs in graph.items():
            for v in vs:
                if v not in rev:
                    rev[v] = []
                rev[v].append(u)
        return rev

    def _topo_sort(self, graph: Dict[str, List[str]], roots: List[str]) -> List[str]:
        visited: Dict[str, int] = {}
        order: List[str] = []

        def dfs(u: str):
            st = visited.get(u, 0)
            if st == 1:
                raise ValueError(f"Dependency cycle detected at '{u}'")
            if st == 2:
                return
            visited[u] = 1
            for v in graph.get(u, []):
                dfs(v)
            visited[u] = 2
            if u not in order:
                order.append(u)

        for r in roots:
            if r in graph:
                dfs(r)
        return order

    def _compute_semantic_hash(self, filepath: str, deps: List[str]) -> str:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)

            class_name = 'DataLoader'
            fn_dump = ''
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for b in node.body:
                        if isinstance(b, ast.FunctionDef) and b.name == 'clean_data':
                            new_body = b.body
                            if new_body and isinstance(new_body[0], ast.Expr) and isinstance(new_body[0].value, ast.Str):
                                new_body = new_body[1:]
                            tmp_fn = ast.FunctionDef(name=b.name, args=b.args, body=new_body, decorator_list=[], returns=b.returns, type_comment=None)
                            ast.fix_missing_locations(tmp_fn)
                            fn_dump = ast.dump(tmp_fn, annotate_fields=True, include_attributes=False)
                            break
                    break
            payload = json.dumps({'fn': fn_dump, 'deps': deps}, sort_keys=True)
            return hashlib.sha256(payload.encode('utf-8')).hexdigest()
        except Exception:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    src = f.read()
                payload = json.dumps({'src': src, 'deps': deps}, sort_keys=True)
                return hashlib.sha256(payload.encode('utf-8')).hexdigest()
            except Exception:
                return hashlib.sha256(os.urandom(16)).hexdigest()

    # ------------------------------ 工具：依赖注入/命名 ------------------------------
    def _normalize_symbol(self, name: str) -> str:
        """去掉 .pkl 后缀（仅规范化，不做重命名）。"""
        return name[:-4] if isinstance(name, str) and name.endswith('.pkl') else name

    def _canonical_symbol(self, name: str) -> str:
        """供属性注入/缓存键使用的安全名：去 .pkl + sanitize。"""
        return self._sanitize_attr_name(self._normalize_symbol(name))

    def _canonical_attr(self, name: str) -> str:
        """DataLoader 实例属性/缓存键用的安全名（去 .pkl + sanitize）。"""
        return self._sanitize_attr_name(self._normalize_symbol(name))


    def _symbol_to_pkl_filename(self, symbol: str) -> str:
        """构造 pkl 文件名，避免 *.pkl.pkl。"""
        return symbol if symbol.endswith('.pkl') else f"{symbol}.pkl"
    
    def _sanitize_attr_name(self, name: str) -> str:
        import re
        s = re.sub(r"[^0-9a-zA-Z_]", "_", name)
        if not s or s[0].isdigit():
            s = f"table_{s or 'dep'}"
        return s

    def _inject_dependencies(self, dl_obj, symbol: str, graph: Dict[str, List[str]], forced_set: Set[str]) -> List[str]:
        """
        将依赖的 DataFrame 以属性形式注入到 DataLoader 实例：
        - 模块依赖：若未加载则调用 _load_symbol(dep, ...) 按优先级加载
        - .pkl 依赖：不进图。在此处按需加载；若为“自依赖 pkl”（dep == symbol）则直接从 source_data/dep.pkl 读，避免递归
        返回注入的属性名列表。
        """
        injected_attrs: List[str] = []

        module_deps = graph.get(symbol, []) or []
        pkl_deps = getattr(self, 'pkl_deps_map', {}).get(symbol, []) or []
        all_deps = module_deps + pkl_deps

        for dep in all_deps:
            df_dep = None

            # 1) 自依赖 pkl：dep 等于 symbol，并且在 pkl_deps 中
            if dep == symbol and dep in pkl_deps:
                pkl_path = os.path.join(self.project_path, 'source_data', f"{dep}.pkl")
                if os.path.exists(pkl_path):
                    df_dep = self._load_pkl_compat(pkl_path)
                else:
                    raise FileNotFoundError(f"自依赖 PKL 未找到：{pkl_path}")

            else:
                # 2) 普通依赖：若未加载则即时加载（会按 DataLoader→Arctic→PKL 或反向策略）
                if (dep not in self.loaded_data
                    and self._canonical_attr(dep) not in self.loaded_data
                    and self._canonical_symbol(dep) not in self.loaded_data):
                    self._load_symbol(dep, forced_set, graph)

                # 逐键取第一个非 None（不能用 or 连接 DataFrame）
                for k in (dep, self._canonical_attr(dep), self._canonical_symbol(dep)):
                    if k in self.loaded_data:
                        val = self.loaded_data[k]
                        if val is not None:
                            df_dep = val
                            break

            if df_dep is None:
                raise KeyError(f"依赖 '{dep}' 未能加载（当前 symbol='{symbol}'）。")

            # 注入为安全属性名（去 .pkl + sanitize；数字开头加前缀）
            attr = self._canonical_attr(dep)
            setattr(dl_obj, attr, df_dep)
            injected_attrs.append(attr)

        return injected_attrs

    # ------------------------------ 加载优先级 -------------------------------
    def _resolve_priority(self, symbol: str, forced_set: Set[str]) -> List[str]:
        default = ["arctic", "dataloader", "pkl"]
        forced = ["dataloader", "arctic", "pkl"]
        return forced if symbol in forced_set else default

    def _load_symbol(self, symbol: str, forced_set: Set[str], graph: Dict[str, List[str]]) -> pd.DataFrame:
        """
        三源按序加载一个 symbol：
        - 默认：Arctic → DataLoader → PKL
        - 强制刷新集合内：DataLoader → Arctic → PKL
        命中 DataLoader 路径时会在 clean_data() 前注入依赖，并兼容 clean_data() 返回 DataFrame 的写法。
        """
        order = self._resolve_priority(symbol, forced_set)
        errors: List[Tuple[str, str]] = []

        for src in order:
            if src == "arctic":
                if self._symbol_exists(symbol):
                    df = self._read_df(symbol)  # 内部已走安全名
                    # 双键写入：真实名 / 安全属性名 / 安全持久化名
                    self.loaded_data[symbol] = df
                    self.loaded_data[self._canonical_attr(symbol)] = df
                    self.loaded_data[self._canonical_symbol(symbol)] = df
                    print(f"[Load] {self._canonical_symbol(symbol)} <- Arctic")
                    return df

            elif src == "dataloader":
                path = os.path.join(self.data_loader_folder, f"{symbol}.py")
                if os.path.exists(path):
                    deps = graph.get(symbol, [])
                    sem_hash = self._compute_semantic_hash(path, deps)
                    dl = self._dynamic_import_loader(path)
                    if not hasattr(dl, 'clean_data'):
                        raise AttributeError(f"DataLoader for symbol '{symbol}' has no clean_data()")

                    # 注入依赖
                    injected_attrs = self._inject_dependencies(dl, symbol, graph, forced_set)

                    # 执行 clean_data：接住返回值并回填 dl.df
                    t0 = time.time()
                    ret = dl.clean_data()
                    cost = time.time() - t0
                    if ret is not None:
                        if not isinstance(ret, pd.DataFrame):
                            raise TypeError(f"clean_data() must return a pandas.DataFrame or None, got {type(ret)}")
                        dl.df = ret
                    if not hasattr(dl, 'df') or dl.df is None:
                        raise AttributeError(f"DataLoader for symbol '{symbol}' produced no df")

                    # 写入 Arctic（安全名）
                    meta = {
                        'semantic_hash': sem_hash,
                        'runtime_seconds': float(cost),
                        'dependencies': deps,
                        'injected_attrs': injected_attrs,
                        'data_source': 'dataloader',
                        'forced': symbol in forced_set,
                        'trigger_reason': 'manual' if symbol in self.force_refresh_symbols_cfg else 'hash_changed',
                        'run_at': datetime.utcnow().isoformat() + 'Z',
                    }
                    self._write_df(symbol, dl.df, metadata=meta)  # 内部转安全名
                    # 双键写入缓存
                    self.loaded_data[symbol] = dl.df
                    self.loaded_data[self._canonical_attr(symbol)] = dl.df
                    self.loaded_data[self._canonical_symbol(symbol)] = dl.df
                    print(f"[Build] {self._canonical_symbol(symbol)} via DataLoader, {cost:.2f}s -> Arctic (deps→{injected_attrs})")
                    return dl.df

            elif src == "pkl":
                pkl_name = symbol if symbol.endswith('.pkl') else f"{symbol}.pkl"
                pkl_path = os.path.join(self.project_path, 'source_data', pkl_name)
                if os.path.exists(pkl_path):
                    df = self._load_pkl_compat(pkl_path)
                    if df is not None:
                        # 双键写入缓存
                        self.loaded_data[symbol] = df
                        self.loaded_data[self._canonical_attr(symbol)] = df
                        self.loaded_data[self._canonical_symbol(symbol)] = df
                        print(f"[Load] {self._canonical_symbol(symbol)} <- PKL: {pkl_path}")

                        # 可选：把 PKL 写回 Arctic（安全名）
                        if self.ingest_pkl_on_load:
                            meta = {
                                'data_source': 'pkl',
                                'ingested_from': 'pkl',
                                'runtime_seconds': 0.0,
                                'dependencies': [],
                                'trigger_reason': 'ingest_pkl',
                                'run_at': datetime.utcnow().isoformat() + 'Z',
                            }
                            try:
                                self._write_df(symbol, df, metadata=meta)  # 内部转安全名
                                print(f"[Ingest] {self._canonical_symbol(symbol)} (from PKL) -> Arctic")
                            except Exception as e:
                                print(f"[Warn] 写回 Arctic 失败（PKL→Arctic）：{symbol}: {e}")
                        return df

        raise RuntimeError(f"未找到或无法加载同名数据集：symbol={symbol}; attempts={order}; errors={errors}")

    def _load_symbol_readonly_arctic(self, symbol: str) -> Optional[pd.DataFrame]:
        if self._symbol_exists(symbol):
            df = self._read_df(symbol)  # 内部已走安全名
            self.loaded_data[symbol] = df
            self.loaded_data[self._canonical_attr(symbol)] = df
            self.loaded_data[self._canonical_symbol(symbol)] = df
            print(f"[Load] {self._canonical_symbol(symbol)} <- Arctic (readonly)")
            return df
        return None

    # 兼容你原先 data_loader.load_pkl 的解析逻辑（只读，不写）
    def _load_pkl_compat(self, file_path: str) -> Optional[pd.DataFrame]:
        try:
            import pickle
            with open(file_path, 'rb') as f:
                data = pickle.load(f)
        except Exception:
            return None

        df: Optional[pd.DataFrame] = None
        fields_def_dict: Optional[Dict] = None

        if isinstance(data, tuple) and len(data) == 2:
            if isinstance(data[0], pd.DataFrame):
                df = data[0]
            if isinstance(data[1], dict) and 'defaultFieldInfos' in data[1] and 'fieldInfos' in data[1]:
                fields_def_dict = data[1]
        elif isinstance(data, pd.DataFrame):
            df = data
        elif isinstance(data, dict) and 'defaultFieldInfos' in data and 'fieldInfos' in data:
            fields_def_dict = data

        if df is None:
            return None

        if fields_def_dict:
            fields_definition = (fields_def_dict.get('defaultFieldInfos') or []) + (fields_def_dict.get('fieldInfos') or [])
            type_dict = {item.get('field'): item.get('fieldType') for item in fields_definition if 'field' in item and 'fieldType' in item}
            for col in list(df.columns):
                if col in type_dict:
                    ftype = type_dict[col]
                    if ftype == 'DataTime':
                        df[col] = pd.to_datetime(df[col], errors='coerce')
                    elif ftype == 'decimal':
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    else:
                        df[col] = df[col].astype(str)
        return df

    # ------------------------------ 预算与决策 -------------------------------
    def _estimate_runtime_total(self, symbols: List[str]) -> float:
        total = 0.0
        for s in symbols:
            md = self._get_latest_metadata(s)
            est = float(md.get('runtime_seconds', self.default_node_seconds))
            total += est
        return total

    def _confirm_or_decide(self, symbols: List[str], estimated_total_sec: float) -> Tuple[bool, str]:
        """
        返回 (do_run, decider)：
        - do_run: 是否执行重算
        - decider: 'user_yes' | 'user_no' | 'force' | 'auto'
        逻辑：
        1) 若提供 confirm_decision_provider，优先调用该回调决定。
        2) 否则进入交互式提示（input）：
            - ≤ 阈值：询问“是否执行重算？”
            -  > 阈值：询问“是否强制重算？（支持 f/force）”
        3) 若设置 DM_NONINTERACTIVE=1（或 input 不可用）：
            - ≤ 阈值：按 auto_approve_short 兜底（True 执行，否则跳过）
            -  > 阈值：按 force_when_long / auto_decline_long 兜底
        """
        import os, sys

        threshold_sec = self.budget_minutes * 60
        total_min = estimated_total_sec / 60.0
        budget_min = self.budget_minutes
        syms = ", ".join(symbols) if symbols else "(none)"
        is_short = estimated_total_sec <= threshold_sec

        # 1) 若提供外部确认回调，优先使用
        if self.confirm_decision_provider:
            ok = bool(self.confirm_decision_provider(symbols, estimated_total_sec))
            return ok, ('user_yes' if ok else 'user_no')

        # 2) 交互 or 非交互判定
        non_interactive = os.getenv("DM_NONINTERACTIVE", "").strip() == "1"

        # 构造提示文案
        if is_short:
            msg_head = (f"[Refresh] 估计总时长 {total_min:.1f} min ≤ 阈值 {budget_min} min。"
                        f"将重算以下 symbol：{syms}")
            prompt = "是否执行重算？ [y/N]: "
        else:
            msg_head = (f"[Refresh] 估计总时长 {total_min:.1f} min > 阈值 {budget_min} min。"
                        f"将重算以下 symbol：{syms}")
            prompt = "是否【强制】重算？ [y/N/f]: "

        # 3) 非交互模式兜底（CI/脚本环境）
        if non_interactive:
            if is_short:
                if self.auto_approve_short:
                    print(f"{msg_head}\n[Refresh] 非交互模式：≤ 阈值，auto_approve_short=1 → 执行。")
                    return True, 'auto'
                else:
                    print(f"{msg_head}\n[Refresh] 非交互模式：≤ 阈值，未配置自动通过 → 跳过。")
                    return False, 'user_no'
            else:
                if self.force_when_long:
                    print(f"{msg_head}\n[Refresh] 非交互模式：> 阈值，force_when_long=1 → 强制执行。")
                    return True, 'auto'
                if not self.auto_decline_long:
                    print(f"{msg_head}\n[Refresh] 非交互模式：> 阈值，auto_decline_long=0 但无交互 → 跳过。")
                    return False, 'user_no'
                print(f"{msg_head}\n[Refresh] 非交互模式：> 阈值，默认跳过。")
                return False, 'user_no'

        # 4) 交互式确认（优先尝试 input；若失败再按兜底策略）
        try:
            print(msg_head)
            ans = input(prompt)
            ans = (ans or "").strip().lower()
        except Exception:
            # input 不可用：退回非交互兜底
            if is_short and self.auto_approve_short:
                return True, 'auto'
            if (not is_short) and self.force_when_long:
                return True, 'auto'
            return False, 'user_no'

        if ans in ('y', 'yes'):
            return True, 'user_yes'
        if (not is_short) and ans in ('f', 'force'):
            return True, 'force'
        return False, 'user_no'


    # ---------------------------- 主流程（刷新策略） ---------------------------
    def _process_with_refresh_policy(self) -> None:
        if not self.target_symbols:
            return

        # 1) 解析依赖，并把“没有 .py 的目标”也纳入图（零依赖）
        graph = self._collect_all_dependencies()
        for t in self.target_symbols:
            graph.setdefault(t, [])
        self.graph = graph

        rev_graph = self._reverse_graph(graph)
        all_symbols = set(graph.keys())

        # 2) 一次性强刷入口
        F_manual = self._read_manual_refresh_symbols(all_symbols)

        # 3) 语义变更集合
        F_semantic = self._collect_semantic_changes(graph)

        # 4) 起点 F0
        F0 = set().union(F_manual, F_semantic)

        # 5) 反向依赖闭包
        F = set(F0)
        queue = list(F0)
        while queue:
            cur = queue.pop(0)
            for depd in rev_graph.get(cur, []):
                if depd not in F:
                    F.add(depd)
                    queue.append(depd)

        # 6) 生成拓扑顺序
        topo_chain = self._topo_sort(graph, self.target_symbols)

        # 7) 预算评估（仅对强制集合 F）
        ordered_forced = [s for s in topo_chain if s in F]
        if ordered_forced:
            estimated_total = self._estimate_runtime_total(ordered_forced)
            do_run, decider = self._confirm_or_decide(ordered_forced, estimated_total)
        else:
            do_run, decider = (False, 'user_no')

        # 8) 执行 / 只读装载
        if do_run:
            for s in topo_chain:
                self._load_symbol(s, F, graph)
        else:
            # ⚠️ 只加载“目标本体”，不加载其依赖，避免不必要的内存占用
            for s in self.target_symbols:
                df = self._load_symbol_readonly_arctic(s)
                if df is None:
                    # PKL 回退（避免 *.pkl.pkl）
                    pkl_name = s if s.endswith('.pkl') else f"{s}.pkl"
                    pkl_path = os.path.join(self.project_path, 'source_data', pkl_name)
                    if os.path.exists(pkl_path):
                        df = self._load_pkl_compat(pkl_path)
                        if df is not None:
                            # 多把键写缓存：原样 / 安全属性名 / 安全持久化名
                            self.loaded_data[s] = df
                            self.loaded_data[self._canonical_attr(s)] = df
                            self.loaded_data[self._canonical_symbol(s)] = df
                            print(f"[Load] {self._canonical_symbol(s)} <- PKL (fallback): {pkl_path}")
                            # 可选：把 PKL 也写回 Arctic（安全名）
                            if self.ingest_pkl_on_load:
                                meta = {
                                    'data_source': 'pkl',
                                    'ingested_from': 'pkl',
                                    'runtime_seconds': 0.0,
                                    'dependencies': [],
                                    'trigger_reason': 'ingest_pkl_fallback',
                                    'run_at': datetime.utcnow().isoformat() + 'Z',
                                }
                                try:
                                    self._write_df(s, df, metadata=meta)
                                    print(f"[Ingest] {self._canonical_symbol(s)} (fallback PKL) -> Arctic")
                                except Exception as e:
                                    print(f"[Warn] 写回 Arctic 失败（fallback PKL→Arctic）：{s}: {e}")

    # ---------------------------- 一次性强刷入口 -----------------------------
    def _read_manual_refresh_symbols(self, all_symbols: Set[str]) -> Set[str]:
        manual: Set[str] = set(self.force_refresh_symbols_cfg)

        env_val = os.getenv('DM_REFRESH', '').strip()
        if env_val:
            if env_val == '*':
                manual.update(all_symbols)
            else:
                parts = [p.strip() for p in env_val.split(',') if p.strip()]
                manual.update(parts)

        list_path = os.path.join(self.data_loader_folder, '.refresh_once.list')
        if os.path.exists(list_path):
            try:
                with open(list_path, 'r', encoding='utf-8') as f:
                    lines = [ln.strip() for ln in f.readlines()]
                items: List[str] = []
                for ln in lines:
                    if not ln or ln.startswith('#'):
                        continue
                    items.append(ln)
                if '*' in items:
                    manual.update(all_symbols)
                else:
                    manual.update(items)
                try:
                    os.remove(list_path)
                except Exception:
                    pass
            except Exception:
                pass

        return {s for s in manual if s in all_symbols}

    # ------------------------------- 基础补齐逻辑 ----------------------------
    def _initial_fill_when_normal(self) -> None:
        """
        仅对“目标 symbol 本身缺失且存在 data_loader/<symbol>.py”的情况做首次补齐。
        不再遍历/补齐依赖节点，避免无谓内存占用与构建。
        """
        # 若本地 Arctic 目录为空，则跳过（初始化阶段交给 _ensure_arctic_ready）
        if self._is_dir_empty(self.arctic_local_dir):
            return

        # 只考虑“缺失的目标本体”
        to_bootstrap = [s for s in (self.target_symbols or []) if not self._symbol_exists(s)]
        if not to_bootstrap:
            return

        graph = self._collect_all_dependencies()  # 仅用于注入依赖时找到依赖名

        for symbol in to_bootstrap:
            path = os.path.join(self.data_loader_folder, f"{symbol}.py")
            if not os.path.exists(path):
                # 目标无 DataLoader.py：交由后续流程按 Arctic/PKL 加载，不在这里构建
                continue

            # try:
            dl = self._dynamic_import_loader(path)
            if not hasattr(dl, 'clean_data'):
                raise AttributeError(f"DataLoader for symbol '{symbol}' has no clean_data()")

            # 只为“目标本体”注入其依赖（必要时即时加载依赖），不对依赖做预构建
            deps = graph.get(symbol, [])
            injected_attrs = self._inject_dependencies(dl, symbol, graph, forced_set=set())

            # 执行 clean_data（兼容返回 DataFrame）
            t0 = time.time()
            ret = dl.clean_data()
            dt = time.time() - t0
            if ret is not None:
                if not isinstance(ret, pd.DataFrame):
                    raise TypeError(f"clean_data() must return a pandas.DataFrame or None, got {type(ret)}")
                dl.df = ret
            if not hasattr(dl, 'df') or dl.df is None:
                raise AttributeError(f"DataLoader for symbol '{symbol}' produced no df")

            # 写回 Arctic（安全名），并写入多把缓存键
            sem_hash = self._compute_semantic_hash(path, deps)
            meta = {
                'semantic_hash': sem_hash,
                'runtime_seconds': float(dt),
                'dependencies': deps,
                'injected_attrs': injected_attrs,
                'trigger_reason': 'bootstrap',
                'decider': 'auto',
                'estimated_chain_cost': float(dt),
                'thresholds_used': {"total": self.budget_minutes * 60},
                'run_at': datetime.utcnow().isoformat() + 'Z',
            }
            self._write_df(symbol, dl.df, metadata=meta)
            self.loaded_data[symbol] = dl.df
            self.loaded_data[self._canonical_attr(symbol)] = dl.df
            self.loaded_data[self._canonical_symbol(symbol)] = dl.df

            # except Exception as e:
            #     print(f"[Bootstrap] {symbol} 初次补齐失败：{e}")

    # ------------------------------- 语义变更集合 -----------------------------
    def _collect_semantic_changes(self, graph: Dict[str, List[str]]) -> Set[str]:
        changed: Set[str] = set()
        for path in self._iter_loader_files():
            symbol = self._symbol_from_path(path)
            deps = graph.get(symbol, [])
            cur_hash = self._compute_semantic_hash(path, deps)
            prev_hash = self._get_latest_metadata(symbol).get('semantic_hash')
            if prev_hash and prev_hash != cur_hash:
                changed.add(symbol)
        return changed

    # ------------------------------- 外部辅助接口 ------------------------------
    def get_loaded(self, symbol: str):
        """
        依次尝试多把键：原样 → 规范化（去 .pkl）→ 安全属性名 → 安全持久化名，
        返回第一个非 None 的 DataFrame；不进行任何布尔运算，避免触发 pandas 的 __or__/truthiness。
        """
        keys = [
            symbol,
            self._normalize_symbol(symbol),
            self._canonical_attr(symbol),
            self._canonical_symbol(symbol),
        ]
        for k in keys:
            if k in self.loaded_data:
                v = self.loaded_data[k]
                if v is not None:
                    return v
        return None

    def close(self):
        """
        显式释放本 DataManager 对 Arctic 的引用。
        当该 LMDB 路径的引用计数归零时，尽力关闭底层 Arctic 句柄。
        """
        path = getattr(self, "arctic_local_dir", None)
        store = getattr(self, "store", None)
        if not path or not store:
            return

        # 引用计数减一
        if path in DataManager._ARCTIC_REFCOUNT:
            DataManager._ARCTIC_REFCOUNT[path] -= 1
            if DataManager._ARCTIC_REFCOUNT[path] <= 0:
                # 最后一个引用，关闭并移除缓存
                try:
                    if hasattr(store, "close"):
                        store.close()  # 某些版本提供 close()
                except Exception:
                    pass
                DataManager._ARCTIC_CACHE.pop(path, None)
                DataManager._ARCTIC_REFCOUNT.pop(path, None)

        # 断开本实例引用，利于 GC
        self.store = None
        self.library = None

    def list_arctic_symbols(self) -> List[str]:
        return sorted(self._list_symbols())

    def describe_arctic(self, data_dir: Optional[str] = None, max_cols: int = 10) -> str:
        """
        描述当前目录（LMDB）的 ArcticDB 内容，返回一段可读字符串。
        注意：同一进程内不可对同一 LMDB 路径创建多个 Arctic 实例。
        """
        from datetime import datetime

        def _safe_iso(ts):
            if ts is None:
                return "-"
            try:
                if isinstance(ts, (int, float)):
                    return datetime.utcfromtimestamp(ts).isoformat() + "Z"
                return str(ts)
            except Exception:
                return "-"

        # 规范化路径，避免 /a/b 与 /a/b/ 被认为不同
        def _norm_path(p: str) -> str:
            return os.path.normpath(os.path.abspath(p))

        target_dir = data_dir or getattr(self, "arctic_local_dir", os.path.join(os.getcwd(), "data"))
        target_dir_n = _norm_path(target_dir)

        lines: List[str] = []
        if not os.path.exists(target_dir):
            return f"[ERR] data 目录不存在：{target_dir}"

        uri = f"lmdb://{target_dir}"
        lines.append(f"[INFO] 连接 ArcticDB：{uri}")

        # ✅ 优先复用：1) 当前实例已打开且路径一致
        store = None
        try:
            cur_dir = getattr(self, "arctic_local_dir", None)
            if cur_dir and _norm_path(cur_dir) == target_dir_n and getattr(self, "store", None) is not None:
                store = self.store
        except Exception:
            store = None

        # ✅ 复用：2) DataManager 进程级缓存里已有该路径
        if store is None:
            try:
                for k, v in DataManager._ARCTIC_CACHE.items():
                    if _norm_path(k) == target_dir_n:
                        store = v
                        break
            except Exception:
                store = None

        # ⚠️ 最后才尝试新建（仅当该路径在本进程未打开过）
        created_here = False
        if store is None:
            try:
                store = Arctic(uri)
                created_here = True
            except Exception as e:
                out = "\n".join(lines + [f"[ERR] 无法连接 ArcticDB：{uri}，原因：{e}"])
                print(out)
                return out

        # 列出库
        try:
            libraries = list(store.list_libraries())
        except Exception as e:
            lines.append(f"[WARN] 无法列出库（list_libraries 失败）：{e}")
            libraries = []

        if not libraries:
            lines.append("[INFO] 未检测到库。请确认库是否已创建，以及当前进程未重复打开同一路径。")
            if created_here:
                try:
                    if hasattr(store, "close"):
                        store.close()
                except Exception:
                    pass
            out = "\n".join(lines)
            print(out)
            return out

        for lib_name in libraries:
            lines.append(f"\n=== Library: {lib_name} ===")
            try:
                lib = store[lib_name]
            except Exception as e:
                lines.append(f"[ERR] 无法打开库 {lib_name}: {e}")
                continue

            try:
                symbols = list(lib.list_symbols())
            except Exception as e:
                lines.append(f"[ERR] 无法列出库 {lib_name} 的 symbols: {e}")
                continue

            if not symbols:
                lines.append("  (空库，无 symbol)")
                continue

            for sym in sorted(symbols):
                try:
                    # 版本统计
                    try:
                        versions = list(lib.list_versions(sym))
                        vcount = len(versions)
                        last_version = versions[-1] if versions else None
                        last_version_id = getattr(last_version, "version", None)
                        last_created_at = getattr(last_version, "creation_ts", None)
                    except Exception:
                        vcount = -1
                        last_version_id = None
                        last_created_at = None

                    item = lib.read(sym)
                    df = item.data
                    meta = dict(item.metadata or {})

                    shape = getattr(df, "shape", None)
                    cols = list(getattr(df, "columns", []))
                    cols_preview = cols[:max_cols]
                    tail = " ..." if len(cols) > max_cols else ""

                    lines.append(f"- {sym}")
                    lines.append(f"    versions      : {vcount} (latest={last_version_id})")
                    lines.append(f"    latest_written: {_safe_iso(last_created_at)}")
                    lines.append(f"    shape         : {shape}")
                    lines.append(f"    columns       : {cols_preview}{tail}")
                    lines.append(f"    metadata keys : {list(meta.keys())}" if meta else f"    metadata      : {{}}")

                except Exception as e:
                    lines.append(f"  [ERR] 读取 symbol '{sym}' 失败：{e}")

        # 如果是本函数新建的 store，尽力关闭；复用的不要关（交给引用计数/持有者）
        if created_here:
            try:
                if hasattr(store, "close"):
                    store.close()
            except Exception:
                pass

        out = "\n".join(lines)
        print(out)
        return out



if __name__ == "__main__":
    # 示例：按固定阈值策略运行；若希望 ≤10 分钟时自动执行，可设置环境变量 DM_AUTO_APPROVE_SHORT=1
    # dm = DataManager(target_symbols=["prices", "features"], project_path="/path/to/project")
    pass