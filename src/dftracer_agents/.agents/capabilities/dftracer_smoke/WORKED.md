# What worked: dftracer_smoke

Portable recipes (`${WS}` = session workspace, `${PFS}` = system-detected parallel file system, e.g. `$LUSTRE_ROOT/$USER`).

- [ior/smoke/run] `session_service_start(run_id)`; `session_run_smoke_test(run_id, command="${WS}/install_ann/bin/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o ${PFS}/workspaces/ior/ior_smoke_test", subfolder="build_ann")`; `session_service_stop(run_id)`; `dftracer_smoke_check(run_id)` passes.
- [tool:session_run_smoke_test] apps that MPI_Init unconditionally: `command="mpirun -np 1 <abs binary> <args>"` (the tool strips the launcher to one rank).
- [flashx/smoke/run] PRELOAD pivot: env_extra `{"DFTRACER_INIT":"PRELOAD","DFTRACER_DATA_DIR":"all","LD_PRELOAD":"<dftracer lib>/libdftracer_core.so.<version>"}`.
