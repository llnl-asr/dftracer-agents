---
name: feedback-write-only-in-user-space
description: Never write outside the user's own space; shared group trees are read-only; never find / 
metadata:
  type: feedback
---

**Never write anywhere outside the user's own space.** Shared/group project directories
(other people's workspaces, group data trees, shared venvs, shared sample/input dirs) are
**READ-ONLY** — read and copy FROM them, never write, `mkdir`, `pip install` into, or
redirect application output into them.

Writes are allowed ONLY in:
- the dftracer session workspace under `$PROJECT_ROOT/workspaces/<session>/`
- the user's own PFS scratch space (e.g. `$LUSTRE_ROOT/$USER/...`)

**Concrete traps seen in practice:**
- A shared app runner sets `OUTPUT_DIR=$data_dir` pointing back into the read-only
  sample directory. Every derived copy of that script must redirect `--output_dir` to
  the session's own `<WS>/dataset/<run_name>/` before it is ever launched, and this
  needs re-verifying at the run step, not just fixed once.
- A shared group venv that the app "already works in" is tempting to `pip install`
  dftracer into. Don't — rebuild the venv inside the session workspace instead. Other
  people depend on the shared one.

**Also: never run `find` (or any recursive scan) against `/` or a system root.** Scope
every search to a specific known directory. Broad scans are slow, noisy, and hit
permission-denied trees.

**Why:** these trees are shared with other researchers; a stray write can corrupt a
colleague's environment or data, and is not something the session can safely undo.

**How to apply:** state the read-only constraint explicitly in EVERY subagent dispatch —
subagents do not infer it, and several have attempted writes into shared trees when it
was not spelled out. Pair it with the destination they SHOULD use.

Related: [[feedback-app-exec-cwd]], [[feedback-lustre-io]],
[[feedback-app-uses-real-pfs-path-not-symlink]], [[feedback_data_cleanup_quota]].
