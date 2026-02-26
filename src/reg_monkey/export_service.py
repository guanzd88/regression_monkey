"""
export_service.py - 程序化导出服务

从 output_mapping.json 读取配置，导出表格和数据集。
支持 TUI 调用和独立命令行使用。

Usage:
    # 方式1: 从配置文件加载
    service = ExportService.load_from_config(
        config_path="output_mapping.json",
        results_df=results_df,
    )
    service.export_tables(output_dir="./output/", format="xlsx")

    # 方式2: 直接传入 TableMatrix 列表
    service = ExportService(tables=tables)
    service.export_tables(output_dir="./output/")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from reg_monkey.code_generator import CodeGenerator

if TYPE_CHECKING:
    from reg_monkey.table_matrix import TableMatrix, MatrixColumn
    from reg_monkey.table_config import TableConfig

# 用于识别 raw bracket 列名的正则（与 CodeExecutor 一致）
_RAW_BRACKET_COL_PAT = re.compile(r"^[A-Za-z0-9_]+\[\s*[+-]?\d+\s*,\s*\d+\s*\]$")
_REPRO_HELPER_FILENAME = "helpers.R"
_REPRO_HELPER_START = "# ---- rm_helper_start ----"
_REPRO_HELPER_END = "# ---- rm_helper_end ----"




class ExportService:
    """
    导出服务类

    支持导出表格文件（xlsx/txt/latex）和复现材料（数据集/代码）。
    """

    def __init__(
        self,
        config_path: str = "output_mapping.json",
        results_df: Optional[pd.DataFrame] = None,
        exec_results: Optional[Dict[str, Any]] = None,
        tables: Optional[List["TableMatrix"]] = None,
        datasets: Optional[Dict[str, pd.DataFrame]] = None,
        plan: Any = None,
    ):
        self.config_path = config_path
        self.results_df = results_df if results_df is not None else pd.DataFrame()
        self.exec_results = exec_results or {}
        self.tables: List["TableMatrix"] = tables or []
        self.datasets = datasets or {}  # 数据集: name -> DataFrame
        self.plan = plan  # Plan 对象引用

    @classmethod
    def load_from_config(
        cls,
        config_path: str,
        results_df: pd.DataFrame,
    ) -> "ExportService":
        """
        从配置文件加载，恢复 TableMatrix 对象

        Args:
            config_path: output_mapping.json 路径
            results_df: to_materialize() 返回的 DataFrame

        Returns:
            ExportService 实例
        """
        from reg_monkey.output_mapping import OutputMapping
        from reg_monkey.table_matrix import TableMatrix

        service = cls(config_path=config_path, results_df=results_df)
        service._preload_exec_results()

        mapping = OutputMapping.load(config_path)
        for spec in mapping.tables:
            matrix = TableMatrix.from_spec(spec, service.exec_results, results_df)
            service.tables.append(matrix)
        return service

    def _preload_exec_results(self) -> None:
        """预加载执行结果到缓存"""
        if self.results_df.empty:
            return
        for _, row in self.results_df.iterrows():
            task_id = row.get("task_id")
            exec_result = row.get("exec_result")
            if task_id and exec_result is not None:
                if hasattr(exec_result, "value"):
                    exec_result = exec_result.value
                self.exec_results[task_id] = exec_result

    def export_tables(
        self,
        output_dir: str,
        format: str = "xlsx",  # xlsx, txt, latex
        table_indices: Optional[List[int]] = None,
        config: Optional["TableConfig"] = None,
    ) -> List[str]:
        """
        导出表格文件

        Args:
            output_dir: 输出目录
            format: 输出格式 (xlsx/txt/latex)
            table_indices: 要导出的表格索引列表，None 表示全部
            config: TableConfig 对象，用于渲染配置

        Returns:
            导出的文件路径列表
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        exported = []
        tables_to_export = (
            [self.tables[i] for i in table_indices if 0 <= i < len(self.tables)]
            if table_indices
            else self.tables
        )

        for matrix in tables_to_export:
            filename = matrix.filename or f"{matrix.title}.{format}"

            if format == "xlsx":
                if not filename.endswith(".xlsx"):
                    filename = filename.rsplit(".", 1)[0] + ".xlsx"
                filepath = output_path / filename
                self._export_xlsx(matrix, str(filepath), config)

            elif format == "txt":
                if not filename.endswith(".txt"):
                    filename = filename.rsplit(".", 1)[0] + ".txt"
                filepath = output_path / filename
                content = matrix.to_txt(config=config)
                filepath.write_text(content, encoding="utf-8")

            elif format == "latex":
                if not filename.endswith(".tex"):
                    filename = filename.rsplit(".", 1)[0] + ".tex"
                filepath = output_path / filename
                content = self._render_latex(matrix, config)
                filepath.write_text(content, encoding="utf-8")

            exported.append(str(filepath))

        return exported

    def _export_xlsx(
        self,
        matrix: "TableMatrix",
        filepath: str,
        config: Optional["TableConfig"] = None,
    ) -> None:
        """导出为 Excel 格式"""
        from reg_monkey.table_config import TableConfig

        cfg = config or TableConfig()

        if matrix.is_empty:
            # 空表格创建空的 Excel 文件
            pd.DataFrame().to_excel(filepath, index=False)
            return

        # 构建数据
        rows = []
        n_cols = len(matrix.columns)
        total_cols = n_cols + 1  # 变量名列 + 数据列

        # 记录需要合并的行
        merge_rows = []  # [(row_index, start_col, end_col, is_title), ...]

        # 表格标题
        title_row = [matrix.title or "Regression Results"] + [""] * n_cols
        merge_rows.append((len(rows) + 1, 1, total_cols, True))  # Excel 行号从 1 开始, is_title=True
        rows.append(title_row)

        # 表格描述（如果有）
        if matrix.description:
            desc_row = [matrix.description] + [""] * n_cols
            merge_rows.append((len(rows) + 1, 1, total_cols, False))  # is_title=False
            rows.append(desc_row)

        # 空行
        rows.append([""] * total_cols)

        # 列标签行
        rows.append([""] + [col.label or f"({i+1})" for i, col in enumerate(matrix.columns)])

        # 因变量行
        rows.append(["VARIABLES"] + [col.y for col in matrix.columns])

        # 空行
        rows.append([""] * total_cols)

        # 收集变量
        all_vars = matrix._collect_sorted_variables(cfg)

        # 系数行
        for var in all_vars:
            # 系数值行
            coef_row = [var]
            for col in matrix.columns:
                coef = matrix._get_column_coefficient_data(col, var)
                if coef is not None:
                    est, p_val = coef.get("Estimate"), coef.get("P_Value")
                    coef_row.append(cfg.format_coefficient(est, p_val))
                else:
                    coef_row.append("")
            rows.append(coef_row)

            # 标准误行
            se_row = [""]
            for col in matrix.columns:
                coef = matrix._get_column_coefficient_data(col, var)
                if coef is not None:
                    se = coef.get("Std_Error")
                    t_stat = coef.get("T_Value") or coef.get("t_value")
                    se_row.append(cfg.format_second_row(se, t_stat))
                else:
                    se_row.append("")
            rows.append(se_row)

        # 空行
        rows.append([""] * total_cols)

        # Observations
        obs_row = ["Observations"]
        for col in matrix.columns:
            n = matrix._get_column_observations(col)
            obs_row.append(cfg.format_observations(n) if n else "")
        rows.append(obs_row)

        # R-squared
        r2_row = ["R-squared"]
        for col in matrix.columns:
            r2 = matrix._get_column_r_squared(col)
            r2_row.append(f"{r2:.{cfg.decimal_places}f}" if r2 is not None else "")
        rows.append(r2_row)

        # 固定效应控制行
        all_effects = matrix._collect_all_effects()
        for effect_name in all_effects:
            fe_row = [f"{effect_name} FE"]
            for col in matrix.columns:
                controlled = matrix._check_effect_controlled(col, effect_name)
                fe_row.append(cfg.fixed_effect_labels.yes if controlled else cfg.fixed_effect_labels.no)
            rows.append(fe_row)

        # 创建 DataFrame 并保存（使用 openpyxl 引擎以支持合并单元格）
        df = pd.DataFrame(rows)

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, header=False, sheet_name='Sheet1')

            # 合并单元格
            worksheet = writer.sheets['Sheet1']
            from openpyxl.utils import get_column_letter
            from openpyxl.styles import Font, Alignment

            for row_idx, start_col, end_col, is_title in merge_rows:
                start_cell = f"{get_column_letter(start_col)}{row_idx}"
                end_cell = f"{get_column_letter(end_col)}{row_idx}"
                worksheet.merge_cells(f"{start_cell}:{end_cell}")

                # 设置样式（左对齐，标题加粗）
                cell = worksheet[start_cell]
                cell.alignment = Alignment(horizontal='left')
                if is_title:
                    cell.font = Font(bold=True)

    def _render_latex(
        self,
        matrix: "TableMatrix",
        config: Optional["TableConfig"] = None,
    ) -> str:
        """渲染为 LaTeX 格式"""
        from reg_monkey.table_config import TableConfig

        cfg = config or TableConfig()

        if matrix.is_empty:
            return "% Empty table"

        n_cols = len(matrix.columns)
        lines = []

        # LaTeX 表格头
        lines.append(r"\begin{table}[htbp]")
        lines.append(r"\begin{flushleft}")  # 左对齐

        # 标题（加粗）
        title = matrix.title or "Regression Results"
        lines.append(rf"\textbf{{{title}}} \\")

        # 描述（不加粗）
        if matrix.description:
            lines.append(rf"{matrix.description} \\")

        lines.append(r"\end{flushleft}")
        lines.append(r"\vspace{0.5em}")

        # 标签（使用安全的标签名）
        safe_label = title.lower().replace(" ", "_").replace("-", "_")
        lines.append(r"\label{tab:" + safe_label + "}")

        lines.append(r"\begin{tabular}{l" + "c" * n_cols + "}")
        lines.append(r"\toprule")

        # 列标签行
        labels = [col.label or f"({i+1})" for i, col in enumerate(matrix.columns)]
        lines.append(" & " + " & ".join(labels) + r" \\")

        # 因变量行
        depvars = [col.y for col in matrix.columns]
        lines.append("VARIABLES & " + " & ".join(depvars) + r" \\")
        lines.append(r"\midrule")

        # 收集变量
        all_vars = matrix._collect_sorted_variables(cfg)

        # 系数行
        for var in all_vars:
            # 系数值行
            coef_cells = []
            for col in matrix.columns:
                coef = matrix._get_column_coefficient_data(col, var)
                if coef is not None:
                    est, p_val = coef.get("Estimate"), coef.get("P_Value")
                    coef_cells.append(cfg.format_coefficient(est, p_val))
                else:
                    coef_cells.append("")
            # 转义下划线
            var_escaped = var.replace("_", r"\_")
            lines.append(f"{var_escaped} & " + " & ".join(coef_cells) + r" \\")

            # 标准误行
            se_cells = []
            for col in matrix.columns:
                coef = matrix._get_column_coefficient_data(col, var)
                if coef is not None:
                    se = coef.get("Std_Error")
                    t_stat = coef.get("T_Value") or coef.get("t_value")
                    se_cells.append(cfg.format_second_row(se, t_stat))
                else:
                    se_cells.append("")
            lines.append(" & " + " & ".join(se_cells) + r" \\")

        lines.append(r"\midrule")

        # Observations
        obs_cells = []
        for col in matrix.columns:
            n = matrix._get_column_observations(col)
            obs_cells.append(cfg.format_observations(n) if n else "")
        lines.append("Observations & " + " & ".join(obs_cells) + r" \\")

        # R-squared
        r2_cells = []
        for col in matrix.columns:
            r2 = matrix._get_column_r_squared(col)
            r2_cells.append(f"{r2:.{cfg.decimal_places}f}" if r2 is not None else "")
        lines.append("R-squared & " + " & ".join(r2_cells) + r" \\")

        # 固定效应控制行
        all_effects = matrix._collect_all_effects()
        for effect_name in all_effects:
            fe_cells = []
            for col in matrix.columns:
                controlled = matrix._check_effect_controlled(col, effect_name)
                fe_cells.append(cfg.fixed_effect_labels.yes if controlled else cfg.fixed_effect_labels.no)
            effect_escaped = effect_name.replace("_", r"\_")
            lines.append(f"{effect_escaped} FE & " + " & ".join(fe_cells) + r" \\")

        # 表格尾
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")

        # 显著性说明
        sl = cfg.significance_levels
        note = rf"\footnotesize{{*** p<{sl.three_star}, ** p<{sl.two_star}, * p<{sl.one_star}}}"
        lines.append(rf"\begin{{flushleft}}{note}\end{{flushleft}}")
        lines.append(r"\end{table}")

        return "\n".join(lines)

    def _export_stepwise_code(self, task: Any) -> List[str]:
        exec_result = getattr(task, "exec_result", None)
        if exec_result is None or not isinstance(exec_result, dict):
            return []
        raw_steps = exec_result.get("stepwise_results") or []
        if not raw_steps:
            return []

        task_id = getattr(task, "task_id", None)
        table_marked = set()
        export_map = getattr(self, "_stepwise_export_map", {}) or {}
        if task_id and task_id in export_map:
            table_marked = export_map[task_id]

        marked: List[Dict[str, Any]] = []
        for step in raw_steps:
            try:
                idx_val = step.get("step", -1)
                if isinstance(idx_val, (list, tuple)):
                    idx_val = idx_val[0]
                if isinstance(idx_val, (float, str)) and str(idx_val).isdigit():
                    idx = int(idx_val)
                else:
                    idx = int(idx_val)
            except Exception:
                continue

            if table_marked and idx not in table_marked:
                continue
            if not table_marked and not bool(step.get("export_marked")):
                continue

            controls = self._normalize_controls(step.get("controls_included"))
            label = self._clean_step_label(step.get("label"), idx)
            marked.append({"index": idx, "label": label, "controls": controls})

        if not marked:
            return []

        marked.sort(key=lambda s: s["index"])

        ctx = getattr(task, "_data_context", {}) or {}
        reg_var = ctx.get("regression_data_variable", "df_prep_main_baseline")
        base_terms = self._get_base_terms(task)
        fe_terms = [f"factor({fe})" for fe in (getattr(task, "category_controls", []) or []) if fe]

        model_type = (getattr(task, "model", "OLS") or "OLS").upper()
        engine_fun = "stats::lm" if model_type == "OLS" else "plm"
        extract_fn = self._get_extract_function(model_type)
        model_arg = None
        effect_arg = None
        if model_type != "OLS":
            inferred = CodeGenerator._infer_plm_model_effect(task.to_json())
            if model_type == "RE":
                model_arg = "random"
            else:
                model_arg = inferred.get("model") or "within"
                effect_arg = inferred.get("effect") if model_arg == "within" else None
            if model_arg is None:
                model_arg = "within"

        selected_controls: List[str] = []
        seen_controls: Set[str] = set()
        for info in marked:
            for ctrl in info.get("controls", []):
                if ctrl and ctrl not in seen_controls:
                    seen_controls.add(ctrl)
                    selected_controls.append(ctrl)

        all_vars = self._collect_stepwise_vars(task, controls_override=selected_controls)
        safe_id = self._sanitize_identifier(getattr(task, "task_id", None) or getattr(task, "name", None), "stepwise")

        lines = []
        lines.append(f"# Stepwise controls for {task.task_id}")
        lines.append(f"stepwise_vars_{safe_id} <- c({self._format_r_vector(all_vars)})")
        lines.append(f"df_stepwise_{safe_id} <- stats::na.omit({reg_var}[, stepwise_vars_{safe_id}])")
        lines.append(f"stepwise_results_{safe_id} <- list()")

        y_var = getattr(task, "y", "y")
        for info in marked:
            idx = info["index"]
            idx_str = self._format_step_index(idx)
            orig_label = info.get("label", "")
            comment_label = str(orig_label).replace("\n", " ").strip()
            label = self._escape_r_string(orig_label)
            controls = info.get("controls", [])
            rhs_terms = base_terms + controls
            rhs = " + ".join(rhs_terms + fe_terms) if (rhs_terms or fe_terms) else "1"
            lines.append(f"# Stepwise: Step {idx_str} ({comment_label})")
            call = f"{engine_fun}({y_var} ~ {rhs}, data = df_stepwise_{safe_id}"
            if model_type != "OLS":
                call += f", model = \"{model_arg}\""
                if effect_arg:
                    call += f", effect = \"{effect_arg}\""
            call += ")"
            lines.append(f"reg_{safe_id}_step{idx} <- {call}")
            summary_var = f"reg_{safe_id}_step{idx}_summary"
            lines.append(f"{summary_var} <- {extract_fn}(reg_{safe_id}_step{idx})")

            ctrl_vector = f"c({self._format_r_vector(controls)})" if controls else "c()"
            lines.append("stepwise_entry <- list(")
            lines.append(f"  step = {idx},")
            lines.append(f"  label = \"{label}\",")
            lines.append(f"  controls_included = {ctrl_vector},")
            lines.append("  export_marked = TRUE,")
            lines.append(f"  result = {summary_var}")
            lines.append(")")
            lines.append(f"stepwise_results_{safe_id}[[length(stepwise_results_{safe_id}) + 1]] <- stepwise_entry")
            lines.append("")

        return lines[:-1] if lines and not lines[-1].strip() else lines

    def export_data_and_code(
        self,
        output_dir: str,
        task_ids: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        """
        导出数据集和 R 代码（用于审稿人复现）

        改进：
        1. 数据集导出前应用列名编码（与 CodeExecutor 一致）
        2. 分离 prepare_code，每个数据集只执行一次
        3. 收集并添加完整的依赖库
        4. 按 Plan 的执行顺序组织代码

        Args:
            output_dir: 输出目录
            task_ids: 要导出的 task_id 列表，None 表示从表格中收集

        Returns:
            {"code": [文件路径...], "datasets": [文件路径...]}
        """
        # 确保输出目录存在
        output_path = Path(output_dir)
        try:
            output_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"Unable to create output directory {output_dir}: {e}")

        exported = {"code": [], "datasets": []}

        # ========================================
        # 1. 导出数据集（feather 格式，应用列名编码）
        # ========================================
        dataset_load_info = []  # [(dataset_key, filename), ...]

        if self.datasets:
            for dataset_name, df in self.datasets.items():
                if isinstance(df, pd.DataFrame) and not df.empty:
                    dataset_key = self._to_dataset_key(dataset_name)
                    filename = f"{dataset_key}.feather"
                    dataset_file = output_path / filename

                    # P0 修复：应用与 CodeExecutor 相同的列名编码
                    sanitized_df = self._sanitize_df_for_export(df)

                    try:
                        sanitized_df.to_feather(str(dataset_file))
                        exported["datasets"].append(str(dataset_file))
                        dataset_load_info.append((dataset_key, filename))
                    except Exception:
                        # feather 失败则用 csv
                        csv_filename = f"{dataset_key}.csv"
                        csv_file = output_path / csv_filename
                        sanitized_df.to_csv(str(csv_file), index=False)
                        exported["datasets"].append(str(csv_file))
                        dataset_load_info.append((dataset_key, csv_filename))

        # 在导出前确保 Plan 已分配数据上下文
        if self.plan is not None and hasattr(self.plan, "assign_data_contexts"):
            try:
                self.plan.assign_data_contexts()
            except Exception:
                pass

        # ========================================
        # 2. 收集需要导出的 task_id（按执行顺序）
        # ========================================
        table_task_ids = set(self._get_task_ids_from_tables())
        if not table_task_ids:
            exported["message"] = "No tasks found to export (no regression results in the tables)."
            return exported

        ordered_task_ids = self._get_task_ids_in_execution_order(table_task_ids)
        if not ordered_task_ids:
            ordered_task_ids = list(table_task_ids)

        # ========================================
        # 3. 收集依赖库
        # ========================================
        all_deps = self._collect_dependencies()

        # ========================================
        # 4. 构建 task_id -> code_text 映射
        # ========================================
        task_code_map = self._build_task_code_map()
        self._stepwise_export_map = self._collect_stepwise_from_tables()
        self._helper_blocks: List[str] = []
        self._helper_hashes: Set[int] = set()

        # ========================================
        # 5. 构建 task_id -> task 对象映射（获取上下文）
        # ========================================
        task_obj_map = self._build_task_obj_map()

        # ========================================
        # 6. 计算数据准备顺序
        # ========================================
        var_providers: Dict[str, Any] = {}
        for task in task_obj_map.values():
            ctx = getattr(task, "_data_context", None)
            if isinstance(ctx, dict):
                output_var = ctx.get("output_data_variable")
                if output_var and output_var not in var_providers:
                    var_providers[output_var] = task

        required_output_vars: List[str] = []
        seen_outputs: Set[str] = set()
        for task_id in ordered_task_ids:
            task = task_obj_map.get(task_id)
            ctx = getattr(task, "_data_context", None)
            if not isinstance(ctx, dict):
                continue
            reg_var = ctx.get("regression_data_variable")
            if reg_var and reg_var.startswith("df_prep_") and reg_var not in seen_outputs:
                seen_outputs.add(reg_var)
                required_output_vars.append(reg_var)

        prep_sequence: List[Tuple[str, Optional[str], str]] = []
        for output_var in required_output_vars:
            provider = var_providers.get(output_var)
            if provider is None:
                continue
            provider_id = getattr(provider, "task_id", None)
            code_entry = task_code_map.get(provider_id)
            segments = self._unpack_code_text(code_entry)
            prepare_code = (segments.get("prepare_code") or "").strip()
            prepare_code = self._strip_helper_blocks(prepare_code)
            if not prepare_code:
                continue
            prep_sequence.append((output_var, provider_id, prepare_code))

        # ========================================
        # 7. 组装 main.R
        # ========================================
        main_code_parts = []

        # --- 文件头 ---
        main_code_parts.append("# ============================================")
        main_code_parts.append("# Regression Monkey - Reproducibility Script")
        main_code_parts.append("# This file contains all regression code referenced by the exported tables")
        main_code_parts.append("# ============================================")
        main_code_parts.append("")

        mapping_comment = self._build_table_mapping_comment()
        if mapping_comment:
            main_code_parts.extend(mapping_comment)
            main_code_parts.append("")

        # --- 依赖安装/加载 ---
        main_code_parts.append("# Install and load required packages")
        main_code_parts.append("required_packages <- c(")
        dep_list = sorted(set(all_deps) | {"arrow"})
        main_code_parts.append("    " + ", ".join(f'"{d}"' for d in dep_list))
        main_code_parts.append(")")
        main_code_parts.append("for (pkg in required_packages) {")
        main_code_parts.append('    if (!require(pkg, character.only = TRUE, quietly = TRUE)) {')
        main_code_parts.append('        install.packages(pkg, repos = "https://cloud.r-project.org")')
        main_code_parts.append("        library(pkg, character.only = TRUE)")
        main_code_parts.append("    }")
        main_code_parts.append("}")
        main_code_parts.append("")
        if self._helper_blocks:
            main_code_parts.append("# Load helper functions")
            main_code_parts.append(f'source("{_REPRO_HELPER_FILENAME}")')
            main_code_parts.append("")

        # --- 数据加载 ---
        main_code_parts.append("# Load data")
        main_code_parts.append("# Data frames are named df_raw_{dataset_key} to match the regression code")
        for dataset_key, filename in dataset_load_info:
            var_name = f"df_raw_{dataset_key}"
            if filename.endswith(".feather"):
                main_code_parts.append(f'{var_name} <- arrow::read_feather("{filename}")')
            else:
                main_code_parts.append(f'{var_name} <- read.csv("{filename}")')
        main_code_parts.append("")

        # --- 数据准备 ---
        main_code_parts.append("# ============================================")
        main_code_parts.append("# Data preparation")
        main_code_parts.append("# ============================================")

        if not prep_sequence:
            main_code_parts.append("# (No additional preparation required)")
        else:
            for output_var, provider_id, prepare_code in prep_sequence:
                header = provider_id or output_var
                main_code_parts.append(f"# --- {output_var} (task: {header}) ---")
                main_code_parts.append(prepare_code)
                main_code_parts.append("")
        main_code_parts.append("")

        # --- 回归执行 ---
        main_code_parts.append("# ============================================")
        main_code_parts.append("# Regression execution")
        main_code_parts.append("# ============================================")
        main_code_parts.append("")
        main_code_parts.append("")

        added_execute_hashes: Set[int] = set()
        task_count = 0

        for task_id in ordered_task_ids:
            code_text = task_code_map.get(task_id)
            if not code_text:
                continue

            ct = self._unpack_code_text(code_text)
            execute_code = (ct.get("execute_code", "") or "").strip()
            execute_code = self._strip_helper_blocks(execute_code)
            if not execute_code:
                continue

            task_obj = task_obj_map.get(task_id)
            block_lines = self._assemble_task_block(task_id, execute_code, task_obj)
            if not block_lines:
                continue

            combined_exec = "\n".join(block_lines)
            code_hash = hash(combined_exec.strip())
            if code_hash in added_execute_hashes:
                continue
            added_execute_hashes.add(code_hash)

            task_count += 1
            main_code_parts.extend(block_lines)
            main_code_parts.append("")

        # --- 写入文件 ---
        if task_count > 0:
            main_r_file = output_path / "main.R"
            main_r_file.write_text("\n".join(main_code_parts), encoding="utf-8")
            exported["code"].append(str(main_r_file))
            if self._helper_blocks:
                helper_path = self._write_helper_file(output_path)
                exported["code"].append(str(helper_path))

        # 添加统计信息
        exported["total_tasks"] = task_count
        exported["total_datasets"] = len(exported["datasets"])

        if not exported["code"]:
            exported["message"] = "No executable code found (make sure CodeExecutor.run() has been executed)."

        return exported

    # ========================================
    # 辅助方法
    # ========================================

    def _to_dataset_key(self, name: str) -> str:
        """将 dataset 名称转换为 R 变量名安全的 key（与 CodeGenerator._get_dataset_key 一致）"""
        safe = re.sub(r'[^A-Za-z0-9_]', '_', str(name))
        if safe and safe[0].isdigit():
            safe = '_' + safe
        return safe or "main"

    @staticmethod
    def _sanitize_identifier(value: Optional[str], default: str = "task") -> str:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", str(value) if value else "")
        if not safe:
            safe = default
        if safe[0].isdigit():
            safe = f"_{safe}"
        return safe.lower()

    @staticmethod
    def _format_r_vector(values: List[str]) -> str:
        return ", ".join(repr(v) for v in values if v)

    def _build_table_mapping_comment(self) -> List[str]:
        """构建表格列与 R 代码任务的映射注释。"""
        if not self.tables:
            return []

        lines: List[str] = ["# Table-to-script mapping"]
        table_count = len(self.tables)
        for idx, matrix in enumerate(self.tables):
            title = getattr(matrix, "title", "") or f"Table {idx + 1}"
            filename = getattr(matrix, "filename", "") or ""
            header = f"# Table {title}"
            if filename:
                header += f" ({filename})"
            lines.append(header)

            columns = getattr(matrix, "columns", []) or []
            if not columns:
                lines.append("#   (no columns selected)")
            else:
                for col_idx, column in enumerate(columns, 1):
                    lines.append(self._format_column_mapping_line(column, col_idx))

            if idx < table_count - 1:
                lines.append("#")

        return lines

    def _format_column_mapping_line(self, column: "MatrixColumn", position: int) -> str:
        label = column.label or f"({position})"
        source_id = getattr(column, "source_task_id", None) or column.task_id or "unknown"
        parts = [f"#   {label} -> Task {source_id}"]
        target_desc = self._describe_column_target(column)
        if target_desc:
            parts.append(target_desc)
        return " ".join(parts)

    def _describe_column_target(self, column: "MatrixColumn") -> str:
        target = (getattr(column, "result_target", "forward") or "forward").lower()
        group = getattr(column, "group_label", None) or ""

        if target == "stepwise":
            bits: List[str] = []
            if column.stepwise_index is not None:
                bits.append(f"Step {self._format_step_index(column.stepwise_index)}")
            if column.stepwise_label:
                bits.append(column.stepwise_label)
            detail = " ".join(bits) if bits else "Stepwise"
            if group:
                detail += f" ({group})"
            return f"[stepwise: {detail}]"

        if target == "opposite":
            detail = "Opposite subset"
        elif target == "forward":
            detail = "Forward"
        else:
            detail = target.title()

        if group:
            detail += f" ({group})"
        return f"[{detail}]"

    def _collect_stepwise_vars(self, task: Any, *, controls_override: Optional[List[str]] = None) -> List[str]:
        ordered: List[str] = []
        seen: Set[str] = set()
        seq: List[str] = []
        seq.append(getattr(task, "y", ""))
        seq.extend(self._get_base_terms(task))
        controls_seq = controls_override if controls_override is not None else list(getattr(task, "controls", []) or [])
        seq.extend(controls_seq)
        seq.extend(list(getattr(task, "category_controls", []) or []))
        for value in seq:
            if not value:
                continue
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

    def _assemble_task_block(self, task_id: str, execute_code: str, task_obj: Any) -> List[str]:
        execute_code = (execute_code or "").strip()
        if not execute_code and task_obj is None:
            return []

        lines: List[str] = []
        lines.append("# --------------------------------------------")
        lines.append(f"# Task: {task_id}")
        if task_obj is not None:
            name = getattr(task_obj, "name", "")
            if name:
                lines.append(f"# Name: {name}")
        lines.append("# --------------------------------------------")

        if task_obj is not None:
            stepwise_lines = self._export_stepwise_code(task_obj)
            if stepwise_lines:
                lines.extend(stepwise_lines)
                lines.append("")

        if execute_code:
            lines.append(execute_code)

        summary_lines = self._build_regression_summary_lines(task_obj, execute_code)
        if summary_lines:
            lines.append("")
            lines.extend(summary_lines)

        return [line for line in lines if line is not None]

    def _build_regression_summary_lines(self, task: Any, execute_code: str) -> List[str]:
        if task is None or not execute_code:
            return []

        model_type = (getattr(task, "model", "OLS") or "OLS").upper()
        extract_fn = self._get_extract_function(model_type)
        forward_var = self._get_forward_result_var(task)
        lines: List[str] = []

        if forward_var and f"{forward_var} <-" in execute_code:
            lines.append(f"{forward_var}_summary <- {extract_fn}({forward_var})")

        opposite_var = f"{forward_var}_opposite" if forward_var else None
        if opposite_var and f"{opposite_var} <-" in execute_code:
            lines.append(f"{opposite_var}_summary <- {extract_fn}({opposite_var})")

        return lines

    def _get_forward_result_var(self, task: Any) -> str:
        name = getattr(task, "name", None) or getattr(task, "task_id", None) or "task"
        safe = self._sanitize_identifier(name, "task")
        return f"reg_{safe}_result"

    @staticmethod
    def _get_extract_function(model_type: str) -> str:
        mt = (model_type or "OLS").upper()
        if mt in {"FE", "RE", "PLM"}:
            return "extract_coefficients_plm"
        if not mt:
            return "extract_coefficients_OLS"
        return f"extract_coefficients_{mt}"

    @staticmethod
    def _normalize_controls(controls: Any) -> List[str]:
        if controls is None:
            return []
        if isinstance(controls, str):
            return [controls] if controls else []
        if isinstance(controls, Iterable):
            normalized = []
            for item in controls:
                if item is None:
                    continue
                text = str(item).strip()
                if text:
                    normalized.append(text)
            return normalized
        text = str(controls).strip()
        return [text] if text else []

    @staticmethod
    def _escape_r_string(value: str) -> str:
        text = str(value or "")
        text = text.replace("\\", "\\\\")
        text = text.replace("\"", "\\\"")
        text = text.replace("\n", " ")
        return text

    @staticmethod
    def _format_step_index(idx: Any) -> str:
        try:
            value = int(idx)
            return f"{value:02d}"
        except Exception:
            return str(idx)

    def _strip_helper_blocks(self, code: str) -> str:
        if not code or _REPRO_HELPER_START not in code:
            return code
        lines = code.splitlines()
        out: List[str] = []
        collecting = False
        buffer: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not collecting and stripped == _REPRO_HELPER_START:
                collecting = True
                buffer = []
                continue
            if collecting:
                if stripped == _REPRO_HELPER_END:
                    collecting = False
                    block = "\n".join(buffer).strip()
                    if block:
                        h = hash(block)
                        if h not in self._helper_hashes:
                            self._helper_hashes.add(h)
                            self._helper_blocks.append(block)
                    buffer = []
                else:
                    buffer.append(line)
                continue
            out.append(line)
        return "\n".join(out).strip()

    def _write_helper_file(self, output_path: Path) -> Path:
        helper_path = output_path / _REPRO_HELPER_FILENAME
        helper_path.write_text("\n\n".join(self._helper_blocks) + "\n", encoding="utf-8")
        return helper_path

    def _get_base_terms(self, task: Any) -> List[str]:
        base_terms: List[str] = []
        X = getattr(task, "X", None)
        if isinstance(X, dict):
            for vars_list in X.values():
                if isinstance(vars_list, list):
                    base_terms.extend(vars_list)
                elif isinstance(vars_list, str):
                    base_terms.append(vars_list)
        elif isinstance(X, list):
            base_terms.extend(X)
        elif isinstance(getattr(task, "X_flat", None), list):
            base_terms.extend(task.X_flat)
        return [term for term in base_terms if term]

    def _sanitize_df_for_export(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        P0 修复：对 raw bracket 列名做编码（与 CodeExecutor._sanitize_df_for_r 一致）
        例如：car[-30,0] → car__30_0_
        """
        if df is None or not isinstance(df, pd.DataFrame):
            return df

        try:
            from reg_monkey.util import name_bracket_bidir
        except ImportError:
            return df

        cols = list(df.columns)
        rename_dict: Dict[str, str] = {}

        for c in cols:
            if not isinstance(c, str):
                continue
            if _RAW_BRACKET_COL_PAT.match(c):
                new_c = name_bracket_bidir(c)
                if new_c != c:
                    rename_dict[c] = new_c

        if not rename_dict:
            return df

        df2 = df.copy()
        df2.rename(columns=rename_dict, inplace=True)
        return df2

    def _get_task_ids_in_execution_order(self, filter_ids: Set[str]) -> List[str]:
        """P1 修复：按 Plan 的 DFS 执行顺序获取 task_ids"""
        if self.plan is None:
            return []

        ordered_ids = []
        try:
            # 尝试使用 Plan 的 output_r_code_by_tree 方法
            if hasattr(self.plan, "output_r_code_by_tree"):
                for tree_obj in self.plan.output_r_code_by_tree():
                    for task in tree_obj.get("nodes", []):
                        task_id = getattr(task, "task_id", None)
                        if task_id and task_id in filter_ids and task_id not in ordered_ids:
                            ordered_ids.append(task_id)
            # 否则遍历 roots
            elif hasattr(self.plan, "roots"):
                for root in self.plan.roots:
                    self._traverse_for_order(root, filter_ids, ordered_ids)
        except Exception:
            pass

        return ordered_ids

    @staticmethod
    def _clean_step_label(label: Any, idx: Any) -> str:
        text = str(label or f"Step {idx}")
        text = text.strip().replace("\n", "")
        if text.startswith("[1]"):
            text = text[3:].strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        return text or f"Step {idx}"

    def _traverse_for_order(self, node: Any, filter_ids: Set[str], result: List[str]) -> None:
        """DFS 遍历收集 task_id"""
        task = getattr(node, "task", None)
        if task is not None:
            task_id = getattr(task, "task_id", None)
            if task_id and task_id in filter_ids and task_id not in result:
                result.append(task_id)
        for child in getattr(node, "children", []):
            self._traverse_for_order(child, filter_ids, result)

    def _collect_dependencies(self) -> List[str]:
        """P1 修复：从 Plan 收集所有依赖库"""
        deps: Set[str] = set()

        if self.plan is None:
            return list(deps)

        try:
            if hasattr(self.plan, "output_r_code_by_tree"):
                for tree_obj in self.plan.output_r_code_by_tree():
                    tree_deps = tree_obj.get("deps", [])
                    deps.update(tree_deps)
        except Exception:
            pass

        return list(deps)

    def _build_task_obj_map(self) -> Dict[str, Any]:
        """构建 task_id -> task 对象的映射"""
        obj_map: Dict[str, Any] = {}

        if self.plan is not None:
            try:
                for root in getattr(self.plan, "roots", []):
                    self._traverse_for_obj(root, obj_map)
            except Exception:
                pass

        return obj_map

    def _traverse_for_obj(self, node: Any, obj_map: Dict[str, Any]) -> None:
        """遍历收集 task 对象"""
        task = getattr(node, "task", None)
        if task is not None:
            task_id = getattr(task, "task_id", None)
            if task_id:
                obj_map[task_id] = task
        for child in getattr(node, "children", []):
            self._traverse_for_obj(child, obj_map)

    def _get_task_dataset_key(self, task: Any) -> str:
        """获取任务的数据集 key"""
        if task is None:
            return "main"
        if hasattr(task, "get_dataset_key") and callable(getattr(task, "get_dataset_key")):
            return task.get_dataset_key()
        dataset = getattr(task, "dataset", None)
        if dataset:
            return self._to_dataset_key(dataset)
        return "main"

    def _unpack_code_text(self, ct: Any) -> Dict[str, Any]:
        """解包 code_text 为标准结构"""
        if isinstance(ct, str):
            result = {
                "prepare_code": "",
                "execute_code": ct,
                "post_regression_code": "",
                "combined": ct,
            }
            result["execute_code"] = self._strip_embedded_stepwise(result["execute_code"])
            result["combined"] = self._strip_embedded_stepwise(result["combined"])
            return result
        if isinstance(ct, dict):
            sanitized = dict(ct)
            for key in ["execute_code", "post_regression_code", "combined"]:
                val = sanitized.get(key)
                if isinstance(val, str):
                    sanitized[key] = self._strip_embedded_stepwise(val)
            return sanitized
        return {}

    def _get_task_ids_from_tables(self) -> List[str]:
        """从所有输出表格中收集 task_id（按原始任务 dedup）"""
        task_ids = []
        seen = set()

        for matrix in self.tables:
            for col in getattr(matrix, "columns", []) or []:
                source_id = getattr(col, "source_task_id", None) or getattr(col, "task_id", None)
                if source_id and source_id not in seen:
                    task_ids.append(source_id)
                    seen.add(source_id)

        return task_ids

    def _collect_stepwise_from_tables(self) -> Dict[str, Set[int]]:
        mapping: Dict[str, Set[int]] = {}
        for matrix in self.tables:
            for col in getattr(matrix, "columns", []) or []:
                if getattr(col, "result_target", None) != "stepwise":
                    continue
                idx = getattr(col, "stepwise_index", None)
                if idx is None:
                    continue
                try:
                    idx = int(idx)
                except Exception:
                    continue
                source_id = getattr(col, "source_task_id", None) or getattr(col, "task_id", None)
                if not source_id:
                    continue
                mapping.setdefault(source_id, set()).add(idx)
        return mapping

    def _build_task_code_map(self) -> Dict[str, Any]:
        """构建 task_id -> code_text 的映射"""
        code_map = {}

        # 方法1: 从 Plan 对象获取
        if self.plan is not None:
            try:
                for root in getattr(self.plan, "roots", []):
                    self._traverse_for_code(root, code_map)
            except Exception:
                pass

        # 方法2: 从 results_df 获取（如果有 code_text 列）
        if not code_map and not self.results_df.empty and "code_text" in self.results_df.columns:
            for _, row in self.results_df.iterrows():
                task_id = row.get("task_id")
                code_text = row.get("code_text")
                if task_id and code_text:
                    code_map[task_id] = code_text

        return code_map

    def _traverse_for_code(self, node: Any, code_map: Dict[str, Any]) -> None:
        """遍历节点树收集 code_text"""
        task = getattr(node, "task", None)
        if task is not None:
            task_id = getattr(task, "task_id", None)
            code_text = getattr(task, "_code_segments", None) or getattr(task, "code_text", None)
            if task_id and code_text:
                code_map[task_id] = code_text

        # 递归遍历子节点
        for child in getattr(node, "children", []):
            self._traverse_for_code(child, code_map)

    def _extract_code_from_code_text(self, code_text: Any) -> Optional[str]:
        """从 code_text 中提取 R 代码"""
        if code_text is None:
            return None

        if isinstance(code_text, str):
            return code_text

        if isinstance(code_text, dict):
            # 优先使用 combined
            combined = code_text.get("combined")
            if combined:
                return self._strip_embedded_stepwise(str(combined))

            # 否则拼接各部分
            parts = []
            for key in ["prepare_code", "execute_code", "post_regression_code"]:
                part = code_text.get(key)
                if part:
                    parts.append(str(part))
            if parts:
                return self._strip_embedded_stepwise("\n\n".join(parts))

        return None

    @staticmethod
    def _strip_embedded_stepwise(code: str) -> str:
        """移除 CodeGenerator 自动注入的 stepwise 数据段与控制段。"""
        if "stepwise_results_" not in code:
            return code

        lines = code.splitlines()
        output: List[str] = []
        skipping_setup = False
        skipping_controls = False
        for line in lines:
            stripped = line.strip()

            if not skipping_setup and "stepwise_results_" in line and "<- list" in line:
                skipping_setup = True
                continue
            if skipping_setup:
                if stripped.startswith("stepwise_res <-"):
                    output.append("stepwise_res <- NULL")
                    skipping_setup = False
                continue

            if not skipping_controls and line.startswith("# Stepwise controls for"):
                skipping_controls = True
                continue
            if skipping_controls:
                if stripped.startswith("python_output <-"):
                    output.append(line)
                    skipping_controls = False
                continue

            output.append(line)

        return "\n".join(output)

    def get_all_task_ids(self) -> List[str]:
        """获取所有表格中的 task_id（公共接口）"""
        return self._get_task_ids_from_tables()

    def __repr__(self) -> str:
        return f"ExportService(tables={len(self.tables)}, config_path={self.config_path!r})"
