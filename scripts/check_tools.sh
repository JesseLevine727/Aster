#!/usr/bin/env bash
set -euo pipefail

missing=0

check_tool() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    printf 'OK   %-30s %s\n' "$name" "$(command -v "$name")"
  else
    printf 'MISS %-30s\n' "$name"
    missing=1
  fi
}

check_tool verilator
check_tool python3
check_tool make
check_tool riscv32-unknown-elf-gcc
check_tool riscv32-unknown-elf-objcopy
check_tool riscv32-unknown-elf-objdump

# Phase 18 reference model (docs/phase18.md): Spike and the device-tree
# compiler it calls at start-up.
# The install records its source commit next to bin/ (docs/toolchain.md).
SPIKE="${SPIKE:-$HOME/tools/spike/bin/spike}"
SPIKE_PIN=0bff12123b1fd510e19e19634dd997dbade70e54
SPIKE_COMMIT_FILE="$(dirname "$SPIKE")/../SPIKE_COMMIT"
if [[ ! -x "$SPIKE" ]]; then
  printf 'MISS %-30s %s\n' spike "$SPIKE"
  missing=1
elif [[ "$(cat "$SPIKE_COMMIT_FILE" 2>/dev/null)" != "$SPIKE_PIN" ]]; then
  printf 'MISS %-30s %s is not recorded as commit %s (%s)\n' spike "$SPIKE" "$SPIKE_PIN" "$SPIKE_COMMIT_FILE"
  missing=1
else
  printf 'OK   %-30s %s (%s)\n' spike "$SPIKE" "${SPIKE_PIN:0:8}"
fi
check_tool dtc

if [[ "$missing" -ne 0 ]]; then
  echo "One or more required tools are missing. See docs/toolchain.md."
  exit 1
fi

echo "Aster Phase 0 toolchain is available."
