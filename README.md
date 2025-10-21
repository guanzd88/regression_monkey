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
  "arctic_uri": "lmdb:///path/to/arctic",   // 可选
  "data_root": "./data",
  "pkl_root": "./cache"
}
```

## 运行示例（最小）

```python
from reg_monkey import DataManager, DataLoader

class Demo(DataLoader):
    output_pkl_name = "demo.pkl"
    def clean_data(self):
        import pandas as pd
        self.df = pd.DataFrame({"x":[1,2,3],"y":[2,4,6]})
        return self.df

dm = DataManager(project_root=".")
df = dm.get("demo.pkl", loader_module="demo_loader.Demo")  # 指向你的模块路径
print(df.head())
```

## 贡献

欢迎 PR：
- 新的语言模板（如 Stata、Python statsmodels）
- 更多模型类型（RE/IV/PSM/Heckman 等）
- DataManager 的后端增强（DuckDB/Delta/Glue…）

## 许可证

MIT

> **注意**：在 PyPI 上包名是 `reg_monkey`，但导入仍然是 `import reg_monkey`。

> **注意**：PyPI 包名是 `regression_monkey`，导入名是 `import reg_monkey`。
