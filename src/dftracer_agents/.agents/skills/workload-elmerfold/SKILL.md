---
name: workload-elmerfold
description: Elmerfold — LLNL's OpenFold monomer-structure-prediction campaign workflow (lbann/openfold, monomer-structure-prediction/) on Tuolumne. Env build via setup-env.sh (uv, ROCm 7.2.1, PrgEnv-gnu), the private-repo step that fails, the gen_launch_script -> run.sh -> flux batch launch with Rabbit staging and dbcast, the per-sequence task DAG, process layout (driver / process-pool workers / GPU inference server), and the verified dftracer provenance recipe. Load for any elmerfold / OpenFold structure-prediction session.
---

# workload-elmerfold

Elmerfold predicts protein structures for MGnify sequences at campaign scale.
Source: `lbann/openfold` (LC GitLab). The workflow driver is
`monomer-structure-prediction/ems_structure_prediction.py`, launched per input
chunk by `submit_smsp.sh`; GPU inference runs in a separate server process
(`openfold_server_ems.py`). Reference data lives under `$LUSTRE_ROOT/lbann/elmerfold`.

## Index
- [Env build](#env)
- [Launching a run](#launch)
- [Per-sequence task DAG](#dag)
- [Process layout](#procs)
- [Provenance recipe](#prov)
- [Lessons](#lessons)

<a id="env"></a>
## Env build

`./setup-env.sh --use-rocm7 --no-managed-python <venv>` -- loads `ml rocm/7.2.1 PrgEnv-gnu`,
creates a **uv** venv (no `pip` inside), builds hmmer 3.3.2, hh-suite 3.3.0, MMseqs2,
OpenMM, ofcpp, relaxcpp, and pulls kalign from the site Lustre copy.

- Put the venv on Lustre, not NFS: the partial NFS venv was 16 GB before torch finished
  installing; the finished Lustre venv is ~6 GB.
- The script refuses to run if `source/build/` exists -- remove it before a retry.
- `pip install git+ssh://.../openfoldcollab/emf_data.git` fails for accounts without
  access to that GitLab project. Nothing in the openfold tree imports `emf_data`; run the
  remaining steps by hand: `uv pip install -e ./ems`, `cppdbfixer` (git+ssh, needed by the
  relaxation code), then `patches/openmm1.patch` and `openmm2.patch` into site-packages.
  Verify with `python -c "import ems.metrics, cppdbfixer, openfold, ofcpp, relaxcpp, openmm, torch"`.

<a id="launch"></a>
## Launching a run

```
python utils/gen_launch_script.py --config configs/tuolumne_test.yaml \
  --inchunks $LUSTRE_ROOT/lbann/elmerfold/splits/1n_10s \
  --submit-script <src>/monomer-structure-prediction/submit_smsp.sh [--src-dir <src>] \
  --output-dir <run>/output --env-dir <venv> --timing-dir test_timing --log-dir test_logs \
  --prestage-script <src>/monomer-structure-prediction/prestage_check.sh -- run.sh
flux batch -t 1h --queue pdebug run.sh
```

- `run.sh` is self-contained: `#flux: --nodes 2 --exclusive` plus a 1300 GiB Rabbit XFS
  `#DW jobdw` staging area; one node is drained as the launch node.
- **`dbcast` must be on PATH in the submitting shell** (`flux batch` propagates the env).
  Without it run.sh exits in ~30 s with `dbcast not found, load mpifileutils`.
  Load **`mpifileutils/0.11.1`** -- the app pins it because 0.12.0 is broken
  (`monomer-structure-prediction/generate_campaign_scripts.sh`).
- `--src-dir` selects the source tree the job runs (use it to point at an instrumented copy).
- `1n_10s` (10 sequences): ~5 min broadcasting reference data (pdb70 87 GB, mmCIF 309 GB)
  to Rabbit, ~3 min of prediction; ~520 s total. Output: `<run>/output/chunk_000000/<seq_id>.tar`.
- The `spindle_be: No route to host` lines at job teardown are noise.

<a id="dag"></a>
## Per-sequence task DAG

Defined in `OF2StructurePredictionPipeline._build_tasks`:
`init` → `load` (`load_and_merge_msas`: jackhmmer + hhblits MSA members out of shared
archives via pickle indices) → `filter` (mmseqs filtera3m) → `filter-cleanup` (deletes the
merged MSA, up to ~2.4 GB) → `featgen` (MSA feature bundle) and `of2templates` (hhsearch vs
pdb70) → `templatefeat` (allow_failure) → two GPU inferences, `model_3_ptm` (no templates)
and `model_1_ptm` (templates, allow_failure) → in-server relax → `best` (max mean pLDDT)
→ `save` (zstd members into `<seq_id>.tar`) → `cleanup`. In parallel, the OF3 template chain:
`a3m-to-sto` → `of3-hmmbuild` → `of3templates` (pdb_seqres).

Tar contents: `all_pred_metric.csv`, `best_structure_relaxed.pdb`, the filtered a3m,
`hhsearch_output.hhr`, `hmm_output.sto`, and both per-model relaxed PDBs under
`extra_structures/`.

<a id="procs"></a>
## Process layout

Three kinds of process touch data: the driver (main loop + ImmediateTasks), a process
pool running FunctionTasks, and the GPU inference server(s). Subprocess tasks run external
binaries (mmseqs, hhsearch, hmmbuild). Any per-process instrumentation must initialize in
all three: driver `main()`, `executor_worker_init`, and the server's `initialize()`.

<a id="prov"></a>
## Provenance recipe (verified)

Spec: 23 entity types, 16 activities; artifacts = best structure + metrics, per-model
structures, filtered MSA, template hits/alignment, per-sequence tar. See
[[dftracer-provenance]] CASES.md "Structure prediction — elmerfold" for the type/id table.

- Ids: sequence-scoped types use `seq_id`; per-model types use `<seq_id>/<model>`;
  reference files `basename@size:mtime`; run config `preset:<config_preset>` when no
  config JSON is passed (the production default).
- The driver→server edge is the `inference_request` entity (`<seq_id>/<model>`), generated
  in a `ServerTask` subclass's `start()` and used in the server's `run_inference`.
- Results on `1n_10s`: 29 trace files, 240 entities, 181 activities, 761 edges, 0 dangling;
  run time 526 s vs 520 s untraced; tar members and metrics byte-identical to the plain run.

<a id="lessons"></a>
## Lessons

See also [[dftracer-provenance]] Lessons for the general provenance traps.
