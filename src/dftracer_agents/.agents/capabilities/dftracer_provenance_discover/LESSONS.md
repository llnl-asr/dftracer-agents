# Lessons: dftracer_provenance_discover

- [L-760a71f2] Optional config absent in the run -> the config root silently disappeared from the lineage because no file existed to name it -> identify the config by what actually configured the run (e.g. `preset:<name>`) and declare it as an input type (2026-10-01)
- [L-431d5f3e] Per-step entities at fine output cadence (simulation) -> the entity count explodes and the graph becomes unreadable -> record only steps that are written or consumed downstream; keep members as an id range (`samples[0:4096]@epoch=3`) (2026-10-01)
- [L-a1c78021] DDP training graph merged ranks' models -> model ids carried the rank instead of the epoch, and allreduce was not modelled -> treat allreduce as a communicate edge and include the epoch in model ids (2026-10-01)
- [L-3fc06d6e] Artifact picked silently by the agent -> wrong entity set, re-annotation -> always propose 1-3 candidates with evidence and ask; the user's rule: track all scientific artifacts plus everything that leads to them and is derived from them (2026-10-01)
- [L-76d81835] Users could not map "the predicted structures" to a type in queries -> types had code identifiers and no description -> every type carries a one-or-two-sentence description in domain words and a role; validate_spec now rejects a missing or short description and a missing role (2026-10-01)
