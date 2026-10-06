#!/usr/bin/env python3
"""Phase 19.4: the Phase 19 SoC's gate programs (software/npu2) on its
simulation (verification/npu/tb_npu_soc.cpp, which checks every NPU job, every
NPU write, every snoop and each CPU result the programs ask it to against the
independent reference). Each program is built with the C tests' flags and
start-up and must end with tohost 1, the testbench's PASS and its own PASS
records; then docs/npu.md §7's gates, in the SoC:

- utilization, JOB_MACS / (16 x JOB_CYCLES), at least 50% on each dense GEMM
  case (64x64x64, 96x96x96, 128x64x128);
- speedup end to end (the CPU's packing and GEMM against the NPU's descriptor,
  job and end, both to C in memory), at least 5x on each GEMM case;
- MNIST's batch-one MLP, at least 2x per image (the worst of the 32 images);
- the coherence program's jobs, error jobs, aborts, AMOs and reservation all
  as expected, and the fault program's traps (sub-word and atomic accesses to
  the NPU's registers);

and the N = 1 cases' utilization (MNIST's first layer and Conv2D), measured.
Prints one line a result; fails unless all pass, every record present.

    npu_soc.py --sim build/npu/npu_soc [--build-dir DIR]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ["-march=rv32ima_zicsr_zifencei", "-mabi=ilp32", "-nostdlib", "-nostartfiles", "-O2", "-ffreestanding",
         "-fno-pic", "-fno-stack-protector", "-msmall-data-limit=0", "-Wall", "-Wextra", "-Werror",
         f"-I{ROOT / 'software/drivers'}", f"-I{ROOT / 'software/npu2'}", f"-I{ROOT / 'software/benchmarks'}"]
# Each program: the testbench's counts it must end with (completed jobs, jobs
# ended by an error or an abort, CPU results checked).
PROGRAMS = {"npu2_gemm_gate": (5, 0, 3), "npu2_mnist_gate": (65, 0, 0), "npu2_coherence": (162, 4, 0),
            "npu2_faults": (0, 0, 0)}
# The records the programs must print (GEMM and N1 by shape).
EXPECTED = {("GEMM", 64, 64, 64), ("GEMM", 96, 96, 96), ("GEMM", 128, 64, 128), ("N1", 32, 1, 784),
            ("N1", 784, 1, 25), ("MNIST",), ("COHERENCE",)}
# The fault program is a trap test, built with their environment.
TRAP_ENV = [f"-I{ROOT / 'verification/core/env'}", f"-I{ROOT / 'vendor/riscv-tests/isa/macros/scalar'}",
            f"-I{ROOT / 'vendor/riscv-arch-test/riscv-test-suite/env'}", f"-I{ROOT / 'verification/core/traps'}"]


def build_and_run(sim: Path, build_dir: Path, name: str, prefix: str) -> tuple[str, list[dict[str, str]]]:
    elf = build_dir / f"{name}.elf"
    link = f"-T{ROOT / 'verification/core/directed/link.ld'}"
    source = ROOT / f"software/npu2/{name}.S"
    if source.exists():
        subprocess.run([f"{prefix}gcc", "-march=rv32ima_zicsr_zifencei", "-mabi=ilp32", "-nostdlib", "-nostartfiles",
                        *TRAP_ENV, link, str(source), "-o", str(elf)], check=True)
    else:
        subprocess.run([f"{prefix}gcc", *FLAGS, link, str(ROOT / "verification/core/c/start.S"),
                        str(ROOT / f"software/npu2/{name}.c"), "-o", str(elf)], check=True)
    binary = elf.with_suffix(".bin")
    subprocess.run([f"{prefix}objcopy", "-O", "binary", str(elf), str(binary)], check=True)
    symbols = subprocess.run([f"{prefix}nm", str(elf)], capture_output=True, text=True, check=True).stdout
    tohost = re.search(r"^([0-9a-f]+) \S tohost$", symbols, re.M).group(1)
    console = build_dir / f"{name}.console"
    console.unlink(missing_ok=True)
    run = subprocess.run([str(sim), f"+bin={binary}", f"+tohost={tohost}", f"+console={console}",
                          "+max_cycles=50000000"], capture_output=True, text=True, timeout=1800)
    line = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr.strip()
    records = []
    if console.exists():
        for text in console.read_text().splitlines():
            kind, _, rest = text.partition(",")
            fields = dict(item.split("=", 1) for item in rest.split(",") if "=" in item)
            fields["kind"] = kind
            records.append(fields)
    return line, records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/npu/soc")
    args = parser.parse_args()
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    args.build_dir.mkdir(parents=True, exist_ok=True)
    failed = False

    def result(ok: bool, text: str) -> None:
        nonlocal failed
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: {text}")

    for name, (jobs, unfinished, cpu_checks) in PROGRAMS.items():
        line, records = build_and_run(args.sim, args.build_dir, name, prefix)
        counts = dict(re.findall(r"(\w+)=(\w+)", line))
        tb_ok = (line.startswith("SOC PASS") and counts.get("tohost") == "1" and counts.get("npu_jobs") == str(jobs)
                 and counts.get("npu_unfinished") == str(unfinished) and counts.get("cpu_checks") == str(cpu_checks))
        records_ok = (bool(records) or name == "npu2_faults") and all(r.get("status") == "PASS" for r in records)
        result(tb_ok and records_ok, f"the Phase 19 SoC runs {name}: every NPU job ({jobs} completed, {unfinished} "
               f"ended by an error or an abort), write and snoop, {cpu_checks} CPU results and "
               f"{int(counts.get('loads', '0')):,} main-memory loads as the reference"
               + (" (and its 7 traps: sub-word and atomic accesses to the NPU's registers)" if name == "npu2_faults" else "")
               + ("" if tb_ok and records_ok else f" [{line}; {records}]"))
        for r in records:
            key = (r["kind"], int(r["m"]), int(r["n"]), int(r["k"])) if r["kind"] in ("GEMM", "N1") else (r["kind"],)
            if key not in EXPECTED:
                result(False, f"a record not expected, or twice: {r}")
                continue
            EXPECTED.discard(key)
            if r["kind"] == "GEMM":
                m, n, k = int(r["m"]), int(r["n"]), int(r["k"])
                macs, job, cpu, npu = int(r["job_macs"]), int(r["job_cycles"]), int(r["cpu_cycles"]), int(r["npu_cycles"])
                utilization = 100.0 * macs / (16 * job)
                speedup = cpu / npu
                result(macs == m * n * k and utilization >= 50.0,
                       f"GEMM {m}x{n}x{k} utilization in the SoC {utilization:.1f}% (gate 50%): {macs:,} MACs in "
                       f"{job:,} job cycles ({100.0 * macs / (16 * npu):.1f}% end to end)")
                result(speedup >= 5.0,
                       f"GEMM {m}x{n}x{k} speedup end to end {speedup:.2f}x (gate 5x): the best CPU code (DOT8) "
                       f"{cpu:,} cycles ({macs / cpu:.2f} MACs a cycle), the NPU {npu:,}")
            elif r["kind"] == "N1":
                m, k = int(r["m"]), int(r["k"])
                macs, job, npu = int(r["job_macs"]), int(r["job_cycles"]), int(r["npu_cycles"])
                result(macs == m * k, f"N=1 {m}x1x{k} (K-split) utilization in the SoC, measured: "
                       f"{100.0 * macs / (16 * job):.1f}% ({job:,} job cycles, {npu:,} end to end)")
            elif r["kind"] == "MNIST":
                images, cpu, npu = int(r["images"]), int(r["cpu_cycles"]), int(r["npu_cycles"])
                worst = int(r["worst_ratio_x1000"]) / 1000.0
                result(images == 32 and r.get("mismatches") == "0" and worst >= 2.0,
                       f"MNIST MLP 784-32-10 batch one, speedup per image {worst:.2f}x at the worst of {images} "
                       f"(gate 2x), {cpu / npu:.2f}x over all: the best CPU code (DOT8) {cpu // images:,} cycles "
                       f"an image, the NPU {npu // images:,}; logits as the reference on both, "
                       f"{r.get('correct_labels')}/{images} labels")
            elif r["kind"] == "COHERENCE":
                result({f: r.get(f) for f in ("jobs", "racing", "errors", "aborts", "reservation", "status")}
                       == {"jobs": "160", "racing": "80", "errors": "2", "aborts": "2", "reservation": "1",
                           "status": "PASS"} and int(r.get("amo_adds", "0")) > 0,
                       f"coherence: {r['jobs']} jobs ({r['racing']} with the CPU reading C as the NPU writes it), "
                       f"C's lines cached before each and read after, A and B stored just before START with no "
                       f"fence, the interrupt, {r.get('amo_adds')} AMO and lr/sc adds racing the jobs; {r['errors']} "
                       f"error jobs and {r['aborts']} aborts, each followed by a completed job; an sc after an NPU "
                       f"write to its word fails")
    result(not EXPECTED, "every gate record present" + (f" (missing {sorted(EXPECTED)})" if EXPECTED else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
