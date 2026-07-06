---
description: Annotate all C/C++ source files using DFTracer clang MCP tools.
name: annotate-stage
mode: subagent
model: ollama/kimi-k2.7-code:cloud
temperature: 0.1
permission:
  read: allow
  edit:
    "*": ask
  bash:
    "uvx *": allow
    "python *": allow
    "mkdir *": allow
    "cp *": allow
    "mv *": allow
    "ls *": allow
    "cat *": allow
    "rg *": allow
    "grep *": allow
    "*": ask
  task: allow
  glob: allow
  grep: allow
  list: allow
  skill: allow
  todowrite: allow
  external_directory:
    "workspaces/**": allow
    "*": ask
---

# annotate-stage

Inputs expected:
  - run_id
  - workspace
  - language (default "c", or "cpp")

Steps:
1. Prefer a single project-level annotation:
   `clang_annotate_project(run_id=RUN_ID, language=language, init_args="NULL, NULL, NULL",
    exclude_patterns=["test/", "tests/", "vendor/", "third_party/"])`
2. If the project-level call reports errors for specific files, switch to per-file mode for those files only:
   - `clang_extract_functions(run_id=RUN_ID, filepath=<file>)`
   - `clang_estimate_function_cost(run_id=RUN_ID, filepath=<file>, function_name=<name>)`
   - `clang_annotate_file(run_id=RUN_ID, filepath=<file>, is_entry=<bool>, language=language,
      init_args="NULL, NULL, NULL", comp_overrides='{"fn_name": "comm", "other_fn": "io"}')`
3. Validate every annotated file:
   - `clang_syntax_check(run_id=RUN_ID, filepath=<file>)`
   - `clang_lint_annotations(run_id=RUN_ID, filepath=<file>)`
   - On syntax failure: fix only the failing function via clang_annotate_file with comp_overrides/exclude (max 2 attempts), then mark PENDING on 2nd failure.
   - On lint failure: use clang_insert_line to fix only the reported line.
4. Print per-file status: `✓ <file>  (<n> functions annotated, lint PASSED)`

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "annotate",
  "summary": "Project annotated and validated",
  "commands": ["clang_annotate_project(...)", "clang_syntax_check(...)", "clang_lint_annotations(...)"],
  "notes": ["N files annotated", "M functions skipped", "any PENDING functions"],
  "handoff": {
    "run_id": "<RUN_ID>",
    "files_annotated": 0,
    "functions_annotated": 0,
    "functions_skipped": 0,
    "pending_functions": [],
    "language": "c"
  }
}
```

If annotation completely fails (tool error), return JSON with `error` field.
