# Changelog

All notable changes are documented here. This project follows semantic versioning once it reaches
1.0.0; before that, APIs may change between minor releases.

## [0.2.0] - 2026-02-25
- Persist regression plans via a dedicated config schema so CodeExecutor caches now serialize tasks,
  options, and generated code safely, resolving prior weakref/Jinja serialization crashes.
- Capture exec results plus dataset snapshots in cache payloads, restore them into rebuilt plans, and
  let the TUI bootstrap offline without any Arctic refresh cycle.
- Extend `StandardRegTask` specs to store `_code_segments`/`code_text`, enabling reproducibility
  exports and helper services to reuse the exact R blocks that were executed.
- Update run_app, SharedState, and ExportService so cached sessions expose the same datasets, tables,
  and metadata as live runs, making coefficient browsing and feather/main.R exports possible from
  caches alone.
- Harden helper utilities (result browser, plan traversal, dataset snapshots) around the new cache
  workflow so repeated executions deduplicate work while retaining task IDs and stepwise metadata.

## [0.1.1] - 2026-02-20
- Initial PyPI metadata.
- Helper scripts and docs for publishing to TestPyPI/PyPI.
- Baseline DataLoader/DataManager/CodeGenerator/CodeExecutor integration.

## [0.1.0] - 2026-02-10
- First public preview: DataLoader framework, semantic refresh policy, regression planner, and TUI
  table editor/result browser.

---

Unreleased changes should be recorded under a new heading (e.g., `## [0.1.3] - YYYY-MM-DD`) before
being merged to `main`.
