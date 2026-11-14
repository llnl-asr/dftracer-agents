"""dftracer_provenance_discover: provenance mode, discovery and agreement stage. Inventory what the plain run produced,
find the code touch-points where data is accessed, stored, communicated or computed, and persist the entity spec the
user approved; a deterministic validator rules on that spec before annotation starts."""
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.provenance import (
    SPEC_REL,
    inventory_outputs,
    scan_candidates,
    validate_spec,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok, _safe_session_path, _ws


def _spec_checks(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group validate_spec's problems into named checks, each with its repair."""
    probs = validate_spec(spec)
    groups = [
        ("artifacts", ("scientific_artifacts", "scientific artifact"),
         "name the artifact type(s) the user agreed on, declare each in entity_types with role: output, and give it a producer activity"),
        ("entity_types", ("entity_types is empty", "entity_type without name", "duplicate entity_type", "unknown store"),
         "declare every entity type once, with a name and a store from memory|file|db|object|network|gpu|other"),
        ("id_scheme", ("id_scheme",), "say how a unique, stable id is formed (relative path, step=N, seq_id ...), never a pointer or a timestamp alone"),
        ("role", ("role must be",), "tag every type input | output | intermediate (artifacts are always output)"),
        ("description", ("description",), "write one or two sentences in domain words: what the type is, where it comes from or goes"),
        ("activities", ("activity",), "every activity needs a name, where (file:function), and used and/or generated types that are declared"),
        ("orphans", ("orphan",), "every declared type takes part in some activity, or drop it with the user's agreement"),
    ]
    out, taken = [], set()
    for name, keys, fix in groups:
        mine = [p for p in probs if p not in taken and any(k in p for k in keys)]
        taken.update(mine)
        out.append({"check": name, "ok": not mine, "detail": "; ".join(mine) or "ok", "fix": fix if mine else ""})
    rest = [p for p in probs if p not in taken]
    if rest:
        out.append({"check": "other", "ok": False, "detail": "; ".join(rest), "fix": "repair the spec as each problem says"})
    return out


def register(mcp):
    @mcp.tool()
    def provenance_inventory_outputs(run_id: str, output_dir: str = "dataset",
                                     newer_than_epoch: float = 0.0) -> str:
        """Inventory what a plain (un-instrumented) run produced, to find the scientific artifact.

        Walks ``<WS>/<output_dir>`` (default ``dataset`` -- the PFS-backed run
        output dir) and groups files by extension with counts, bytes, examples and
        a domain hint (``.pdb`` -> molecular structure, ``.h5`` -> HDF5 dataset ...).
        Use it in provenance discovery BEFORE talking to the user: the artifact is
        usually the largest/most domain-specific output, not a log.

        Args:
            run_id: session id.
            output_dir: path relative to the session workspace (symlinks followed at the top).
            newer_than_epoch: only count files modified after this unix time
                (pass the run's start time to isolate one run's outputs).
        """
        ws = _ws(run_id)
        try:
            root = _safe_session_path(ws, output_dir) if output_dir not in (".", "") else ws
        except ValueError:
            # dataset/ is a symlink onto the PFS -- resolve() leaves the workspace by design
            root = ws / output_dir
        if not root.exists():
            return _err(f"{root} does not exist", run_id=run_id)
        return _ok("inventory complete", run_id=run_id, root=str(root),
                   **inventory_outputs(root, newer_than=newer_than_epoch))

    @mcp.tool()
    def provenance_scan_candidates(run_id: str, source_subfolder: str = "source",
                                   include_regex: str = "", max_hits: int = 400) -> str:
        """Find code touch-points where entities are read, written, created or stored.

        Regex sweep over C/C++/Python for file/HDF5/netCDF/MPI-IO/ADIOS/DB/torch/
        numpy/pandas/structure-library calls. Returns ``file:line``, enclosing
        function and kind (read/write/open/create/db/exec) so the agent can open
        only those lines. Use ``include_regex`` to narrow to the files the
        artifact's producer chain lives in. Pair with ``graph_query`` for call
        chains (who calls the writer of the artifact).
        """
        ws = _ws(run_id)
        try:
            root = _safe_session_path(ws, source_subfolder)
        except ValueError as e:
            return _err(str(e))
        if not root.exists():
            return _err(f"{root} does not exist")
        return _ok("scan complete", run_id=run_id, root=str(root),
                   **scan_candidates(root, include_regex, max_hits))

    @mcp.tool()
    def provenance_write_spec(run_id: str, spec_yaml: str, force: bool = False) -> str:
        """Validate and persist the user-approved provenance spec to ``<WS>/provenance/spec.yaml``.

        Schema::

            app: <name>
            scientific_artifacts: [<entity_type>, ...]     # agreed with the user
            entity_types:
              - name: protein_structure          # entity TYPE (science/data class)
                kind: science|input|intermediate|config|model|derived
                store: file|memory|db|object|network|gpu|other
                id_scheme: "output path relative to run dir"   # how the unique id is built
                where: "path/to/file.py:func"    # where instances appear
            activities:
              - name: predict_structure          # event name
                activity_type: inference         # optional; defaults to name
                where: "run_alphafold.py:predict_structure"
                used: [msa, model_params]
                generated: [protein_structure]
            decisions: ["free-text notes of what the user approved/excluded"]

        Rejected (unless ``force``) if: no artifact, artifact never generated,
        undeclared types in activities, entity types with no id_scheme, or orphan
        types. Returns the problem list either way.
        """
        import yaml
        try:
            spec = yaml.safe_load(spec_yaml) or {}
        except yaml.YAMLError as e:
            return _err(f"spec is not valid YAML: {e}")
        probs = validate_spec(spec)
        if probs and not force:
            return _err("spec failed validation", problems=probs)
        p = _ws(run_id) / SPEC_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(spec, sort_keys=False))
        return _ok("spec written", path=str(p), problems=probs,
                   entity_types=len(spec.get("entity_types") or []),
                   activities=len(spec.get("activities") or []))

    @mcp.tool()
    def dftracer_provenance_discover_check(spec_yaml: str = "", run_id: str = "") -> dict:
        """Validate a provenance spec deterministically: the contract the annotate stage builds against. Pass the spec text
        (spec_yaml) or a run_id whose <WS>/provenance/spec.yaml is checked. Checks: YAML parses; at least one scientific
        artifact, declared with role output and generated by some activity; every entity type has a unique name, a store, an
        id_scheme, a role (input|output|intermediate) and a description of >= 20 characters; every activity has where and
        used/generated types that are declared; no orphan types. Use after provenance_write_spec (or before it, on the draft).
        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}]}."""
        import yaml
        text = spec_yaml
        if not text.strip():
            if not run_id.strip():
                return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "give spec_yaml or run_id",
                                                              "fix": "pass the spec text, or the session run_id after provenance_write_spec"}]}
            p = _ws(run_id) / SPEC_REL
            if not p.is_file():
                return {"status": "inconclusive", "checks": [{"check": "spec_present", "ok": False,
                                                              "detail": f"unknown run or no spec: {SPEC_REL} missing for {run_id!r}",
                                                              "fix": "agree the entity set with the user, then provenance_write_spec"}]}
            text = p.read_text()
        try:
            spec = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            return {"status": "fail", "checks": [{"check": "yaml", "ok": False, "detail": f"not valid YAML: {e}"[:300],
                                                  "fix": "fix the YAML syntax (indentation, quoting) and validate again"}]}
        if not isinstance(spec, dict):
            return {"status": "fail", "checks": [{"check": "yaml", "ok": False, "detail": "the spec is not a mapping",
                                                  "fix": "the spec is a mapping with app, scientific_artifacts, entity_types, activities"}]}
        checks = [{"check": "yaml", "ok": True, "detail": "parses", "fix": ""}] + _spec_checks(spec)
        return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}
