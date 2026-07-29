---
name: software-alphafold3
description: AlphaFold3 (AF3) inference — JAX/XLA on ROCm, venv rebuild recipe, where the MSA cost actually lives (pybind11 C++), and dftracer annotation caveats. Load this skill for any AF3 / AlphaFold build, annotation, tracing, or optimization work.
---

# AlphaFold 3 (AF3) inference

AF3 ships **inference only** — no training source; the trained weights are supplied
separately under a restricted-use licence. A prediction is two phases:
**(1) MSA (multi-sequence alignment) search** and **(2) diffusion-based structure
prediction**. MSA search is the classic bottleneck, which is why sites cache MSAs.

## Stack

- **JAX / XLA — not PyTorch.** On AMD this needs the ROCm JAX wheels, not stock jax.
- Python **3.11** (>= 3.11 required).
- Packaged with **scikit-build-core** + CMake + Ninja + **pybind11**; ~35 C++ sources
  build into extension modules. `pip install --no-deps -e .` triggers that build.
- Entry point: `run_alphafold.py --json_path=<input.json> --output_dir=<dir> --flash_attention_implementation=xla`

## Venv build recipe (AMD / ROCm)

```bash
ml python/3.11.5                       # verify with python -V BEFORE creating the venv
virtualenv --system-site-packages af3env
source af3env/bin/activate
module load rocm/6.0.0                 # required at BOTH install time and run time
pip install -r llnl-requirements.txt
pip install --no-deps -e .             # scikit-build-core / CMake / pybind11 build
pip install <ROCm jax release>/jaxlib-0.4.34-cp311-cp311-manylinux_2_28_x86_64.whl
pip install <ROCm jax release>/jax_rocm60_pjrt-0.4.34-py3-none-manylinux_2_28_x86_64.whl \
            <ROCm jax release>/jax_rocm60_plugin-0.4.34-cp311-cp311-manylinux_2_28_x86_64.whl
build_data                             # generates required chemical-component data
pip install pandas==1.5.3 numpy==1.26  # exact pins
```

**Mandatory AMD patch** — in `af3env/lib/python3.11/site-packages/jax_triton/__init__.py` set:
```python
get_compute_capability = None
get_serialized_metadata = None
```
Without it AF3 fails at import on AMD.

**Runtime flags:** `module load rocm/6.0.0` and
`export XLA_FLAGS="--xla_gpu_enable_triton_softmax_fusion=true --xla_gpu_triton_gemm_any=True"`

**Acceptance gate** (run all four; `import` alone is not enough):
```bash
python -V                                                  # 3.11.x
python -c "import jax; print(jax.__version__)"             # 0.4.34
python -c "import jax; print(jax.devices())"               # MUST list ROCm devices
python -c "from alphafold3.data import parsers; print('ok')"
```
If `jax.devices()` lists no GPU, the install is NOT usable — do not proceed.

## Pre-computed MSA mode (what changes when MSAs are cached)

Input JSON can reference already-computed alignments instead of running a search:
```json
"sequences": [{"protein": {"id":"A", "sequence":"...",
   "unpairedMsaPath":"<msa_db>/<id>_A_unpaired_msa.a3m",
   "pairedMsaPath":  "<msa_db>/<id>_A_paired_msa.a3m"}}]
```
This bypasses the jackhmmer/nhmmer search entirely. **Do not assume MSA search is the
bottleneck when these paths are present — it isn't even running.**

**Measured (single node, 5 model seeds, pre-computed MSAs):** wall time tracks MSA
*file size*, not GPU work — ~18 MB MSA -> ~3 min total, ~27 MB -> ~7 min, ~80 MB ->
~46 min, while "model inference with 5 seeds" stayed ~146 s throughout. So in cached-MSA
mode the cost is dominated by **reading / parsing / featurising the .a3m files on the CPU
data path**, not by diffusion inference. Confirm per-session with traces before optimising.

## Where the MSA cost actually lives: pybind11 C++, not Python

`src/alphafold3/data/parsers.py` is a thin (~178-line) wrapper that delegates to
compiled extensions:
```python
from alphafold3.cpp import fasta_iterator
from alphafold3.cpp import msa_conversion
```
The real work is in `src/alphafold3/parsers/cpp/` (`fasta_iterator_lib.cc`,
`msa_conversion_pybind.cc`, `cif_dict_lib.cc`) and `src/alphafold3/data/cpp/`
(`msa_profile_pybind.cc`).

**Implication for dftracer:** you do **not** have to annotate the C++ to measure this.
- The annotated Python wrapper functions *bracket* the pybind call, so the time spent
  inside C++ is attributed to the enclosing Python region.
- dftracer's POSIX interception captures the `open`/`read` syscalls the extensions make.
Only add C++ annotation if traces show a large unattributed gap *inside* one of those
wrappers.

## dftracer annotation caveats (AF3-specific)

- **Never decorate `@overload` stub signatures or abstract `typing.Protocol` methods**
  (body `...`). `python_estimate_function_cost` scores them like real functions and
  `python_annotate_file` will annotate them. Observed 4 such cases across
  `run_alphafold.py`, `model_class/alphafold3.py`, `data/template_store.py`
  (`TemplateFeatureProvider.__call__`), `data/tools/msa_tool.py` (`MsaTool.query`).
  Strip them after the annotation pass.
- `data/msa_identifiers.py` is pure regex/string work with no I/O — correctly skipped
  by the Rule 0 cost gate; a zero-annotation result there is not a tool failure.
- `data/tools/*.py` (hmmalign, hmmbuild, hmmsearch, jackhmmer, nhmmer, msa_tool,
  subprocess_utils) wrap external subprocess MSA-search binaries. In cached-MSA mode
  they do not execute, but annotate them so a non-cached run is covered by the same tree.
- Leave `jax/attention/flash_attention.py` and `jax/gated_linear_unit/block.py` alone —
  `load` there is a Pallas/Triton tensor-kernel primitive, not file I/O, and these are
  per-layer hot paths.
- `model/params.py.__init__` is one-time weight loading; exclude from per-layer gating
  but keep as a phase boundary if you want weight-load time attributed.
- Useful phase boundaries for splitting total runtime cleanly:
  `common/folding_input.py::_read_file` / `from_sequence` (input read),
  `data/pipeline.py` + `data/featurisation.py` (data prep),
  `model_class/alphafold3.py` (inference),
  `model/post_processing.py::write_output` / `write_embeddings` (output write).

## Batch/launch shape

Stock site workflow submits `flux batch -N 1 --exclusive` per batch of structures and
loops one structure at a time, with `--output_dir` shared across the batch. When
adapting it, redirect `--output_dir` to a writable PFS path you own — the stock script
sets `OUTPUT_DIR=$data_dir`, which points back into the (often read-only) sample dir.

Related: [[software-rocm]], [[system-tuolumne]], [[tools-pydftracer]],
[[dftracer-annotate-python]], [[bug-dftracer-cray-runtime-silent-noop]].
