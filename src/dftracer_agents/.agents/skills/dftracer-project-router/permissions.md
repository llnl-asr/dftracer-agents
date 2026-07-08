## Permissions

- Operate only inside the current session workspace.
- Read canonical paths from `session_get_run_paths`.
- Dispatch stage agents against `baseline/`, `annotated/`, `traces/`, and `opt<n>/` only.
- Do not invent paths outside the session-owned tree.
