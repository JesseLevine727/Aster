#!/usr/bin/env python3
"""Phase 19's gate programs on the Phase 20 SoC against the Phase 19 SoC (milestone 20.2; docs/soc.md §10.4).

Each gate program (software/npu2: the GEMM gate, MNIST, the coherence and
fault programs) is built once, as scripts/npu_soc.py builds it, and run on
three simulations:
- the Phase 19 SoC (build/npu/npu_soc);
- the Phase 20 SoC's regression build without the NPU's request buffer and
  register stage (soc_shell_p19: NPU_BUFFER = 0, NPU_REG_Q = 0). Its cycles
  can differ from Phase 19's only where the fabric resolves a bank conflict
  between the hart and the NPU the other way; the testbench counts the NPU's
  waits by what held it (an instruction refill, a data access, an AMO, a
  held-back unit, other) and the hart's waits behind the NPU;
- the Phase 20 SoC as built (soc_shell: the buffer and the register stage the
  owner approved, 7 October 2026).
Every run must pass with the program's NPU jobs, errors and CPU checks, and
each console record must be the same in all three apart from its cycle counts
(status, shapes, MACs, mismatches, labels, ...). The cycles (§10.4: "any
cycle that differs is traced to a bank conflict between the hart and the
NPU"), on each Phase 20 build:
- the CPU's own measurements (cpu_cycles: the hart alone) are Phase 19's;
- every cycle the NPU waited has a cause in the fabric's arbitration, which
  the testbench counts: a hart's refill or data access in its bank, an AMO,
  a held-back unit; none is left unexplained (npu_waits_other = 0). The
  testbench also counts the cycles a hart waited behind the NPU, which Phase
  19's data cache never did (it had priority);
- without the buffer and register stage, a program with no such conflict in
  either direction has Phase 19's cycles exactly;
- each program's difference from Phase 19, and its conflict counts, are the
  traced ones recorded below (TRACED: phase20.md, 20.2). Any change fails
  until it is traced again.
Phase 19's own gates (utilization, speedup, MNIST) are also run on the Phase
20 SoC as built (scripts/npu_soc.py).

    soc_gates.py --p19 SIM --p20-base SIM --p20 SIM --build-dir DIR
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import npu_soc  # noqa: E402

CYCLE_FIELDS = re.compile(r"^(\w+_)?cycles$|^worst_ratio_x1000$")    # a record's cycle counts, and MNIST's ratio of two
CAUSES = ("npu_waits_irefill", "npu_waits_daccess", "npu_waits_amo", "npu_waits_blk", "npu_waits_other")
# Each program's traced difference from Phase 19 on each Phase 20 build: (cycles, the NPU's waits, the harts'
# waits behind the NPU). Phase 19 had one memory: its refills on a port of their own, its data cache always
# ahead of the NPU on the other. In Phase 20's banks the NPU waits behind refills in its bank, a hart can wait
# behind the NPU, and the NPU no longer waits behind data accesses in other banks. As built, the NPU's request
# buffer and register stage (the owner's, 7 October 2026) add their own cycles.
TRACED = {
    "npu2_gemm_gate": {"p20-base": (4, 4, 4), "p20": (120, 0, 0)},
    "npu2_mnist_gate": {"p20-base": (0, 0, 0), "p20": (264, 0, 0)},
    "npu2_coherence": {"p20-base": (206, 406, 348), "p20": (565, 418, 370)},
    "npu2_faults": {"p20-base": (0, 0, 0), "p20": (0, 0, 0)},
}


def run(sim: Path, elf: Path, binary: Path, tohost: int, console: Path) -> tuple[dict, list[dict]]:
    console.unlink(missing_ok=True)
    result = subprocess.run([str(sim), f"+bin={binary}", f"+tohost={tohost:x}", f"+console={console}",
                             "+max_cycles=50000000"], capture_output=True, text=True, timeout=3600)
    line = next((l for l in result.stdout.splitlines() if l.startswith("SOC ")), result.stderr.strip()[-300:])
    status = {"status": line.split()[1] if line.startswith("SOC ") else f"(no status) {line}", "exit": result.returncode}
    status.update(dict(re.findall(r"(\w+)=(\d+)", line)))
    records = []
    if console.exists():
        for text in console.read_text().splitlines():
            kind, _, rest = text.partition(",")
            records.append({"kind": kind, **dict(item.split("=", 1) for item in rest.split(",") if "=" in item)})
    return status, records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--p19", type=Path, required=True)
    parser.add_argument("--p20-base", type=Path, required=True)
    parser.add_argument("--p20", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    args = parser.parse_args()
    prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
    args.build_dir.mkdir(parents=True, exist_ok=True)
    sims = {"p19": args.p19, "p20-base": args.p20_base, "p20": args.p20}
    failures = 0

    def report(ok: bool, text: str) -> None:
        nonlocal failures
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}: {text}", flush=True)

    built = {name: npu_soc.build_program(args.build_dir, name, prefix) for name in npu_soc.PROGRAMS}
    tasks = [(name, label) for name in npu_soc.PROGRAMS for label in sims]
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        outputs = dict(zip(tasks, pool.map(
            lambda t: run(sims[t[1]], *built[t[0]], args.build_dir / f"{t[0]}.{t[1]}.console"), tasks)))
    for name, (jobs, unfinished, cpu_checks) in npu_soc.PROGRAMS.items():
        statuses = {label: outputs[(name, label)][0] for label in sims}
        records = {label: outputs[(name, label)][1] for label in sims}
        ran = all(s["status"] == "PASS" and s["exit"] == 0 and s.get("tohost") == "1"
                  and s.get("npu_jobs") == str(jobs) and s.get("npu_unfinished") == str(unfinished)
                  and s.get("cpu_checks") == str(cpu_checks) for s in statuses.values())
        same = len({len(r) for r in records.values()}) == 1 and (bool(records["p19"]) or name == "npu2_faults")
        for index, record in enumerate(records["p19"] if same else []):
            for label in ("p20-base", "p20"):
                other = records[label][index]
                if {k: v for k, v in record.items() if not CYCLE_FIELDS.search(k)} != \
                        {k: v for k, v in other.items() if not CYCLE_FIELDS.search(k)} or record.keys() != other.keys():
                    same = False
        cycles = {label: int(statuses[label].get("cycles", 0)) for label in sims}
        report(ran and same,
               f"{name}: the same results in the Phase 19 SoC and the Phase 20 SoC ({jobs} NPU jobs, {unfinished} "
               f"ended by an error or abort, {cpu_checks} CPU results; every record the same but its cycles)"
               + ("" if ran and same else f" [{statuses}]"))
        for label, title in (("p20-base", "without the buffer and register stage"), ("p20", "as built")):
            st, delta = statuses[label], cycles[label] - cycles["p19"]
            waits, behind = int(st.get("npu_waits", -1)), int(st.get("hart_waits_npu", -1))
            cpu_fields = sum(1 for record in records["p19"] if "cpu_cycles" in record)
            cpu_same = all(other.get(k) == record.get(k) for record, other in zip(records["p19"], records[label])
                           for k in record if k == "cpu_cycles") if same else False
            explained = st.get("npu_waits_other") == "0" and sum(int(st.get(c, 0)) for c in CAUSES) == waits
            # (without the buffer and register stage; as built, they add cycles of their own)
            no_conflict_same = label != "p20-base" or not (waits == 0 and behind == 0) or delta == 0
            traced = TRACED.get(name, {}).get(label) == (delta, waits, behind)
            report(ran and cpu_same and explained and no_conflict_same and traced,
                   f"{name}, Phase 20 {title}: {cycles[label]:,} cycles against Phase 19's {cycles['p19']:,} "
                   f"({delta:+,}); " + (f"the CPU's own cycles ({cpu_fields} measured) the same" if cpu_fields
                                        else "no CPU measurement") + f"; the NPU waited {waits} cycles (refill "
                   f"{st.get('npu_waits_irefill')}, data access {st.get('npu_waits_daccess')}, AMO "
                   f"{st.get('npu_waits_amo')}, held-back unit {st.get('npu_waits_blk')}, unexplained "
                   f"{st.get('npu_waits_other')}), a hart behind the NPU {behind}"
                   + ("" if cpu_same else "; the CPU's own cycles differ")
                   + ("" if explained else "; an NPU wait unexplained")
                   + ("" if no_conflict_same else "; cycles differ with no conflict")
                   + ("" if traced else f"; not the traced {TRACED.get(name, {}).get(label)} (cycles, NPU waits, "
                                        f"hart waits): trace the change and record it"))
        for index, record in enumerate(records["p19"] if same else []):
            fields = [k for k in record if CYCLE_FIELDS.search(k)]
            shape = "x".join(record[k] for k in ("m", "n", "k") if k in record)
            deltas = ", ".join(f"{k} {int(record[k]):,} / {int(records['p20-base'][index][k]) - int(record[k]):+,} / "
                               f"{int(records['p20'][index][k]) - int(record[k]):+,}" for k in fields)
            print(f"   {record['kind']} {shape}: {deltas}  (Phase 19 / Phase 20 without / as built)")
    gates = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "npu_soc.py"), "--sim", str(args.p20),
                            "--build-dir", str(args.build_dir / "npu_soc")], capture_output=True, text=True)
    for line in gates.stdout.splitlines():
        if line.startswith("PASS: GEMM") or line.startswith("PASS: MNIST") or line.startswith("PASS: N=1") \
                or line.startswith("FAIL"):
            print(f"   {line}")
    report(gates.returncode == 0, "Phase 19's gates (utilization, speedup, MNIST, the coherence and fault "
                                  "programs' checks) on the Phase 20 SoC as built (scripts/npu_soc.py)")
    print(f"{'PASS' if not failures else 'FAIL'}: Phase 19's gate programs on the Phase 20 SoC: results as in the "
          f"Phase 19 SoC, every cycle that differs traced" + (f" ({failures} failed)" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
