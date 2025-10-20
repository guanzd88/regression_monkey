from __future__ import annotations

"""
Baseline 组合器（Baseline Explorer / BaselineSpec Generator）

目标：在不执行任何回归的前提下，系统性地从多个因变量、解释变量与控制变量的组合中
生成、筛选并组织 baseline（基线）回归规格，为后续机制、异质性与稳健性分析提供统一入口。

— 设计要点 —
1) 系统性探索：支持 Y/X/controls/FE/model/options 的多维组合与多种组合模式
2) 筛选而非执行：仅做数据体检与经济学约束过滤，打分与排序
3) 分层衔接：输出 BaselineSpec 对象，可转 StandardRegTask 以衔接 PaperSections
4) 灵活可控：limit、scoring_rules、人工挑选标记等

本文件包含：
- 数据类：StandardRegTask, BaselineSpec
- 组合器：BaselineExplorer（核心）
- 枚举工具函数：powerset, choose_k_subsets, cartesian, lockstep_zip, expand_options_grid
- 数据体检：缺失率、样本量、方差/唯一值、winsorize 预检（不改变原数据）
- 输出接口：materialize, to_dataframe, to_tasks
- 最小可运行示例：见 __main__
"""

from dataclasses import dataclass, field, asdict
from reg_monkey.task_obj import StandardRegTask
from typing import List, Dict, Any, Iterable, Optional, Tuple, Callable
import pandas as pd
import numpy as np
import hashlib,itertools,math

# =============================
# 数据类定义
# =============================

@dataclass
class BaselineSpec:
    """标准化的 Baseline 规格对象（探索阶段产物）"""
    y: str
    X: List[str]
    controls: List[str]
    category_controls: List[str]
    model: str
    subset: Optional[Dict[str, Any]]
    options: Dict[str, Any]
    spec_id: str
    score: Optional[float] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, Any] = field(default_factory=dict)  # e.g., {"is_primary": False, "is_candidate": True}

    def to_task(self, base_task: StandardRegTask, name_suffix: Optional[str] = None) -> StandardRegTask:
        name = base_task.name
        if name_suffix:
            name = f"{name}_{name_suffix}"
        return StandardRegTask(
            name=name,
            dataset=base_task.dataset,
            y=self.y,
            X=self.X,
            controls=self.controls,
            category_controls=self.category_controls,
            model=self.model,
            options=self.options.copy(),
        )

# =============================
# 组合与工具函数
# =============================

def powerset(iterable: Iterable[Any]) -> Iterable[Tuple[Any, ...]]:
    """幂集（包含空集），例如 [a,b,c] -> (), (a), (b), (c), (a,b), (a,c), (b,c), (a,b,c)"""
    items = list(iterable)
    for r in range(len(items) + 1):
        for combo in itertools.combinations(items, r):
            yield combo

def choose_k_subsets(iterable: Iterable[Any], k: int, at_most: bool = False) -> Iterable[Tuple[Any, ...]]:
    """选择固定 k 或最多 k 个元素的子集。"""
    items = list(iterable)
    if at_most:
        for r in range(0, min(k, len(items)) + 1):
            yield from itertools.combinations(items, r)
    else:
        if k <= len(items):
            yield from itertools.combinations(items, k)

def cartesian(dict_lists: Dict[str, List[str]]) -> Iterable[Dict[str, str]]:
    """对 {var_name: [alt1, alt2, ...]} 做笛卡尔积，返回每个 var 对应一次选择的映射。"""
    keys = list(dict_lists.keys())
    if not keys:
        yield {}
        return
    arrays = [dict_lists[k] for k in keys]
    for combo in itertools.product(*arrays):
        yield {k: v for k, v in zip(keys, combo)}

def lockstep_zip(dict_lists: Dict[str, List[str]]) -> Iterable[Dict[str, str]]:
    """锁步组合：按索引对齐，如 {A:[A1,A2], B:[B1,B2]} -> {A:A1,B:B1}, {A:A2,B:B2}
    若长度不等，采用最短长度对齐。"""
    if not dict_lists:
        yield {}
        return
    keys = list(dict_lists.keys())
    min_len = min(len(v) for v in dict_lists.values())
    for i in range(min_len):
        yield {k: dict_lists[k][i] for k in keys}

def expand_options_grid(options_grid: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """将 options_grid 视作独立的选项组合列表；若为 None/空，则返回 [ {} ] 代表默认空选项。"""
    if not options_grid:
        return [{}]
    # 允许传入的 options_grid 是：
    # 1) 已经是列表，每个元素就是一个 options 字典
    # 2) 或者是一个“网格”字典，需要笛卡尔展开（例如 {"winsorize_percent": [0.01,0.02], "cluster":["firm","industry"]}）
    if isinstance(options_grid, dict):
        keys = list(options_grid.keys())
        values = [options_grid[k] for k in keys]
        result = []
        for combo in itertools.product(*values):
            result.append({k: v for k, v in zip(keys, combo)})
        return result
    return options_grid

def stable_hash(obj: Any) -> str:
    """对任意对象生成稳定的短哈希（用于 spec_id）。"""
    raw = pd.util.hash_pandas_object(pd.Series([str(obj)]), index=False).astype(str).iloc[0]
    # 再做一遍 md5 缩短
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

# =============================
# BaselineExplorer 组合器
# =============================

class BaselineExplorer:
    """
    Baseline 组合器（Baseline Explorer）

    用途
    ----
    在 **不执行任何回归** 的前提下，从因变量 Y / 自变量替代测度 X / 控制变量 / 固定效应 /
    模型 / 子集 / 选项 的多维组合里，系统性生成“baseline 规格（BaselineSpec）”，
    作为后续机制、异质性与稳健性分析的统一入口。产物既可导出成表，也可转为
    `StandardRegTask` 以接入你的回归管线。

    快速上手
    -------
    >>> explorer = BaselineExplorer(
    ...     df=my_df,
    ...     base_task=base_task,                      # 一个 StandardRegTask 模板（提供 dataset/name 等）
    ...     Ys=["ROA", "ROE"],                        # 不填则默认 [base_task.y]
    ...     X_alternatives={"Size": ["Size_ln","Size_pctile"], "Leverage": ["Lev_mkt","Lev_book"]},
    ...     controls_pool=["Age","Growth","CashFlow"],
    ...     category_controls_pool=["Industry","Year"],
    ...     X_mode="cartesian",                       # 见“组合策略”
    ...     controls_mode="fixed",
    ...     model_list=["OLS","FE"],
    ...     options_grid=[{"cluster":"firm"}],        # 也可传字典做网格展开
    ...     limit=200                                 # 强烈建议设置以防组合爆炸
    ... )
    >>> specs = explorer.materialize()                # -> List[BaselineSpec]
    >>> df_specs = explorer.to_dataframe()            # -> pd.DataFrame（查看/筛选/存盘）
    >>> tasks = explorer.to_tasks(top_n=5, name_suffix="baseline")  # -> List[StandardRegTask]

    关键概念
    --------
    * **BaselineSpec**：单个规格（y/X/controls/category_controls/model/subset/options/spec_id）。
    * **base_task**：母体任务模板；转回归任务时会沿用其 dataset/name/options 等。

    构造参数
    -------
    df : pd.DataFrame
        用于枚举与（可选的）体检的数据表。
    base_task : StandardRegTask
        模板任务对象（提供默认 y/X/controls/category_controls/model/options）。
    Ys : list[str] | None
        备选因变量；默认 `[base_task.y]`。
    X_alternatives : dict[str, list[str]] | None
        自变量族及替代测度映射，如 {"Size": ["Size_ln","Size_pctile"]}。
    controls_pool : list[str] | None
        控制变量全集；默认 `base_task.controls`。
    category_controls_pool : list[str] | None
        固定效应来源列全集；默认 `base_task.category_controls`。
    subset_list : list[dict] | None
        异质性/子集设置（如 {"name":"HighLev","expr":"HighLev==1"}）；默认 `[None]`。
    X_mode : {"cartesian","lockstep","one_at_a_time"}
        X 组合策略：笛卡尔 / 按索引锁步 / 逐一替换（其余取默认）。
    controls_mode : {"fixed","powerset","choose_k","at_most_k"}
        控制变量组合策略；后两者需配合 `controls_k`。
    controls_k : int | None
        与上面策略配套的 k。
    fe_mode : {"fixed","powerset"}
        固定效应组合策略。
    model_list : list[str] | None
        备选模型名；默认 `[base_task.model]`。
    options_grid : list[dict] | dict[str, list[Any]] | None
        运行选项；可直接给列表，也可给字典做笛卡尔展开。
    limit : int | None
        生成上限；用于控制组合规模。
    scoring_rules : list[Callable] | None
        评分规则（当前实现保留接口但已不再使用）。

    公开方法
    --------
    materialize(limit: int | None = None) -> list[BaselineSpec]
        生成并缓存全部组合（遵循 `limit`）；再次调用直接返回缓存。
    to_dataframe() -> pd.DataFrame
        将已 materialize 的组合导出为结构化表（含 spec_id / 诊断等信息）。
    to_tasks(top_n: int | None = None, name_suffix: str | None = None) -> list[StandardRegTask]
        将（前 `top_n` 个）规格转为回归任务，便于接入下游执行。

    组合策略速览
    -----------
    * X_mode="cartesian"     ：对每个自变量族取笛卡尔积（最全，但规模最大）
    * X_mode="lockstep"      ：按索引锁步匹配（各列表长度对齐，取最短）
    * X_mode="one_at_a_time" ：在默认选择的基础上逐一替换某一族

    注意事项
    --------
    * 当前 `_diagnose()` 恒返回 `(True,{})`，即不做缺失率/样本量/方差等真实体检，仅保留接口。
    * `spec_id` 由稳定哈希生成；相同内容的规格应得到一致 ID。
    * 真实项目中请务必设置 `limit`，并谨慎选择组合策略以避免内存/时间开销。
    """
    def __init__(
        self,
        df: pd.DataFrame,
        base_task: StandardRegTask,
        Ys: Optional[List[str]] = None,
        X_alternatives: Optional[Dict[str, List[str]]] = None,
        controls_pool: Optional[List[str]] = None,
        category_controls_pool: Optional[List[str]] = None,
        subset_list: Optional[List[Dict[str, Any]]] = None,
        *,
        # 组合策略
        X_mode: str = "cartesian",  # "cartesian" | "lockstep" | "one_at_a_time"
        controls_mode: str = "fixed",  # "fixed" | "powerset" | "choose_k" | "at_most_k"
        controls_k: Optional[int] = None,
        fe_mode: str = "fixed",  # "fixed" | "powerset"
        model_list: Optional[List[str]] = None,
        options_grid: Optional[List[Dict[str, Any]] | Dict[str, List[Any]]] = None,
        # 输出控制
        limit: Optional[int] = None,
        scoring_rules: Optional[List[Callable[[Dict[str, Any]], float]]] = None,
    ) -> None:
        self.df = df
        self.base_task = base_task
        self.Ys = Ys if Ys else [base_task.y]
        self.X_alternatives = X_alternatives or {x: [x] for x in base_task.X}
        self.controls_pool = controls_pool if controls_pool is not None else base_task.controls
        self.category_controls_pool = category_controls_pool if category_controls_pool is not None else base_task.category_controls
        self.subset_list = subset_list or [None]
        self.X_mode = X_mode
        self.controls_mode = controls_mode
        self.controls_k = controls_k
        self.fe_mode = fe_mode
        self.model_list = model_list or [base_task.model]
        self.options_grid = expand_options_grid(options_grid)
        self.limit = limit
        self.scoring_rules = scoring_rules or [self.default_scoring_rule]
        self._materialized: List[BaselineSpec] = []

    # ---------- 公开接口 ----------
    def materialize(self, limit: Optional[int] = None) -> List[BaselineSpec]:
        """生成符合约束的 BaselineSpec 列表。limit 为上限阈值，防止组合爆炸。"""
        if self._materialized:
            return self._materialized

        limit = self.limit if limit is None else limit
        produced = 0
        specs: List[BaselineSpec] = []

        # 1) 枚举 X 组合
        X_combos: List[List[str]] = list(self._enumerate_X())
        # 2) 枚举 controls 组合
        control_combos: List[List[str]] = list(self._enumerate_controls())
        # 3) 枚举 FE 组合
        fe_combos: List[List[str]] = list(self._enumerate_fes())

        for y, x_vars, ctrls, fes, model, subset, opts in itertools.product(
            self.Ys, X_combos, control_combos, fe_combos, self.model_list, self.subset_list, self.options_grid
        ):
            if limit is not None and produced >= limit:
                break

            # 体检（不修改原数据）
            diag_ok, diagnostics = self._diagnose(y, x_vars, ctrls, fes, subset, opts)
            if not diag_ok:
                continue

            spec_dict = {
                "y": y,
                "X": tuple(x_vars),
                "controls": tuple(ctrls),
                "category_controls": tuple(fes),
                "model": model,
                "subset": subset,
                "options": tuple(sorted(opts.items())) if opts else None,
            }
            spec_id = stable_hash(spec_dict)
            # 不再计算得分：score 始终为 None
            spec = BaselineSpec(
                y=y,
                X=x_vars,
                controls=ctrls,
                category_controls=fes,
                model=model,
                subset=subset,
                options=opts,
                spec_id=spec_id,
                diagnostics=diagnostics,
            )
            specs.append(spec)
            produced += 1

        # 不再按 score 排序，保持生成顺序
        self._materialized = specs
        return specs

    def to_dataframe(self) -> pd.DataFrame:
        """导出所有 baseline 组合为结构化表格。若尚未 materialize，会先生成。"""
        if not self._materialized:
            self.materialize()
        rows = []
        for s in self._materialized:
            row = {
                "spec_id": s.spec_id,
                "y": s.y,
                "X": ",".join(s.X),
                "controls": ",".join(s.controls),
                "category_controls": ",".join(s.category_controls),
                "model": s.model,
                "subset": str(s.subset) if s.subset else None,
                **{f"opt.{k}": v for k, v in (s.options or {}).items()},
                **{f"diag.{k}": v for k, v in s.diagnostics.items()},
            }
            rows.append(row)
        return pd.DataFrame(rows)

    def to_tasks(self, top_n: Optional[int] = None, name_suffix: Optional[str] = None) -> List[StandardRegTask]:
        """将（前 top_n 个）BaselineSpec 转为 StandardRegTask。"""
        if not self._materialized:
            self.materialize()
        specs = self._materialized if top_n is None else self._materialized[:top_n]
        return [s.to_task(self.base_task, name_suffix=name_suffix) for s in specs]

    # ---------- 内部：枚举 ----------
    def _enumerate_X(self) -> Iterable[List[str]]:
        # X_mode: cartesian / lockstep / one_at_a_time
        mapping = self.X_alternatives
        if self.X_mode == "cartesian":
            for m in cartesian(mapping):
                yield list(m.values())
        elif self.X_mode == "lockstep":
            for m in lockstep_zip(mapping):
                yield list(m.values())
        elif self.X_mode == "one_at_a_time":
            # 逐一替换一个自变量：其余取第一个测度
            keys = list(mapping.keys())
            base_choice = {k: mapping[k][0] for k in keys}
            # 同时包含“全部默认”的一种
            yield list(base_choice.values())
            for k in keys:
                for alt in mapping[k]:
                    if alt == base_choice[k]:
                        continue
                    choice = base_choice.copy()
                    choice[k] = alt
                    yield list(choice.values())
        else:
            raise ValueError(f"Unknown X_mode: {self.X_mode}")

    def _enumerate_controls(self) -> Iterable[List[str]]:
        pool = self.controls_pool or []
        if self.controls_mode == "fixed":
            yield list(pool)
        elif self.controls_mode == "powerset":
            for combo in powerset(pool):
                yield list(combo)
        elif self.controls_mode in {"choose_k", "at_most_k"}:
            if self.controls_k is None:
                raise ValueError("controls_k must be set when controls_mode is 'choose_k' or 'at_most_k'")
            at_most = self.controls_mode == "at_most_k"
            for combo in choose_k_subsets(pool, self.controls_k, at_most=at_most):
                yield list(combo)
        else:
            raise ValueError(f"Unknown controls_mode: {self.controls_mode}")

    def _enumerate_fes(self) -> Iterable[List[str]]:
        pool = self.category_controls_pool or []
        if self.fe_mode == "fixed":
            yield list(pool)
        elif self.fe_mode == "powerset":
            for combo in powerset(pool):
                yield list(combo)
        else:
            raise ValueError(f"Unknown fe_mode: {self.fe_mode}")

    # ---------- 内部：体检与打分 ----------
    def _diagnose(
        self,
        y: str,
        X: List[str],
        controls: List[str],
        fes: List[str],
        subset: Optional[Dict[str, Any]],
        options: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        体检已移除：始终接受组合，不做缺失率/样本量/方差/唯一值等数据检查。
        该函数仅用于保持接口一致，永远返回 (True, {}).
        """
        return True, {}

    def default_scoring_rule(self, ctx: Dict[str, Any]) -> float:
        """[已废弃] 评分已被移除；此函数仅为兼容保留，不再被调用。"""
        return 0.0

    def _score_spec(self, ctx: Dict[str, Any]) -> float:
        return float(sum(rule(ctx) for rule in self.scoring_rules))

# =============================
# 最小使用示例（合成数据 + 快速枚举）
# =============================
if __name__ == "__main__":
    # 构造一个玩具数据集
    rng = np.random.default_rng(48)
    n = 1200
    df = pd.DataFrame({
        "ROA": rng.normal(0.05, 0.02, n),
        "ROE": rng.normal(0.12, 0.05, n),
        "TobinQ": rng.lognormal(mean=0.1, sigma=0.3, size=n),
        # X 替代测度
        "Size_ln": rng.normal(8.0, 1.2, n),
        "Size_pctile": rng.uniform(0, 1, n),
        "Lev_mkt": rng.uniform(0, 1, n),
        "Lev_book": rng.uniform(0, 1, n),
        # 控制
        "Age": rng.integers(1, 50, n),
        "Growth": rng.normal(0.08, 0.03, n),
        "CashFlow": rng.normal(0.1, 0.04, n),
        "Capex": rng.normal(0.06, 0.02, n),
        "Liquidity": rng.normal(0.5, 0.1, n),
        # 固定效应来源列（例如行业/年份代码，后续由下游转换为哑变量）
        "Industry": rng.integers(1, 10, n),
        "Year": rng.integers(2015, 2021, n),
        # 异质性子集所需列
        "HighLev": (rng.uniform(0, 1, n) > 0.5).astype(int),
        "Post_MA": (rng.uniform(0, 1, n) > 0.7).astype(int),
    })

    # 定义基础任务模板（可看作论文任务母体）
    base = StandardRegTask(
        name="explore_demo",
        dataset="A",
        y="ROA",
        X=["Size_ln"],
        controls=[],
        category_controls=["Industry", "Year"],
        model="OLS",
        options={"cluster": "firm"},
        if_reprep=True
    )

    # 异质性子集定义举例
    subset_list = [
        None,
        {"name": "HighLev", "expr": "HighLev == 1"},
        {"name": "Post_MA", "expr": "Post_MA == 1"},
    ]

    explorer = BaselineExplorer(
        df=df,
        base_task=base,
        Ys=["ROA", "ROE", "TobinQ"],
        X_alternatives={
            "Size": ["Size_ln", "Size_pctile"],
            "Leverage": ["Lev_mkt", "Lev_book"],
        },
        controls_pool=["Age", "Growth", "CashFlow", "Capex", "Liquidity"],
        category_controls_pool=["Industry", "Year"],
        # 组合策略
        X_mode="cartesian",
        # controls_mode="at_most_k",
        controls_k=3,
        fe_mode="fixed",
        model_list=["OLS", "FE"],
        options_grid=[
            {"winsorize_percent": 0.02, "cluster": "Age"},
        ],
        limit=500,  # 防止组合爆炸
    )

    specs = explorer.materialize()
    print(f"Generated {len(specs)} specs. Top-5 by score:")
    for s in specs[:5]:
        print(s.spec_id, s.score, s.y, s.X, s.controls, s.category_controls, s.model, s.subset, s.options)

    df_specs = explorer.to_dataframe()
    print("\nDataFrame preview:\n", df_specs.head(10))

    tasks_top3 = explorer.to_tasks(top_n=3, name_suffix="baseline")
    print("\nTasks (top-3):")
    for t in tasks_top3:
        print(asdict(t))