# What worked: dftracer_build

Portable recipes (`${WS}` = session workspace).

- [tool:session_configure] cmake: `session_configure(run_id, extra_cmake_flags="-D<opt>=<val> ...")` with options read from CMakeLists.txt option(...); then `session_build_install(run_id)`; `dftracer_build_check(run_id, stage="original")` passes.
- [tool:session_configure] pip/mesonpy: `session_configure(run_id, extra_pip_flags='--config-settings=setup-args="-Dopt=val"')`; `session_build_install` still called to record app_built.
- [tool:session_build_annotated] cmake: same extra_cmake_flags as the original; then `dftracer_build_check(run_id, stage="annotated")` shows the binary names libdftracer_core.
- [ior/any/build_annotated] `custom_build_cmd="bash ${WS}/annotated/scripts/build.sh"` where build.sh sources `${WS}/scripts/env.sh`, runs `autoreconf -fi -I config && ./configure --prefix=${WS}/install_ann --with-hdf5=yes CPPFLAGS=... LDFLAGS=... LIBS=-ldftracer_core && make -j && make install`.
- [any/any/autotools-annotated] install -> session_generate_dftracer_pc -> session_patch_build -> session_build_annotated.
