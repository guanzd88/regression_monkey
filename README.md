# regmonkey

面向“回归类研究（计量/实证）”的一体化流水线：**数据加载 → 依赖追踪/重算 → 代码生成（R/Stata/其他）→ 执行与标准化产出**。

> 目标是把「一次性的分析脚本」升级为**可重现、可复用、可审计**的工作流。

## 功能一览

- **DataLoader**：极简数据加载/清洗基类；一个 DataLoader 对应一个“产物”（如某个 DataFrame / PKL / ArcticDB 表）。
- **DataManager**：统一的加载/重算/持久化调度器；支持 *ArcticDB*、本地 PKL、以及动态导入的 DataLoader，带**语义指纹**与**依赖传播**。
- **StandardRegTask**：标准回归任务对象，描述变量、模型类型、固定效应、聚类、PSM 等；可序列化与指纹化。
- **CodeGenerator**：基于 `jinja2` 的模板渲染器，把 `StandardRegTask` 生成 **R**（或其他语言）脚本；自动汇总依赖包并插入安装/加载段。
- **CodeExecutor**：任务树执行编排器（对接 `rpy2` 等）并产出标准化结果。
- **Planner**：树状任务编排（章节/标签/节点），便于结构化地组织回归组。

## 安装

### 方式一：本地安装（开发）
```bash
git clone <your-fork-or-path>/regmonkey
cd regmonkey
pip install regression_monkey ".[dev]"
```
### 方式二：从源码打包安装
```bash
pip install regression_monkey
python -m build
pip install regression_monkey
```

> 需要的关键依赖：`pandas`, `numpy`, `jinja2`, `rpy2`, `arcticdb`（可选）, `pyyaml`。详见下方 `pyproject.toml`。

## 快速上手

### 1) 定义一个 DataLoader
```python
from reg_monkey import DataLoader
import pandas as pd

class MyUsersLoader(DataLoader):
    output_pkl_name = "users.pkl"
    dependency = ["raw/users.csv"]

    def clean_data(self):
        df = pd.read_csv("raw/users.csv")
        # minimal cleaning …
        df = df.dropna(subset=["id"]).rename(columns={"signup_time":"ts"})
        self.df = df
        return df
```

### 2) 使用 DataManager 读取/重算
```python
from reg_monkey import DataManager
dm = DataManager(project_root=".", arctic_uri=None)   # 无 ArcticDB 时可为 None

# 首次会动态导入并执行 DataLoader，之后按优先级命中缓存/PKL/ArcticDB
users = dm.get("users.pkl", loader_module="my_loaders.users_loader")
```

### 3) 声明一个回归任务并生成 R 代码
```python
from reg_monkey import StandardRegTask, CodeGenerator, PublicConfig

task = StandardRegTask(
    task_id="T1",
    data_key="users.pkl",
    y="y",
    X=["x1","x2","x3"],
    model="OLS",
    fe=["industry","year"],
    cluster="firm_id"
)

cg = CodeGenerator(public_config=PublicConfig())
code = cg.render(task)      # R 脚本字符串
open("out/T1.R","w",encoding="utf-8").write(code)
```

### 4) 执行（可选，依赖 rpy2）
```python
from reg_monkey import CodeExecutor
executor = CodeExecutor(r_home=None)   # 如需，设置 R_HOME
res = executor.run_script_text(code)   # 返回标准化的结果字典/表
```

## 设计要点

- **三源优先级与回退**：ArcticDB ↔ DataLoader(动态导入) ↔ PKL；失败自动回退。
- **语义指纹与依赖传播**：对 `clean_data()` AST 与依赖列表做哈希；变动即触发重算，并沿反向依赖闭包传播。
- **预算与交互**：基于历史耗时估算链路成本；可设阈值区分“自动/需确认”的策略。
- **标准化结果**：把多模型的输出（系数、稳健性、PSM、Heckman 等）统一为结构化表格，便于对比/制表。

## 项目目录结构（建议）

```
├── config.json （项目配置文件）
├── data (存放arcticdb数据文件，自动生成无需手工建立)
├── data_loader
│   ├── A.py
│   ├── B1.py
│   ├── B2.py
│   ├── C.py
│   ├── D.py
│   ├── fuck.py
│   ├── performance.py
├── source_data （存放原始数据，必须以pkl格式存储）
│   ├── 185.pkl
│   ├── 286.pkl
│   ├── A.pkl
│   ├── B1.pkl
│   ├── B2.pkl
│   ├── C.pkl
│   └── D.pkl
├── main.py
```
## 配置

项目根目录放置 `config.json`：
```jsonc
{
    "project_name": "p1",
    "project_description": "Please add your project description here",
    "language": "R",
    "significance_level": 0.01
}
```

## 运行示例（最小）

```python
# -*- coding: utf-8 -*-
"""
测试 CodeExecutor 与 BaselineExplorer/Plan 的对接
用法：
    python test_code_executor_baseline.py
"""

from __future__ import annotations

import sys
import hashlib
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# === 你的工程内对象 ===
from reg_monkey.task_obj import StandardRegTask
from reg_monkey.planner import Plan
from reg_monkey.baseline_spec import BaselineExplorer

# === 我们实现的执行器（上一条消息给出的文件） ===
from reg_monkey.code_executor import CodeExecutor, ResultNotFoundError, ExecutionRuntimeError

# ---------- 测试数据集（与你给的示例一致） ----------
def make_toy_df(n: int = 1200, seed: int = 48) -> pd.DataFrame:
    n_firms=200
    years=range(2015, 2021)
    seed=42
    rng = np.random.default_rng(seed)
    T = len(years)

    # --- Firm-level 常量/慢变量 ---
    firm_ids = [f"F{idx:04d}" for idx in range(1, n_firms + 1)]
    industry = rng.integers(1, 11, n_firms)                 # 1..10，时间不变
    size_base = rng.normal(8.0, 0.6, n_firms)               # firm 平均规模
    profitability_base = rng.normal(0.05, 0.015, n_firms)   # firm 固定效应（ROA 水平差异）
    lev_propensity = rng.uniform(0.35, 0.65, n_firms)       # 倾向（决定 HighLev 的长期均值）

    # --- 年份冲击（可做年固定效应用） ---
    year_effect = {y: rng.normal(0.0, 0.004) for y in years}

    rows = []
    for i, fid in enumerate(firm_ids):
        ind = industry[i]
        size_level = size_base[i]
        roa_level = profitability_base[i]

        # HighLev：有持久性（马尔可夫转移），并保证每个公司至少 2 个年份为 1
        p_stay = 0.80      # 前一年的状态持续的概率
        p_switch_up = 0.35 # 从 0 切到 1 的概率（当不持续时）
        p_switch_dn = 0.35 # 从 1 切到 0 的概率（当不持续时）

        # 初始状态按 firm 倾向
        h = 1 if rng.random() < lev_propensity[i] else 0
        highlev_series = []
        for t, y in enumerate(years):
            if t > 0 and rng.random() > p_stay:
                if h == 0 and rng.random() < p_switch_up:
                    h = 1
                elif h == 1 and rng.random() < p_switch_dn:
                    h = 0
            highlev_series.append(h)

        # 若为 1 的年份少于 2，强制补足（确保 subset==1 时每个公司至少 2 期）
        ones_idx = [k for k, v in enumerate(highlev_series) if v == 1]
        if len(ones_idx) < 2:
            need = 2 - len(ones_idx)
            cand = [k for k in range(T) if k not in ones_idx]
            flip_idx = rng.choice(cand, size=need, replace=False)
            for k in np.atleast_1d(flip_idx):
                highlev_series[k] = 1

        # 生成逐年观测
        size_it = size_level
        roe_shock = rng.normal(0.12, 0.02)  # firm 水平差异
        for t, y in enumerate(years):
            # Size_ln：带 AR(1) 波动，保证“组内变动”存在
            size_it = 0.85 * size_it + 0.15 * (size_base[i] + rng.normal(0, 0.25))
            size_ln = size_it

            # 其他控制，含 firm 水平 + 年冲击 + idiosyncratic
            growth = rng.normal(0.08, 0.02) + year_effect[y] + rng.normal(0, 0.01)
            cashflow = rng.normal(0.10, 0.02) + rng.normal(0, 0.01)
            capex = rng.normal(0.06, 0.015) + rng.normal(0, 0.005)
            liquidity = rng.normal(0.5, 0.08) + rng.normal(0, 0.02)
            lev_mkt = np.clip(rng.beta(2, 3), 0, 1)
            lev_book = np.clip(rng.beta(2, 3), 0, 1)

            # 产出变量（与 size、growth、year FE、firm FE 挂钩）
            roa = (
                roa_level
                + 0.004 * (size_ln - size_base[i])            # 组内：规模上升少许提高 ROA
                + 0.20 * (growth - 0.08)                      # 成长偏离对 ROA 的贡献
                + year_effect[y]                              # 年度冲击（便于 Year FE 吸收）
                + rng.normal(0, 0.01)                         # 干扰项
            )
            roe = roe_shock + 0.6 * (roa - roa_level) + rng.normal(0, 0.02)

            # 事件窗口收益类，跟成长/规模/年冲击相关
            car1 = 0.02 + 0.02 * (growth - 0.08) + 0.003 * (size_ln - size_base[i]) + rng.normal(0, 0.03)
            car2 = 0.03 + 0.03 * (growth - 0.08) + 0.004 * (size_ln - size_base[i]) + rng.normal(0, 0.03)

            # 你原先有个列名叫 "Fuck"，保持以免下游任务崩；令其为噪声＋轻微与 size 正相关
            fuck = 0.10 + 0.005 * (size_ln - size_base[i]) + rng.normal(0, 0.03)

            # TobinQ：对数正态+与 growth 轻微正相关
            tobinq = np.exp(rng.normal(0.1, 0.25)) * (1 + 0.05 * (growth - 0.08))

            # Post_MA：设为 2018 年及以后更可能为 1（年内仍带随机）
            post_ma = 1 if (y >= 2018 and rng.random() < 0.6) else (1 if rng.random() < 0.2 else 0)

            rows.append({
                "Firm": fid,
                "Year": y,
                "Industry": ind,
                "Size_ln": float(size_ln),
                "Size_pctile": float(rng.uniform(0, 1)),
                "Lev_mkt": float(lev_mkt),
                "Lev_book": float(lev_book),
                "Age": int(rng.integers(1, 50)),
                "Growth": float(growth),
                "CashFlow": float(cashflow),
                "Capex": float(capex),
                "Liquidity": float(liquidity),
                "ROA": float(roa),
                "ROE": float(roe),
                "CAR1": float(car1),
                "CAR2": float(car2),
                "Fuck": float(fuck),
                "TobinQ": float(tobinq),
                "HighLev": int(highlev_series[t]),
                "Post_MA": int(post_ma),
            })

    df = pd.DataFrame(rows)

    # 一些合理边界裁剪（避免极端值）
    df["ROA"] = df["ROA"].clip(-0.5, 0.5)
    df["ROE"] = df["ROE"].clip(-1.0, 1.0)
    return df

# ---------- 兜底装配：缺字段/模板时的最小可执行补丁 ----------
R_DEFAULT_DEPENDENCIES = ["dplyr", "broom", "fixest"]

R_DEFAULT_PREP = r"""
# 最简单的“清洗”占位：确保存在 input_r_df 变量即可
input_r_df <- input_r_df
"""

def ensure_task_minimums(task: StandardRegTask, default_fp: Optional[str] = None) -> None:
    """
    - 若缺 dependencies，则补上常用包
    - 若缺 prep_fingerprint，则按 Y/X/controls 计算一个
    - 若缺 code_text['prep']，则放入极简清洗段（不会改动数据）
    - 若缺 cg.TEMPLATE_MAP['r'] 的安装片段，执行器会回退到内置 install_missing()
    """
    if not hasattr(task, "code_text") or task.code_text is None:
        task.code_text = {}

    # 依赖
    deps = task.code_text.get("dependencies") or []
    if not isinstance(deps, list) or len(deps) == 0:
        task.code_text["dependencies"] = list(R_DEFAULT_DEPENDENCIES)

    # 清洗段
    if not task.code_text.get("prep"):
        task.code_text["prep"] = R_DEFAULT_PREP

    # 指纹
    if not getattr(task, "prep_fingerprint", None):
        fp_src = f"{getattr(task,'y',None)}|{getattr(task,'X',None)}|{getattr(task,'controls',None)}"
        hexdigest = hashlib.md5(fp_src.encode("utf-8")).hexdigest()[:10]
        setattr(task, "prep_fingerprint", default_fp or hexdigest)

    # if_reprep 缺省为 False
    if not hasattr(task, "if_reprep"):
        setattr(task, "if_reprep", False)

def ensure_tasks_minimums(tasks: List[StandardRegTask]) -> None:
    # baseline 的指纹可作为默认指纹传给其余任务
    baseline_fp = None
    if tasks:
        ensure_task_minimums(tasks[0])
        baseline_fp = getattr(tasks[0], "prep_fingerprint", None)
    for t in tasks[1:]:
        ensure_task_minimums(t, default_fp=baseline_fp)

# ---------- 构建 Plan（完全沿用你给的示例） ----------
def build_plan_and_tasks(df: pd.DataFrame) -> Plan:
    base = StandardRegTask(
        name="explore_demo",
        dataset="A",
        y="ROA",
        X=["Size_ln"],
        controls=[],
        category_controls=["Industry", "Year"],
        model="OLS",
        options={"cluster": "firm"},
        subset=                {
                    "classification_field": "HighLev",
                    "operator": "==",
                    "classification_conditions": "1",
                }
    )

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
        model_list=["FE"],
        # options_grid=[
        #     {"winsorize_percent": 0.02, "cluster": "Age"},
        # ],
        limit=500,
        # subset_list=subset_list,  # 若你的 BaselineExplorer 接受该参数；否则忽略
    )

    # 取少量任务避免组合爆炸
    tasks = [base]

    # 用你下游的 Plan API
    plan = Plan(tasks)
    # 示例中的后处理与代码渲染
    try:
        # plan.post_perf(ys=['CAR1', 'CAR2'],if_reprep=True)
        plan.mechanisms(ys=['Fuck'])
        plan.mechanisms(ys=['Fuck'],if_reprep=True)
        plan.skip('mechanisms')
        pass
    except Exception:
        # 若你的 Plan 无该方法，安全忽略
        pass
    try:
        plan.render_code_in_batch()
    except Exception:
        # 如果你暂未接好 CodeGenerator，这里继续用兜底策略
        pass
    # 兜底补齐字段，保证能被 CodeExecutor 执行
    ensure_tasks_minimums(tasks)
    return plan

# ---------- 运行执行器并检查结果 ----------
def run_executor(plan: Plan, df: pd.DataFrame) -> None:
    ce = CodeExecutor(plan=plan, df=df, language='R')  # 语言也可从 util.ConfigLoader 读
    ce.run()

    # 展示每棵树里每个任务的结果
    trees = plan.flatten_by_tree()
    for ti, tree in enumerate(trees):
        print(f"\n=== Task Tree #{ti} ===")
        for i, t in enumerate(tree):
            res = getattr(t, "exec_result", None)
            print(f"[Task {i}] name={getattr(t,'name',None)} --> type={type(res)}")
            if isinstance(res, pd.DataFrame):
                print(res.head())
            else:
                print(str(res)[:300])

    # 简单断言：至少有一个任务产出结果
    any_ok = False
    for tree in plan.flatten_by_tree():
        for t in tree:
            if getattr(t, "exec_result", None) is not None:
                any_ok = True
    assert any_ok, "所有任务的 exec_result 都是 None —— 请检查 R 代码是否设置了 python_output"

if __name__ == "__main__":
    # 检查 rpy2 是否可用（友好提示）
    try:
        import rpy2  # noqa: F401
    except Exception as e:
        print("本测试需要 rpy2：请先安装 `pip install rpy2`，并确保本机已安装 R。")
        sys.exit(1)

    df = make_toy_df()
    plan = build_plan_and_tasks(df)
    # plan.render_code_in_batch()

    try:
        ce = CodeExecutor(plan=plan, df=df)  # 语言也可从 util.ConfigLoader 读
        ce.run()

        # 展示每棵树里每个任务的结果
        trees = plan.flatten_by_tree()
        for ti, tree in enumerate(trees):
            print(f"\n=== Task Tree #{ti} ===")
            for i, t in enumerate(tree):
                res = getattr(t, "exec_result", None)
                print(f"[Task {i}] name={getattr(t,'name',None)} --> type={type(res)}")
                if isinstance(res, pd.DataFrame):
                    print(res.head())
                else:
                    print(str(res)[:300])
        # 简单断言：至少有一个任务产出结果
        any_ok = False
        for tree in plan.flatten_by_tree():
            for t in tree:
                if getattr(t, "exec_result", None) is not None:
                    any_ok = True
        print("CodeExecutor 测试完成：结果已写回各任务的 exec_result。")
    except ResultNotFoundError as e:
        print("运行失败：R 端未生成 `python_output`。")
        print("   请确认你的 CodeGenerator 在每个任务代码末尾设置：python_output <- <你的结果对象>")
        raise
    except ExecutionRuntimeError as e:
        print("R 执行错误：", e)
        raise

```

## 贡献

欢迎 PR：
- 新的语言模板（如 Stata、Python statsmodels）
- 更多模型类型（RE/IV/PSM/Heckman 等）
- DataManager 的后端增强（DuckDB/Delta/Glue…）

## 许可证

MIT
