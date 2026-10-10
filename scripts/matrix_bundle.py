#!/usr/bin/env python3
"""20.4's evidence bundle (docs/matrix.md §6, §8 step 4) from one matrix run per family.

It checks that the runs are one capture:
  - every family once, all from one commit with a clean tree and one toolchain;
  - no entry listed twice.
Then it writes, into OUT (docs/results/phase20/matrix-20.4):
  - manifest.json: schema aster.phase20.matrix.v1, every planned combination of every family (one entry a line),
    with the runs' determinism, cold/warm pairs and totals summed and kept per family. matrix.md §7's unsupported
    configurations (each captured case and method) and methods (each case they name) are listed with their
    reasons, beside the runner's own; the cache geometry axis is 20.5's (matrix.md §2): each captured case and
    method is also listed at 2 and 8 KiB, planned for 20.5;
  - raw-<family>.tar.xz: the family's raw records and console logs, and its determinism repeat's, packed
    reproducibly (sorted, fixed times and owners); each entry's `records` names its path inside;
  - overlap.json (scripts/matrix_overlap.py) and gates.json (scripts/matrix_gates.py);
  - SHA256SUMS over every file but itself.
The README is written by hand (then --sums-only). It exits 1 if the runs are not one clean capture, an entry
failed, or the overlap analysis finds a stamping fault.

  matrix_bundle.py RUN_DIR... --out OUT        matrix_bundle.py --out OUT --sums-only
"""
import argparse
import hashlib
import io
import json
import lzma
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path

import matrix_gates
import matrix_overlap

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.phase20.matrix.v1"
# matrix.md §7's unsupported combinations, which the runner does not plan: the configurations (each captured case
# and method, at R warm otherwise) and the methods (each case they name)
CONFIGURATIONS = [
    ("soc_icache_off", None, "the instruction cache off: the core fetches only through its cache (matrix.md §7)"),
    ("soc_l2", None, "an L2: set aside by the owner (matrix.md §7)"),
    ("soc_zero_wait", None, "zero-wait memory: the memory is block RAM with a two-cycle read (matrix.md §7)"),
    ("soc_n2x2", "npu", "a 2x2 NPU: the NPU v2 is built 4x4 or 8x8 (matrix.md §7)"),
    ("soc_n8p4", "npu", "8x8 on a 32-bit port: the 8x8 array needs the 64-bit port (matrix.md §7)"),
]
# a case name that carries its method (Conv2D's, MNIST's, CIFAR's), stripped to the case for §7's methods
METHOD_SUFFIX = re.compile(r"_(scalar|multicore|dot8|npu|npu_direct|npu_im2col|npu_batched)$")
COHERENT = ("atomic_add", "lrsc_counter", "cas_counter", "lock_sum", "false_shared", "padded", "ping_pong", "spsc_queue",
            "shared_mix", "producer_consumer")
METHODS = [
    ("npu", lambda f, c: (f, c) in {("cpu", "fft"), ("dsp", "fft"), ("cpu", "sort_search"), ("cpu", "coremark"),
                                    ("cpu", "dhrystone"), ("cpu", "strided")} or (f == "coherence" and c in COHERENT),
     "the NPU for FFT, sort/search, CoreMark, Dhrystone, strided and the coherence cases: no GEMM in them (matrix.md §7)"),
    ("multicore", lambda f, c: f == "cpu" and c in ("coremark", "dhrystone", "sort_search", "strided"),
     "single-threaded v1 kernels: scaling is the multicore family's question (matrix.md §7)"),
    ("dot8", lambda f, c: c == "fft", "DOT8 for the FFT: its Q15 butterflies are not int8 dot products (matrix.md §7)"),
    ("dma", lambda f, c: f in ("cpu", "dsp", "ml", "npu_gemm", "coherence"),
     "the DMA as a compute method: it only moves bytes; it is in the DMA family and ECG's pipeline (matrix.md §7)"),
]
GEOMETRY = ("planned for 20.5: the 2 and 8 KiB caches (matrix.md §2, soc.md §9), with the L1 tests at each size "
            "and ABI 4's line-geometry metadata following the parameter")
MTIME = 1791504000           # 2026-10-09 00:00 UTC: the archives' fixed time


def pack(run: Path, archive: Path) -> int:
    """The run's records/ and console/ (and repeat/'s), sorted, with fixed metadata; xz at preset 9."""
    files = sorted(p for sub in ("records", "console", "repeat/records", "repeat/console")
                   for p in (run / sub).glob("*") if p.is_file())
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path in files:
            info = tarfile.TarInfo(str(path.relative_to(run)))
            info.size, info.mtime, info.mode = path.stat().st_size, MTIME, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(path.read_bytes()))
    archive.write_bytes(lzma.compress(buffer.getvalue(), preset=9))
    return len(files)


def write_sums(out: Path) -> None:
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}"
            for p in sorted(out.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sums-only", action="store_true", help="rewrite SHA256SUMS alone (after the README)")
    args = parser.parse_args()
    if args.sums_only:
        write_sums(args.out)
        return 0
    problems = []
    manifests = [(run, json.loads((run / "manifest.json").read_text())) for run in args.runs]
    families = [f for _, m in manifests for f in m["families"]]
    if len(families) != len(set(families)):
        problems.append(f"a family in two runs: {families}")
    for key in ("source", "toolchain"):
        values = {json.dumps(m[key], sort_keys=True) for _, m in manifests}
        if len(values) != 1:
            problems.append(f"the runs' {key} differ: {values}")
    if any(m["source"]["dirty"] for _, m in manifests):
        problems.append("a run was made from a dirty tree")
    if any(m["schema"] != SCHEMA for _, m in manifests):
        problems.append("a run's schema differs")

    args.out.mkdir(parents=True, exist_ok=True)
    entries, per_family, totals = [], {}, Counter()
    for run, m in manifests:
        family = m["families"][0] if len(m["families"]) == 1 else "+".join(m["families"])
        archive = f"raw-{family}.tar.xz"
        files = pack(run, args.out / archive)
        for e in m["entries"]:
            if e.get("records"):
                e["archive"] = archive
            e.pop("firmware_bin", None)                  # (the build tree's; the firmware's hash stays)
            if "totals" in e:
                for k, v in e["totals"].items():
                    totals[k] += v
            entries.append(e)
        where = run.resolve()
        per_family[family] = dict(run=str(where.relative_to(ROOT) if where.is_relative_to(ROOT) else where), counts=m["counts"], determinism=m["determinism"],
                                  cold_warm_pairs=m["cold_warm_pairs"], seconds=m["seconds"], created=m["created"],
                                  archive=archive, archived_files=files)
    allrec = {}
    for run in args.runs:
        allrec.update(matrix_overlap.records_of(run))
    uses_npu = {(e["family"], e["case"], e["method"]) for e, recs in allrec.values()
                if any(int(r["npu_jobs"]) for r in recs)}
    captured = sorted({(e["family"], e["case"], e["method"]) for e in entries if e["status"] == "captured"})
    for family, case, method in captured:                # §7's configurations (the NPU's: where it ran)
        for sim, needs, reason in CONFIGURATIONS:
            if needs is None or (family, case, method) in uses_npu:
                entries.append(dict(id=f"{family}/{case}/{method}/{sim}/warm", family=family, case=case, method=method,
                                    sim=sim, axes=dict(cache_state="warm"), status="unsupported", reason=reason))
    have = {(f, METHOD_SUFFIX.sub("", c), m) for f, c, m in captured}
    for family, case in sorted({(f, METHOD_SUFFIX.sub("", c)) for f, c, _ in captured}):     # §7's methods
        for method, applies, reason in METHODS:
            if applies(family, case) and (family, case, method) not in have:
                entries.append(dict(id=f"{family}/{case}/{method}/soc_dev/warm", family=family, case=case,
                                    method=method, sim="soc_dev", axes=dict(cache_state="warm"), status="unsupported",
                                    reason=reason))
    # the cache geometry axis (2 and 8 KiB) is 20.5's (matrix.md §2): each captured case and method, at R warm, planned
    # until a run captures it at that size, warm and at L0 (tuning.md §4.2)
    sized = {(e["family"], e["case"], e["method"], e["sim"]) for e in entries if e["status"] == "captured"
             and e["axes"].get("cache_state") == "warm" and not e["axes"].get("layout")}
    for family, case, method in captured:
        for kib in (2, 8):
            if (family, case, method, f"soc_l1_{kib}k") in sized:
                continue
            entries.append(dict(id=f"{family}/{case}/{method}/soc_l1_{kib}k/warm", family=family, case=case,
                                method=method, sim=f"soc_l1_{kib}k", axes=dict(cache_kib=kib, cache_state="warm"),
                                status="planned", reason=GEOMETRY))
    ids = Counter(e["id"] for e in entries)
    if any(n > 1 for n in ids.values()):
        problems.append(f"entries listed twice: {[i for i, n in ids.items() if n > 1][:5]}")
    counts = Counter(e["status"] for e in entries)
    if counts["failed"]:
        problems.append(f"{counts['failed']} entries failed")

    def summed(key):
        parts = [f[key] for f in per_family.values() if f[key]]
        return dict(checked=sum(p["checked"] for p in parts), identical=sum(p["identical"] for p in parts),
                    differ=[d for p in parts for d in p["differ"]])

    first = manifests[0][1]
    head = dict(schema=SCHEMA, source=first["source"], toolchain=first["toolchain"], families=families,
                counts={k: counts[k] for k in ("captured", "unsupported", "failed", "planned")},
                records=sum(len(recs) for _, recs in allrec.values()),
                determinism=summed("determinism"), cold_warm_pairs=summed("cold_warm_pairs"),
                totals=dict(totals), per_family=per_family)
    for key in ("determinism", "cold_warm_pairs"):
        if head[key]["differ"]:
            problems.append(f"{key}: {head[key]['differ'][:5]}")
    lines = ["{"] + [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()] + [' "entries": [']
    lines += [" " + json.dumps(e, sort_keys=True) + ("," if i + 1 < len(entries) else "") for i, e in enumerate(entries)]
    lines += [" ]", "}"]
    (args.out / "manifest.json").write_text("\n".join(lines) + "\n")

    overlap = matrix_overlap.analyse(args.runs)
    if overlap["faults"]:
        problems.append(f"overlap: stamping faults {overlap['faults'][:5]}")
    (args.out / "overlap.json").write_text(json.dumps(overlap, indent=None, separators=(",", ":")) + "\n")
    gates = dict(scaling=matrix_gates.scaling(allrec), against_v1=matrix_gates.against_v1(allrec))
    (args.out / "gates.json").write_text(json.dumps(gates, indent=1) + "\n")

    write_sums(args.out)
    print(f"{'PASS' if not problems else 'FAIL'}: the bundle ({args.out}): {len(entries)} entries {dict(counts)}; "
          f"determinism {head['determinism']['identical']}/{head['determinism']['checked']}; pairs "
          f"{head['cold_warm_pairs']['identical']}/{head['cold_warm_pairs']['checked']}; totals {dict(totals)}; "
          f"overlap {overlap['classes']}")
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
