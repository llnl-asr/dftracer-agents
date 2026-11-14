# What worked: dftracer_isolation

- [tool:session_verify_isolation] static -> fix findings -> `mode="launch", isolation_root=${PFS}/iso` (outside `${WS}`) -> `mode="status"` until `finished` with `exit_code` 0 -> `dftracer_isolation_check(run_id, package_dir, require_launch=True)` passes.
