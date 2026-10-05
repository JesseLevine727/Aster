#!/usr/bin/env python3
"""Phase 19.3: what im2col costs the CPU on the Aster core (with its caches,
on the CPU shell's two-cycle memory), for the convolutions 19.3 lowers both
ways — Conv2D and CIFAR's two (verification/npu/im2col_cost.c). Each is
built with the C tests' flags and start-up and run in the CPU shell, the
window around the im2col loop alone; prints each layer's window cycles,
retired instructions and the bytes it writes, and fails unless every run
passes its checks.

    npu_im2col_cost.py --sim build/aster_core/core_ports_aster_l1 [--build-dir DIR]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LAYERS = {0: ("Conv2D 32x32, 5x5", 784 * 25), 1: ("CIFAR conv1 3x16x16, 3x3", 196 * 27),
          2: ("CIFAR conv2 16x7x7, 3x3", 25 * 144)}
FLAGS = ["-march=rv32ima_zicsr_zifencei", "-mabi=ilp32", "-nostdlib", "-nostartfiles", "-O2", "-ffreestanding",
         "-fno-pic", "-fno-stack-protector", "-msmall-data-limit=0", "-Wall", "-Wextra", "-Werror"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/npu/im2col_cost")
    args = parser.parse_args()
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    args.build_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for layer, (name, written) in LAYERS.items():
        elf = args.build_dir / f"im2col_{layer}.elf"
        subprocess.run([f"{prefix}gcc", *FLAGS, f"-DLAYER={layer}", f"-T{ROOT / 'verification/core/directed/link.ld'}",
                        str(ROOT / "verification/core/c/start.S"), str(ROOT / "verification/npu/im2col_cost.c"),
                        "-o", str(elf)], check=True)
        binary = elf.with_suffix(".bin")
        subprocess.run([f"{prefix}objcopy", "-O", "binary", str(elf), str(binary)], check=True)
        symbols = subprocess.run([f"{prefix}nm", str(elf)], capture_output=True, text=True, check=True).stdout
        tohost = re.search(r"^([0-9a-f]+) \S tohost$", symbols, re.M).group(1)
        run = subprocess.run([str(args.sim), f"+bin={binary}", f"+tohost={tohost}", "+io_page", "+cache_model",
                              "+max_cycles=5000000"], capture_output=True, text=True, timeout=600)
        line = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
        cycles = re.search(r"window_cycles=(\d+)", line)
        retired = re.search(r"window_retired=(\d+)", line)
        ok = line.startswith("SHELL PASS") and cycles and retired
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: im2col on the Aster core, {name}: "
              + (f"{int(cycles.group(1)):,} cycles, {int(retired.group(1)):,} instructions, {written:,} bytes written "
                 f"({int(cycles.group(1)) / written:.2f} cycles a byte)" if ok else line))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
