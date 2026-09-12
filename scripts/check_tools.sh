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

if [[ "$missing" -ne 0 ]]; then
  echo "One or more required tools are missing. See docs/toolchain.md."
  exit 1
fi

echo "Aster Phase 0 toolchain is available."
