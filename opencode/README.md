# OpenCode Configuration for DFTracer Agents

This folder contains the OpenCode agent/command configuration needed to run the DFTracer annotation, trace, and optimization pipeline.

## Installation

Copy or symlink this folder into your project as `.opencode/`:

```bash
# Option A: symlink (keeps this repo as the source of truth)
ln -s $(pwd)dftracer-agents/opencode .opencode

# Option A2: symlink the skills directory (source of truth for dftracer skills)
ln -s ../.agents/skills .opencode/skills

# Option B: copy
rsync -av $(pwd)dftracer-agents/opencode/ .opencode/
```

`.agents/skills/` is the source of truth for the DFTracer skill definitions used by the agents.

> **Note:** The absolute paths in the examples above (`$(pwd)dftracer-agents/...`) are specific to this repository. Adjust them to match your own checkout location.

## Set model placeholders

`opencode.json` and the agent files use placeholders for the orchestrator and action models. Run:

```bash
./.opencode/set-models.sh
```

Optional arguments:

```bash
./.opencode/set-models.sh --orchestrator ollama/qwen3.5:397b-cloud --action ollama/kimi-k2.7-code:cloud
```

Defaults:

- Orchestrator: `ollama/qwen3.5:397b-cloud`
- Action: `ollama/kimi-k2.7-code:cloud`

## Restart OpenCode

After changing any configuration, agent, or command file, restart OpenCode so it reloads the configuration.

```bash
# If running in a terminal, exit and relaunch opencode
opencode
```

## MCP Server

The DFTracer MCP server is provided via `uvx` from the `feature/fresh-start` branch of `llnl/dftracer-agents`:

```json
"mcp": {
  "dftracer": {
    "type": "local",
    "command": [
      "uvx",
      "--from",
      "git+https://github.com/llnl/dftracer-agents@feature/fresh-start",
      "dftracer-mcp-server"
    ],
    "enabled": true,
    "env": {}
  }
}
```

## Agents

| Path                                            | Description                                                                            |
| ----------------------------------------------- | -------------------------------------------------------------------------------------- |
| `agents/dftracer-pipeline.md`                   | Primary orchestrator for the full annotation, trace, and optimization pipeline.        |
| `agents/subagents/detect-stage.md`              | Detect HDF5/MPI and create the DFTracer session.                                       |
| `agents/subagents/build-setup-stage.md`         | Configure, build, install original app and DFTracer baseline.                          |
| `agents/subagents/annotate-stage.md`            | Annotate all C/C++ source files using clang MCP tools.                                 |
| `agents/subagents/build-with-dftracer-stage.md` | Build annotated source and run the DFTracer smoke test.                                |
| `agents/subagents/trace-collection-stage.md`    | Run the DFTracer-instrumented smoke test, collect traces, and run an initial analysis. |
| `agents/subagents/postprocess-stage.md`         | Compact, index, split, and summarize traces.                                           |
| `agents/subagents/dfanalyzer-stage.md`          | Plan DFAnalyzer visualization and analysis commands.                                   |
| `agents/subagents/test-dfanalyzer-stage.md`     | IOR/MPI-specific DFAnalyzer analysis plan.                                             |
| `agents/subagents/optimization-stage.md`        | Run the iterative L1/L2/L3 optimization pipeline.                                      |

## Commands

| Path                            | Command              | Description                     |
| ------------------------------- | -------------------- | ------------------------------- |
| `commands/dftracer-pipeline.md` | `/dftracer-pipeline` | Run the full DFTracer pipeline. |

Invoke the command with arguments. `$ARGUMENTS` is parsed as space-separated `key=value` pairs:

```text
/dftracer-pipeline url=https://github.com/example/app ref=main smoke_cmd="./smoke" extra_flags="-DENABLE_X=ON"
```

If arguments are missing, the command handler will ask for them one at a time.

## Important Notes

- The orchestrator can run the pipeline end-to-end itself or delegate each major stage to the subagents via the `task` tool.
- All source annotation must use the MCP clang tools (`clang_annotate_project`, `clang_annotate_file`, `clang_syntax_check`, `clang_lint_annotations`). Manual macro insertion is forbidden.
- Each subagent returns a JSON envelope with `stage`, `summary`, `commands`, `notes`, and `handoff` fields.
- The final orchestrator combines all stage outputs into `{"summary": "...", "stages": {...}}`, writes `session_report.md`, and prints a one-line summary.
