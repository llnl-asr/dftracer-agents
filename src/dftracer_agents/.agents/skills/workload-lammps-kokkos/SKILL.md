---
name: workload-lammps-kokkos
description: Build/annotate/trace caveats for LAMMPS (lammps/lammps) with the KOKKOS package on AMD MI300A APUs — the elcapitan preset, the Cray GTL/ROCm soname mismatch that blocks every GPU build, the dftracer `finalize()` symbol collision in main.cpp, annotation scoping for a 676-file tree, and the setup-function event blowup. Load this skill for any LAMMPS or Kokkos-on-HIP dftracer session.
---

# workload-lammps-kokkos

LAMMPS + KOKKOS/HIP on Tuolumne-class MI300A (Cray PE, Flux, ROCm 7.x).

## Build

Use the upstream **`cmake/presets/elcapitan_kokkos.cmake`** preset — El Capitan is the
same MI300A APU hardware. It sets `Kokkos_ENABLE_HIP`, `Kokkos_ARCH_AMD_GFX942_APU`,
`CMAKE_CXX_COMPILER=hipcc`, `FFT_KOKKOS=HIPFFT`, and the Cray MPICH link line.
`Kokkos_ENABLE_OPENMP` must stay **OFF** (incompatible with hipcc's second pass).

The `in.lj` Lennard-Jones melt benchmark needs **no extra packages** — `-DPKG_KOKKOS=ON
-DBUILD_MPI=ON` is the whole configure. Do not add MOLECULE/KSPACE for it.

Deviation from the usual `export CC=$(which mpicc) CXX=$(which mpic++)` rule: the preset
FORCEs `CMAKE_CXX_COMPILER=hipcc` because Kokkos-HIP requires it, and handles MPI through
explicit `-I$MPICH_DIR/include` + link flags instead of the wrapper.

Build cost is real: ~514 objects, and the `fix_*_kokkos.cpp` / `pair_*_kokkos.cpp` files
each take minutes under hipcc's two-pass GPU codegen. On a shared login node this can
crawl at ~1 object per 5 minutes. **Finish the build on a compute node** inside your
allocation (`flux run -N1 -n1 -c 96 bash -c "cd <build> && make -j 96"`) — it is roughly
an order of magnitude faster and costs nothing extra if you need the allocation anyway.

### BLOCKER: Cray GTL vs ROCm soname mismatch

`cray-mpich/9.0.1`'s GPU-transport library needs `libamdhip64.so.6`, but ROCm 7.x ships
`.so.7`. The binary then **fails to start**: `error while loading shared libraries:
libamdhip64.so.6`. This hits the pristine build too — it is not caused by annotation.

Check which GTL matches your ROCm before choosing an MPI:

```bash
for v in /opt/cray/pe/mpich/*/gtl/lib/libmpi_gtl_hsa.so; do
  echo "$v -> $(objdump -p $v | grep -o 'libamdhip64.so.[0-9]')"
done
```

On this stack `cray-mpich/9.1.0`'s GTL needs `.so.7`. Keep `libmpi` from the MPI you
compiled against and take **only the GTL** from the matching version:

```
-L<mpich-9.0.1>/ofi/crayclang/20.0/lib -lmpi
-L/opt/cray/pe/mpich/9.1.0/gtl/lib -lmpi_gtl_hsa -Wl,-rpath,/opt/cray/pe/mpich/9.1.0/gtl/lib
```

Do NOT "solve" this by dropping `-lmpi_gtl_hsa`. Without GPU-aware MPI the run dies with
`cxil_map: write error` from the Slingshot CXI provider, because on an MI300A APU with
XNACK every buffer is device-accessible. `-pk kokkos ... comm host` does **not** avoid it.

### Required runtime env

- `HSA_XNACK=1` — `Kokkos_ARCH_AMD_GFX942_APU` needs it for host allocations from device;
  without it Kokkos warns on every rank.
- `MPICH_GPU_SUPPORT_ENABLED=1`.

### Launch flags

`-k on g <N>` must match the GPUs the launcher actually gave the rank. Under
`flux run -g1` (one GPU per rank) use **`-k on g 1`**; `-k on g 4` aborts every rank with
`Requested GPU with id '3' but only 1GPU(s) available!`.

Working 2-node shape: `flux run -N2 -n8 -g1 -c22` (4 ranks/node, 22 cores each, leaving
cores for the `dftracer_service` daemon on a 96-core / 4-socket node).

## dftracer annotation

### `main.cpp`: the `finalize()` symbol collision (silent trace loss)

`dftracer/dftracer.h` declares a **global `void finalize();`** and defines
`DFTRACER_CPP_FINI()` as `finalize()`. LAMMPS `src/main.cpp` has its own file-local
`static void finalize()` helper. Two consequences:

1. Build error: `static declaration of 'finalize' follows non-static declaration`.
2. Worse, if you resolve it the wrong way, `DFTRACER_CPP_FINI()` binds to **LAMMPS's**
   static helper — the tracer never finalizes, never flushes, and every trace is 0 bytes
   with `rc=0`.

Fix: rename the LAMMPS-local helper (e.g. `lmp_local_finalize`) at its definition and all
7 call sites. Do not rename `lammps_kokkos_finalize` / `lammps_python_finalize` /
`lammps_plugin_finalize`.

### `main.cpp` has 7 exit paths

`main()` has six `catch` blocks (each ending in `MPI_Abort`/`MPI_Finalize`+`exit(1)`) plus
the normal fall-through. Put `DFTRACER_CPP_REGION_END` + `DFTRACER_CPP_FINI` before the
`finalize()` call on **every** one, with `DFTRACER_CPP_INIT` immediately after `MPI_Init`.
Hand-place these; the annotator's entry-point pass is unreliable on this shape.

### Scoping a 676-file tree

`src/` has ~366 top-level `.cpp` and `src/KOKKOS/` ~310. Annotating everything is wasteful
and slow. A scope of **50 files** (37 core + 13 KOKKOS) covers the whole `in.lj -sf kk`
execution path: `main/lammps/input/run/finish/update/output/thermo/timer`,
`domain/atom/atom_vec*/comm*/irregular`, `neighbor/neigh_list/nbin*/npair`,
`integrate/verlet/modify/fix/fix_nve`, `force/pair/pair_lj_cut`, the `read_*`/`write_*`/
`dump*` I/O path, and the `*_kokkos.cpp` counterparts. Leave `lib/kokkos/` entirely alone —
brahma's GOTCHA interception captures the MPI/POSIX/STDIO layers for free, and rocprofiler
captures the GPU layer.

### Three annotator defects seen on this tree

1. **Device code.** The clang annotator inserts `DFTRACER_CPP_FUNCTION()` into
   `KOKKOS_INLINE_FUNCTION` / `KOKKOS_FUNCTION` bodies (seen in `fix_nve_kokkos.cpp`,
   `domain_kokkos.cpp`, `nbin_kokkos.cpp`, `npair_kokkos.cpp`). Host tracing macros in
   device code do not compile and would fire per-atom. Strip any macro whose enclosing
   signature carries a `KOKKOS_*_FUNCTION` marker, and re-check after every annotate call.
2. **Include placement.** The include goes after the *last* `#include` in the file.
   `neighbor.cpp` has mid-file `#include "style_nbin.h"` blocks inside namespace scope, so
   the include landed at line 796 while macros started at line 115 — ~20 compile errors.
   Verify on **every** file that the include line number is below the first macro line;
   the safe repair is to move it directly after the *first* `#include`.
3. **The cost filter drops the hot dispatchers.** `CommKokkos::forward_comm` /
   `reverse_comm`, `NeighborKokkos::build`, `ModifyKokkos::initial_integrate` /
   `final_integrate` all score "trivial" because each body is a one-line dispatch — yet
   they are exactly what `VerletKokkos::run` calls every timestep. Add them back
   explicitly. Always read the *skipped* list, not just the annotated count.

### Hot-loop exclusions (mandatory)

Never annotate these — they are per-atom or per-lattice-point:

- `domain.cpp`: `closest_image`, `remap`, `remap_near`, `unmap`, `minimum_image*`
- `lattice.cpp`: `lattice2box`  ← see below
- `create_atoms.cpp`: `create_atom`

`lattice2box` and `create_atom` are one-time **setup** functions but still produced
**2,721,792** and **1,024,000** events respectively — 89% of all app events in a 1M-atom
run — because they run once per lattice point / per atom. They do not inflate `Loop time`
(setup is outside it) but they dominate trace volume and bury the timestep signal.
Exclude them via `clang_annotate_file(exclude_functions=[...])`.

## Run sizing

`bench/in.lj` scales with `-var x/y/z` (atoms = `4 * (20x)(20y)(20z)`). Measured on 2 nodes
× 4 MI300A GPUs (8 ranks): 1,024,000 atoms × 1000 steps = **4.2 s** loop time,
241.8 Matom-step/s untraced-equivalent. Scale steps, not just atoms, to reach a target
wall time. `run` is hardcoded in `in.lj` — copy the input and edit it rather than trying
to override `run` with `-var`.

## Verified full-feature trace (2 nodes, 8 ranks, 1M atoms, 1000 steps)

~5.53M events across 8 rank traces + 2 node-service traces. All layers present:

| layer | cat | events |
|---|---|---|
| app annotation | `CPP_APP` | 4,186,332 |
| GPU runtime | `HIP_RUNTIME_API` | 719,842 |
| GPU kernels | `KERNEL_DISPATCH` | 159,965 |
| GPU copies | `MEMORY_COPY` | 64,864 |
| GPU scratch | `SCRATCH_MEMORY` | 8 |
| MPI point-to-point | `p2p` | 295,632 |
| MPI collective | `collective` | 1,504 |
| STDIO | `STDIO` | 84,284 |
| POSIX | `POSIX` | 6,176 |
| node counters | `sys`/`net`/`io` | 6,596 / 204 / 1,156 |
| PAPI | `papi` (type 11) | 62 |
| Variorum power | `gpu` (type 13) | 34 |

PAPI `PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS` all landed with
**`multiplex: 0`** — 4 counters fit the 5 hardware slots exactly. Read `multiplex` from
the compute-node trace, never from a login-node probe.

Tracing overhead at maximum feature capture: loop time 4.23 s traced vs 0.94 s with
`DFTRACER_ENABLE=0` (~4.5×), driven mostly by `HIP_RUNTIME_API` interception plus
per-timestep app events. Final thermo is **bit-identical** between the two, confirming the
annotation is correctness-preserving.

## Cross-references

[[system-tuolumne]], [[software-rocm]], [[software-papi]], [[tools-dftracer]],
[[dftracer-annotate-cpp]], [[flux-alloc]]
