Pipeline overview
=================

The pipeline is the end-to-end flow from source clone through annotation,
trace collection, diagnosis, and optimization.

What the pipeline does
----------------------

At a high level, it:

1. creates a session workspace
2. detects the build system and site details
3. builds the original application
4. installs dftracer into the session
5. scopes and annotates source files
6. rebuilds the annotated binary
7. runs a smoke test
8. captures traces under dftracer
9. splits and analyzes traces
10. diagnoses bottlenecks
11. proposes and validates optimizations

Pipeline stages
---------------

The current stage agents are:

* ``dftracer-session-setup``
* ``dftracer-build-app``
* ``dftracer-build-dftracer``
* ``dftracer-annotator``
* ``dftracer-build-smoke``
* ``dftracer-tracer``
* ``dftracer-analyzer``
* ``dftracer-diagnoser``
* ``dftracer-optimizer``

The planner agent, ``dftracer-pipeline-planner``, decides the execution order
and maps each stage to the narrowest executor subagent.

The stage agents use symbolic model levels so the backend can be selected from
the shared model matrix rather than being hardcoded in the docs or agent files.

ReadTheDocs flowchart source
---------------------------

The older markdown flowchart in ``docs/pipeline.md`` describes the same
pipeline in more graphical detail. This rst page is the ReadTheDocs-friendly
entry point.
