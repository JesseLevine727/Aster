#!/usr/bin/env python3
"""soc.md §11's ECG gate (tuning.md §5; matrix.md §4.8): stage overlap shown, or the pipeline labelled sequential per
chunk, from the stamped twins' MATRIX_STAGE lines (software/matrix/ecg.c: each chunk's bring, FIR and classifier on
the harts, and its DMA and NPU jobs at their submission for their own JOB_CYCLES, on the window's clock).

Overlap is between chunks: a stage of one chunk running while a stage of another runs (a chunk's FIR on hart 1
beside the last chunk's classifier on hart 0, or an engine's job beside another chunk's stage). A chunk's own jobs
inside its own stages are not overlap. A chunk overlaps when its stages meet another chunk's for more than
MIN_OVERLAP cycles (the two stamps' cost, as matrix.md §10.7's minimum). A pipeline is:
  - overlapped: every chunk after the first overlaps another;
  - sequential per chunk: none does;
  - partly overlapped: otherwise.

  matrix_ecg_overlap.py RUN_DIR... [--json OUT]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

MIN_OVERLAP = 40
KINDS = ("bring", "fir", "classify", "dma", "npu")


def stages(lines: list[str], window: str) -> list[dict]:
    """Each chunk's stage intervals (begin, end) in one window, by chunk."""
    out = []
    for line in lines:
        if not line.startswith(f"MATRIX_STAGE,window={window},"):
            continue
        fields = dict(t.split("=", 1) for t in line.split(",")[1:])
        chunk = {k: tuple(int(x) for x in fields[k].split("-")) for k in KINDS if k in fields}
        out.append(dict(chunk=int(fields["chunk"]), stages={k: v for k, v in chunk.items() if v[1] > v[0]}))
    return sorted(out, key=lambda c: c["chunk"])


def overlap(chunks: list[dict]) -> dict:
    """The cycles each chunk's stages meet another chunk's (each pair once), and the classification."""
    per = Counter()
    for i, a in enumerate(chunks):
        for b in chunks[i + 1:]:
            for x in a["stages"].values():
                for y in b["stages"].values():
                    meet = min(x[1], y[1]) - max(x[0], y[0])
                    if meet > 0:
                        per[a["chunk"]] += meet
                        per[b["chunk"]] += meet
    later = [c["chunk"] for c in chunks[1:]]
    over = [c for c in later if per[c] > MIN_OVERLAP]
    label = ("overlapped" if later and len(over) == len(later) else "sequential per chunk" if not over
             else "partly overlapped")
    total = sum(per.values()) // 2
    return dict(label=label, chunks=len(chunks), overlapping_chunks=len(over), overlap_cycles=total)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows, revisions, problems = {}, set(), []
    for run in args.runs:
        manifest = json.loads((run / "manifest.json").read_text())
        revisions.add(manifest["source"]["revision"][:7] + (" (dirty)" if manifest["source"]["dirty"] else ""))
        for e in manifest["entries"]:
            if e["status"] != "captured" or not e["case"].endswith("_stages"):
                continue
            for window in ("e2e", "kernel"):
                chunks = stages(e.get("extras", []), window)
                if not chunks:
                    continue
                if [c["chunk"] for c in chunks] != list(range(len(chunks))):
                    problems.append(f"{e['id']} {window}: chunks {[c['chunk'] for c in chunks][:5]}...")
                    continue
                rows[f"{e['id']} {window}"] = overlap(chunks) | dict(case=e["case"], window=window, sim=e["sim"])
    by_case = {}
    for key, r in rows.items():
        by_case.setdefault(r["case"], Counter())[r["label"]] += 1
    out = dict(revisions=sorted(revisions), min_overlap=MIN_OVERLAP, rows=rows,
               by_case={c: dict(v) for c, v in sorted(by_case.items())}, problems=problems)
    if args.json:
        args.json.write_text(json.dumps(out, indent=1) + "\n")
    print(f"ECG's stage overlap ({', '.join(sorted(revisions))}): {len(rows)} stamped windows; problems {len(problems)}")
    for case, labels in sorted(by_case.items()):
        print(f"  {case}: {dict(labels)}")
    r = rows.get("ecg/ecg_pipeline_overlap_stages/pipeline/soc_dev/warm e2e")
    if r:
        print(f"  v1's case, the two-hart pipeline, warm e2e: {r['overlap_cycles']} cycles of overlap, "
              f"{r['overlapping_chunks']} of {r['chunks'] - 1} later chunks")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
