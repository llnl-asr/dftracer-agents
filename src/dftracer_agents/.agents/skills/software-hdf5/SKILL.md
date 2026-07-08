---
name: software-hdf5
description: >
  HDF5 optimization strategies (L2 software tuning), version compatibility,
  ALWAYS building HDF5 from source (never Cray/system module), Cray HDF5
  pitfalls, and dftracer HDF5 tracing setup.
  Load this skill for any HDF5 or parallel I/O work.
---

Cross-references: [[dftracer-io-optimization]] [[workload-h5bench]] [[workload-ior]] [[software-mpi]]

---

## Version Compatibility

### Always use HDF5 ≥ 1.14.x for parallel I/O

HDF5 1.10.x silently degrades optimization effectiveness:
- `H5Pset_page_buffer_size()` is a no-op with the MPI-IO VFD
- `H5Fcreate_async()` falls through to synchronous create
- L2 optimizations compile and run without error but have zero effect

**dftracer-compatible HDF5 versions (exact series only):**
`1.8.23` | `1.10.5` | `1.12.3` | `1.14.5` (preferred)

---

## ALWAYS build HDF5 from source — never use the Cray/system HDF5 module

**Hard rule:** For every dftracer session, install HDF5 from source into the session
workspace (`<ws>/install_hdf5`) and point the app + dftracer at it. Do **not** load or
link the Cray `cray-hdf5` / `cray-hdf5-parallel` module (or any site HDF5 module) as the
HDF5 the app builds against.

Why:
- Cray HDF5 ships header typos (`chid_t`, tentative-def issues) that break dftracer/brahma
  C++ linkage — see the patches below; a clean source build is easier to control.
- Cray HDF5 version/ABI drifts with the PE and is not one of the dftracer-compatible
  exact series, silently degrading L2 optimizations.
- A source build gives a stable, known-good prefix that dftracer HDF5 tracing links
  against reliably (RPATH/patchelf targets one predictable location).

Use the module system only for the compiler/MPI toolchain (to get `mpicc`/`mpicxx`), then
build HDF5 1.14.5 from source with that toolchain. If a session was set up against Cray
HDF5, rebuild from source before proceeding.

## Building HDF5 from Source

### Standard build

```bash
wget https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.4/hdf5-1.14.4.tar.gz
tar xf hdf5-1.14.4.tar.gz && cd hdf5-1.14.4
CC=mpicc ./configure \
  --prefix=<ws>/hdf5_1.14 --enable-parallel --enable-shared \
  --enable-build-mode=production --with-zlib=/usr
make -j$(nproc) && make install
# Verify:
h5cc -showconfig | grep "Version:"   # must show 1.14.x
```

### Cray PE: GitHub 404s on tarballs — use HDF Group FTP

```bash
curl -fkL https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.14/hdf5-1.14.3/src/hdf5-1.14.3.tar.gz \
  -o hdf5-1.14.3.tar.gz
```

### Cray HDF5 `chid_t` typo (H5Apublic.h:932)

After building 1.14.3 from source, patch the typo before using with dftracer/brahma:

```bash
sed -i 's/H5Aread_async(chid_t attr_id/H5Aread_async(hid_t attr_id/' \
  <ws>/hdf5_1.14/include/H5Apublic.h
```

This affects both the Cray module HDF5 AND upstream HDF5 1.14.3 (same typo).
IOR's C frontend tolerates `chid_t`; dftracer/brahma (C++) cannot.

---

## L2 Software — HDF5 Chunk and Cache Tuning

Applies when bottleneck is `small_io_pct` high (metric: bandwidth or iops).

```c
// Set chunk dimensions aligned to collective I/O buffer (e.g. 1 MB):
hid_t dcpl = H5Pcreate(H5P_DATASET_CREATE);
hsize_t chunk_dims[] = {1048576};
H5Pset_chunk(dcpl, 1, chunk_dims);

// Enable collective metadata:
H5Pset_coll_metadata_write(fapl, 1);
H5Pset_all_coll_metadata_ops(fapl, 1);

// Set chunk cache (64 MB, 521 slots):
H5Pset_cache(fapl, 0, 521, 64*1024*1024, 1.0);

// For parallel HDF5: align to stripe size:
H5Pset_alignment(fapl, 0, stripe_size);
```

---

## L2 Software — HDF5 Metadata Tuning

Applies when bottleneck is `metadata_time_pct` high (metric: metadata_ops).

```c
// Disable metadata cache evictions during write-heavy phases:
H5AC_cache_config_t mdc_config;
H5Pget_mdc_config(fapl, &mdc_config);
mdc_config.evictions_enabled = FALSE;
H5Pset_mdc_config(fapl, &mdc_config);

// Batch attribute writes:
H5Pset_object_track_times(dcpl, 0);   // disable timestamp tracking
```

---

## L2 Software — HDF5 Collective IOR Flags

For IOR HDF5 workloads (see [[workload-ior]]):

```bash
# IOR flags that synergize with romio_cb_write:
-a HDF5 -b 64m -t 16m -s 4 -c -Y
#   -c  collective I/O
#   -Y  collective HDF5 metadata writes
```

---

## HDF5 in dftracer

Build dftracer with HDF5 support enabled:

```bash
cmake -DDFTRACER_ENABLE_HDF5=ON -DHDF5_ROOT=<ws>/hdf5_1.14 ...
```

Without `DFTRACER_ENABLE_HDF5=ON`, `H5Fcreate/H5Dwrite` events are absent from
traces even when annotated.

After building, set `HDF5_DIR` and `LD_LIBRARY_PATH` in every subsequent command:

```bash
export HDF5_DIR=<ws>/hdf5_1.14
export LD_LIBRARY_PATH=<ws>/hdf5_1.14/lib:$LD_LIBRARY_PATH
```

---

## HDF5 Version Detection in Pipeline

```bash
h5cc --version 2>/dev/null || h5dump --version 2>/dev/null || \
  pkg-config --modversion hdf5 2>/dev/null
```

The MCP `session_detect` tool reports `hdf5_system.compatible=true/false` and
`hdf5_system.recommended` with the preferred patch release.

If system HDF5 is NOT in a compatible series:
1. Build HDF5 1.14.5 from source into `<WS>/hdf5_1.14/`
2. Add `-DHDF5_DIR=<WS>/hdf5_1.14` to all cmake steps
3. Set `HDF5_DIR` and `LD_LIBRARY_PATH` in every shell command

---

## Citations

**WisIO (Yildirim et al., ICS 2025)** — covers `small_io_pct`, `metadata_time_pct`
URL: https://dl.acm.org/doi/10.1145/3721145.3730395

**Drishti (Bez et al., PDSW 2022)** — HDF5 chunk/cache/metadata L2 suggestions
URL: https://ieeexplore.ieee.org/document/10027503

## Failed Configurations

Entries below were applied during optimization loops and caused regressions or had no effect.
Check this section before proposing any configuration for this workload/software/filesystem.

Format per entry:
  date, app, workload, filesystem, system, bottleneck,
  config_attempted, result, metrics_before, metrics_after, delta,
  root_cause, do_not_use_when

<!-- New failed-config entries are appended below by the optimization loop (Step 8d-iii-FAIL) -->

