#!/usr/bin/env bash
# Run a memory-heavy job under a cgroup cap and a global lock.
#
# Why this exists
# ---------------
# The Phase 16 full-chip signoff has a few LibreLane steps that spike well past
# the machine's RAM (`magic-writelef` peaks at 32-54 GiB, `klayout-drc` at
# 25 GiB, `magic-spiceextraction` at 21 GiB), while the gate-level Icarus
# simulation of the 199 MB netlist wants ~10 GiB. Running two of these at once,
# with the desktop (gnome-shell/chrome/codex) on the same box, exhausts memory
# and `systemd-oomd` starts killing user-slice processes (chrome, the sim, tmux)
# because `user@.service` ships with `ManagedOOMMemoryPressure=kill` at a 50%
# limit and nothing is capped.
#
# This wrapper does two things:
#   1. serialises heavy jobs with a global `flock`, so only one ever runs; and
#   2. puts the job in a cgroup with a hard `MemoryMax`, so if it misbehaves it
#      is killed *inside its own cgroup* instead of taking the desktop with it.
#
# Usage:
#   scripts/memguard.sh <command> [args...]
#   scripts/memguard.sh --high 12G --max 16G --swap 8G -- vvp build.vvp +rom=...
#
# scripts/run_asic.py already runs LibreLane under this wrapper; do not wrap it
# again (a nested flock on the same lock deadlocks).
#
# Environment overrides: ASTER_MEM_HIGH, ASTER_MEM_MAX, ASTER_MEM_SWAP_MAX,
# ASTER_MEM_LOCK.
set -euo pipefail

HIGH=${ASTER_MEM_HIGH:-48G}
MAX=${ASTER_MEM_MAX:-56G}
SWAP=${ASTER_MEM_SWAP_MAX:-32G}
LOCK=${ASTER_MEM_LOCK:-/tmp/opencode/aster-heavy.lock}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --high) HIGH=$2; shift 2 ;;
        --max)  MAX=$2;  shift 2 ;;
        --swap) SWAP=$2; shift 2 ;;
        --lock) LOCK=$2; shift 2 ;;
        --)     shift; break ;;
        *)      break ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "usage: $0 [--high 48G] [--max 56G] [--swap 32G] -- <command...>" >&2
    exit 2
fi

mkdir -p "$(dirname "$LOCK")"

# MALLOC_ARENA_MAX keeps glibc from fragmenting the big EDA heaps; OMP/BLAS
# thread caps stop OpenROAD/KLayout from spawning one arena per core.
export MALLOC_ARENA_MAX=${MALLOC_ARENA_MAX:-2}
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}

echo "memguard: lock=$LOCK high=$HIGH max=$MAX swap=$SWAP" >&2
exec flock "$LOCK" \
    systemd-run --user --scope --collect --quiet \
        -p MemoryHigh="$HIGH" \
        -p MemoryMax="$MAX" \
        -p MemorySwapMax="$SWAP" \
        -- "$@"
