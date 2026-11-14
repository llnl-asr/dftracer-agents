# What worked: dftracer_install

Portable recipes (`${WS}` = session workspace, `${MPI_ROOT}`/`${HDF5_ROOT}` = the application's MPI and HDF5).

- [tool:session_install_dftracer] after `session_build_install` recorded app_built: `session_install_dftracer(run_id, mpi=True, hdf5=<app uses HDF5>, papi=False, hip=False, variorum=False)` (prebuilt="compose" default) -> pre-release dftracer[modules] + dftracer-modules into `${WS}/venv`, then `scripts/compose_dftracer.sh` in env.sh; `dftracer_install_check(run_id, require="mpi,hdf5")` passes.
- [tool:session_generate_dftracer_pc] autotools apps: run after install; export the returned pkg_config_path (session_build_annotated does it).
- [tool:dftracer_wheelhouse] `dftracer_wheelhouse(action="search", system=<machine>, python_tag=cp313)` before any source build; `action="retire", key=<key>` for a wheel that failed its install check.
- [any/tuolumne/install] 2026-11-13: pre-release composed with Cray MPICH 9.0 + HDF5 1.14: `dftracer-compose --enable mpi,hdf5 --disable papi,hwloc,hip,cuda`, venv cmake first on PATH.
- [h5bench/tuolumne/install] 2026-07-10: `session_detect(run_id, hdf5_prefix=${WS}/hdf5_1.14, mpicc=${MPI_ROOT}/bin/mpicc, mpicxx=${MPI_ROOT}/bin/mpicxx)` then install.
- [any/any/hdf5] HDF5 1.14.5 from source: `CC=mpicc ./configure --prefix=${WS}/hdf5_1.14 --enable-parallel --enable-shared --enable-build-mode=production --with-zlib=/usr && make -j && make install`.
- [vpic-kokkos/tuolumne/dftracer-cmake] 2026-07-14: two passes, `-DDFTRACER_INSTALL_DEPENDENCIES=ON` build, then `=OFF` build + install in the same dir.
