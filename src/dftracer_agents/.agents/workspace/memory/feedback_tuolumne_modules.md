---
name: feedback-tuolumne-modules
description: "Module compatibility rules for Tuolumne — inactive module detection, HDF5 source install, MPI/compiler never source install"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `system-tuolumne` skill (already carries the module
compatibility/inactive-module rules in full).

Always check for "Inactive Modules" after any `module load` on Tuolumne. An inactive module was silently disabled due to stack incompatibility — do not assume it is usable.

**Known incompatibility**: `cce/21.0.0 + cray-mpich/9.1.0 + rocm/7.1.1 + rccl/fast-env-slows-mpi` deactivates `cray-hdf5-parallel/1.14.3.7`.

**Rules by software type:**
- **HDF5 or data-format libraries** → source install when module goes inactive. Never rely on the bundled pip wheel (fork-unsafe in DataLoader workers).
- **MPI or compilers** → NEVER source install. Load the most constrained software first (e.g. `rccl/fast-env-slows-mpi`) to force compatible versions. Use `module spider` to find prerequisite chains.

**Discovery rule**: NEVER use `find /usr/tce`, `find /opt/cray` etc. — use `module avail <name>` and `module spider <name>` only.

**Why:** Recursive find on `/opt/cray` hangs or times out on large trees. Inactive modules produce silent failures.

**How to apply:** After any `module load` block in wrapper scripts, grep output for "Inactive Modules" and abort if required libraries are listed. For HDF5 on Tuolumne: build from source into `<WS>/install/hdf5/`.
