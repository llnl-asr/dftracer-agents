# What worked: dftracer_system

- [tool:system_detect] 2026-07-06: system_detect() (or hostname="<system><node>", probe_features=False for a config lookup) -> markdown with modules in order, env, launcher, notes and the probed PAPI/power report; dftracer_system_check(detect_output=<markdown>) -> pass with the scheduler.
- [tool:system_save] 2026-10-02: dftracer_system_check(config={description, sudo, modules, env (${VAR} only), mpi_launcher}, name) passes, then system_save(name, description, sudo, modules, env, mpi_launcher, notes) merges onto the existing entry, keeping tracing:.
- [tool:system_papi_counters] 2026-10-02: system_papi_counters(code_type="compute"|"memory"|..., budget=<n> when planning another node) -> one DFTRACER_PAPI_EVENTS per run within the budget, each with PAPI_TOT_CYC; verify args.multiplex in the trace.
