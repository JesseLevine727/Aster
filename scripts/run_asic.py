#!/usr/bin/env python3
"""Phase 15 ASIC flow driver.

SystemVerilog is converted to Verilog with sv2v (Yosys cannot parse several
constructs Aster uses, such as ``return`` inside a function), then LibreLane
runs the SKY130 Classic flow on the converted netlist.

    python3 scripts/run_asic.py                      # full Classic flow
    python3 scripts/run_asic.py --to Yosys.Synthesis # stop after synthesis

The ROM init path inside the RTL is relative, so the flow always runs from the
repository root.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASIC = ROOT / "asic" / "sky130"
BUILD = ASIC / "build"

SV2V = os.environ.get("SV2V", str(Path.home() / "tools" / "sv2v" / "sv2v-Linux" / "sv2v"))
LIBRELANE = os.environ.get("LIBRELANE", str(Path.home() / "tools" / "librelane-venv" / "bin" / "librelane"))

RTL = [
    "vendor/picorv32/picorv32.v",
    "rtl/core/aster_picorv32.sv",
    "rtl/cache/aster_l1_cache.sv",
    "rtl/memory/aster_rom.sv",
    "rtl/memory/aster_ram.sv",
    "rtl/memory/aster_sram_macro.sv",
    "rtl/peripherals/aster_uart.sv",
    "rtl/peripherals/aster_perf_counters.sv",
    "rtl/core/aster_hart.sv",
    "rtl/soc/aster_minimal.sv",
    "rtl/peripherals/aster_uart_tx.sv",
    "asic/sky130/aster_asic.sv",
]

DEFINES = ["RISCV_FORMAL", "SYNTHESIS"]


def convert():
    BUILD.mkdir(parents=True, exist_ok=True)
    output = BUILD / "aster_asic.v"
    command = [SV2V]
    for define in DEFINES:
        command += ["-D", define]
    command += [str(ROOT / path) for path in RTL]
    print(f"sv2v -> {output.relative_to(ROOT)}", flush=True)
    with output.open("w") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, check=True)
    strip_comb_guards(output)
    return output


def strip_comb_guards(path):
    """Drop sv2v's ``if (_sv2v_0) ;`` always_comb guard.

    sv2v emits this empty statement to preserve always_comb's time-zero
    evaluation. It is a no-op for synthesis but Yosys' Verilog parser rejects
    an empty statement, so remove the pair.
    """
    lines = path.read_text().splitlines(keepends=True)
    kept = []
    index = 0
    while index < len(lines):
        if "_sv2v_0" in lines[index] and index + 1 < len(lines) and lines[index + 1].strip() == ";":
            index += 2
            continue
        kept.append(lines[index])
        index += 1
    path.write_text("".join(kept))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT", str(Path.home() / ".ciel")))
    parser.add_argument("--to", default=None, help="stop at this LibreLane step id")
    parser.add_argument("--run-tag", default=None)
    parser.add_argument("--skip-convert", action="store_true")
    parser.add_argument("librelane_args", nargs="*")
    args = parser.parse_args()

    if not args.skip_convert:
        convert()

    command = [LIBRELANE, "--docker-no-tty", "--dockerized", "--pdk-root", args.pdk_root]
    if args.to:
        command += ["-T", args.to]
    if args.run_tag:
        command += ["--run-tag", args.run_tag]
    command += args.librelane_args
    command += [str(ASIC / "config.json")]

    print("librelane:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
