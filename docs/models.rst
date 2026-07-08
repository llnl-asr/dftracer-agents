Models and configuration
========================

The canonical model selection lives in
``src/dftracer_agents/.agents/workspace/models.yaml``.

The intent is:

* keep the agent content the same across all harnesses
* choose the backend model from one configurable source
* allow different provider families such as Claude, Copilot-backed models,
  and Ollama-hosted cloud models

The shared model file defines four levels:

* ``level_1`` for deterministic orchestration and tool plumbing
* ``level_2`` for routing, planning, and lightweight reasoning
* ``level_3`` for analysis, diagnosis, and stage-to-stage decisions
* ``level_4`` for deep synthesis and optimization

The stage agents refer to these symbolic levels through frontmatter, while the
actual provider-specific value for each level is controlled in the shared YAML
file. That means the prompt/content stays stable, and only the selected model
backend changes.

Current provider mapping
------------------------

The shared YAML currently maps the levels to three backend families:

* Claude
* Copilot-backed models
* Ollama cloud-hosted models

Update that file to re-point the levels without rewriting every agent file.
