# What worked: dftracer_optimize

What finally worked (portable: ${SESSION}, ${LUSTRE}). One entry per key, newest wins.

- [ior/hdf5-vast/l2-romio] 2026-06-24: Cray MPICH, 192 procs on 2 nodes, 48 GiB, VAST: `MPICH_MPIIO_HINTS="*:romio_cb_write=enable"` (no cb_read, ds_write left default) with IOR `-a HDF5 -b 64m -t 16m -s 4 -c -Y`: 168.8 -> 112.9 s (-33%), write 352 -> 557 MiB/s, read 1705 -> 1991 MiB/s, POSIX calls -89%.
- [montage/workflow/l2-fadvise] 2026-07-06: `#define _DEFAULT_SOURCE` first line, `#include <fcntl.h>`, after each hot-path `fits_open_file()` open a separate O_RDONLY fd, `posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)`, close it: 740.6 -> 859.8 MB/s, ops/bytes unchanged.
- [pegasus/workflow/l3-lustre] 2026-07-06: run pegasus-plan from a directory on ${LUSTRE} and check `grep -m1 "^TASK mProject" <run-dir>/montage-0.dag | grep -oP '(?<=-w )\S+'` starts with the Lustre mount before submitting; move the previous wf-scratch aside.
- [tool:session_optimization_iteration/compare] 2026-06-22: isolate the iteration's new traces, `split(directory=<clean>, output_dir=<clean_split>, force=True)`, `comparator(baseline=${SESSION}/opt<N-1>/traces_split, variant=<clean_split>)`.
- [tool:session_generate_optimization_proposals/ops_slope] 2026-06-22: posix_*_ops_slope bottlenecks are mapped manually per the skill's Corner cases, with citations from the iteration's paper search.
