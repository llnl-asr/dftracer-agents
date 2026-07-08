## Lessons Learned

- Keep dftracer install logic in the MCP tool unless the tool itself is wrong.
- Update the skill files at the moment a new install pitfall appears.
- Leave a clear trail for the next session in the lesson files.

### Flash-X + Tuolumne Session (2026-07-08)

- **Cray MPICH 9.0.1 IS compatible for MPI-IO tracing on tuolumne** (user-confirmed 2026-07-08). Do NOT treat a dftracer version-range check that skips MPI as "unsupported" — that was a misdiagnosis. MPI is compatible; the fix is to pass the MPI implementation version + include headers (Cray MPICH 9.0.1 via GNU 11.2 wrappers, include dir under /opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2) so dftracer builds its MPI interception. If the build auto-disables MPI-IO, override the version gate / pass the MPI version explicitly rather than accepting PRELOAD-POSIX-only.

- **Session-built HDF5 + GNU MPI wrappers require explicit pin**: When using session-built HDF5 (1.14.5) with Cray MPICH GNU wrappers (not Cray PE compilers), pass both hdf5_prefix AND mpicc/mpicxx explicitly to session_detect, or detect will mix system HDF5 with session MPI. Pattern: `session_detect(run_id, hdf5_prefix="<WS>/hdf5_1.14", mpicc="/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc", mpicxx="...")`.

- **HIP disable for CPU-only workloads**: CPU-only workloads (e.g., HPC I/O benchmarks with no GPU acceleration) can safely set `DFTRACER_ENABLE_HIP_TRACING=OFF` to simplify the build (no ROCm dependencies). Impact on I/O tracing: NONE; HIP is for GPU kernel tracing, not I/O.

- **Tuolumne LD_LIBRARY_PATH setup before dftracer install**: CCE (Cray Compiler Environment) libs must be in LD_LIBRARY_PATH BEFORE running session_install_dftracer, or dlopen link fails with --no-allow-shlib-undefined. Pattern: `export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"` (session_detect + session_install_dftracer handle this automatically via system_detect env, but verify in case of future changes).
