# Lessons: dftracer_analyze

- [L-1b7f4780] analyze() reported 527,206 events / 4 processes on a 16-rank VPIC best_case trace, consistently across reruns -> silent under-count, distinct from run-to-run non-determinism; ground truth from event_count and the 16 rank files was 2,111,806 events / 16 ranks -> cross-check analyze's process/event count against event_count or the rank file listing; if they disagree, aggregate dur by name/cat with aggregator/view (TraceViewer) and label it manual analysis (2026-07-14)

- [L-9edf7bb3] bare dfanalyzer CLI with analyzer.preset.async_layers=[...] found 6 of 48 processes and missed compute, preprocess, communication_except_io, stdio -> the CLI skips the analyze tool's _ensure_analyzable_path split+index; the same directory through the analyze tool gave 48 procs and every category -> never hand-invoke dfanalyzer against a compact/raw trace dir; extend the analyze tool with the override, or replicate its split+index first (2026-07-22)

- [L-d15f9248] posix/dlio presets returned returncode=0 with an empty Layer Breakdown and zero-row flat views, discarding 130M-148M events (PECAN/PDBspheres) -> the trace's cat values (hdf5 + custom dft_event_logging strings) match neither preset's fixed layer-name list -> run analyze(analyzer_preset="generic") first (now the default); posix/dlio only afterwards for their semantic breakdown (2026-07-20)

- [L-b232ec03] concurrent analyze() workers crashed with 'Failed to open RocksDB ... .dftindex/*.log: No such file or directory' -> every worker raced to build the RocksDB trace index on an already-compact directory -> index(directory=<compact>, force=True, executor_threads=1) before analyze() (2026-07-20)

- [L-7a1e1944] no u_<layer>_time_proc overlap columns in the flat view; the overlap metric seemed not to exist -> async_layers defaults to [] for the base and generic presets (only DLIO ships a list, with DLIO layer names) -> pass async_layers=[<the app's own layers>] through the analyze tool and re-run; it diffs only against compute_time_proc (2026-07-22)

- [L-a1744e62] same-pid sweep-line overlap of compute vs data-loading reported 0.00 s before and after a prefetch fix that improved wall time (PECAN) -> a single-threaded loop is serial on its own pid; DataLoader workers run on other pids -> use the cross-process dynamic-interval event-splitting algorithm (max busy per pid per bucket, pigeonhole bound) (2026-07-22)

- [L-64c6f307] bucket-bound overlap gave overlap_pct = 204.75% with p99-sized buckets (PECAN) -> events longer than the bucket were attributed whole to one bucket, so busy time exceeded the width -> split events at bucket boundaries whenever the width is below 2*max(dur); no overlap percentage may exceed 100 (2026-07-22)

- [L-65cdcc06] spawn-context DataLoader run (num_workers=6) produced zero unnamed <run>-<hash>-.pfw.gz worker traces while fork produced 64 -> dftracer per-process bootstrap does not re-initialize in a spawned child (suspected); DFTRACER_INIT=HYBRID is not a valid ProfileInitType and silently no-ops; LD_PRELOAD alone also gave none and hung at teardown -> count unnamed worker files before computing overlap on a spawn trace; if 0, report the overlap as not computable and the win's mechanism as unverified; gap still open (2026-07-22)

- [L-99646e35] analyze MCP call hung forever / 'MCP error -32000: Connection closed' though dfanalyzer printed all output within a minute -> dfanalyzer's dask LocalCluster hangs at teardown after 'Cluster teardown' at ~99% CPU; subprocess.run blocks until exit -> the tool tolerated a timeout-after-output (later removed: no timeout now, since a fixed bound killed real 26M+ event analyses); via Bash, background it, read the log and kill that exact PID after 'Cluster teardown', never wait on it (2026-07-06)

- [L-c1e3523a] analyze tool failed with 'TypeError: can't concat str to bytes' in its timeout handler (h5bench-run1) -> TimeoutExpired.stdout/stderr are bytes even with text=True -> decode exc.stdout/exc.stderr when they are bytes before concatenating (fixed in the tool; needed a server restart) (2026-07-08)

- [L-ff54ff6d] analyze() timed out twice at 300 s on an 8.6 MB / ~47,588-event RAJAPerf compact trace with no stdout captured -> unknown: likely a hang in the analysis phase itself, not teardown; trace size does not predict the hang -> do not assume small traces are safe; if it recurs, investigate the analysis phase for that trace shape/preset; meanwhile aggregate with aggregator/view, not by hand-decompressing (2026-08-05)

- [L-69121ce7] dfanalyzer: error: unrecognized arguments: --trace-path ... from the analyze tool -> dfanalyzer is a Hydra CLI; _hydra_args generated GNU-style flags -> use positional overrides trace_path=<p> view_types=[...] analyzer/preset=posix analyzer.checkpoint=True analyzer.checkpoint_dir=<p> output=console cluster=local (fixed in _hydra_args) (2026-06-24)

- [L-5789e6ba] session_analyze_traces still reported 391 events from 1 file after re-splitting 98 trace files -> traces_split/idx/ is built on the first analyze call and reused even when split chunks change -> rm -rf <ws>/traces_split/idx/, re-split with force=True, then re-analyze (2026-06-22)

- [L-fd57c5fb] session_analyze_traces failed with 'No files found' on <run_name>/traces/compact/ -> compact traces were folder-segregated by workload/replicate and the tool scans one directory non-recursively -> pass subpath="<workload>/<rep>" (tool fixed to accept it) (2026-07-10)

- [L-e35a599f] dfanalyzer reported 'Total Files: 0' and no POSIX events while C_APP annotations recorded -> DFTRACER_INIT=0 disables the dftracer constructor and the POSIX interceptor -> leave DFTRACER_INIT unset (FUNCTION) or PRELOAD/HYBRID as appropriate, never 0 when POSIX tracing is wanted; fix collection before analysis (2026-06-18)

- [L-5a6f3184] STDIO dominated the event count (166,952 events) but cost 1.50 s while POSIX 670,158 events cost 65.5 s -> event counts do not measure time (~9 us/op, ~11-byte STDIO writes) -> rank by aggregated dur per category/op, never by event count (undated)

- [L-8eff3cad] vpic-kokkos dump_energies tagged comp="io" but 99.7% of its time was MPI_Allreduce -> comp= is the annotator's guess from the function name -> verify with interval-containment of child spans before recommending a fix for the labelled category (2026-07-14)
