# dftracer-agents

MCP (Model Context Protocol) server that exposes the [dftracer](https://github.com/llnl/dftracer) I/O tracing toolkit as tools for LLM agents.

Includes three services:

- **dftracer_utils** — 21 tools wrapping the `dftracer_*` CLI binaries (`info`, `stats`, `merge`, `split`, `index`, `reader`, `pgzip`, `tar`, `organize`, `replay`, …)
- **dfanalyzer** — 5 tools for trace analysis (`analyze`, `summarize_trace`, `detect_preset`, `query`, `list_presets`)
- **dftracer_plot** — 2 tools for generating charts from trace data (`plot`, `plot_all`)

The server speaks the MCP stdio transport and works with any MCP-compatible agent (Goose, Claude Desktop, custom agents).

---

## Setup

```bash
git clone <this-repo>
cd dftracer-agents

python -m venv venv
source venv/bin/activate

pip install -e .
```

This installs console scripts including:

- `dftracer_agents_stack` — start the whole stack (MCP server + profiling + MLflow)
- `dftracer-mcp-server`
- `dftracer-profile-collector`
- `dftracer-install-skills`
- `dftracer-install-agents`
- `dftracer-bootstrap-workspace`
- `dftracer-configure-harness`
- `dftracer-configure-env` — create/update `.env` with API keys and auth tokens (see [Configuring API keys and auth tokens](#configuring-api-keys-and-auth-tokens))

### Quick setup (pip or uv)

dftracer-agents must be installed **into an environment** — a Python venv or a
conda env. The stack launcher discovers its daemons from that environment's
`bin/`, so an unactivated environment is a hard error rather than a silent
fallback to the system Python.

```bash
# Option A: pip
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e '.[profile]'

# Option B: uv
uv venv
source .venv/bin/activate
uv pip install -e '.[profile]'
```

The `[profile]` extra pulls in MLflow. Without it everything still runs and each
session still gets its `performance/` report — only the MLflow mirroring is
skipped.

---

## Running the whole stack

`dftracer_agents_stack` starts the three long-lived processes in one command and
prints the environment Claude Code needs:

| Service | Default port | What it does |
| --- | --- | --- |
| `mlflow` | 5001 | Tracking server + UI, SQLite-backed |
| `collector` | 4318 | Receives Claude Code OTLP telemetry, attributes it to pipeline steps |
| `mcp` | 5000 | The dftracer MCP tool surface (auto-reloads on source edits) |

```bash
# Start (or refresh) everything
dftracer_agents_stack start

# Launch Claude Code wired into the stack
eval "$(dftracer_agents_stack env)" && claude

# What is up, what is stale, and what has the current run cost?
dftracer_agents_stack status

# Delete stale pid files and reap orphaned daemons
dftracer_agents_stack clean
dftracer_agents_stack clean --untracked  # also kill daemons the stack didn't start

# Re-point the harness configs at the managed server (start does this for you)
dftracer_agents_stack client

# Follow a service's log
dftracer_agents_stack logs collector     # mlflow | collector | mcp | graph

# Stop everything (the collector flushes its profile first)
dftracer_agents_stack stop
```

### VSCode extension users: export the env before VSCode starts

`eval "$(dftracer_agents_stack env)" && claude` works because the exporter is
configured from the environment Claude Code is **launched with**. The VSCode
extension spawns its own `claude` binary and never runs that `eval`, so telemetry
silently does not export. The `env` block in `.claude/settings.json` does not
rescue this: it reaches tool subprocesses, but not the telemetry SDK, which is
configured at process start.

The symptom is a profile that looks healthy but has no numbers — `profile_bind`
succeeds, the MLflow parent run appears, steps record their wall time and
retries, and every token and dollar column reads zero. `profile_status` shows
`events_seen: 0` and the raw log under `<workspace>/performance/otlp/` stays at
zero bytes.

The env has to be in the real process environment of the **vscode-server**, which
every extension host and every `claude` process inherits. Neither `~/.profile`
nor `~/.bashrc` is reliable here: Remote-SSH launches the server through a
non-login, non-interactive shell. Remote-SSH does source
`~/.vscode-server/server-env-setup` before starting the server, so put it there:

```bash
dftracer_agents_stack env >> "$HOME/.vscode-server/server-env-setup"
```

Then restart the server — **not** a window reload. Reloading respawns the
extension host from the server process that is *already running* with the old
environment, so it changes nothing:

- **Remote SSH:** Command Palette → **"Remote-SSH: Kill VS Code Server on Host"**,
  pick the host, then reconnect. The server respawns and sources the file.
- **Fallback, from a terminal on the remote host:** `pkill -u "$USER" -f vscode-server`,
  then reconnect from VSCode.
- **Local (non-remote) VSCode:** quit the application entirely — not just the
  window — and relaunch it from a shell that has the env exported.

Verify the server itself picked it up:

```bash
tr '\0' '\n' < /proc/$(pgrep -u "$USER" -f server-main.js | head -1)/environ | grep -c '^OTEL_'
```

Anything less than the full count means the server did not source the file. Then
call `profile_status` and check that `events_seen` climbs on its own as you work.

> Do not diagnose this by grepping `env` inside a Bash tool call. Claude Code does
> not re-export `OTEL_*` to child processes, so their absence there is expected
> and proves nothing — in either the working or the broken state. `events_seen`,
> and the server's own `/proc/<pid>/environ`, are the only reliable signals.

If `events_seen` is still `0` once the server env is confirmed, check that the
collector is actually up and reachable (`dftracer_agents_stack logs collector`).
To separate a dead receiver from a silent sender, POST a synthetic record and
watch the raw log grow:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:4318/v1/logs \
  -H 'Content-Type: application/json' \
  -d '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":"probe"}}]}]}]}'
```

A `200` plus a new line in `<workspace>/performance/otlp/events-<date>.jsonl`
means the receiver and MLflow sink are healthy and the problem is upstream env.
Delete the probe line afterwards so it does not pollute the profile.

### The stack runs the server; the harnesses connect to it

By default every harness *spawns its own private stdio MCP server* from a
`command` entry in its config, ignoring anything you started yourself. `start`
therefore rewrites those entries to point at the managed HTTP server:

| Harness | Config file | Entry written |
| --- | --- | --- |
| Claude Code | `.mcp.json` | `mcpServers.dftracer.url` |
| GitHub Copilot | `.vscode/mcp.json` | `servers.dftracer.url` |
| OpenCode | `.opencode/opencode.jsonc` | `mcp.dftracer.url` |

`opencode.jsonc` holds your provider and model settings alongside comments that a
JSON round-trip would delete, so if it has comments the exact snippet to paste is
printed instead of rewritten.

Restart the harness after the first `start` — a client only reads its MCP config
at launch. Afterwards `dftracer_agents_stack status` should show no untracked
`mcp` process; if it does, that harness is still spawning its own.

### Finding stale and orphaned processes

Pid files record what the launcher *believes* is running. `status` reconciles
them against `/proc` and reports what they miss:

- **stale pid file** — the process died; the file outlived it.
- **orphan** — a supervisor was killed but the child that binds the port
  survived, or a daemon of ours is sitting on a port we manage. `clean` reaps it.
- **untracked** — a daemon this run didn't start but still belongs to *you*: a
  harness's own stdio server, or a leftover from a different checkout / port
  config of your own. Never another user's process — daemons are matched by
  process owner (`uid`) first, unconditionally, regardless of command line or
  ports. Reported, never killed without `--untracked`.

Orphans get `SIGTERM` before `SIGKILL`, so the collector still flushes its
profile and closes its MLflow run on the way out.

`start` is **idempotent**. Re-running it never launches a second copy: each
service is left alone if it is healthy and configured identically, and is
restarted only if it died, stopped answering its port, or its configuration
changed. So it doubles as a refresh command after editing config or
reinstalling. If a port is held by a process the stack did not start, it refuses
to touch it and tells you which one.

Ports and paths are overridable:

```bash
MCP_PORT=5100 MLFLOW_PORT=5101 COLLECTOR_PORT=4400 dftracer_agents_stack start
DFTRACER_WORKSPACES=/path/to/workspaces dftracer_agents_stack start
```

Or let `dftracer-configure-env` pick free ports for you once and persist them
to `.env` (recommended on shared HPC login nodes, where the defaults collide
often) — see [Configuring API keys and auth tokens](#configuring-api-keys-and-auth-tokens).

State lives under `workspaces/_stack/` (pid files, logs) and
`workspaces/_mlflow/` (SQLite DB, artifacts) — never `/tmp`.

The knowledge graph (`graphify`) is warmed at start rather than run as a daemon,
so the first agent `graph_query` does not pay the build cost. Re-warming is free
when nothing changed.

> **First run only:** the MCP server asks once, interactively, which harness and
> models to use. A daemon cannot answer that, so run
> `dftracer-configure-harness` before the first `dftracer_agents_stack start`.

---

## Configuring API keys and auth tokens

Run `dftracer-configure-env` once after installing to create `.env` at the
project root and fill in the values it needs:

```bash
dftracer-configure-env                    # interactive prompts
dftracer-configure-env --non-interactive  # only auto-generate tokens; seed
                                           # API keys from the environment
                                           # when already exported
```

It finds the project root the same way `dftracer_agents_stack` does (walks up
from the current directory for `workspaces/`, then `.git`/`pyproject.toml`),
so it's safe to run from anywhere inside a checkout. It's idempotent — re-run
any time; it only fills in blanks and never overwrites a value you already
set, and it never prints secret values back to the terminal.

It manages three groups of `.env` values:

- **Auth tokens** — `DFTRACER_MCP_TOKEN` / `DFTRACER_COLLECTOR_TOKEN`,
  auto-generated with `secrets.token_hex(32)` if blank. These gate the
  Docker Compose stack's MCP/OTLP endpoints behind Caddy (see
  [Docker](#docker-containerized-stack-with-authenticated-remote-access) below).
- **Academic paper search keys** — `SEMANTIC_SCHOLAR_API_KEY`, `CORE_API_KEY`,
  `OPENALEX_MAILTO`. All optional: every source in the `AcademicPapers` tool
  group (arXiv, Semantic Scholar, OpenAlex, Crossref, CORE, DBLP, web search —
  see [docs/tools.rst](docs/tools.rst)) falls back to anonymous, client-side
  rate-limited access when these are blank, so leaving them empty is a normal,
  fully supported configuration. Semantic Scholar's rate limit is 1
  request/second whether or not a key is set — a key only raises the *daily*
  quota — so the client-side limiter always keeps a buffer above 1.0s
  regardless.
- **Stack ports** — `MCP_PORT` / `COLLECTOR_PORT` / `MLFLOW_PORT`. Each is
  probed with a real bind on `127.0.0.1`: if the launcher's default
  (5000/4318/5001) is free it's used as-is, otherwise the next free port is
  picked automatically (ports already claimed by one of the *other* two
  services in the same run are skipped too, so you never get two services
  assigned the same port). Left blank only if no free port is found in range —
  set it by hand in that case. Shared HPC login nodes commonly have the
  defaults already taken by another user's session, which is exactly the
  `dftracer_agents_stack start` → `REFUSING to start ... is held by a
  process` failure this sidesteps.

`.env` is only ever read by two things: `docker compose` (automatically), and
`dftracer_agents_stack` in bare-metal/local mode, which now sources it before
starting the `mcp-server` daemon so these values reach the running process
either way.

---

## Docker: containerized stack with authenticated remote access

For running the stack as a container instead of `dftracer_agents_stack start`
on bare metal — e.g. so a local Docker Desktop instance or a remote HPC login
node can be reached from VS Code over a fixed set of authenticated ports. Full
details, including secret generation and remote-tunnel setup, are in
[docs/docker.rst](docs/docker.rst); summary below.

```bash
cp .env.example .env
# fill in DFTRACER_MCP_TOKEN, DFTRACER_COLLECTOR_TOKEN,
# MLFLOW_BASIC_AUTH_USER, MLFLOW_PASSWORD_HASH — see docs/docker.rst
docker compose up -d
```

This builds [docker/Dockerfile](docker/Dockerfile) (the `dftracer_agents_stack`
supervising mcp/collector/mlflow inside the container) and fronts it with a
[docker/Caddyfile](docker/Caddyfile) reverse proxy that is the **only**
published port:

| Endpoint | URL | Auth |
| --- | --- | --- |
| MCP | `http://localhost:8443/mcp` | `Authorization: Bearer $DFTRACER_MCP_TOKEN` |
| OTLP collector | `http://localhost:8443/v1/traces` (etc.) | `Authorization: Bearer $DFTRACER_COLLECTOR_TOKEN` |
| MLflow UI | `http://localhost:8443/mlflow` | HTTP basic-auth prompt |

The `mcp`/`collector`/`mlflow` containers themselves sit on an internal-only
Docker network with no host ports published — they are unreachable except
through Caddy's auth checks.

### Generating the secrets

```bash
# Bearer tokens — any random string
openssl rand -hex 32   # -> DFTRACER_MCP_TOKEN
openssl rand -hex 32   # -> DFTRACER_COLLECTOR_TOKEN

# MLflow basic-auth password hash (never store the plaintext)
docker run --rm caddy:2 caddy hash-password --plaintext '<your password>'
# -> MLFLOW_PASSWORD_HASH (quote it in .env: the leading $2a$ confuses
#    Compose's .env variable-interpolation if left unquoted)
MLFLOW_BASIC_AUTH_USER=admin
```

You type the **plaintext** password into the browser's basic-auth prompt; only
the bcrypt hash ever lives in `.env`.

### Connecting from VS Code / Claude Code

```json
{
  "servers": {
    "dftracer": {
      "type": "http",
      "url": "http://localhost:8443/mcp",
      "headers": { "Authorization": "Bearer ${env:DFTRACER_MCP_TOKEN}" }
    }
  }
}
```

```bash
claude mcp add --transport http dftracer http://localhost:8443/mcp \
  --header "Authorization: Bearer $DFTRACER_MCP_TOKEN"

export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8443
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $DFTRACER_COLLECTOR_TOKEN"
```

### Reaching a remote instance

The stack (bare-metal or containerized) only ever binds `8443`/`5000`/`4318`/`5001`
to `127.0.0.1` — never publish it on a public interface, auth or not. Reach a
remote host over an SSH tunnel and connect to `localhost` exactly as above:

```bash
ssh -N -L 8443:localhost:8443 <remote-host>
```

`.env` is gitignored and per-host by design: generate independent tokens and
passwords for each environment you run this in, so a leak on one machine does
not compromise another.

---

## Pipeline profiling (MLflow)

Every agent step is measured: how long it took, how many times it was tried, how
many tokens it burned, and what that cost in USD.

**Where the numbers come from.** With the stack's environment exported, Claude
Code emits OpenTelemetry. Its `claude_code.api_request` event already carries
`cost_usd`, `duration_ms`, all four token counts and the `agent.name` that
produced them; `claude_code.tool_result` carries per-tool timings and failures.
The collector receives those over OTLP/JSON, and attributes each event to
whichever pipeline step's attempt interval contains its timestamp — not to
whatever step happens to be open when the event arrives, because Claude Code
buffers events for up to 5 s and they routinely land a step late.

**How steps are marked.** Agents call the `profile_*` MCP tools:

| Tool | Purpose |
| --- | --- |
| `profile_bind` | Attach the profile to a session (once, after `session_create`) |
| `profile_step_begin` | Open a step. **Same step name again = a retry**, recorded as a second attempt |
| `profile_step_end` | Close the attempt with an outcome (`ok`, `lint_failed`, …) |
| `profile_status` | Running totals: cost, tokens, per-step timing, tries, retries |
| `profile_report` | Flush and return the rendered performance report |

If the collector is not running, these return `profiling: disabled` and succeed.
Profiling is observability: an agent never abandons a step because the observer
is down.

**Where the results land.** Each session gets a `performance/` directory:

```text
workspaces/<app>/<run_id>/performance/
    performance_report.md     ← summary, per-step table, rework, slowest tools
    summary.json              ← the whole profile snapshot
    steps/<n>-<step>.json     ← one file per pipeline step, live-updated
    otlp/events-<date>.jsonl  ← raw telemetry
    mlflow.json               ← experiment / parent-run / UI deep link
```

`session_final_report` folds this into the deliverable as `final_report/PERFORMANCE.md`
plus `final_report/performance/`.

In MLflow (default `http://localhost:5001`) the same data appears as one parent
run per session and one **nested run per pipeline step**, with metrics for
`cost_usd`, `tokens_*`, `exec_s`, `wall_s`, `tries`, `retries`,
`failed_attempts` and `cost_usd_per_tool_call`. A step that fails and is retried
shows `FAILED → RUNNING → FINISHED`.

The report also reconciles the sum of per-event costs against the independent
`claude_code.cost.usage` counter, and says so when they disagree — silent
under-counting would make the profile worse than none.

Bootstrap the workspace links from source-of-truth files:

```bash
dftracer-bootstrap-workspace --target . --force
```

Configure harness backend and model classes (haiku/sonnet/opus):

```bash
# Show current harness/model mapping
dftracer-configure-harness --show

# Interactive guided setup (recommended)
dftracer-configure-harness --interactive

# Example: set OpenCode to Ollama with semantic class levels
dftracer-configure-harness \
  --harness opencode \
  --provider ollama \
  --class-level-1 haiku \
  --class-level-2 sonnet \
  --class-level-3 sonnet \
  --class-level-4 opus

# Example: explicit model override for one level
dftracer-configure-harness \
  --harness opencode \
  --model-level-4 deepseek-v3.2:cloud
```

Start MCP server daemon (default behavior):

```bash
dftracer-mcp-server
```

Daemon control commands:

```bash
# Start/restart daemon (kills existing PID if running)
dftracer-mcp-server start

# Stop daemon
dftracer-mcp-server stop

# Status
dftracer-mcp-server status

# Run in foreground (no daemon)
dftracer-mcp-server run --service both --transport http
```

Runtime files are stored at:

```bash
/tmp/$USER/dftracer-agents/
```

Including:

- `dftracer-mcp-server.pid`
- `dftracer-mcp-server.out.log`
- `dftracer-mcp-server.err.log`

Configuration state files are stored in:

- `src/dftracer_agents/.agents/workspace/active-models.json`
- `src/dftracer_agents/.agents/workspace/setup-state.json`

On startup, the server now runs an interactive setup flow first:

- if previous setup exists, asks whether to continue with previous harness/model config
- choose harness(es)
- choose provider per harness
- choose model for each level (Enter accepts defaults)

After that, it applies setup/symlink steps and starts/restarts the MCP daemon.

The server prints:

- which setup steps ran (`Skills`, `Agents`, `Workspace`)
- which harnesses are configured (`claude`, `opencode`, `copilot`)
- provider and resolved model for `level_1..level_4`

---

## Installing agent skills

The package bundles a set of agent skills (Claude Code / Goose SKILL.md files) that teach the AI agent how to use dftracer effectively. After `pip install`, copy them into your project so the agent harness can find them:

```bash
# Copy skills into ./.agents/skills/ (current directory)
dftracer-install-skills

# Copy into a specific project directory
dftracer-install-skills /path/to/my-project

# Replace any existing skills with the packaged versions
dftracer-install-skills --overwrite

# Print the path to the bundled skills without copying
dftracer-install-skills --list
```

Skills are installed to `<target>/.agents/skills/` and cover:

| Skill | Purpose |
|---|---|
| `dftracer-pipeline` | Full annotation + trace pipeline workflow |
| `dftracer-annotate-c` / `cpp` / `python` | Per-language annotation rules |
| `dftracer-trace-utils` | When and how to use MCP trace tools |
| `dftracer-install` | Installation and configuration |
| `dftracer-io-optimization` | I/O bottleneck analysis and optimization |
| `dftracer-lessons` / `dftracer-pitfalls` | Hard-won annotation lessons and common mistakes |
| `dftracer-cheatsheet` | Quick reference for dftracer macros and APIs |
| … | 18 skills total |

You can also locate or install skills programmatically:

```python
from dftracer_agents import bundled_skills_dir, install_skills

# Path to the skills inside the installed package
path = bundled_skills_dir()

# Copy to a directory (skips existing by default)
install_skills("/path/to/project", overwrite=False)
```

---

## Pipeline

The full annotation → trace → diagnosis → optimization pipeline is documented with a Mermaid flowchart:

**[docs/pipeline.md](docs/pipeline.md)**

It covers every MCP tool call in order, which sub-service owns each one, the workspace directory layout, and how per-file annotation parallelism and L1 optimization iterations work.

---

## Running the tests

Tests call the real MCP tool functions and compare output against direct subprocess calls — no mocking.

```bash
source venv/bin/activate
cd dftracer-agents

# Run everything
python -m pytest test/ -v

# Run only dfanalyzer service tests
python -m pytest test/test_dfanalyzer_service.py -v

# Run only dftracer_utils tool tests
python -m pytest test/test_dftracer_utils_mcp_tools.py -v

# Run a single test by name
python -m pytest test/test_dfanalyzer_service.py::test_summarize_trace_on_sample_data -v
```

Example output:

```
test/test_dfanalyzer_service.py::test_hydra_args_minimal PASSED
test/test_dfanalyzer_service.py::test_summarize_trace_on_sample_data PASSED

  summarize_trace output:
  DFTracer Trace Summary: test/data/cm1_1_48_20240926
  ============================================================
    Trace files : 48 total
    Total events: 284,041
    Processes   : 48
    Threads     : 48
    Duration    : 145.364s
    Bytes read  : 2.7 MB
    Bytes written: 688.3 MB

  Top I/O operations:
    write                  112,353
    __xstat                 46,899
    fclose                  25,992
    open                    21,814
    close                   21,563
    ...

test/test_dftracer_utils_mcp_tools.py::test_pgzip_on_empty_dir_succeeds PASSED
test/test_dftracer_utils_mcp_tools.py::test_tool_and_subprocess_fail_consistently[info] PASSED
...
33 passed, 3 skipped
```

The 3 skipped tests are `server` (blocks forever), `aggregator_mpi`, and `call_tree_mpi` (require an MPI build).

### What the tests verify

Each parametrized test in `test_dftracer_utils_mcp_tools.py` runs both the direct binary and the MCP tool function, then asserts both produce the same outcome (both succeed or both fail). This catches drift between the MCP wrapper and the underlying binary.

```
test_tool_and_subprocess_fail_consistently[info]
  [info] segfaults on this platform (io_uring probe failure)
  direct: dftracer_info -d test/data/cm1_1_48_20240926 --query summary
    rc=-11
  mcp tool (info): raised=True
    CalledProcessError(returncode=-11)
  both failed — direct rc=-11, mcp rc=-11  ✓
```

---

## Interactive MCP REPL

A text REPL for manual tool exploration — useful for debugging tools without writing test code.

```bash
source venv/bin/activate

# All services: utils + analyzer + plot (28 tools, default)
python test/mcp_repl.py --service both

# dftracer_utils only (21 tools)
python test/mcp_repl.py --service utils

# dfanalyzer + plot (7 tools)
python test/mcp_repl.py --service analyzer
```

Example session:

```
Starting MCP server (both)…
============================================================
  dftracer-agents MCP REPL
============================================================
  28 tools available.  Type 'list' to see them.
  Syntax:  <tool_name> [<json-args>]
  Example: info {"directory": "/path/to/traces"}
  Commands: list, desc <tool>, quit
============================================================

mcp> list
  aggregator              Aggregate trace files …
  analyze                 Analyze an I/O trace using dfanalyzer …
  info                    Show a summary of trace files in a directory …
  summarize_trace         Summarize a dftracer I/O trace directory …
  …

mcp> desc summarize_trace
Tool: summarize_trace
Parameters:
  trace_path: string (required)
  max_files:  integer  default=50

mcp> summarize_trace {"trace_path": "/path/to/traces"}
  → calling summarize_trace({'trace_path': '/path/to/traces'}) ...

DFTracer Trace Summary: /path/to/traces
============================================================
  Trace files : 48 total
  Total events: 284,041
  Processes   : 48
  Duration    : 145.364s
  Bytes read  : 2.7 MB
  Bytes written: 688.3 MB
  …

mcp> quit
```

---

## LLM agent (ollama)

`test/mcp_agent.py` connects the MCP server to a local LLM via the OpenAI-compatible API. You ask questions in plain English; the LLM decides which tools to call.

### Prerequisites

```bash
# Install and start ollama
ollama serve
ollama pull qwen2.5-coder:7b
```

### Configure the LLM endpoint

Edit `test/openai_client_config.json`:

```json
{
  "provider": "openai",
  "base_url": "http://localhost:11434/v1",
  "api_key": "ollama",
  "model": "qwen2.5-coder:7b"
}
```

If running inside Docker and ollama is on the host:

```json
{
  "base_url": "http://host.docker.internal:11434/v1",
  "api_key": "ollama",
  "model": "qwen2.5-coder:7b"
}
```

### Run

```bash
source venv/bin/activate

# All services: utils + analyzer + plot (28 tools)
python test/mcp_agent.py --service both

# dfanalyzer + plot (7 tools)
python test/mcp_agent.py --service analyzer

# dftracer_utils only (21 tools)
python test/mcp_agent.py --service utils

# Custom LLM config file
python test/mcp_agent.py --config /path/to/config.json
```

### Example session

```
Starting MCP server (both)…
Connecting to LLM at http://localhost:11434/v1 (model=qwen2.5-coder:7b)

============================================================
  dftracer-agents LLM Agent
============================================================
  Model : qwen2.5-coder:7b
  Tools : 28 MCP tools available
  Type your question in plain English.  Ctrl-C or 'quit' to exit.
============================================================

you> summarize the trace at /data/cm1_trace

  [tool] summarize_trace({"trace_path":"/data/cm1_trace"})
  [result] DFTracer Trace Summary: /data/cm1_trace …

agent> The trace contains 48 processes, ran for 145 seconds, and performed
       112,353 write calls transferring 688 MB. The dominant operation was
       write (40% of all calls), followed by stat and open/close pairs typical
       of HPC checkpoint I/O.

you> which files were accessed most?

  [tool] analyze({"trace_path":"/data/cm1_trace","view_types":["file_name"]})
  …
```

---

## Goose integration

[Goose](https://github.com/block/goose) is an open-source AI agent that natively supports MCP servers as extensions.

### Install Goose

```bash
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh \
  | CONFIGURE=false GOOSE_BIN_DIR=/usr/local/bin bash
```

Node.js ≥ 20 is required for `goose tui`. Install it if not present:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
```

### Configure the dftracer extension

Add to `~/.config/goose/config.yaml`:

```yaml
extensions:
  dftracer:
    type: stdio
    cmd: dftracer-mcp-server
    args: []
    enabled: true
    description: >
      DFTracer I/O tracing toolkit. Use summarize_trace() for a quick overview
      of any trace directory, analyze() for full dfanalyzer pipeline output,
      or the dftracer_utils tools (info, stats, merge, split, …) for file-level
      operations.
```

If `dftracer-mcp-server` is not on your PATH (e.g. installed in a venv), point directly at the Python interpreter:

```yaml
extensions:
  dftracer:
    type: stdio
    cmd: /path/to/dftracer-agents/venv/bin/python
    args:
      - /path/to/dftracer-agents/dftracer_mcp_server.py
    enabled: true
```

Expose only one service if you want a smaller tool set:

```yaml
extensions:
  dftracer:
    type: stdio
    cmd: dftracer-mcp-server
    args: [--service, analyzer]   # or: --service utils
    enabled: true
```

### Using Goose to annotate an application and run the full pipeline

The annotation pipeline clones a target application, detects languages,
auto-annotates source files with dftracer macros, verifies correctness with
a build + smoke test, pauses for your review, then collects and analyzes
traces.

Pipeline files live in `dftracer-agents/recipes/`:

| File | Purpose |
|---|---|
| `pipeline.yaml` | Main orchestration recipe (headless) |
| `annotate-c.yaml` | Per-file C annotation sub-recipe |
| `annotate-cpp.yaml` | Per-file C++ annotation sub-recipe |
| `annotate-python.yaml` | Per-file Python annotation sub-recipe |
| `_inc-top.inc` | Shared: Step 0 (lessons), General Rules, Step 1 (read) |
| `_inc-write.inc` | Shared: Step 5 (write back) |
| `_inc-report.inc` | Shared: Step 7 (report format) + General Pitfalls |

A lessons-learned file at
`.agents/skills/dftracer-annotation-lessons/SKILL.md` is read by every
annotation agent at startup and appended after each session.

#### Option A — Interactive session (recommended)

Runs interactively: the agent asks you questions, pauses for confirmation
before the trace run, and lets you request fixes between steps.

```bash
# Prerequisites: Node.js >=20 (for goose tui, if using that route)
# The recommended interactive method uses the pipeline skill + wrapper script:

./dftracer-agents/run-pipeline.sh
```

This starts a `goose session`, injects the startup prompt automatically,
then connects your keyboard for the Q&A. The agent will ask:

```
What is the Git URL of the application you want to annotate?
> https://github.com/org/myapp

Which branch or tag? (default: main)
> main

Smoke test command? (leave blank to auto-detect)
> make test

Extra CMake build flags? (leave blank to skip)
>
```

After annotation and build verification it pauses:

```
┌─────────────────────────────────────────────────────────┐
│  ANNOTATION REPORT — myapp/20260616_120000              │
│  src/io.c       DONE   12 annotated  2 skipped          │
│  src/main.c     DONE    4 annotated  0 skipped          │
│  Coverage: 16 / 18   io=10  comm=4  mem=2  cpu=0        │
│  Build: PASSED   Smoke test: PASSED                      │
└─────────────────────────────────────────────────────────┘

Proceed with dftracer trace run? [yes / no / fix <file>]
> yes
```

Alternatively, start a plain `goose session` and paste this prompt manually:

```
Load and follow the dftracer annotation pipeline skill from:
/workspaces/dftracer-agents/.agents/skills/dftracer-pipeline/SKILL.md

Read the full SKILL.md file now, then immediately start Step 1 by asking me:
  'What is the Git URL of the application you want to annotate?'
```

#### Option B — Headless `goose run` (CI / scripted use)

Pass all inputs as `--params`. The pipeline runs end-to-end unattended,
annotating files in parallel via sub-recipes, then prints the full report
and trace analysis.

```bash
goose run --recipe dftracer-agents/recipes/pipeline.yaml \
  --params app_url="https://github.com/org/myapp" \
  --params ref="main" \
  --params smoke_cmd="make test" \
  --params extra_flags="-DENABLE_MPI=ON"
```

`smoke_cmd` and `extra_flags` are optional (auto-detected if omitted).

#### Annotation recipes directly

You can also invoke a single-file annotation recipe without the pipeline:

```bash
goose run --recipe dftracer-agents/recipes/annotate-c.yaml \
  --params run_id="myapp/20260616_120000" \
  --params filepath="src/io.c"
```

Pass `build_errors` to fix a specific compiler failure:

```bash
goose run --recipe dftracer-agents/recipes/annotate-c.yaml \
  --params run_id="myapp/20260616_120000" \
  --params filepath="src/io.c" \
  --params build_errors="io.c:42: error: 'DFTRACER_C_FUNCTION_END' undeclared"
```

### Example Goose session — trace analysis

```bash
goose session
```

```
Goose is running! Enter your instructions, or try asking for help.

( O )> summarize the I/O trace at /data/cm1_1_48_20240926

─── Tool use: summarize_trace ────────────────────────────────────
{
  "trace_path": "/data/cm1_1_48_20240926"
}
──────────────────────────────────────────────────────────────────

DFTracer Trace Summary: /data/cm1_1_48_20240926
============================================================
  Trace files : 48 total
  Total events: 284,041
  Processes   : 48
  Threads     : 48
  Duration    : 145.364s
  Bytes read  : 2.7 MB
  Bytes written: 688.3 MB

Top I/O operations:
  write                  112,353
  __xstat                 46,899
  fclose                  25,992
  open                    21,814
  close                   21,563
  fopen64                 20,496
  read                    13,489
  opendir                  9,215

This trace shows a write-heavy HPC workload (CM1 atmospheric model) with
48 MPI ranks. The high write volume (688 MB) relative to reads (2.7 MB)
is typical of checkpoint I/O. The __xstat and opendir calls suggest
frequent directory polling, common in parallel file systems.

( O )> compress all the .pfw files in that directory

─── Tool use: pgzip ──────────────────────────────────────────────
{
  "directory": "/data/cm1_1_48_20240926"
}
──────────────────────────────────────────────────────────────────
…
```

The annotation pipeline prompt and the analysis workflow below can be combined —
after the pipeline completes, ask Goose to continue with `analyze` and `query`
on the trace directory it just produced.

### Recommended analysis workflow

When working with a new trace, follow this sequence — the agent will do this automatically if you ask it to "analyse" a trace:

```
1. detect_preset(trace_path)
   → reads event categories from the trace
   → returns "posix" (HPC/scientific) or "dlio" (AI/ML deep learning)

2. summarize_trace(trace_path)
   → pure-Python overview: file count, duration, bytes read/written, top ops
   → always works, no native C++ required

3. query(trace_path, view_type="proc_name")
   → per-process I/O breakdown (who writes the most?)

4. query(trace_path, view_type="file_name",
         filter_expr='cat == "POSIX" and dur > 1000')
   → hot-file analysis (which files take the longest per call?)

5. query(trace_path, view_type="time_range")
   → I/O rate over time (when does the burst happen?)

6. analyze(trace_path, analyzer_preset=<from step 1>)
   → full dfanalyzer pipeline (requires native C++ and dfanalyzer install)
```

**AI/ML auto-detection** (`detect_preset`): inspects the `cat` field of every event against the AI/ML category signatures from [dftracer's ai_common.py](https://github.com/llnl/pydftracer/blob/develop/python/dftracer/python/ai_common.py). If any of `COMPUTE`, `DATA`, `DATALOADER`, `COMM`, `DEVICE`, `CHECKPOINT`, or `PIPELINE` categories are present — or AI/ML function names like `forward`, `backward`, `epoch`, `fetch` — the `dlio` preset is recommended; otherwise `posix`.

### Available tools in Goose

| Tool | Service | Description |
|---|---|---|
| `detect_preset` | analyzer | Auto-detect posix vs dlio preset from trace event categories |
| `summarize_trace` | analyzer | Pure-Python trace summary (always works, no native deps) |
| `query` | analyzer | Exploratory groupby views: file_name, proc_name, time_range, raw |
| `analyze` | analyzer | Full dfanalyzer pipeline (POSIX, DLIO, Darshan presets) |
| `list_presets` | analyzer | Show all dfanalyzer configuration options |
| `plot` | plot | Generate a chart from trace data (5 types, PNG/HTML/SVG) |
| `plot_all` | plot | Generate all 5 standard plots in one call |
| `info` | utils | Trace directory summary |
| `stats` | utils | Per-function I/O statistics |
| `merge` | utils | Merge multiple trace files |
| `split` | utils | Split a trace by process/time |
| `pgzip` | utils | Parallel gzip compression |
| `index` | utils | Build bloom-filter index for fast queries |
| `reader` | utils | Read and dump trace events |
| `organize` | utils | Organize traces by run |
| `replay` | utils | Replay trace I/O for benchmarking |
| `tar` | utils | Archive trace directories |
| … | utils | 21 dftracer_utils tools total |

### `query` filter expression syntax

The `filter_expr` parameter uses the same DSL as `dftracer_stats --query`. Available fields:

| Field | Type | Example |
|---|---|---|
| `cat` | string | `cat == "POSIX"` |
| `name` | string | `name in ("read", "write")` |
| `dur` | int (µs) | `dur > 1000` |
| `ts` | int (µs) | — |
| `pid`, `tid` | int | `pid == 3537780` |

Expressions can be combined with `and` / `or`:
```
'cat == "POSIX" and dur > 500'
'name in ("read", "write") and dur > 10000'
'cat == "COMPUTE"'
```

---

## MCP server directly

You can also run the server manually for debugging or to wire it into any MCP client:

```bash
# Start the server (it blocks, reading MCP requests from stdin)
dftracer-mcp-server --service both

# Or via Python
python dftracer_mcp_server.py --service analyzer
```

The server writes MCP JSON-RPC to stdout and reads from stdin. Any MCP client (Goose, Claude Desktop, a custom script) can connect by spawning it as a subprocess.
