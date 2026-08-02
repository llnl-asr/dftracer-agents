---
name: feedback-flux-proxy-wrapper
description: "For flux proxy shell commands, always write a wrapper script that sources modules/env before running"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `flux-alloc` skill (the wrapper-script and `--env`
special-character pitfalls already cover this rule).

When running any command via `flux proxy <JOBID>`, always write a bash wrapper script to disk first, then invoke it as `flux proxy <JOBID> bash <script_path>`. Never pass module loads or env vars inline via `flux proxy bash -c "..."`.

**Why:** `flux proxy bash -c "..."` does not properly propagate `module load` / `ml` commands or env vars into the subprocess. This causes silent failures (wrong library versions, missing MPI, ROCm not found). A wrapper script that sources `/usr/share/lmod/lmod/init/bash` and uses `module load` explicitly is the only reliable method.

**How to apply:**
1. Write `<ws>/tmp/run_<name>.sh` with:
   - `source /usr/share/lmod/lmod/init/bash`
   - all `module load` lines
   - all `export` env var lines
   - the actual command at the end
2. Make it executable: `chmod +x <script>`
3. Run: `flux proxy <JOBID> bash <script>`

Always do this for smoke tests, dftracer runs, installs, and any other flux-proxied commands. See [[feedback-dftracer-aiml-venv]].
