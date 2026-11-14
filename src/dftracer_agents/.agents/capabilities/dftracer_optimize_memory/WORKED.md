# What worked: dftracer_optimize_memory

One entry per key, newest wins. Portable: no user paths.

- [tool:opt_memory_checklist] 2026-11-13: render the 10 dimensions and the mem_bw strategies, fill one row per dimension (verdict, level, citation, reason, app_impact_pct, system_impact_pct), then dftracer_optimize_memory_check until status pass.
- [ray_molformer/mi300a/memory-capacity] 2026-08-02: read the dftracer_service node-counter series (name == "memory") through view/reader; peak 22.5 GiB of ~490 GiB, zero swap/dirty/writeback -> capacity categories not applicable, memory not the bottleneck.
- [ray_molformer/ddp/weights-mmap] 2026-08-02: query the trace's file names for .bin/.safetensors/.pt/.pth/.ckpt reads; zero matches -> shared/mmap weights not applicable.
- [pecan/mi300a/memory] 2026-07-20: pin_memory, staging-copy and huge-page levers marked not applicable from the unified-memory architecture (arXiv 2508.12743) and the KB; "not memory-bound" verdict for a data-loading-bound job.
