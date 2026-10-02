# Provenance cases

One entry per workload family. Format:

```
## <domain> — <app family>
- artifact: <type(s)> — why it is the artifact
- entity types: <type: id_scheme> ...
- activities: <name: used -> generated> ...
- traps: <symptom -> cause -> fix>
- confirmed: <yes/no + date>
```

Seed patterns below are starting hypotheses, not verified sessions — confirm with
the user and replace with a verified entry.

## Simulation (mesh/particle codes) — seed
- artifact: field/snapshot files, checkpoints, derived diagnostics
- entity types: config(input deck path), mesh_state(step=N), checkpoint_file(rel path), diagnostic(name@step)
- activities: read_config: config -> mesh_state(step=0); step: mesh_state(N) -> mesh_state(N+1); write_ckpt: mesh_state -> checkpoint_file
- traps: per-step entities explode at fine cadence — record only steps that are written or consumed downstream

## ML training — seed
- artifact: trained model weights + eval metrics
- entity types: dataset(name/split), batch(epoch/step range), model_weights(epoch=N), checkpoint_file, metrics(epoch=N)
- activities: load_data, train_epoch: model(N-1)+dataset -> model(N), evaluate: model(N) -> metrics(N), save
- traps: DDP allreduce is a communicate edge between ranks — model ids must include epoch, not rank

## Structure prediction — elmerfold (OpenFold monomer pipeline)
- artifact: best_structure + prediction_metrics (pLDDT/pTM), per-model unrelaxed/relaxed
  structures, filtered_msa, template_hits, of3_template_alignment, structure_tar
- entity types (id): sequence (seq_id); raw_msa (`jackhmmer|hhblits/<seq_id>`); merged/filtered/
  stockholm_msa, hmm_profile, msa_features, template_hits/features, of3_template_alignment,
  best_structure, prediction_metrics, structure_tar (seq_id); unrelaxed/relaxed_structure,
  structure_metrics, inference_request (`<seq_id>/<model>`); reference_db, model_params,
  input_chunk (`basename@size:mtime`); run_config (`preset:<preset>` when no JSON); inference_seed (`seed=<n>`)
- activities: load_inputs, load_and_merge_msas, filter_msa, delete_merged_msa, msa_featgen,
  of2_template_search, template_featgen, a3m_to_sto, of3_hmmbuild, of3_template_search,
  request_inference (driver) -> run_inference + relax_structure (GPU server), select_best,
  save_data, cleanup
- traps: three process kinds (driver, pool, server) all need init/finalize; subprocess
  tasks (mmseqs/hhsearch) are recorded from the driver after completion; allow_failure
  outputs recorded only if the file exists; run_config absent unless the preset fallback is used
- confirmed: yes (2026-10-01, 10 sequences, 240 entities / 181 activities, 0 dangling). See [[workload-elmerfold]].

## Workflows (Pegasus/1000genome style) — seed
- artifact: final analysis outputs of the DAG
- entity types: one per file kind in the DAG; ids = logical file names
- activities: one per task type; files join tasks across processes by identical type+id
