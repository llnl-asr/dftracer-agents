---
name: bugs-optimizer-mcp-tools-block-external-runs
description: Reproducible MCP tool bugs + final_report script-glob gap found during the Megatron-DeepSpeed optimization pass
metadata:
  type: project
---

Reproducible MCP tool defects surfaced by the optimizer dimension agents and the report stage. All block real work; none were papered over with hand-rolled hacks.

1. **`session_generate_optimization_proposals` raises `UnboundLocalError: cannot access local variable 'idx'`** when passed `bottlenecks_json`. Blocks the documented path for turning an existing diagnosis into proposals.

2. **`session_optimize_l1_app` / `l2_software` / `l3_filesystem` hard-fail with "No optimization iterations — run session_optimization_iteration first"** whenever a session has a baseline + diagnosis but no `optimization_history` — i.e. for ANY externally-launched run. Fix direction: let them fall back to `analysis/diagnosis.json` the way `session_generate_optimization_proposals` already accepts `bottlenecks_json`. Until fixed, every proposal-only or externally-measured optimization pass is locked out and must fall back to `opt_proposal_table`.

3. **`search_arxiv` raises `<asyncio.locks.Lock object ...> is bound to a different event loop`** when invoked concurrently with another search tool in the same block. Root cause: a module-level `asyncio.Lock` created at import time on a different loop than the one serving the request. Fix: create the lock lazily inside the running loop, or use `anyio.Lock`.

4. **`session_final_report`'s automatic script glob misses `scripts/*.sh` and `scripts/*.json`** — it collects `tmp/*.sh` diagnostics but not top-level `scripts/run_opt_*.sh` / `optimizer_*.sh` / `ds_config_*.json`. Rule 15 requires EVERY script actually run to be present, so these had to be collected by hand into `scripts/opt/` and `patches/opt_configs/`. Fix the glob.

5. **`privacy_scan()` with no argument resolves to an unrelated default tree.** A bare call landed on an already-committed reference tree (`good-runs/1000genome/...`) that still contains real leaks (flux job ids, `tuolumne####` hostnames, a username in a Lustre path) predating this session. Two implications: (a) always pass an explicit path, and (b) that pre-existing tree needs its own dedicated privacy-guard pass — it is committed data, unrelated to this session.

**Also a coverage gap (not a bug):** the local RAG library (~52 items) is I/O/HDF5-only. `rag_search` / `session_search_optimization_context` returned zero communication-, compute-, or memory-relevant material for a PyTorch/DeepSpeed workload. Seeding it with ZeRO (arXiv:1910.02054), PyTorch DDP (arXiv:2006.15704), Megatron-LM (arXiv:1909.08053), and Thakur 2005 would make it useful for non-I/O dimensions.

**Why:** items 1-3 were hit repeatedly across three separate dimension agents in one session; 4-5 were hit at report time. None are incidental.

**How to apply:** Fix at the tool level in `src/dftracer_agents/mcp_tools/`; ask the user to restart the MCP server afterward so it reloads. See [[project-megatron-deepspeed-gpt-pipeline]].
