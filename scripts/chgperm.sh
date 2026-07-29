#!/bin/bash
# chgperm.sh -- mirror the owner's permissions onto the group, recursively,
#               in parallel, with a single-line progress bar.
#
# Use case: files created by one user must be fully usable (read / modify /
# delete / execute) by every other member of the group.
#
# Semantics (identical in both backends):
#   * every item : chgrp <group>, "g=u" (copy the OWNER's rwx bits onto the
#                  group -- so group gets exactly what the owner has, no more),
#                  "o-rwx" (nothing leaks outside the group).
#                  Group-writable directories are what actually let another
#                  group member delete/rename a file, since delete is a
#                  permission on the PARENT directory.
#   * directories: additionally g+s, so NEW files/subdirs inherit the group.
#   * files      : g-s (the old version of this script set g+s on files; on
#                  Linux that is mandatory locking, not inheritance).
#   For NEW files to be group-WRITABLE as well, each user needs `umask 007` --
#   setgid inherits the group, not the write bit.
#
# Backends:
#   --mpi     mpifileutils (dchmod/dfind) launched with `flux run` -- use inside
#             or alongside an allocation, best for very large trees.
#   --local   plain find + xargs -P background workers on this node -- no
#             allocation needed. (default when no flux allocation is detected)
#
# Usage:
#   ./chgperm.sh [--mpi|--local] [-n RANKS] [-q] [-g GROUP] <dir>
#
# Env equivalents: BACKEND=mpi|local  NRANKS=N  QUEUE=pdebug  GROUP_NAME=iopp
#                  QUIET=1

set -u

group_name=${GROUP_NAME:-dldl}
NRANKS=${NRANKS:-}
QUEUE=${QUEUE:-pdebug}
BACKEND=${BACKEND:-}
QUIET=${QUIET:-0}
CHUNK=${CHUNK:-256}
root=""

while [ $# -gt 0 ]; do
    case "$1" in
        --mpi)    BACKEND=mpi ;;
        --local)  BACKEND=local ;;
        -n)       NRANKS=$2; shift ;;
        -g)       group_name=$2; shift ;;
        -q|--quiet) QUIET=1 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        -*)       echo "unknown option: $1" >&2; exit 1 ;;
        *)        root=$1 ;;
    esac
    shift
done

if [ -z "$root" ] || [ ! -d "$root" ]; then
    echo "usage: $0 [--mpi|--local] [-n RANKS] [-g GROUP] [-q] <dir>" >&2
    exit 1
fi
root=$(readlink -f "$root")

# ---------------------------------------------------------------- output ----
IS_TTY=0
[ -t 2 ] && IS_TTY=1
COLS=$( { tput cols; } 2>/dev/null || echo 80 )

say() { [ "$QUIET" == "1" ] || echo "$*" >&2; }

# bar <done> <total> <label> -- redraws ONE line in place, no vertical spam.
bar() {
    [ "$QUIET" == "1" ] && return 0
    local done=$1 total=$2 label=$3
    local pct=0 width=30 filled
    [ "$total" -gt 0 ] && pct=$(( done * 100 / total ))
    [ "$pct" -gt 100 ] && pct=100
    filled=$(( pct * width / 100 ))
    local b
    printf -v b '%*s' "$filled" ''; b=${b// /#}
    printf -v b '%-*s' "$width" "$b"
    local line
    line=$(printf '  [%s] %3d%%  %s/%s  %s' "$b" "$pct" "$done" "$total" "$label")
    if [ "$IS_TTY" == "1" ]; then
        printf '\r%-*.*s' "$COLS" "$COLS" "$line" >&2
    fi
}
bar_end() {
    [ "$QUIET" == "1" ] && return 0
    [ "$IS_TTY" == "1" ] && printf '\n' >&2
    return 0
}
# spinner_until <pidfile-pid> <label> -- for the MPI backend, where progress is
# owned by the MPI job; one line, elapsed time, no scroll.
spin() {
    [ "$QUIET" == "1" ] && { wait "$1"; return $?; }
    local pid=$1 label=$2 i=0 t0=$SECONDS
    local frames='|/-\'
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$IS_TTY" == "1" ]; then
            printf '\r%-*.*s' "$COLS" "$COLS" \
                "  [${frames:i++%4:1}] ${label}  ${SECONDS}s elapsed" >&2
        fi
        sleep 0.5
    done
    wait "$pid"; local rc=$?
    [ "$IS_TTY" == "1" ] && printf '\r%-*.*s\r' "$COLS" "$COLS" '' >&2
    say "  [done] ${label}  $((SECONDS - t0))s"
    return $rc
}

# --------------------------------------------------------------- backend ----
if [ -z "$BACKEND" ]; then
    if command -v flux >/dev/null 2>&1 && [ -n "${FLUX_URI:-}" ]; then
        BACKEND=mpi
    else
        BACKEND=local
    fi
fi
[ -z "$NRANKS" ] && { [ "$BACKEND" == "mpi" ] && NRANKS=16 || NRANKS=$(nproc --all); }

LOGDIR=$root/.chgperm_logs
mkdir -p "$LOGDIR" 2>/dev/null
LOG=$LOGDIR/chgperm.$(date +%Y%m%d_%H%M%S).log

say "chgperm: $root"
say "  group=$group_name  backend=$BACKEND  parallelism=$NRANKS  log=$LOG"

# ------------------------------------------------------------------- run ----
rc=0
if [ "$BACKEND" == "mpi" ]; then
    module load mpifileutils/0.12 2>/dev/null || {
        echo "chgperm: failed to load mpifileutils/0.12" >&2; exit 1; }
    L=(flux run -n "$NRANKS" -q "$QUEUE")

    # Pass 1: mirror owner bits to group + chgrp, whole tree.
    "${L[@]}" dchmod -g "$group_name" -m "g=u,o-rwx" "$root" >>"$LOG" 2>&1 &
    spin $! "pass 1/2  mirror u->g, chgrp $group_name" || rc=1

    # Pass 2: setgid on directories (dchmod's mode parser rejects 's').
    if [ $rc -eq 0 ]; then
        "${L[@]}" dfind "$root" --type d --exec chmod g+s {} \; >>"$LOG" 2>&1 &
        spin $! "pass 2/2  setgid on directories" || rc=1
    fi
else
    # Local backend: walk once, then chunk the walk through xargs -P workers.
    # Each worker appends its chunk size to a counter file that drives the bar.
    run_pass() {  # run_pass <label> <find-type> <mode>
        local label=$1 ftype=$2 mode=$3
        local list=$LOGDIR/.list.$$ cnt=$LOGDIR/.cnt.$$
        find "$root" -type "$ftype" -not -path "*/.chgperm_logs/*" -print0 >"$list"
        local total
        total=$(tr -dc '\0' <"$list" | wc -c)
        : >"$cnt"
        ( xargs -0 -r -n "$CHUNK" -P "$NRANKS" sh -c '
              chgrp "$1" "${@:3}" 2>>"$2"
              chmod "$0" "${@:3}" 2>>"$2"
              echo $(( $# - 2 )) >>"$2.cnt"
          ' "$mode" "$group_name" "$LOG" <"$list" ) &
        local wpid=$!
        # Note: workers append to $LOG.cnt; mirror it to our counter path.
        while kill -0 $wpid 2>/dev/null; do
            local d=0
            [ -f "$LOG.cnt" ] && d=$(awk '{s+=$1} END{print s+0}' "$LOG.cnt")
            bar "$d" "$total" "$label"
            sleep 0.3
        done
        wait $wpid; local r=$?
        bar "$total" "$total" "$label"; bar_end
        rm -f "$list" "$cnt"
        return $r
    }
    : >"$LOG.cnt"
    run_pass "pass 1/2  directories (g=u,g+s,o-rwx)" d "g=u,g+s,o-rwx" || rc=1
    : >"$LOG.cnt"
    [ $rc -eq 0 ] && { run_pass "pass 2/2  files (g=u,g-s,o-rwx)" f "g=u,g-s,o-rwx" || rc=1; }
    rm -f "$LOG.cnt"
fi

if [ $rc -ne 0 ]; then
    echo "chgperm: FAILED -- see $LOG" >&2
    exit 1
fi

if [ "$QUIET" != "1" ]; then
    echo "chgperm: done. Group '$group_name' now mirrors owner permissions under $root"
    echo "  new entries inherit the group via setgid; set 'umask 007' for group-writable new files"
    echo "  log: $LOG"
fi
