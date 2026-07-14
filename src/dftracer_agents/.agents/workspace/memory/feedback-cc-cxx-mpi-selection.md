---
name: feedback-cc-cxx-mpi-selection
description: Load modules, then export CC=`which mpicc` CXX=`which mpic++`
metadata:
  type: feedback
---

Compiler selection rule for every build/install step that needs MPI (app build, dftracer build, any C/C++ compile): first load the site modules, then resolve the compilers off PATH with backticks:

```
export CC=`which mpicc`
export CXX=`which mpic++`
```

**Why:** User simplified this rule twice — first asked for MPI-wrapper-vs-plain-compiler branching, then said that's not needed: just always resolve `mpicc`/`mpic++` via `which` right after loading modules. This works on Cray too, since `mpicc`/`mpic++` resolve to the Cray wrappers (`cc`/`CC`) once `PrgEnv`/`cray-mpich` modules are loaded onto PATH — no separate Cray-vs-generic branch needed.

**How to apply:** In every build/install wrapper script, source the module-load script FIRST, then in the SAME script do `export CC=\`which mpicc\`` and `export CXX=\`which mpic++\`` (never in a separate Bash call — PATH/module state doesn't persist across tool calls). Only skip this (plain gcc/g++) for a target that truly does not link MPI at all. See [[feedback-tuolumne-modules]] and [[feedback-hpc-python-env]].
