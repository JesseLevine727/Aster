#!/usr/bin/env python3
"""Phase 19.5: npu.md §4.6's options in the NPU shell. Each configuration of the
v2 NPU (rtl/accelerator/aster_npu2*.sv: A_STRIPS, PORT_BYTES, DIM) is built
into the shell (verification/npu/shell_npu_v2.sv, tb_npu.cpp) and run as
`make npu-tests` runs 19.4's: random jobs (every coverage bin required) and
the edge jobs in each memory mode — on the memories that answer on time every
completed job's JOB_CYCLES equal to the cycle model's (npu_model.h's stepped
model) — then the gate cases, the N = 1 cases and the convolutions on the
two-cycle memory. The adopted configuration runs --seeds seeds a mode, the
others one. Prints a line a result and the cases; fails unless all pass and
the adopted configuration's GEMM cases reach 50% utilization.

    npu_options.py --build-dir build/npu/options [--seeds 4] [--jobs 8]
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RTL = ["rtl/accelerator/aster_npu2_ram.sv", "rtl/accelerator/aster_npu2_engine.sv", "rtl/accelerator/aster_npu2.sv",
       "verification/npu/shell_npu_v2.sv"]
# name: (parameters, adopted)
VARIANTS = {
    "s2": ("-GA_STRIPS=2", False),                               # a second A strip buffer
    "p8": ("-GPORT_BYTES=8", False),                             # a 64-bit memory port
    "s2p8": ("-GA_STRIPS=2 -GPORT_BYTES=8", False),              # both
    "d8": ("-GPORT_BYTES=8 -GDIM=8", False),                     # the 8x8 array (on the 64-bit port)
    "s2p8d8": ("-GA_STRIPS=2 -GPORT_BYTES=8 -GDIM=8", True),     # all three: the adopted NPU
}
MODES = {"plain": "+cycle_check", "latency1": "+latency=1 +cycle_check", "stall": "+stall_seed=5",
         "latency1-stall": "+latency=1 +stall_seed=9", "long-stall": "+stall_seed=3 +long_stall",
         "inflight3": "+stall_seed=7 +max_inflight=3"}
CASES = ["64,64,64", "96,96,96", "128,64,128", "32,1,784", "784,1,25", "10,1,32"]
CONVS = ["32,32,1,5,5,1", "16,16,3,3,3,16", "7,7,16,3,3,32"]


def build(name: str, params: str, out: Path) -> Path:
    sim = (out / name / "sim").resolve()
    (out / name).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["verilator", "--cc", "--exe", "--build", "-O3", "--assert", "--Wall", *params.split(),
                             "--top-module", "shell_npu_v2", "--prefix", "Vnpu_shell", "--Mdir", str(out / name / "obj"),
                             "-o", str(sim), *(str(ROOT / f) for f in RTL), str(ROOT / "verification/npu/tb_npu.cpp"),
                             "-CFLAGS", f"-I{ROOT / 'verification/npu'}"], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"FAIL: building option {name}: {result.stderr[-2000:]}")
    return sim


def run(sim: Path, *args: str) -> str:
    result = subprocess.run([str(sim), *args], capture_output=True, text=True, timeout=3600)
    return (result.stdout.strip().splitlines() or ["(no output)"])[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/npu/options")
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    args.build_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        sims = dict(zip(VARIANTS, pool.map(lambda v: build(v, VARIANTS[v][0], args.build_dir), VARIANTS)))
        failed = False
        for name, (params, adopted) in VARIANTS.items():
            sim = sims[name]
            seeds = args.seeds if adopted else 1
            for mode, extra in MODES.items():
                runs = [pool.submit(run, sim, f"+seed={seed}", "+jobs=1000", "+require_coverage", *extra.split())
                        for seed in range(1, seeds + 1)]
                runs.append(pool.submit(run, sim, "+edges", *extra.split()))
                lines = [r.result() for r in runs]
                ok = all(line.startswith("NPU PASS") for line in lines)
                failed |= not ok
                print(f"{'PASS' if ok else 'FAIL'}: v2 NPU option {name} ({params}) in the NPU shell, {mode}: {seeds} "
                      f"seed{'s' if seeds > 1 else ''} x 1,000 jobs and the edge jobs as the reference, every coverage "
                      f"bin" + (", every job as the cycle model" if "cycle_check" in extra else "")
                      + ("" if ok else f" [{[line for line in lines if not line.startswith('NPU PASS')][0]}]"))
            for case in CASES:
                line = run(sim, f"+case={case}", "+cycle_check")
                ok = line.startswith("NPU PASS")
                util = float(re.search(r"utilization=([\d.]+)", line).group(1)) if ok else 0.0
                gate = adopted and case.count(",") == 2 and ",1," not in case
                ok = ok and (not gate or util >= 50.0)
                failed |= not ok
                print(f"{'PASS' if ok else 'FAIL'}: v2 NPU option {name}, two-cycle memory"
                      + (" (gate 50%)" if gate else "") + ": " + line.removeprefix("NPU PASS "))
            for conv in CONVS:
                line = run(sim, f"+conv={conv}", "+cycle_check")
                ok = line.startswith("NPU PASS")
                failed |= not ok
                print(f"{'PASS' if ok else 'FAIL'}: v2 NPU option {name}, convolution direct and im2col: "
                      + line.removeprefix("NPU PASS "))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
