#!/usr/bin/env python3
"""Phase 15 post-layout gate-level simulation.

Builds the routed ``aster_asic`` netlist with the SKY130 functional cell models
and runs it with the signoff SDF back-annotated. The oracle is the same
"Hello from Aster\\n" firmware used by the RTL and board tests.

    python3 scripts/run_asic_gl_sim.py                 # latest run
    python3 scripts/run_asic_gl_sim.py --run p15-gds --corner nom_tt_025C_1v80

The sky130 Verilog models expose their behaviour only when ``FUNCTIONAL`` is
defined; without it the cells are timing-only stubs and every flop stays X.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASIC = ROOT / "asic" / "sky130"
TB = ASIC / "tb_aster_asic_gl.v"
BUILD = ASIC / "build"


def tool(name, fallback):
    return os.environ.get(name.upper(), fallback)


def find_iverilog():
    local = Path.home() / "tools" / "iverilog" / "usr" / "bin"
    return tool("iverilog", str(local / "iverilog")), tool("vvp", str(local / "vvp"))


def find_ivl_root():
    local = Path.home() / "tools" / "iverilog" / "usr" / "lib"
    candidates = list(local.glob("*/ivl"))
    return str(candidates[0]) if candidates else None


def pdk_lib_ref(pdk_root):
    """Return the sky130A libs.ref directory LibreLane selected."""
    versions = sorted(
        (Path(pdk_root) / "ciel" / "sky130" / "versions").glob("*/sky130A/libs.ref"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for ref in versions:
        if (ref / "sky130_fd_sc_hd" / "verilog" / "sky130_fd_sc_hd.v").is_file():
            return ref
    raise SystemExit(f"no sky130A cell library found under {pdk_root}")


def latest_run():
    runs = sorted(
        (ASIC / "runs").glob("p15-*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run in runs:
        if (run / "final" / "nl" / "aster_asic.nl.v").is_file():
            return run
    raise SystemExit("no completed p15 run with a routed netlist found")


def strip_macro_timing(source, target):
    """Drop the SRAM macro's CELL block from the SDF.

    Icarus cannot parse the escaped macro instance name
    (``soc\\.g_sram\\.ram\\.macro``), and the macro's access time is not needed
    for the cycle-accurate SoC oracle.
    """
    text = source.read_text()
    kept = []
    index = 0
    while index < len(text):
        if text.startswith("(CELL", index):
            depth = 0
            end = index
            while end < len(text):
                if text[end] == "(":
                    depth += 1
                elif text[end] == ")":
                    depth -= 1
                    if depth == 0:
                        end += 1
                        break
                end += 1
            block = text[index:end]
            if "sky130_sram_2kbyte" not in block:
                kept.append(block)
            index = end
        else:
            kept.append(text[index])
            index += 1
    target.write_text("".join(kept))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default=None, help="run directory name under asic/sky130/runs")
    parser.add_argument("--corner", default="nom_tt_025C_1v80")
    parser.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT", str(Path.home() / ".ciel")))
    args = parser.parse_args()

    run = ASIC / "runs" / args.run if args.run else latest_run()
    netlist = run / "final" / "nl" / "aster_asic.nl.v"
    sdf = run / "final" / "sdf" / args.corner / f"aster_asic__{args.corner}.sdf"
    if not netlist.is_file() or not sdf.is_file():
        raise SystemExit(f"missing netlist or SDF under {run}")

    ref = pdk_lib_ref(args.pdk_root)
    verilog = ref / "sky130_fd_sc_hd" / "verilog"
    sram = ref / "sky130_sram_macros" / "verilog" / "sky130_sram_2kbyte_1rw1r_32x512_8.v"

    iverilog, vvp = find_iverilog()
    command = [iverilog, "-g2012", "-gspecify", "-DFUNCTIONAL", "-o", str(BUILD / "gl_sim.vvp")]
    ivl_root = find_ivl_root()
    if ivl_root:
        command += ["-B", ivl_root]
    command += [
        str(TB),
        str(netlist),
        str(verilog / "primitives.v"),
        str(verilog / "sky130_fd_sc_hd.v"),
        str(sram),
    ]
    print("iverilog:", " ".join(command), flush=True)
    subprocess.run(command, check=True)

    filtered = BUILD / "gl_sim.sdf"
    strip_macro_timing(sdf, filtered)

    run_command = [vvp, str(BUILD / "gl_sim.vvp"), f"+sdf={filtered}"]
    print("vvp:", " ".join(run_command), flush=True)
    return subprocess.run(run_command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
