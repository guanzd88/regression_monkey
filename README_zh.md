# Regression Monkey（中文说明）

Regression Monkey 是一套面向实证研究的可复现回归工作流。它将数据加载、依赖感知刷新、模板化代码生成、批量执行以及 Textual TUI 表格管理串联起来，帮助研究者替代零散 Notebook，构建可追踪、自动化友好的分析链路。

## 核心特点
- **确定性的数据刷新**：`DataLoader` 负责产出唯一数据制品；`DataManager` 追踪 ArcticDB / PKL / DataLoader 来源、语义哈希以及依赖传播，透明记录刷新成本与决策。
- **任务驱动的建模方式**：`StandardRegTask` 统一描述 Y/X/控制变量、固定效应、面板 ID、分类过滤与增量控制选项，支持序列化与指纹校验，便于审计与导出。
- **模板化代码生成+批量执行**：`CodeGenerator` 通过 Jinja2 渲染 R 模板（OLS/FE/RE/Stepwise 等）；`CodeExecutor` 基于 rpy2 注入数据集、调度任务树、捕获标准化的 `python_output` 含 stepwise 结果。
- **表格编辑 TUI**：Textual UI（表格列表 → 表格编辑 → 结果浏览）支持检索任务、添加/排序/重命名列、启用 stepwise 控制、导出复现包 (`main.R` + 数据集)。
- **可离线重放的缓存**：执行完成后自动生成包含 Plan 配置、任务代码、执行结果与数据集快照的缓存文件，TUI 和导出服务可直接读取最新缓存，无需重新跑数据。

## 目录结构
```
regression_monkey/
├── README.md / README_zh.md
├── src/reg_monkey
│   ├── data_loader.py / data_manager.py
│   ├── task_obj.py / planner.py / code_generator.py / code_executor.py
│   ├── export_service.py
│   ├── tui/  # Textual TUI 入口与组件
│   └── r_template.jinja
├── scripts/
└── dev/（个人草稿，默认已被 .gitignore 排除）
```

## 快速上手
1. **实现 DataLoader**：在 `clean_data()` 中输出清洗后的 DataFrame，声明依赖。
2. **通过 DataManager 刷新/读取**：`dm = DataManager(target_symbols=["mock_fmb"])`，自动决定是否重算或直接读 Arctic/PKL。
3. **描述回归任务**：使用 `StandardRegTask` 指定 y、X、控制、固定效应、模型类型、面板 ID、subset、增量控制等。
4. **生成 + 执行代码**：`CodeGenerator` 渲染 R 代码；`CodeExecutor` 调度任务树、注入数据集、抓取 `python_output`。
5. **TUI 管理表格与导出**：`run_app(code_executor=ce)`，在 TUI 中挑选列、启用 stepwise、导出 `main.R` 和数据集，或离线加载最近的缓存。

## 复现导出
`ExportService` 会根据表格配置打包：
- `main.R`：包含依赖安装、数据载入、准备流程、去重后的回归块（保留 stepwise 选择）。
- 数据集：默认导出 Feather（若失败则退回 CSV），列名与 CodeExecutor 保持一致。
- 元数据：与 TUI 表格展示一致的列注释、任务 ID、Stepwise 选择等。

## 开发注意
- 需要 Python 3.14+；运行 R 代码需本地安装 R+rpy2。
- 贡献代码时请保持所有用户提示为英文，确保 `output_mapping.json` 向后兼容。
- 运行 `pytest` / `ruff` / `black` 可快速验证常规逻辑和风格。

## 许可
MIT License。

如需英文说明与更多示例，请参阅仓库根目录的 `README.md`。
