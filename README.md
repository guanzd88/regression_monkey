# Regression Monkey

Regression Monkey is a reproducible regression workflow for empirical research. It connects
structured data loading, dependency-aware refreshing, templated code generation, batch execution,
and a Textual-based TUI for curating final tables. The goal is to replace ad-hoc notebooks with a
traceable, automation-friendly stack. (中文介绍请见 [README_zh.md](README_zh.md)。)

## Highlights
- **Deterministic data refresh** – DataLoader modules produce single artifacts; DataManager tracks
  ArcticDB/PKL/DataLoader sources, semantic hashes, and dependency propagation.
- **Task-centric modeling** – `StandardRegTask` captures Y/X/control/fixed-effect specs, cluster
  options, incremental controls, and classification filters; tasks serialize cleanly and carry
  fingerprints for auditing.
- **Code generation and execution** – `CodeGenerator` renders Jinja2 templates (currently R) with
  dependency injection; `CodeExecutor` orchestrates task trees via rpy2, wiring datasets and
  capturing normalized results (including stepwise regressions).
- **Table editing TUI** – Textual UI lets you search tasks, attach columns (including stepwise
  variants), reorder/rename columns, and export reproducibility bundles (`main.R`, datasets).
- **International-ready messaging** – All runtime prompts, logs, and TUI notifications are in
  English for cross-team collaboration.

## Components at a Glance
| Component            | Purpose |
| -------------------- | ------- |
| `DataLoader`         | Minimal class for defining `clean_data()` → DataFrame/PKL/Arctic output with declared dependencies. |
| `DataManager`        | Orchestrates multi-source loading (Arctic ↔ DataLoader ↔ PKL), semantic fingerprinting, cost-aware refresh decisions, and caching. |
| `StandardRegTask`    | Declarative regression spec with serialization, subset filters, incremental controls, and acceptance tests. |
| `CodeGenerator`      | Jinja2 macro toolkit that emits R code (OLS/FE/RE, stepwise, etc.) and dependency stubs. |
| `CodeExecutor`       | rpy2-based runner that feeds datasets, executes generated code, captures `python_output`, and records stepwise metadata. |
| `Planner`            | Builds task trees (sections/nodes) and coordinates downstream rendering/execution. |
| `tui/*`              | Textual UI for browsing tasks, selecting columns, editing tables, and exporting reproducibility bundles. |

## Installation
Requires Python 3.14+.

```bash
pip install regression_monkey
```

For development extras (testing, linting, packaging):

```bash
pip install "regression_monkey[dev]"
```

### External Requirements
- **R** runtime if you plan to execute generated R code.
- **rpy2** is installed automatically on non-Windows platforms (you can install it manually on
  Windows if R is available).
- **ArcticDB** requires system dependencies compatible with LMDB.

## Quick Start

### 1. Define a DataLoader
```python
# data_loader/users.py
from reg_monkey.data_loader import DataLoader
import pandas as pd

class UsersLoader(DataLoader):
    output_pkl_name = "users.pkl"

    def clean_data(self):
        df = pd.read_csv("source_data/users_raw.csv")
        df = df.dropna(subset=["firm_id"]).rename(columns={"signup_time": "ts"})
        self.df = df
        return df
```

### 2. Refresh/load datasets
```python
from reg_monkey.data_manager import DataManager

dm = DataManager(target_symbols=["users"], project_root=".")
df_users = dm.get("users")  # hits Arctic/Pickle/DataLoader according to priority
```

### 3. Describe a regression task
```python
from reg_monkey.task_obj import StandardRegTask
from reg_monkey.code_generator import CodeGenerator

task = StandardRegTask(
    name="baseline",
    dataset="users",
    y="y",
    X=["treatment"],
    controls=["size","age"],
    category_controls=["industry","year"],
    model="OLS",
    incremental_controls=True,
)

cg = CodeGenerator(task)
segments = cg.assembly(internal_output=True)
print(segments["combined"])  # rendered R script
```

### 4. Execute and inspect results
```python
from reg_monkey.code_executor import CodeExecutor

executor = CodeExecutor(plan=None, datasets={"users": df_users})
executor.run_single_task(task, segments["combined"])  # custom helper you implement
print(task.exec_result["forward_res"]["coefficients"].head())
```

### 5. Launch the TUI
```python
from reg_monkey.tui import run_app

run_app(code_executor=executor, config_path="output_mapping.json")
```
Use the TUI (`Table List → Table Editor → Result Browser`) to add columns, toggle stepwise results,
rename labels, and export reproducibility bundles (`main.R` + datasets + metadata).

## Reproducibility Exports
`ExportService` bundles:
- `main.R` with dependency installation, dataset loading, preparation sections, and regression
  execution (deduplicated by code hash).
- Feather/CSV datasets referenced in tables.
- Stepwise columns honoring user selections (enable columns via TUI and choose steps in the modal).

## Project Layout
```
src/
  reg_monkey/
    data_loader.py
    data_manager.py
    task_obj.py
    code_generator.py
    code_executor.py
    planner.py
    export_service.py
    tui/
    r_template.jinja
  prd/        # design docs (Chinese allowed)
  bk/         # backups / historical references
```

## Development Tips
- Run `pytest` for unit tests; TUI flows are best verified manually.
- Use `ruff` + `black` for lint/format.
- When touching the TUI, ensure `output_mapping.json` remains backward compatible (columns carry
  `controls`, `parent_task_id`, etc.).
- All user-facing text must remain in English.

## Contributing
Pull requests are welcome. Please include:
1. A clear description of the change.
2. Tests or manual verification steps for regression-critical paths.
3. Documentation updates if behavior changes.

## License
MIT
