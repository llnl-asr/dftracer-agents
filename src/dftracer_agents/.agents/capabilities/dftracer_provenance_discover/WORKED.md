# What worked: dftracer_provenance_discover

## elmerfold/provenance/discover
Structure prediction, elmerfold (OpenFold monomer pipeline), confirmed 2026-10-01 (10 sequences, 240 entities / 181 activities, 0 dangling).
- artifact: best_structure + prediction_metrics (pLDDT/pTM), per-model unrelaxed/relaxed structures, filtered_msa, template_hits, of3_template_alignment, structure_tar
- entity types (id): sequence (seq_id); raw_msa (`jackhmmer|hhblits/<seq_id>`); merged/filtered/stockholm_msa, hmm_profile, msa_features, template_hits/features, of3_template_alignment, best_structure, prediction_metrics, structure_tar (seq_id); unrelaxed/relaxed_structure, structure_metrics, inference_request (`<seq_id>/<model>`); reference_db, model_params, input_chunk (`basename@size:mtime`); run_config (`preset:<preset>` when no JSON); inference_seed (`seed=<n>`)
- activities: load_inputs, load_and_merge_msas, filter_msa, delete_merged_msa, msa_featgen, of2_template_search, template_featgen, a3m_to_sto, of3_hmmbuild, of3_template_search, request_inference (driver) -> run_inference + relax_structure (GPU server), select_best, save_data, cleanup
- traps: three process kinds (driver, pool, server) all need init/finalize; subprocess tasks (mmseqs/hhsearch) are recorded from the driver after completion; allow_failure outputs recorded only if the file exists; run_config absent unless the preset fallback is used

## tool:provenance_inventory_outputs
`provenance_inventory_outputs(run_id, "dataset/<plain run>", newer_than_epoch=<run start>)`, then rank by `hint` and bytes: the artifact is the largest, most domain-specific output, never a log.

## tool:provenance_write_spec
Write the spec only after the user answered the discovery questions; run `dftracer_provenance_discover_check(spec_yaml=...)` on the draft first, then `provenance_write_spec(run_id, spec_yaml)` and the check on the run_id.
