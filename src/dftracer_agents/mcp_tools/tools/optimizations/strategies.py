"""Shared data structures and helpers for the optimization tools.

This module contains:
- ``_METRIC_SYNONYM_PAIRS``: per-metric arXiv search synonym pairs.
- ``_GENERAL_FALLBACK_QUERIES``: broadest fallback search queries.
- ``_fetch_arxiv_papers``: fetch papers from the arXiv API.
- ``_BUILTIN_REFS``: built-in citable references (WisIO, Drishti).
- ``_L1_STRATEGIES``, ``_L2_STRATEGIES``, ``_L3_STRATEGIES``: per-level strategy tables.
- ``_gen_level_proposals``: generate citation-backed proposals for one level.

Imported by diagnose.py, iteration.py, and levels.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..session.workspace import _run


# ---------------------------------------------------------------------------
# Optimization-loop helpers (module-level so they can be unit-tested)
# ---------------------------------------------------------------------------

#: Per-metric lists of (primary_phrase, synonym_phrase) pairs.
#: Index 0 is the most specific; deeper indices are progressively fuzzier.
_METRIC_SYNONYM_PAIRS: Dict[str, List[tuple]] = {
    "small_io": [
        ("small I/O aggregation buffering",     "small write coalescing HPC"),
        ("two-phase I/O collective optimization","I/O forwarding staging parallel"),
        ("burst buffer staging optimization",    "write-behind cache aggregation"),
        ("ROMIO hints MPI-IO optimization",      "MPI-IO aggregator collective"),
        ("I/O middleware optimization HPC",      "data staging I/O optimization"),
    ],
    "small_read": [
        ("small read prefetching optimization",  "read-ahead policy optimization"),
        ("cache hit ratio I/O optimization",     "temporal locality I/O"),
        ("file read buffer optimization HPC",    "read coalescing parallel I/O"),
        ("I/O request merging readahead",        "prefetch sequential"),
        ("data placement locality optimization", "cache-oblivious I/O"),
    ],
    "small_write": [
        ("small write buffering optimization",   "write aggregation parallel I/O"),
        ("write coalescing delayed write HPC",   "asynchronous write optimization"),
        ("write-back cache I/O optimization",    "write combining parallel"),
        ("write ordering optimization",          "journaling write HPC storage"),
        ("N-to-1 write optimization",            "write barrier optimization"),
    ],
    "rand": [
        ("random I/O access pattern optimization", "data layout optimization HPC"),
        ("random access prefetching storage",      "out-of-core random access"),
        ("storage layout striping optimization",   "random I/O sequential"),
        ("index-based access sorted layout",       "spatial locality data reorder"),
        ("data reorganization optimization HPC",   "Z-order curve data layout"),
    ],
    "seq": [
        ("sequential I/O fragmentation optimization", "contiguous I/O performance"),
        ("sequential prefetch I/O",                   "streaming read optimization"),
        ("file fragmentation repair",                 "sequential throughput HPC"),
        ("disk layout sequential",                    "data contiguity optimization"),
        ("defragmentation I/O performance",           "stripe alignment HPC"),
    ],
    "metadata": [
        ("metadata operation scalability parallel filesystem", "directory overhead HPC"),
        ("POSIX metadata bottleneck inode",              "namespace scalability"),
        ("metadata server optimization",                 "open close overhead reduction"),
        ("lazy metadata caching optimization",           "MDT optimization Lustre"),
        ("metadata aggregation batching",                "stat operation reduction"),
    ],
    "read_time": [
        ("parallel I/O read throughput optimization",  "read bandwidth HPC"),
        ("collective read optimization",               "read scalability parallel"),
        ("data staging read pipeline",                 "prefetch overlap"),
        ("storage read latency reduction",             "I/O read hiding"),
        ("read performance tuning HPC",                "read-ahead buffer tuning"),
    ],
    "write_time": [
        ("parallel I/O write throughput optimization", "checkpoint I/O performance"),
        ("asynchronous checkpoint write",              "write buffering parallel I/O"),
        ("write overlap compute I/O",                  "non-blocking write HPC"),
        ("incremental checkpoint optimization",        "write scalability parallel"),
        ("write barrier pipeline HPC",                 "online checkpoint compression"),
    ],
    "imbalance": [
        ("I/O load imbalance HPC optimization",        "uneven I/O workload distribution"),
        ("I/O load balancing parallel",                "process I/O skew reduction"),
        ("collective I/O load balance",                "work stealing I/O"),
        ("I/O redistribution optimization",            "task scheduling I/O"),
        ("dynamic load balancing I/O",                 "I/O contention reduction"),
    ],
    "bw": [
        ("I/O bandwidth utilization optimization",     "storage throughput HPC"),
        ("I/O bandwidth bottleneck",                   "network bandwidth storage"),
        ("bandwidth saturation HPC parallel",          "I/O bandwidth balance"),
        ("memory bandwidth I/O bottleneck",            "memory-I/O co-optimization"),
        ("effective bandwidth utilization",            "bandwidth steering I/O"),
    ],
    "intensity": [
        ("I/O intensity compute overlap",              "asynchronous I/O pipeline"),
        ("I/O bound application optimization",         "I/O hiding technique"),
        ("non-blocking I/O overlap compute",           "prefetch I/O pipeline"),
        ("I/O compute ratio optimization",             "data access pattern optimization"),
        ("I/O interleaving optimization",              "overlap communication computation"),
    ],
    "fetch": [
        ("data prefetching deep learning I/O",         "training data pipeline optimization"),
        ("data loader bottleneck GPU",                 "async data loading optimization"),
        ("prefetch buffer pipeline GPU training",      "storage data pipeline ML"),
        ("data augmentation I/O optimization",         "GPU I/O pipeline throughput"),
        ("data ingestion optimization HPC ML",         "storage-side preprocessing"),
    ],
    "checkpoint": [
        ("checkpoint I/O optimization HPC",            "fault tolerance checkpoint"),
        ("incremental checkpoint optimization",        "checkpoint compression"),
        ("asynchronous checkpoint restart",            "multi-level checkpoint SCR"),
        ("FTI checkpoint parallel",                    "checkpoint scalability"),
        ("online checkpoint write optimization",       "restart optimization HPC"),
    ],
    "epoch": [
        ("epoch straggler optimization distributed",   "load imbalance deep learning"),
        ("distributed training I/O straggler",         "batch processing imbalance"),
        ("data pipeline straggler mitigation",         "I/O straggler deep learning"),
        ("elastic training optimization",              "fault tolerant training I/O"),
        ("synchronous SGD straggler",                  "all-reduce optimization HPC"),
    ],
    "fs_bw": [
        ("parallel filesystem bandwidth utilization",  "storage system throughput HPC"),
        ("OST bandwidth balance Lustre",                "filesystem contention optimization"),
        ("storage system utilization study HPC",        "parallel file system workload characterization"),
        ("I/O bandwidth saturation shared filesystem",  "filesystem client tuning HPC"),
        ("production filesystem bandwidth analysis",    "storage utilization deep learning HPC"),
    ],
    "comm": [
        ("MPI collective communication optimization",  "collective algorithm selection HPC"),
        ("MPI point-to-point latency optimization",     "network communication overlap HPC"),
        ("all-reduce communication optimization",       "gradient communication overlap distributed training"),
        ("communication computation overlap MPI",       "non-blocking collective optimization"),
        ("interconnect topology-aware communication",   "collective communication tuning HPC"),
    ],
    "mem_bw": [
        ("memory bandwidth optimization HPC",          "STREAM benchmark memory-bound kernel"),
        ("NUMA memory locality optimization",           "cache-aware data layout HPC"),
        ("memory access pattern optimization",          "cache blocking tiling optimization"),
        ("memory-bound kernel optimization",            "data locality optimization HPC"),
        ("bandwidth-limited loop kernel optimization",  "memory hierarchy optimization HPC"),
    ],
    "compute": [
        ("roofline compute optimization HPC",          "arithmetic intensity optimization"),
        ("compute kernel vectorization optimization",   "SIMD optimization HPC"),
        ("GPU utilization optimization deep learning",  "kernel occupancy optimization"),
        ("compute bound optimization parallel",         "instruction-level parallelism optimization"),
        ("floating point performance optimization HPC", "compute throughput tuning"),
    ],
}

#: Canonical optimization-loop order: I/O first, then communication, then
#: memory, then compute.  Bottlenecks and proposals are always addressed in
#: this order regardless of raw severity score, so I/O issues (the cheapest
#: and highest-leverage fixes in most HPC/DL workloads) are never starved by
#: a lower tier of the stack.
_CATEGORY_ORDER: List[str] = ["io", "communication", "memory", "compute"]

#: Metric-name fragment -> optimization category. Checked in fragment order;
#: first match wins. Anything unmatched defaults to "io" since the current
#: DFDiagnoser presets are still primarily I/O-metric based.
_METRIC_CATEGORY: Dict[str, str] = {
    "comm":            "communication",
    "mpi_wait":        "communication",
    "collective":      "communication",
    "sync_time":       "communication",
    "allreduce":       "communication",
    "mem_bw":          "memory",
    "memory":          "memory",
    "cache_miss":      "memory",
    "numa":            "memory",
    "compute":         "compute",
    "cpu_bound":       "compute",
    "gpu_util":        "compute",
    "flops":           "compute",
    # Everything else (small_io, read_time, write_time, metadata, fetch_pressure,
    # epoch_straggler, fs_bw, rand, seq, imbalance, bw, intensity, checkpoint, ...)
    # is an I/O-tier metric.
}

#: DL-specific dimensions that must always be evaluated in every optimization
#: iteration for deep-learning workloads, independent of raw severity ranking:
#:   1. application dataloader / epoch-time performance
#:   2. filesystem bandwidth / utilization for the storage the run is on
_DL_ALWAYS_ON_METRICS: Dict[str, List[str]] = {
    "dataloader_epoch": ["fetch_pressure", "epoch_straggler"],
    "fs_bandwidth":     ["fs_bw", "bw", "imbalance"],
}


def _metric_category(metric: str) -> str:
    """Classify *metric* into one of ``_CATEGORY_ORDER`` (default: ``"io"``)."""
    m = (metric or "").lower()
    for fragment, category in _METRIC_CATEGORY.items():
        if fragment in m:
            return category
    return "io"


def _category_sort_key(bottleneck: Dict[str, Any]) -> tuple:
    """Sort key enforcing I/O -> communication -> memory -> compute ordering,
    with severity as the tiebreaker within a category."""
    _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    cat = _metric_category(bottleneck.get("metric", ""))
    cat_rank = _CATEGORY_ORDER.index(cat) if cat in _CATEGORY_ORDER else len(_CATEGORY_ORDER)
    sev_rank = _SEV.get(bottleneck.get("severity", "trivial"), 0)
    return (cat_rank, -sev_rank)

# Broadest final-fallback queries used when all metric-specific attempts exhaust
_GENERAL_FALLBACK_QUERIES = [
    "I/O performance optimization parallel filesystem HPC",
    "storage optimization high performance computing",
]


def _fetch_arxiv_papers(query: str, n: int = 5) -> List[Dict[str, Any]]:
    """Fetch up to *n* arXiv papers matching *query*.  Returns an empty list on failure."""
    import urllib.parse
    import xml.etree.ElementTree as _ET

    _ARXIV = "https://export.arxiv.org/api/query"
    _NS    = {"atom": "http://www.w3.org/2005/Atom"}

    params = {
        "search_query": f"all:{query}",
        "max_results":  n,
        "sortBy":       "relevance",
        "sortOrder":    "descending",
    }
    url = f"{_ARXIV}?{urllib.parse.urlencode(params)}"
    r = _run(["curl", "-s", "--max-time", "30", url], timeout=45)
    if not r.get("success") or not r.get("stdout"):
        return []
    try:
        root = _ET.fromstring(r["stdout"])
        papers = []
        for entry in root.findall("atom:entry", _NS):
            def _t(tag):
                el = entry.find(tag, _NS)
                return el.text.strip() if el is not None and el.text else ""
            arxiv_id = _t("atom:id").split("/abs/")[-1]
            authors  = [
                a.find("atom:name", _NS).text.strip()
                for a in entry.findall("atom:author", _NS)
                if a.find("atom:name", _NS) is not None
            ]
            papers.append({
                "title":     _t("atom:title").replace("\n", " "),
                "authors":   authors[:4],
                "published": _t("atom:published")[:10],
                "abstract":  _t("atom:summary").replace("\n", " ")[:500],
                "url":       f"https://arxiv.org/abs/{arxiv_id}",
            })
        return papers
    except Exception:
        return []


# ── Built-in citable references (always available) ───────────────────────
_BUILTIN_REFS: Dict[str, Dict[str, Any]] = {
    "wisio": {
        "authors": ["Yildirim, I.", "Devarajan, H.", "Kougkas, A.", "Sun, X.-H.", "Mohror, K."],
        "title": "WisIO: Automated I/O Bottleneck Detection with Multi-Perspective Views for HPC Workflows",
        "venue": "Proc. 39th ACM ICS 2025, pp. 749–763",
        "year": "2025",
        "url": "https://dl.acm.org/doi/10.1145/3721145.3730395",
    },
    "drishti": {
        "authors": ["Bez, J.L.", "Ather, H.", "Byna, S."],
        "title": "Drishti: Guiding end-users in the I/O optimization journey",
        "venue": "2022 IEEE/ACM PDSW, pp. 1–6",
        "year": "2022",
        "url": "https://ieeexplore.ieee.org/document/10027503",
    },
    "mpi_collective": {
        "authors": ["Thakur, R.", "Rabenseifner, R.", "Gropp, W."],
        "title": "Optimization of Collective Communication Operations in MPICH",
        "venue": "Intl. Journal of High Performance Computing Applications, 19(1), pp. 49–66",
        "year": "2005",
        "url": "https://doi.org/10.1177/1094342005051521",
    },
    "stream": {
        "authors": ["McCalpin, J.D."],
        "title": "Memory Bandwidth and Machine Balance in Current High Performance Computers",
        "venue": "IEEE Computer Society Technical Committee on Computer Architecture Newsletter",
        "year": "1995",
        "url": "https://www.cs.virginia.edu/stream/ref.html",
    },
    "roofline": {
        "authors": ["Williams, S.", "Waterman, A.", "Patterson, D."],
        "title": "Roofline: An Insightful Visual Performance Model for Multicore Architectures",
        "venue": "Communications of the ACM, 52(4), pp. 65–76",
        "year": "2009",
        "url": "https://doi.org/10.1145/1498765.1498785",
    },
    "data_stalls": {
        "authors": ["Mohan, J.", "Phanishayee, A.", "Raniwala, A.", "Chidambaram, V."],
        "title": "Analyzing and Mitigating Data Stalls in DNN Training",
        "venue": "Proc. VLDB Endowment, 14(5), pp. 771–784",
        "year": "2021",
        "url": "https://doi.org/10.14778/3446095.3446100",
    },
    "fs_bandwidth": {
        "authors": ["Lockwood, G.K.", "Snyder, S.", "Wang, T.", "Byna, S.", "Carns, P.", "Wright, N.J."],
        "title": "A Year in the Life of a Parallel File System",
        "venue": "Proc. SC18: Intl. Conf. for High Performance Computing, Networking, Storage and Analysis",
        "year": "2018",
        "url": "https://doi.org/10.1109/SC.2018.00077",
    },
}

#: Default built-in citation per optimization category, used when a strategy
#: does not already specify a more specific ``builtin_cite`` key.
_CATEGORY_DEFAULT_CITE: Dict[str, str] = {
    "io":            "wisio",
    "communication": "mpi_collective",
    "memory":        "stream",
    "compute":       "roofline",
}

#: Built-in citation overrides for the two always-on DL dimensions.
_DL_DIMENSION_CITE: Dict[str, str] = {
    "dataloader_epoch": "data_stalls",
    "fs_bandwidth":     "fs_bandwidth",
}

# ── Per-level strategy tables (from optimize-l1/l2/l3-*.yaml) ───────────
# Each entry: metric_pattern → list of strategy dicts
# strategy dict keys: title, change, expected_delta, risk, builtin_cite, finding
_L1_STRATEGIES: Dict[str, List[Dict[str, Any]]] = {
    "small_io": [
        {"title": "Coalesce small reads into 4 MB staging buffer",
         "change": "Replace per-call read() with staging buffer; flush at buffer-full or loop-end",
         "expected_delta": "+20–40% bandwidth",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO Table 2: small_io primary L1 fix — increase application-level buffer size"},
    ],
    "small_write": [
        {"title": "Batch small writes into pre-allocated buffer",
         "change": "Accumulate writes in a pre-allocated 4 MB buffer; flush once per segment",
         "expected_delta": "+15–35% bandwidth",
         "risk": "LOW",
         "builtin_cite": "drishti",
         "finding": "Drishti category small-io, L1 suggestion: batch writes to reduce syscall frequency"},
    ],
    "rand_pct": [
        {"title": "Sort access indices before I/O to improve sequentiality",
         "change": "Sort file offsets / dataset sample indices before issuing reads",
         "expected_delta": "+10–30% bandwidth",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO: sequentiality bottleneck L1 fix — reorder access to improve seq_pct"},
        {"title": "Add posix_fadvise(POSIX_FADV_SEQUENTIAL) before sequential reads",
         "change": "Insert posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL) before read loop",
         "expected_delta": "+5–15% read bandwidth",
         "risk": "LOW",
         "builtin_cite": "drishti",
         "finding": "Drishti L1 sequentiality: posix_fadvise hint allows kernel readahead"},
    ],
    "read_time": [
        {"title": "Pre-open file descriptors across iterations",
         "change": "Move open()/close() outside hot loop; keep FDs alive across repetitions",
         "expected_delta": "–10–25% read latency",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO read-time: open overhead is a significant fraction of per-call latency"},
        {"title": "Overlap I/O with compute using async I/O (io_uring / aiofiles)",
         "change": "Replace blocking read() with io_uring submission (C) or asyncio/aiofiles (Python)",
         "expected_delta": "–20–40% wall-clock I/O time",
         "risk": "MEDIUM",
         "builtin_cite": "wisio",
         "finding": "WisIO read-time L1 fix: async I/O reduces idle CPU waiting for I/O"},
    ],
    "write_time": [
        {"title": "Pre-allocate file size with fallocate before writing",
         "change": "Call fallocate(fd, 0, 0, total_size) before the write loop",
         "expected_delta": "–5–20% write time",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO write-time: fragmentation from incremental allocation adds latency"},
        {"title": "Write checkpoints asynchronously in a background thread",
         "change": "Move checkpoint save to a daemon thread; signal main loop when done",
         "expected_delta": "–30–60% checkpoint stall time",
         "risk": "MEDIUM",
         "builtin_cite": "drishti",
         "finding": "Drishti L1 checkpoint: async write removes checkpoint from critical path"},
    ],
    "metadata_time": [
        {"title": "Cache stat() results; avoid repeated lstat on same paths",
         "change": "Replace per-sample os.path.exists()/stat() with a pre-built dict cache",
         "expected_delta": "–20–50% metadata overhead",
         "risk": "LOW",
         "builtin_cite": "drishti",
         "finding": "Drishti metadata L1: caching stat results eliminates redundant syscalls"},
        {"title": "Open files once per epoch, not once per sample",
         "change": "Hoist open()/close() out of the per-sample loop; reuse FD per epoch",
         "expected_delta": "–15–40% metadata time",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO metadata-time: repeated open/close contributes to metadata_time_pct"},
    ],
    "fetch_pressure": [
        {"title": "Increase DataLoader num_workers to cpu_count//2",
         "change": "Set DataLoader(num_workers=os.cpu_count()//2, prefetch_factor=4, persistent_workers=True)",
         "expected_delta": "–20–50% fetch latency",
         "risk": "LOW",
         "builtin_cite": "data_stalls",
         "finding": "Mohan et al. (VLDB'21): increasing DataLoader workers/prefetch factor mitigates data stalls"},
    ],
    "epoch_straggler": [
        {"title": "Sort dataset by sample size before training",
         "change": "Pre-sort dataset indices by file size; shuffle only indices within sorted buckets",
         "expected_delta": "–10–30% tail batch latency",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO stragglers L1 fix: homogeneous batch sizes reduce rank imbalance"},
    ],
    "comm_wait": [
        {"title": "Overlap gradient all-reduce with backward pass compute",
         "change": "Register gradient-ready hooks to launch all-reduce as soon as each layer's gradient is computed, instead of waiting for the full backward pass",
         "expected_delta": "–15–35% communication-bound step time",
         "risk": "MEDIUM",
         "builtin_cite": "mpi_collective",
         "finding": "Thakur et al.: overlapping collective communication with computation hides communication latency"},
    ],
    "mem_bw": [
        {"title": "Increase batch/tile size to raise arithmetic intensity",
         "change": "Restructure the hot loop to reuse loaded data across more operations before eviction (cache blocking)",
         "expected_delta": "–10–25% memory-bound stall time",
         "risk": "LOW",
         "builtin_cite": "stream",
         "finding": "McCalpin STREAM: memory-bound kernels benefit from maximizing data reuse per byte fetched"},
    ],
    "compute_time": [
        {"title": "Vectorize / batch the hot compute loop",
         "change": "Replace per-element Python/scalar loop with a vectorized (NumPy/SIMD) batched operation",
         "expected_delta": "–20–50% compute time",
         "risk": "LOW",
         "builtin_cite": "roofline",
         "finding": "Williams et al. Roofline: compute-bound kernels below peak FLOP/s benefit from vectorization"},
    ],
}

_L2_STRATEGIES: Dict[str, List[Dict[str, Any]]] = {
    "small_io": [
        {"title": "Enable ROMIO collective buffering (cb_buffer_size=64MB)",
         "change": "Set ROMIO_HINTS env var: cb_buffer_size=67108864;romio_cb_read=enable;romio_cb_write=enable",
         "expected_delta": "+30–60% bandwidth for shared-file MPI-IO",
         "risk": "LOW",
         "builtin_cite": "drishti",
         "finding": "Drishti L2 small-io: collective I/O aggregates small requests into large transfers",
         "delivery": "env_var",
         "env_key": "ROMIO_HINTS"},
        {"title": "Enable ROMIO data sieving for non-contiguous access",
         "change": "Set ROMIO_HINTS: romio_ds_read=enable;romio_ds_write=enable",
         "expected_delta": "+10–25% for non-contiguous patterns",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO small-io L2: data sieving bridges gaps in non-contiguous access patterns",
         "delivery": "env_var",
         "env_key": "ROMIO_HINTS"},
    ],
    "rand_pct": [
        {"title": "Add POSIX_FADV_SEQUENTIAL via LD_PRELOAD wrapper",
         "change": "Write a small LD_PRELOAD library that calls posix_fadvise on every open(); inject via env",
         "expected_delta": "+5–15% sequential read bandwidth",
         "risk": "LOW",
         "builtin_cite": "drishti",
         "finding": "Drishti L2 sequentiality: POSIX readahead hint at library level without source change",
         "delivery": "env_var",
         "env_key": "LD_PRELOAD"},
    ],
    "read_time": [
        {"title": "Tune ROMIO collective buffer for read aggregation",
         "change": "Set ROMIO_HINTS: cb_buffer_size=67108864;romio_cb_read=enable",
         "expected_delta": "–15–30% MPI-IO read time",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO read-time L2: collective read buffer amortizes per-rank I/O overhead",
         "delivery": "env_var",
         "env_key": "ROMIO_HINTS"},
    ],
    "metadata_time": [
        {"title": "Disable HDF5 metadata cache evictions during write phases",
         "change": "Set HDF5_METADATA_CACHE_EVICT_ON_CLOSE=0 env var before run",
         "expected_delta": "–10–25% HDF5 metadata overhead",
         "risk": "LOW",
         "builtin_cite": "drishti",
         "finding": "Drishti L2 metadata: suppressing cache evictions keeps metadata hot in memory",
         "delivery": "env_var",
         "env_key": "HDF5_METADATA_CACHE_EVICT_ON_CLOSE"},
    ],
    "fetch_pressure": [
        {"title": "Tune PyTorch DataLoader env vars for I/O throughput",
         "change": "Set OMP_NUM_THREADS=<cores>, MALLOC_ARENA_MAX=2 to reduce threading/memory overhead",
         "expected_delta": "–5–15% DataLoader overhead",
         "risk": "LOW",
         "builtin_cite": "data_stalls",
         "finding": "Mohan et al. (VLDB'21): DNN data stalls are reduced by tuning worker threading/memory overhead",
         "delivery": "env_var",
         "env_key": "OMP_NUM_THREADS"},
    ],
    "comm_wait": [
        {"title": "Select topology-aware collective algorithm",
         "change": "Set OMPI_MCA_coll_hcoll_enable=1 (or MPICH equivalent) to select a topology-aware all-reduce/all-gather algorithm",
         "expected_delta": "–10–25% collective communication time",
         "risk": "LOW",
         "builtin_cite": "mpi_collective",
         "finding": "Thakur et al.: algorithm selection based on message size and topology reduces collective latency",
         "delivery": "env_var",
         "env_key": "OMPI_MCA_coll_hcoll_enable"},
    ],
    "write_time": [
        {"title": "Tune Linux dirty page flush timing",
         "change": "Set MALLOC_ARENA_MAX=2 and pre-configure vm.dirty_writeback_centisecs via sysctl before run (L2-safe read of current value)",
         "expected_delta": "–5–15% write stall time",
         "risk": "LOW",
         "builtin_cite": "wisio",
         "finding": "WisIO write-time L2: dirty page writeback timing affects write throughput",
         "delivery": "env_var",
         "env_key": "MALLOC_ARENA_MAX"},
    ],
    "mem_bw": [
        {"title": "Enable transparent huge pages / madvise for large allocations",
         "change": "Set MALLOC_MMAP_THRESHOLD_ and MALLOC_TRIM_THRESHOLD_ env vars so large buffers are backed by huge pages, cutting TLB miss rate",
         "expected_delta": "–5–15% memory-bound stall time",
         "risk": "LOW",
         "builtin_cite": "stream",
         "finding": "McCalpin STREAM: TLB pressure from 4KB pages measurably reduces effective memory bandwidth on large working sets",
         "delivery": "env_var",
         "env_key": "MALLOC_MMAP_THRESHOLD_"},
        {"title": "Select a NUMA-aware allocator (jemalloc/tcmalloc) via LD_PRELOAD",
         "change": "LD_PRELOAD=libjemalloc.so with MALLOC_CONF=narenas:<numa_nodes> to keep per-thread arenas NUMA-local",
         "expected_delta": "+10–20% allocation-heavy workload throughput",
         "risk": "LOW",
         "builtin_cite": "stream",
         "finding": "Allocator arena locality reduces cross-NUMA memory traffic for allocation-heavy codes",
         "delivery": "env_var",
         "env_key": "LD_PRELOAD"},
    ],
    "compute_time": [
        {"title": "Enable vendor math-library auto-tuning via env var",
         "change": "Set OMP_NUM_THREADS/MKL_NUM_THREADS (or ROCBLAS_LAYER for ROCm) to match the node's physical core/CU count, avoiding oversubscription",
         "expected_delta": "–10–30% compute time from reduced thread contention",
         "risk": "LOW",
         "builtin_cite": "roofline",
         "finding": "Williams et al. Roofline: compute throughput below peak is often a thread-oversubscription artifact, not an algorithmic limit",
         "delivery": "env_var",
         "env_key": "OMP_NUM_THREADS"},
    ],
}

_L3_STRATEGIES: Dict[str, List[Dict[str, Any]]] = {
    "small_io": [
        {"title": "Increase Lustre stripe size to 4 MB to align with buffer sizes",
         "change": "lfs setstripe -S 4m <data_dir>",
         "expected_delta": "+10–30% bandwidth for large-file sequential I/O",
         "risk": "LOW",
         "privilege": "no-sudo",
         "rollback": "lfs setstripe -S 1m <data_dir>",
         "side_effect": "Affects new files created in that directory only",
         "builtin_cite": "drishti",
         "finding": "Drishti L3 small-io: stripe size aligned to collective buffer maximizes OST utilization"},
    ],
    "rand_pct": [
        {"title": "Increase kernel readahead on data device",
         "change": "sudo blockdev --setra 4096 /dev/<data_device>  (sets 2 MB readahead)",
         "expected_delta": "+10–25% sequential read bandwidth",
         "risk": "MEDIUM",
         "privilege": "sudo",
         "rollback": "sudo blockdev --setra 128 /dev/<data_device>",
         "side_effect": "Affects all processes reading from this device",
         "builtin_cite": "drishti",
         "finding": "Drishti L3 sequentiality: blockdev readahead amplifies OS prefetch for sequential reads"},
    ],
    "read_time": [
        {"title": "Reduce vfs_cache_pressure to retain page cache longer",
         "change": "sudo sysctl -w vm.vfs_cache_pressure=50",
         "expected_delta": "–10–20% re-read latency (data fits in RAM)",
         "risk": "MEDIUM",
         "privilege": "sudo",
         "rollback": "sudo sysctl -w vm.vfs_cache_pressure=100",
         "side_effect": "Reduces kernel tendency to reclaim page cache system-wide",
         "builtin_cite": "wisio",
         "finding": "WisIO read-time: lower vfs_cache_pressure keeps hot data in page cache"},
    ],
    "write_time": [
        {"title": "Tune vm.dirty_ratio and vm.dirty_background_ratio",
         "change": "sudo sysctl -w vm.dirty_ratio=20 vm.dirty_background_ratio=5",
         "expected_delta": "–5–15% write stall time",
         "risk": "MEDIUM",
         "privilege": "sudo",
         "rollback": "sudo sysctl -w vm.dirty_ratio=20 vm.dirty_background_ratio=10",
         "side_effect": "Affects all dirty-page behavior on the node",
         "builtin_cite": "wisio",
         "finding": "WisIO write-time L3: dirty-page ratios control when background flush begins"},
    ],
    "metadata_time": [
        {"title": "Set Lustre stripe count=1 for small metadata-heavy directories",
         "change": "lfs setstripe -c 1 <metadata_dir>  (reduces cross-OST lock contention)",
         "expected_delta": "–10–20% metadata latency for small files",
         "risk": "LOW",
         "privilege": "no-sudo",
         "rollback": "lfs setstripe -c -1 <metadata_dir>",
         "side_effect": "Affects new files in that directory only",
         "builtin_cite": "drishti",
         "finding": "Drishti L3 metadata: single-OST placement reduces MDS lock traffic for small files"},
    ],
    "fetch_pressure": [
        {"title": "Pin process to NUMA node with local memory",
         "change": "Prefix run command with: numactl --cpunodebind=0 --membind=0",
         "expected_delta": "–5–15% memory access latency",
         "risk": "LOW",
         "privilege": "no-sudo",
         "rollback": "Remove numactl prefix from run command",
         "side_effect": "Restricts process to one NUMA node; may underutilize cores on other nodes",
         "builtin_cite": "stream",
         "finding": "McCalpin STREAM: NUMA-local memory placement reduces cross-node bandwidth pressure"},
    ],
    "fs_bw": [
        {"title": "Increase Lustre stripe count across more OSTs to raise aggregate bandwidth",
         "change": "lfs setstripe -c 8 -S 4m <data_dir>  (spread I/O across more OSTs)",
         "expected_delta": "+20–50% aggregate filesystem bandwidth for large shared datasets",
         "risk": "LOW",
         "privilege": "no-sudo",
         "rollback": "lfs setstripe -c 1 -S 1m <data_dir>",
         "side_effect": "Affects new files created in that directory only; higher OST contention if many jobs share the filesystem",
         "builtin_cite": "fs_bandwidth",
         "finding": "Lockwood et al. (SC'18): production parallel filesystem bandwidth is underutilized without wide OST striping"},
    ],
    "comm_wait": [
        {"title": "Bind ranks topology-aware to minimize inter-node hop count",
         "change": "flux run/mpirun rank-to-node mapping so ranks that communicate most are co-located (same node/switch) — e.g. `--map-by numa` or a custom rankfile",
         "expected_delta": "–10–25% collective/point-to-point latency",
         "risk": "LOW",
         "privilege": "no-sudo",
         "rollback": "Revert to default round-robin rank placement",
         "side_effect": "May reduce per-node memory-bandwidth parallelism if communicating ranks share a NUMA domain",
         "builtin_cite": "mpi_collective",
         "finding": "Thakur et al.: network topology-aware placement reduces the hop count collective algorithms pay per message"},
    ],
    "mem_bw": [
        {"title": "Pin process/thread to the NUMA node holding its working set",
         "change": "Prefix run command with: numactl --cpunodebind=<n> --membind=<n> (or hwloc-bind equivalent)",
         "expected_delta": "–10–20% memory access latency, +bandwidth headroom",
         "risk": "LOW",
         "privilege": "no-sudo",
         "rollback": "Remove numactl/hwloc-bind prefix",
         "side_effect": "Restricts the process to one NUMA node's memory bandwidth",
         "builtin_cite": "stream",
         "finding": "McCalpin STREAM: cross-NUMA memory access can cost 2x+ local bandwidth; binding avoids remote-node traffic"},
    ],
    "compute_time": [
        {"title": "Set CPU/GPU frequency governor to performance mode",
         "change": "sudo cpupower frequency-set -g performance (CPU) or set the GPU clock profile to max via vendor tool (rocm-smi --setperflevel high)",
         "expected_delta": "+5–15% sustained compute throughput",
         "risk": "MEDIUM",
         "privilege": "sudo",
         "rollback": "sudo cpupower frequency-set -g powersave (or vendor default)",
         "side_effect": "Increases power draw and thermal load node-wide",
         "builtin_cite": "roofline",
         "finding": "Williams et al. Roofline: compute-bound kernels are directly limited by sustained clock frequency, not just algorithmic FLOP count"},
    ],
}


def _gen_level_proposals(
    bottlenecks: List[Dict[str, Any]],
    strat_table: Dict[str, List[Dict[str, Any]]],
    level_tag: str,
    searched_papers: Dict[str, List[Dict[str, Any]]],
    max_per_level: int = 3,
    extra_fields: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Map *bottlenecks* to *strat_table* strategies, attach citations.

    Bottlenecks are processed in the canonical optimization order
    I/O -> communication -> memory -> compute (``_category_sort_key``), with
    severity as the tiebreaker within a category, so lower layers of the
    stack are never optimized ahead of I/O.

    Returns (proposals, cited_searched, cited_builtin).
    Proposals without a URL-backed citation are silently dropped.
    """
    _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    proposals: List[Dict[str, Any]] = []
    cited_searched = 0
    cited_builtin  = 0
    prop_counter   = [0]

    for bn in sorted(bottlenecks, key=_category_sort_key):
        met = bn.get("metric", "")
        sev = bn.get("severity", "trivial")
        if _SEV.get(sev, 0) < 2:
            continue  # skip trivial / low

        strats = strat_table.get(met, [])
        if not strats:
            for key in strat_table:
                if met.startswith(key) or key.startswith(met.split("_")[0]):
                    strats = strat_table[key]
                    break

        added = 0
        for strat in strats:
            if added >= max_per_level:
                break
            # Citation selection: searched papers first, built-in fallback
            cite_src = None
            finding  = ""
            searched = searched_papers.get(met, [])
            if searched:
                p = searched[0]
                yr = (p.get("published") or "")[:4]
                cite_src = {
                    "authors": p.get("authors", []),
                    "title":   p.get("title", ""),
                    "venue":   f"arXiv {yr}",
                    "year":    yr,
                    "url":     p.get("url", ""),
                }
                finding = (
                    f"arXiv search for '{met}': "
                    + p.get("title", "")[:80] + "…"
                )
                cited_searched += 1
            else:
                if met in _DL_ALWAYS_ON_METRICS["dataloader_epoch"]:
                    default_key = _DL_DIMENSION_CITE["dataloader_epoch"]
                elif met in _DL_ALWAYS_ON_METRICS["fs_bandwidth"]:
                    default_key = _DL_DIMENSION_CITE["fs_bandwidth"]
                else:
                    default_key = _CATEGORY_DEFAULT_CITE.get(_metric_category(met), "wisio")
                key = strat.get("builtin_cite", default_key)
                cite_src = dict(_BUILTIN_REFS.get(key, _BUILTIN_REFS[default_key]))
                finding  = strat.get("finding", "")
                cited_builtin += 1

            if not cite_src or not cite_src.get("url"):
                continue  # citation rule: no URL → drop

            prop_counter[0] += 1
            p_dict: Dict[str, Any] = {
                "id":             f"{level_tag.upper()}-{prop_counter[0]}",
                "level":          level_tag,
                "title":          strat["title"],
                "bottleneck":     met,
                "severity":       sev,
                "change":         strat["change"],
                "expected_delta": strat["expected_delta"],
                "risk":           strat["risk"],
                "citation": {
                    "authors": cite_src.get("authors", []),
                    "title":   cite_src.get("title", ""),
                    "venue":   cite_src.get("venue", ""),
                    "year":    cite_src.get("year", ""),
                    "url":     cite_src.get("url", ""),
                    "finding": finding,
                },
            }
            for fld in (extra_fields or []):
                if fld in strat:
                    p_dict[fld] = strat[fld]
            proposals.append(p_dict)
            added += 1

    return proposals, cited_searched, cited_builtin


def _build_sys_context(sys_info: dict) -> str:
    """Build a short hardware context string from a system_config dict.

    Used to refine academic search queries with hardware-specific terms so
    results are relevant to the actual deployment environment.

    Examples: ``"Lustre ARM64 InfiniBand"`` or ``"NFS x86_64 Ethernet 10G"``.
    """
    parts: List[str] = []

    # CPU architecture
    cpu = sys_info.get("cpu", {})
    arch = cpu.get("architecture", "") or cpu.get("arch", "")
    if arch:
        parts.append(arch)

    # All mounted filesystem types, sorted: parallel/network FSes first, local last.
    _PSEUDO_FS = {"proc", "sysfs", "devtmpfs", "cgroup", "cgroup2", "devpts",
                  "mqueue", "hugetlbfs", "pstore", "securityfs", "overlay",
                  "nsfs", "bpf", "tracefs", "debugfs", "efivarfs",
                  "squashfs", "iso9660", "autofs"}
    _FS_LABEL = {
        "lustre": ("Lustre", 10), "gpfs": ("GPFS", 10), "beegfs": ("BeeGFS", 10),
        "daos":   ("DAOS",   10), "pvfs2": ("OrangeFS", 10),
        "nfs":    ("NFS",     8), "nfs4": ("NFS", 8), "glusterfs": ("GlusterFS", 8),
        "ceph":   ("Ceph",    8), "xfs":  ("XFS", 5), "zfs": ("ZFS", 5),
        "ext4":   ("ext4",    2), "btrfs": ("btrfs", 2), "vfat": ("vfat", 1),
    }
    seen_fs: Dict[str, int] = {}  # label → max priority
    has_shm = False
    has_tmp = False
    for fs in sys_info.get("filesystems", []):
        ftype = (fs.get("type") or "").lower()
        mount = (fs.get("mount") or fs.get("mountpoint") or "").rstrip("/")
        if ftype == "tmpfs":
            if mount in ("/dev/shm", "/run/shm"):
                has_shm = True
            elif mount in ("/tmp", "/var/tmp"):
                has_tmp = True
            continue
        if ftype in _PSEUDO_FS:
            continue
        if ftype:
            label, prio = _FS_LABEL.get(ftype, (ftype.upper()[:8], 1))
            if label not in seen_fs or prio > seen_fs[label]:
                seen_fs[label] = prio
    fstypes = [lbl for lbl, _ in sorted(seen_fs.items(), key=lambda x: -x[1])]
    if fstypes:
        parts.extend(fstypes)
    if has_shm:
        parts.append("SHM")
    if has_tmp:
        parts.append("tmpfs-staging")

    ib = any(
        iface.get("name", "").startswith(("ib", "mlx", "hfi"))
        for iface in sys_info.get("network", {}).get("interfaces", [])
        if isinstance(iface, dict)
    )
    if ib:
        parts.append("InfiniBand")

    parts.append("HPC")
    return " ".join(parts) if parts else "HPC parallel filesystem"


def _bottleneck_search_queries(
    metric: str,
    description: str,
    sys_context: str,
    max_queries: int = 10,
) -> List[str]:
    """Return up to *max_queries* progressively fuzzier search queries for *metric*.

    Strategy:
    - Queries 1-2: most specific (metric primary phrase + system context)
    - Queries 3-8: synonym pairs with and without system context
    - Queries 9-10: broadest fallbacks (domain + context, then generic)
    """
    syn_pairs: List[tuple] = []
    for fragment, pairs in _METRIC_SYNONYM_PAIRS.items():
        if fragment in metric:
            syn_pairs = pairs
            break

    if not syn_pairs:
        desc_lower = description.lower()
        for fragment, pairs in _METRIC_SYNONYM_PAIRS.items():
            if fragment in desc_lower:
                syn_pairs = pairs
                break

    if not syn_pairs:
        syn_pairs = _METRIC_SYNONYM_PAIRS.get("bw", [])

    queries: List[str] = []

    primary = syn_pairs[0][0] if syn_pairs else f"I/O optimization {metric}"
    queries.append(f"{primary} {sys_context}")
    queries.append(f"{primary} parallel filesystem optimization")

    for phrase_a, phrase_b in syn_pairs[1:]:
        queries.append(f"{phrase_a} {sys_context}")
        queries.append(f"{phrase_b} optimization")
        if len(queries) >= max_queries - 2:
            break

    if len(queries) < max_queries - 2 and syn_pairs:
        queries.append(f"{syn_pairs[0][1]} {sys_context}")
        queries.append(f"{syn_pairs[0][1]} optimization")

    queries.append(f"I/O optimization {sys_context} storage")
    queries.extend(_GENERAL_FALLBACK_QUERIES)

    return list(dict.fromkeys(queries))[:max_queries]
