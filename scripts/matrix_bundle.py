#!/usr/bin/env python3
"""20.4's evidence bundle (docs/matrix.md §6, §8 step 4) from one matrix run per family.

It checks that the runs are one capture:
  - every family once, all from one commit with a clean tree and one toolchain;
  - no entry listed twice.
Then it writes, into OUT (docs/results/phase20/matrix-20.4):
  - manifest.json: schema aster.phase20.matrix.v1, every planned combination of every family (one entry a line),
    with the runs' determinism, cold/warm pairs and totals summed and kept per family. The cache geometry axis is
    20.5's (matrix.md §2): each captured case and method is also listed at 2 and 8 KiB, planned for 20.5;
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
import sys
import tarfile
from collections import Counter
from pathlib import Path

import matrix_gates
import matrix_overlap

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aster.phase20.matrix.v1"
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
        per_family[family] = dict(run=str(run.resolve().relative_to(ROOT)), counts=m["counts"], determinism=m["determinism"],
                                  cold_warm_pairs=m["cold_warm_pairs"], seconds=m["seconds"], created=m["created"],
                                  archive=archive, archived_files=files)
    # the cache geometry axis (2 and 8 KiB) is 20.5's (matrix.md §2): each captured case and method, at R warm
    for family, case, method in sorted({(e["family"], e["case"], e["method"]) for e in entries
                                        if e["status"] == "captured"}):
        for kib in (2, 8):
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

    allrec = {}
    for run in args.runs:
        allrec.update(matrix_overlap.records_of(run))
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
