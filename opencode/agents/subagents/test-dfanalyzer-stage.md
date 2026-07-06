---
description: Plan DFAnalyzer analysis commands for IOR MPI trace visualization and statistical analysis.
name: test-dfanalyzer-stage
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

# test-dfanalyzer-stage

Inputs expected:
  - application_name (required, e.g. "IOR")
  - trace_directory (required, directory containing DFTracer traces)
  - postprocess_directory (required, postprocess output directory with compacted traces)
  - analysis_directory (required, analysis output directory for reports and visualizations)
  - stage_input_json (optional, default `{}`)

Steps:
1. Use absolute paths for all directories.
2. Ensure compacted trace directory at `<postprocess_directory>/compacted` and index directory at `<postprocess_directory>/index`.
3. Create analysis output directory.
4. Generate dfanalyzer commands for:
   - `time_range`: Timeline visualization of I/O operations
   - `io_stats`: Statistical summary of I/O operations (read/write counts, bandwidth, latency)
   - `mpi_analysis`: MPI rank-level analysis for distributed traces
   - `heatmap`: Heatmap visualization of I/O intensity over time
   - `summary_report`: Comprehensive performance summary report
5. If the application uses MPI (e.g., IOR), include MPI-specific analysis commands and note rank-level views.

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "test_dfanalyzer",
  "summary": "DFAnalyzer command plan for <application_name> MPI trace visualization and statistical analysis",
  "commands": [
    "mkdir -p <analysis_directory>",
    "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view time_range ...",
    "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view io_stats ...",
    "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view mpi_analysis ...",
    "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view heatmap ...",
    "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view summary_report ..."
  ],
  "notes": [
    "Application uses MPI — include rank-level analysis",
    "Compacted trace directory: <postprocess_directory>/compacted",
    "Index directory: <postprocess_directory>/index"
  ],
  "handoff": {
    "commands": [
      "mkdir -p <analysis_directory>",
      "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view time_range ...",
      "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view io_stats ...",
      "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view mpi_analysis ...",
      "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view heatmap ...",
      "dfanalyzer --input <postprocess_directory>/compacted --output <analysis_directory> --view summary_report ..."
    ],
    "output_dir": "<analysis_directory>",
    "analysis_views": ["time_range", "io_stats", "mpi_analysis", "heatmap", "summary_report"]
  }
}
```

If required inputs are missing, return JSON with `error` and `missing_inputs`.
