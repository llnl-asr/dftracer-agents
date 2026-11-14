# What worked: dftracer_provenance_graph

## elmerfold/provenance/graph
`provenance_extract_graph(run_id)` over the provenance traces of 10 sequences: healthy, 240 entities / 181 activities, 0 dangling; artifact members and a metrics file byte-identical to the plain run, +1% wall time (2026-10-01).

## tool:provenance_extract_graph
Default trace_dir `provenance/traces`, out_dir `provenance/graph`; `viewer_bin=<venv>/bin/dftracer_view` when the viewer is not on PATH. Then `dftracer_provenance_graph_check(run_id)`.

## provenance/package/isolation
`<WS>/final_provenance/`: `config.ini` (the only place for site values; `${USER}`, never a literal name), `scripts/lib_load_config.sh` sourced by every script (env overrides config), numbered steps `00_check_prereqs ... 08_view`, `run_all.sh`, `patches/<app>_provenance.patch` against the pinned app commit, `inputs/spec.yaml`, and a standalone checker `prov_check.py` (reads EH/ET/ER + `args.relations`, stdlib + PyYAML only) that writes `run.json`/`README.md` and fails on dangling ids, undescribed types or ungenerated artifact types. Pin dftracer/pydftracer/utils by full commit. Verify with `session_verify_isolation` `static` -> `launch` (copy to a new PFS dir, `env -i ... bash -l`, fresh OUTPUT_ROOT) -> `status`: pass = exit 0 and the checker reproduces the session's counts with 0 dangling.

## provenance/view
Interactive: `dftracer_server -d <WS>/provenance/traces/raw --index-dir <WS>/provenance/server_index -p <port>`, Provenance tab (`GET /api/prov/graph`, dftracer-utils `feat/provenance-view`): entities colored by type, a lineage/impact tracer. Shareable page: `render_prov_graph.py graph.json out.html --spec spec.yaml` (Cytoscape + dagre) after redacting `$LUSTRE_ROOT/$USER` paths in `uri`.
