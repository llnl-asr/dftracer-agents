---
name: dftracer_annotate_ai
description: dftracer AI/ML-region annotation of Python (dft_ai.pipeline/compute/data/dataloader/checkpoint/device/comm decorators and iter() wrappers): find_source_files, python_annotate_ai_file, python_write_ai_file, dftracer_get_ai_annotation, session_detect_ml_workload, session_search_local_papers, session_ml_append_lesson. Load for per-file AI region annotation, choosing the dft_ai region for a function, detecting PyTorch/TensorFlow/JAX workloads, or recording an ML annotation lesson.
---

# dftracer_annotate_ai

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents register_ai_tools (session/annotation_ai.py) and the manual STEP 4 recipe of dftracer-ml-annotate. Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_ai_check rules on what the predictors produce.

## Rules

- Predict with python_annotate_ai_file, python_write_ai_file, dftracer_get_ai_annotation, find_source_files, session_detect_ml_workload, session_search_local_papers, session_ml_append_lesson; validate with dftracer_annotate_ai_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-ml-annotate/SKILL.md (STEP 4 manual: python_annotate_ai_file)`

══════════════════════════════════════════════════════════════════════
STEP 4 (manual) — AI/ML REGION ANNOTATION  (python_annotate_ai_file)
══════════════════════════════════════════════════════════════════════

MANDATORY: Use `python_annotate_ai_file` for all files. NEVER manually
write decorators with Edit/Write. The tool handles idempotency, loop
wrapping, and import injection automatically.

Before annotating any function you are unsure about, call:

    dftracer_get_ai_annotation(
      function_name=<fn_name>,
      context=<what it does>,
      phase=<compute|data|dataloader|comm|device|checkpoint|pipeline>
    )

This returns the exact decorator and a ready-to-use code example.

#### 4a. Entry-point files (is_entry=True)

    python_annotate_ai_file(
      run_id=RUN_ID, filepath=<file>,
      category=<module_stem>,
      is_entry=True, annotate_loops=True,
    )

    Verify the result contains:
      - initialize_log injected near the top
      - finalize() injected before program exit
      - @dft_ai on main() / run() / __call__()

#### 4b. Training files

    python_annotate_ai_file(run_id=RUN_ID, filepath=<file>,
      category=<module_stem>, annotate_loops=True)

    Expected decorators:
      train / fit / run_epoch     → @dft_ai.pipeline.train
      evaluate / validate         → @dft_ai.pipeline.evaluate
      test                        → @dft_ai.pipeline.test
      forward                     → @dft_ai.compute.forward
      backward / loss.backward    → @dft_ai.compute.backward
      for epoch in ...:           → dft_ai.pipeline.epoch.iter(...)
      for batch in ...:           → dft_ai.dataloader.fetch.iter(...)

    Optimizer step — MUST use start/stop style (ML-R4):
      dft_ai.compute.step.start()
      optimizer.step()
      dft_ai.compute.step.stop()

    After annotating each file, add inside the batch loop:
      ai.update(step=step, epoch=epoch)   ← ML-R8

#### 4c. Data/Dataset files

    python_annotate_ai_file(run_id=RUN_ID, filepath=<file>,
      category=<module_stem>, annotate_loops=True)

    Expected decorators:
      __getitem__ / read_index / load_sample  → @dft_ai.data.item
      preprocess / transform / augment        → @dft_ai.data.preprocess.derive(name="<op>")
      collate                                 → @dft_ai.data.preprocess.derive(name="collate")
      to_device / .cuda() / .to(device)       → @dft_ai.device.transfer

    **Data I/O rules (ML-R25) — use `ai.data.io.*` for explicit open/read/write/close:**

    Annotate each I/O phase separately instead of lumping everything into `data.item`.
    ALWAYS compute and pass `image_size` (bytes) as metadata to every I/O region.

    Phase mapping:
      open file / open dataset / h5py.File(...)  → @dft_ai.data.io.open
      np.load / f.read() / dataset[idx]          → @dft_ai.data.io.read  + image_size
      f.write() / np.save()                      → @dft_ai.data.io.write + image_size
      f.close() / file handle cleanup            → @dft_ai.data.io.close

    image_size MUST be the byte size of the actual DATA ARRAY, computed from the
    loaded/written object — NEVER from the path string or file metadata:

      ✅ numpy array:   image_size=array.nbytes          (in-memory array bytes)
      ✅ torch tensor:  image_size=tensor.element_size() * tensor.nelement()
      ✅ bytes object:  image_size=len(buf)
      ✅ checkpoint:    image_size=sum(t.nbytes for t in state_dict.values() if hasattr(t, "nbytes"))
      ❌ WRONG:         image_size=os.path.getsize(path) (file-system metadata, not data)
      ❌ WRONG:         image_size=len(path)             (path string length)

    Pass image_size via update() AFTER the read/write so the value is known:

    ```python
    from dftracer.python import ai

    # numpy example
    def load_sample(path: str):
        with ai.data.io.open:
            pass
        with ai.data.io.read:
            data = np.load(path)
            ai.data.io.read.update(image_size=data.nbytes)
        with ai.data.io.close:
            pass
        return data
    ```

    Context-manager style is preferred when the function mixes phases.
    Decorator style is preferred when the method maps 1:1 to a phase.

#### 4d. Checkpoint files

    python_annotate_ai_file(run_id=RUN_ID, filepath=<file>,
      category=<module_stem>)

    Expected decorators:
      save / save_checkpoint / write_ckpt     → @dft_ai.checkpoint.capture
      load / load_checkpoint / restore_ckpt  → @dft_ai.checkpoint.restart

    **Checkpoint I/O rules (ML-R26) — use `ai.checkpoint.io.*` for explicit phases:**

    Wrap the four I/O phases inside the outer capture/restart context.
    ALWAYS compute and pass `image_size` (bytes of the checkpoint) as metadata.

    Phase mapping:
      open file for checkpoint               → @ai.checkpoint.io.open
      torch.load / pickle.load / f.read()    → @ai.checkpoint.io.read  + image_size
      torch.save / pickle.dump / f.write()   → @ai.checkpoint.io.write + image_size
      f.close() / os.remove()               → @ai.checkpoint.io.close

    image_size for checkpoints = total bytes of all tensors in the state dict.
    Compute from the in-memory data — NOT from os.path.getsize or len(path):

      read:  sum(t.nbytes for t in checkpoint.get("model_state_dict", {}).values() if hasattr(t, "nbytes"))
      write: sum(t.nbytes for t in state_dict.get("model_state_dict", {}).values() if hasattr(t, "nbytes"))

    ```python
    from dftracer.python import ai
    import torch

    def save_checkpoint(model, path: str):
        state_dict = {"model_state_dict": model.state_dict()}
        ckpt_bytes = sum(t.nbytes for t in state_dict["model_state_dict"].values() if hasattr(t, "nbytes"))
        with ai.checkpoint.capture:
            with ai.checkpoint.io.open:
                f = open(path, "wb")
            with ai.checkpoint.io.write:
                torch.save(state_dict, f)
                ai.checkpoint.io.write.update(image_size=ckpt_bytes)
            with ai.checkpoint.io.close:
                f.close()

    def load_checkpoint(path: str):
        with ai.checkpoint.restart:
            with ai.checkpoint.io.open:
                f = open(path, "rb")
            with ai.checkpoint.io.read:
                state = torch.load(f)
                ckpt_bytes = sum(t.nbytes for t in state.get("model_state_dict", {}).values() if hasattr(t, "nbytes"))
                ai.checkpoint.io.read.update(image_size=ckpt_bytes)
            with ai.checkpoint.io.close:
                f.close()
        return state
    ```

#### 4e. Other I/O files  (config reads, stats writes, utility I/O — ML-R27)

Any I/O that is NOT inside the DataLoader path or checkpoint save/restore goes
under `ai.other.io.*`. This covers: config file reads, CSV/stats file writes,
rendezvous/coordination files, datagen scripts, logging helpers.

    Phase mapping:
      open file / open dataset           → dft_ai.other.io.open
      f.read() / yaml.load / np.load()   → dft_ai.other.io.read  + image_size
      f.write() / outfile.write(row)     → dft_ai.other.io.write + image_size
      f.close()                          → dft_ai.other.io.close

    Use `ai.other.log` for logging/print sinks that should be traced but
    carry no I/O bytes.

    image_size rules are identical to ML-R25:
      bytes object:  image_size=len(buf)
      numpy array:   image_size=array.nbytes
      encoded str:   image_size=len(s.encode())
      NEVER os.path.getsize or len(path)

    Category decision tree:
      Is the I/O inside __getitem__ / DataLoader path?  → data.io.*
      Is the I/O torch.save / torch.load of model weights?  → checkpoint.io.*
      Everything else  → other.io.*

    ```python
    from dftracer.python import ai

    # config read example
    with ai.other.io.open:
        f = open(config_path, "rb")
    with ai.other.io.read:
        raw = f.read()
        cfg = yaml.safe_load(raw)
        ai.other.io.read.update(image_size=len(raw))
    with ai.other.io.close:
        f.close()

    # stats CSV write example
    with ai.other.io.write:
        outfile.write(row)
        ai.other.io.write.update(image_size=len(row.encode()))
    ```

#### 4f. Distributed communication files  (when DISTRIBUTED=True)

    python_annotate_ai_file(run_id=RUN_ID, filepath=<file>,
      category=<module_stem>)

    Expected — context manager style (ML-R5):
      dist.all_reduce(...)   → with dft_ai.comm.all_reduce(): ...
      dist.barrier()         → with dft_ai.comm.barrier(): ...
      dist.broadcast(...)    → with dft_ai.comm.broadcast(): ...
      dist.all_gather(...)   → with dft_ai.comm.all_gather(): ...

#### 4g. Generic expensive functions  (COST-GATED — python_estimate_file_costs)

**Scope.** Sections 4a–4f above are *semantic*: data loading, model init,
checkpoint, training step, and distributed comm are annotated because of what
they mean, not what they cost. **Always annotate those — never gate them on a
cost score.** This section (4g) is the only place where annotation is
*selective*, and it covers the generic `@_dlp.log` / `dlp.init` instrumentation
of everything left over in OTHER_FILES.

Annotating every remaining function drowns the trace in getters, `__repr__`,
and one-line helpers. Use the AI/ML-aware cost estimator — the Python
counterpart of `clang_estimate_function_cost` — to pick only the functions
worth instrumenting:

    python_estimate_file_costs(run_id=RUN_ID, filepath=<file>, threshold=20)
      → { "annotate": [names...], "skip": [names...], "functions": [ ... ] }

    # single function, when you need the breakdown:
    python_estimate_function_cost(run_id=RUN_ID, filepath=<file>,
                                  function_name=<fn>, threshold=20)

Then annotate ONLY the returned `annotate` list:

    python_annotate_file(run_id=RUN_ID, filepath=<file>,
      category=<module_stem>)
    → uses @_dlp.log / @_dlp.log_init / @_dlp.log_static

**What the estimator scores** (from the AST — no regex): framework I/O
(`torch.load`/`save`, `h5py.File`, `np.load`, `read_parquet`, `pickle`),
checkpoint/persistence (`state_dict`, `load_state_dict`, `from_pretrained`),
distributed comm (`all_reduce`, `barrier`, `broadcast`), data pipeline
(`DataLoader`, `collate`), host↔device transfers (`.to()`, `.cuda()`,
`.numpy()`), autograd/compute (`backward`, `step`, `zero_grad`), loops,
branches, calls, body size.

Weights: io×30, checkpoint×35, comm×25, data×20, xfer×12, compute×10, loop×8,
branch×3, call×2, size bonus ≤20. Default threshold 20.

**Overrides (first match wins) — these beat the score:**

| Condition | Result |
|---|---|
| `@property` / `__repr__`,`__str__`,`__len__`,… **and no I/O** | **skip** (Rule 0) |
| `__getitem__`, `__iter__`, `__next__` | **annotate** (data pipeline) |
| any checkpoint / file-I/O / comm call present | **annotate** (R6/R7) |
| name contains `_load`,`_save`,`_read`,`_write`,`checkpoint`,`train_step`,… | **annotate** |
| ≤5 statements, no loop, no I/O | **skip** (Rule 0) |
| otherwise | score ≥ threshold → annotate |

A `@property` that performs I/O is **not** skipped — the I/O signal wins.

Raise `threshold` to trace less (e.g. 60 keeps only real I/O/comm/checkpoint
work); lower it to trace more. Changing the threshold never disables the
semantic overrides above.

#### 4h. PyTorch / Framework-specific rules

  PyTorch DDP (ML-R11): Annotate INNER model.forward(), not the DDP wrapper.
  Lightning (ML-R12):   training_step → @dft_ai.compute
                        validation_step → @dft_ai.pipeline.evaluate
                        test_step → @dft_ai.pipeline.test
  TensorFlow (ML-R13):  Annotate Python wrapper that calls tf.function, not the
                        tf.function itself.
  JAX (ML-R14):         Call jax.block_until_ready(result) inside annotated fns.

#### 4i. PyTorch Profiler integration (ML-R28) — when "pytorch" in FRAMEWORKS

  When PYTORCH_EXTRAS_OK is True, inject the PyTorch Profiler wrapper into the
  TRAIN_FILE that contains the batch loop. Do this AFTER python_annotate_ai_file
  so the batch loop is already wrapped with dft_ai.dataloader.fetch.iter().

  Inject at the top of the training file (after the dftracer import block):

    ```python
    from torch.profiler import profile, schedule, ProfilerActivity
    from dftracer.python.torch import trace_handler as _dft_torch_trace_handler

    _dft_prof_schedule = schedule(wait=1, warmup=1, active=3, repeat=1)
    ```

  Wrap the batch loop body with the profiler context (enclose the `for batch in ...`
  loop, call `prof.step()` at end of each batch iteration):

    ```python
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        schedule=_dft_prof_schedule,
        on_trace_ready=_dft_torch_trace_handler,
        record_shapes=True,
        with_stack=False,
    ) as _dft_prof:
        for batch in dft_ai.dataloader.fetch.iter(loader):
            # ... forward / backward / optimizer step ...
            _dft_prof.step()
    ```

  Rules:
    - Use ONLY `_dft_torch_trace_handler`; do NOT also set tensorboard handler.
    - `prof.step()` must fire once per batch, inside the batch loop.
    - schedule(wait=1, warmup=1, active=3) skips cold-start batches.
    - Trace verification: category `PP` must appear in session_analyze_traces output.

#### 4j. PyTorch Dynamo integration (ML-R29) — when "pytorch" in FRAMEWORKS

  When PYTORCH_EXTRAS_OK is True and the source calls `torch.compile` (or the
  model would benefit from compile-level tracing), replace the bare
  `model = torch.compile(model)` with the dftracer Dynamo backend.

  At the top of the training file (after the dftracer import block):

    ```python
    from dftracer.python.dynamo import create_backend as _dft_create_backend
    ```

  In the training loop, replace bare `torch.compile(model)` with:

    ```python
    _dft_dynamo_backend = _dft_create_backend(name="<app_name>", epoch=epoch, step=step)
    compiled_model = torch.compile(model, backend=_dft_dynamo_backend)
    ```

  If `torch.compile` is NOT present in the source, add it before the training loop:

    ```python
    # dftracer Dynamo tracing — wraps model forward pass at compile level
    _dft_dynamo_backend = _dft_create_backend(name="<app_name>", epoch=0, step=0)
    model = torch.compile(model, backend=_dft_dynamo_backend)
    ```

  Rules:
    - Update epoch/step each iteration: recreate backend or call update() if available.
    - Do NOT use both `@dynamo.compile` and `create_backend` on the same function.
    - On AMD/ROCm without triton, compile falls back to eager — events still appear.
    - Trace verification: category `DY` must appear in session_analyze_traces output.
    - If model is already wrapped in DDP, apply torch.compile BEFORE DDP wrapping.

Print per-file status after each file: ✓ <file> (<N> AI regions, <M> generic)

## Constraints

- Python ML files only; dft_ai regions are semantic and exempt from the cost gate, generic decorators are not.

## Corner cases

- See LESSONS.md for the dated cases met so far.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_ai_check failures and their repairs:
- `dft_ai imported` false: python_annotate_ai_file inserts `from dftracer.python import dftracer, dft_fn as DFTracerFn, ai as dft_ai`; re-run it rather than adding decorators by hand.
- `dft_ai decorators known` false: a misspelt or invented region; ask dftracer_get_ai_annotation(function_name=...) for the right one.
- `one dft_ai decorator per function` false: keep the most specific region (python_dedup_annotations).
- Python checks (parses, coverage, properties, init/finalize): as in dftracer_annotate_python's Corrector.
Run the check again; the same failure twice stops for the user.

## Self-heal

- If the region table (_AI_REGION_PATTERNS) maps a name wrongly, fix it in the tool and record the case with session_ml_append_lesson.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [tool:python_annotate_ai_file] per-file repair inside the ML fast path: python_annotate_ai_file(run_id, filepath, category=<entry|train|data|ckpt|comm>, is_entry=...) then python_write_ai_file; dft_ai regions are exempt from the cost gate. (dftracer-ml-annotate STEP 4 manual)
