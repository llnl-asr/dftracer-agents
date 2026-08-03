#!/bin/bash
# Fixed: use shared Lustre for Ray address (not node-local /tmp)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib_load_config.sh"
WS="$WORKSPACE_ROOT"
VENV="$WS/install/venv"
DATASET="$WS/dataset/opt1_4node"
SHARED_DIR="$DATASET/ray_setup"  # Shared Lustre location
RAY_ADDR_FILE="$SHARED_DIR/head_address.txt"
LOG_FILE="$WS/artifacts/11_tracer_opt1_4node_run.log"

mkdir -p "$DATASET/ray_object_spill" "$SHARED_DIR"

echo "=== Ray MoLFormer OPT1 bf16-autocast 4-NODE VALIDATION (Lustre-Shared) ===" | tee -a "$LOG_FILE"
RANK="${FLUX_TASK_RANK:-${FLUX_RANK:-0}}"
echo "Start: $(date) Rank: ${RANK}" | tee -a "$LOG_FILE"

module load rocm/6.2.1
unset VIRTUAL_ENV
export PATH="$VENV/bin:$PATH"
export VIRTUAL_ENV="$VENV"

# NOTE: DFTRACER_* env vars and the dftracer LD_LIBRARY_PATH prepend are
# intentionally NOT exported here. Confirmed by direct diagnostic (00:10-00:11
# window, scripts/test_gcs2.sh vs test_gcs.sh): Ray's gcs_server C++ binary
# fails to start (silently, no log files written at all) when spawned as a
# `ray start` subprocess under DFTRACER_ENABLE=1 + the dftracer lib64 on
# LD_LIBRARY_PATH, but starts fine with a clean env. Ray cluster bring-up
# (ray start --head / --address) is pure infrastructure and doesn't need
# tracing; DFTRACER_* is exported ONLY right before the python training
# entry point below, so the app process (and its Ray worker children) get
# traced without poisoning gcs_server/raylet.
unset HIP_VISIBLE_DEVICES
unset ROCR_VISIBLE_DEVICES

# ROOT CAUSE FIX for the GCS-server-never-starts bug (gcs_server.err
# FileNotFoundError after ~35s): ray/_private/services.py::start_ray_process
# unconditionally injects LD_PRELOAD=<venv>/ray/core/libjemalloc.so into
# EVERY ray subprocess (gcs_server, raylet, workers) whenever LD_PRELOAD is
# unset in the parent env (`os.environ.get("LD_PRELOAD") is None` check).
# On Tuolumne, with `module load rocm/6.2.1` already consuming a large share
# of glibc's static TLS surplus, dlopen'ing jemalloc under that already-
# crowded static-TLS budget fails at process startup with "cannot allocate
# memory in static TLS block" -- gcs_server's exec() never reaches main(), so
# it writes ZERO log lines (exactly matching the observed
# "gcs_server.err: No such file or directory" symptom), while the parent
# Popen handle still transiently looks alive to ray's own retry loop.
# Fix: explicitly set LD_PRELOAD to an empty string (not unset!) before any
# `ray start` call, so `os.environ.get("LD_PRELOAD") is None` is False and
# services.py skips the jemalloc injection entirely. Verified standalone via
# scripts/test_gcs_fix.sh: `ray start --head` succeeds and `ray status`
# reports the node active within ~10s (vs. deterministic ~35s failure
# without this fix).
export LD_PRELOAD=""
# RAY_OBJECT_SPILL_DIR/RAY_DEFAULT_OBJECT_STORE_MEMORY_PROPORTION/RAY_TMPDIR
# are NOT exported here (deliberately absent from the minimal env that
# reliably started gcs_server) - set only right before the training launch
# and the worker's `ray start --address=` below, where the object store is
# actually used.
RAY_TMP="/tmp/${USER:-$USER}_ray_$$"
mkdir -p "$RAY_TMP"

python3 -c "import dftracer.dftracer; print('dftracer OK')" | tee -a "$LOG_FILE"

# dftracer_service node-counter daemon: one instance per node, pinned to one
# core, bracketing the real run (Pipeline Policy rule 12 / MANDATORY). Started
# directly here (not via session_service_start, which binds to the MCP
# server's own host rather than the flux-allocated compute node) with the
# required DFTRACER_ENABLE/DFTRACER_LOG_FILE env vars — the daemon silently
# no-ops without them.
SERVICE_BIN="$VENV/bin/dftracer_service"
SERVICE_HOST=$(hostname)
mkdir -p "$WS/traces"
if [ -x "$SERVICE_BIN" ]; then
    echo "Starting dftracer_service on $SERVICE_HOST (rank $RANK)..." | tee -a "$LOG_FILE"
    DFTRACER_ENABLE=1 \
    DFTRACER_LOG_FILE="$WS/traces/service_${SERVICE_HOST}" \
    taskset -c 0 "$SERVICE_BIN" start "$WS/traces/dftracer_service/${SERVICE_HOST}" \
      >> "$LOG_FILE" 2>&1 || echo "WARN: dftracer_service start failed on $SERVICE_HOST" | tee -a "$LOG_FILE"
else
    echo "WARN: dftracer_service binary not found at $SERVICE_BIN" | tee -a "$LOG_FILE"
fi

if [ "$RANK" = "0" ]; then
    echo "HEAD NODE - cleaning up old Ray..." | tee -a "$LOG_FILE"
    rm -f "$RAY_ADDR_FILE"
    ray stop 2>/dev/null || true
    sleep 2
    
    echo "HEAD NODE - starting Ray head..." | tee -a "$LOG_FILE"
    # Launch in a minimal env (PATH+HOME+USER only) matching the standalone
    # diagnostic (scripts/test_gcs.sh) that reliably started gcs_server —
    # RAY_OBJECT_SPILL_DIR/RAY_DEFAULT_OBJECT_STORE_MEMORY_PROPORTION/
    # RAY_TMPDIR and other exports, even individually-harmless-looking ones,
    # were empirically correlated with gcs_server's silent FileNotFoundError
    # crash on this system. Head has no GPUs/object-store workload anyway.
    NODE_IP=$(hostname -I | awk '{print $1}')
    echo "HEAD NODE - using node-ip-address=$NODE_IP" | tee -a "$LOG_FILE"
    # Same bug as the worker's `ray start --address=` (see note there): the
    # head node also runs Ray Train worker actors here (--num-gpus=4), and
    # those actors are forked by raylet using THIS call's env. Without these,
    # actors placed on the head node hang indefinitely on a huggingface.co
    # etag/HEAD network call despite the driver's own HF_HUB_OFFLINE export
    # (which only covers the driver process, not raylet's forked actors).
    export HF_HUB_OFFLINE=1
    export TRANSFORMERS_OFFLINE=1
    ray_addr=""
    for attempt in 1 2 3; do
        echo "HEAD NODE - ray start attempt $attempt/3..." | tee -a "$LOG_FILE"
        ray stop 2>/dev/null || true
        sleep 2
        RAY_TMP_ATTEMPT="${RAY_TMP}_a${attempt}"
        mkdir -p "$RAY_TMP_ATTEMPT"
        # --num-gpus=4: the head node is a real MI300A GPU node too (not a
        # control-plane-only login node), and ScalingConfig(num_workers=
        # args.nodes*4) expects 4 GPUs contributed by EACH of the 2 physical
        # nodes (8 total). The original --num-gpus=0 was a leftover from
        # debugging the (unrelated, now-fixed) jemalloc GCS crash and left
        # the cluster under-resourced: only the worker's 4 GPUs registered,
        # so TorchTrainer's 8-GPU-worker request could never be scheduled
        # and hung indefinitely ("cluster only has ... 4.0 GPUs available").
        # --num-cpus=16: ROOT CAUSE FIX for the "Connected to Ray cluster"-
        # then-hangs-forever-in-ray.init() blocker. Without an explicit
        # --num-cpus, raylet auto-detects num_cpus from the hardware (192
        # cores on this MI300A node) and uses that value DIRECTLY as both
        # --maximum_startup_concurrency and --num_prestart_python_workers
        # (ray/_private/services.py:1667,1910 - "--num_prestart_python_
        # workers={num_cpus}"). raylet then immediately forks 192
        # concurrent `python setup_worker.py ... default_worker.py`
        # processes, ALL of which must `import ray` (grpcio/pyarrow/many
        # .so files) from this session's venv on Lustre at once. That
        # concurrent-import storm against a network filesystem starves
        # every one of them past the worker registration timeout -
        # confirmed via raylet.err showing dozens of "Some workers of the
        # worker process(<pid>) have not registered within the timeout"
        # (worker_pool.cc:586) for consecutively-climbing PIDs seconds
        # after ray start, with NO python-core-worker-*.log ever created
        # for any of them (proving they never got past worker-side Python
        # startup/import). ray.init() itself blocks (RPC to GCS) waiting
        # on this worker pool to stabilize, matching the exact symptom:
        # driver's main thread parked in recvfrom() on an idle GCS socket,
        # all ~35 other threads idle, and ZERO ray:: actor ever spawning.
        # Fix (CONFIRMED via isolated bisection, see below): --num-cpus=16
        # STILL hangs identically (raylet forks 16 concurrent prestart
        # workers, all fail to register, `ray.cluster_resources()` blocks
        # forever in recv()). A minimal standalone 2-node TorchTrainer test
        # with --num-cpus=1 --num-gpus=4 completed a real GPU-distributed
        # fit() in 32s with zero issues; the SAME script with only
        # --num-cpus bumped to 16 reproduced the exact hang (recv(), no
        # cluster_resources() output, timeout). Ray Train's actual worker
        # placement is driven by ScalingConfig's GPU request
        # (num_workers=8, resources_per_worker={"GPU":1}), not by
        # raylet's --num-cpus — so --num-cpus=1 is sufficient; it only
        # controls the (harmful, in this env) prestart-worker-pool size,
        # not how many GPU-bound Ray Train actors can be scheduled.
        head_out=$(ray start --head --num-gpus=4 --num-cpus=1 --port=53456 \
          --node-ip-address="$NODE_IP" --temp-dir="$RAY_TMP_ATTEMPT" 2>&1 | tee -a "$LOG_FILE")
        # NOTE: grep "address=" alone also matches the later
        # "ray.init(_node_ip_address=...)" hint line, which corrupts the
        # awk '{print $NF}' extraction (concatenates both lines' last
        # fields). Anchor on the unique "ray start --address='...'" line and
        # pull just the quoted IP:PORT with a regex.
        ray_addr=$(echo "$head_out" | grep -- "--address='" | grep -oP "(?<=--address=')[^']+")
        if [ -n "$ray_addr" ]; then
            RAY_TMP="$RAY_TMP_ATTEMPT"
            echo "HEAD NODE - succeeded on attempt $attempt" | tee -a "$LOG_FILE"
            break
        fi
        echo "HEAD NODE - attempt $attempt failed, retrying..." | tee -a "$LOG_FILE"
        sleep 3
    done
    echo "HEAD NODE - Ray address: $ray_addr" | tee -a "$LOG_FILE"

    if [ -z "$ray_addr" ]; then
        echo "ERROR: Failed to get Ray address after 3 attempts" | tee -a "$LOG_FILE"
        exit 1
    fi
    
    echo "$ray_addr" > "$RAY_ADDR_FILE"
    echo "HEAD NODE - wrote address to $RAY_ADDR_FILE" | tee -a "$LOG_FILE"
    
    sleep 10
    
    echo "HEAD NODE - running training..." | tee -a "$LOG_FILE"
    cd "$WS/annotated/src"
    # CRITICAL: molformer_ray_descriptors.py calls bare `ray.init()` (no
    # address). Without RAY_ADDRESS set, that creates a BRAND NEW isolated
    # single-node local Ray cluster instead of joining the 2-node/8-GPU
    # cluster we just bootstrapped above -- the pre-started head/worker ray
    # processes are silently ignored. That isolated cluster only has 4 GPUs
    # (this node), but ScalingConfig(num_workers=args.nodes*4=8) requests 8
    # GPU workers, which can never be scheduled -> TorchTrainer hangs
    # forever with near-zero CPU usage (confirmed via `flux exec ps`: the
    # training process was alive but idle, and a SECOND independent
    # gcs_server/raylet pair had spun up on the worker node's IP). Setting
    # RAY_ADDRESS to the address we already wrote to head_address.txt makes
    # the bare ray.init() auto-connect to the real cluster instead.
    export RAY_ADDRESS="$ray_addr"
    # molformer_ray_descriptors.py falls back to a path relative to its own
    # script_dir (annotated/src/../dataset/baseline/...) when
    # DATASET_CSV_PATH isn't set, which does NOT exist (dataset/ lives at
    # $WS/dataset, not under annotated/). Point it at the real PFS-backed
    # dataset location explicitly.
    export DATASET_CSV_PATH="$DATASET/pubchem_filtered.csv"
    export RAY_OBJECT_SPILL_DIR="$DATASET/ray_object_spill"
    export RAY_DEFAULT_OBJECT_STORE_MEMORY_PROPORTION=0.7
    export RAY_TMPDIR="$RAY_TMP"
    export HIP_VISIBLE_DEVICES=0,1,2,3
    export DFTRACER_ENABLE=1
    export DFTRACER_INIT=FUNCTION
    export DFTRACER_INC_METADATA=1
    export DFTRACER_DATA_DIR=all
    export DFTRACER_LOG_FILE="$WS/opt1_4node/traces/raw/opt1_4node"
    export LD_LIBRARY_PATH="$VENV/lib/python3.9/site-packages/dftracer/lib64:$VENV/lib/python3.9/site-packages/torch/lib:$LD_LIBRARY_PATH"
    # The MoLFormer tokenizer/config/model are already cached locally
    # ($HOME/.cache/huggingface/hub/models--ibm--MoLFormer-XL-both-10pct), but
    # AutoTokenizer/AutoConfig.from_pretrained() still perform an etag/HEAD
    # network round-trip to huggingface.co by default even when the cache is
    # warm. Tuolumne compute nodes have no outbound internet, so those calls
    # were the leading suspect for the multi-minute stall observed before any
    # ray:: worker actor appeared (main process alive, near-0% CPU, no
    # "InsufficientResourcesManager" scheduling warning yet -- i.e. stuck
    # BEFORE Tune even started placing trials). Force offline mode so
    # from_pretrained() uses the cache immediately with no network attempt.
    export HF_HUB_OFFLINE=1
    export TRANSFORMERS_OFFLINE=1
    # -u: unbuffered stdout so `tee` shows real progress instead of batching
    # behind Python's block-buffering-when-piped default (the previous runs'
    # apparent "hangs" were partly this: python3 ... | tee only flushed at
    # process exit).
    COMPLETE_MARKER="$SHARED_DIR/head_training_complete"
    rm -f "$COMPLETE_MARKER"
    python3 -u molformer_ray_descriptors_opt1.py \
      --experiment_name=opt1_molformer_dftracer_4node \
      --storage_path="$DATASET" \
      --nodes=4 2>&1 | tee -a "$LOG_FILE"
    TRAIN_EXIT=$?
    touch "$COMPLETE_MARKER"

    ray stop 2>/dev/null || true
    rm -f "$RAY_ADDR_FILE"
    if [ -x "$SERVICE_BIN" ]; then
        "$SERVICE_BIN" stop "$WS/traces/dftracer_service/${SERVICE_HOST}" >> "$LOG_FILE" 2>&1 || true
    fi
    echo "HEAD NODE - exit: $TRAIN_EXIT" | tee -a "$LOG_FILE"
else
    echo "WORKER NODE rank $RANK - stopping old Ray..." | tee -a "$LOG_FILE"
    ray stop 2>/dev/null || true
    sleep 2
    
    echo "WORKER NODE - waiting for head address..." | tee -a "$LOG_FILE"
    # Widened from 60s -> 180s and force a dentry-cache refresh via `ls` on
    # the parent dir each iteration (memory lesson: "repeated stale-dentry-
    # cache artifacts observed where a freshly-written file is briefly
    # invisible to ls/cat from a different client" on this Lustre-shared
    # SHARED_DIR) -- an opt1 run observed the worker's `[ -f ... ]` test
    # miss a file the head had already written ~40s earlier, stranding the
    # cluster at 4/8 GPUs and hanging trainer.fit() forever.
    for i in {1..180}; do
        ls "$SHARED_DIR" >/dev/null 2>&1
        if [ -f "$RAY_ADDR_FILE" ]; then
            echo "WORKER NODE - found address file" | tee -a "$LOG_FILE"
            break
        fi
        sleep 1
    done

    if [ ! -f "$RAY_ADDR_FILE" ]; then
        echo "ERROR: No Ray address file (timeout after 180s)" | tee -a "$LOG_FILE"
        exit 1
    fi
    
    ray_addr=$(cat "$RAY_ADDR_FILE")
    echo "WORKER NODE - connecting to $ray_addr..." | tee -a "$LOG_FILE"
    # Raylet (not gcs_server) is spawned here; workers it forks inherit this
    # env, so DFTRACER_* must be set for THIS call (unlike the head's
    # `ray start --head`, which spawns gcs_server and crashes under
    # DFTRACER_ENABLE/dftracer LD_LIBRARY_PATH — see note above).
    export RAY_OBJECT_SPILL_DIR="$DATASET/ray_object_spill"
    export RAY_DEFAULT_OBJECT_STORE_MEMORY_PROPORTION=0.7
    export RAY_TMPDIR="$RAY_TMP"
    export HIP_VISIBLE_DEVICES=0,1,2,3
    export DFTRACER_ENABLE=1
    export DFTRACER_INIT=FUNCTION
    export DFTRACER_INC_METADATA=1
    export DFTRACER_DATA_DIR=all
    export DFTRACER_LOG_FILE="$WS/opt1_4node/traces/raw/opt1_4node"
    export LD_LIBRARY_PATH="$VENV/lib/python3.9/site-packages/dftracer/lib64:$VENV/lib/python3.9/site-packages/torch/lib:$LD_LIBRARY_PATH"
    # Re-assert the jemalloc-LD_PRELOAD fix (see head-node note above) in case
    # anything upstream cleared it; raylet is spawned by THIS call too.
    export LD_PRELOAD=""
    # CRITICAL: worker actor processes (the ones that actually run
    # train_func_per_worker and call AutoTokenizer/AutoConfig.from_pretrained)
    # are forked by raylet using THIS call's env, not the driver's. The head
    # branch sets HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE right before its own
    # python3 launch, but that only covers the driver process and any actors
    # placed on the head node — actors placed on THIS (worker) node inherit
    # env from here, so without this they still attempt a network etag/HEAD
    # call to huggingface.co on egress-restricted compute nodes and hang
    # indefinitely with no traceback. This was the root cause of the
    # "Connected to Ray cluster, then stalls forever" symptom.
    export HF_HUB_OFFLINE=1
    export TRANSFORMERS_OFFLINE=1
    # --num-cpus=1: CONFIRMED fix (see head node comment above) - 16 still
    # hangs identically to the unbounded default; 1 is proven to work via
    # isolated bisection and is sufficient since GPU count (not CPU count)
    # drives Ray Train's actor placement here.
    ray start --address="$ray_addr" --num-gpus=4 --num-cpus=1 --temp-dir="$RAY_TMP" 2>&1 | tee -a "$LOG_FILE"
    
    echo "WORKER NODE rank $RANK - waiting for training to complete..." | tee -a "$LOG_FILE"
    # Poll for the head's completion marker instead of a fixed sleep: a fixed
    # sleep that's shorter than the actual training time causes the worker to
    # `ray stop` (dropping its 4 GPUs) WHILE the head is still mid-training,
    # silently shrinking the 8-GPU cluster the head's ScalingConfig depends
    # on. Cap at 40 min as a safety net against a truly stuck head.
    COMPLETE_MARKER="$SHARED_DIR/head_training_complete"
    for i in $(seq 1 2400); do
        if [ -f "$COMPLETE_MARKER" ]; then
            echo "WORKER NODE rank $RANK - head signaled training complete" | tee -a "$LOG_FILE"
            break
        fi
        sleep 1
    done
    ray stop 2>/dev/null || true
    if [ -x "$SERVICE_BIN" ]; then
        "$SERVICE_BIN" stop "$WS/traces/dftracer_service/${SERVICE_HOST}" >> "$LOG_FILE" 2>&1 || true
    fi
    echo "WORKER NODE rank $RANK - done" | tee -a "$LOG_FILE"
fi

echo "End: $(date)" | tee -a "$LOG_FILE"
exit ${TRAIN_EXIT:-0}
