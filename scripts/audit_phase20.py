#!/usr/bin/env python3
"""Phase 20's closeout audit (docs/tuning.md §9; phase17-plus.md's rule): read-only, over the final bundle
(docs/results/phase20/matrix-20.5) and the board run's evidence (docs/results/phase20/board-20.5).

It rejects, each as a check that can fail (--self-test plants a fault for each and shows it caught):
  - sums:     an evidence file whose hash is not its SHA256SUMS line, or a file not listed;
  - source:   a bundle not bound to its commit: the capture's revision not a commit of this repository, not an
              ancestor of HEAD, or made from a dirty tree;
  - manifest: missing required workloads (a family absent), a failed or planned entry;
  - records:  a captured entry whose raw records are missing from its archive, or a record that fails AsterBench
              v12's validator or is not PASS (every one re-validated from the archives);
  - totals:   wrong counter attribution: the NPU's bytes against the fabric's and the engines' cycle bounds
              (scripts/matrix.py's reconcile_totals, re-run on every record and its MATRIX_JOBS line);
  - gates:    the scaling and v1 gates re-derived from the raw records (scripts/matrix_gates.py), each met, and
              equal to the bundle's gates.json;
  - ecg:      soc.md §11's ECG gate re-derived from the stamped twins (scripts/matrix_ecg_overlap.py): the
              two-hart pipelines overlapped, v1's sequential per chunk;
  - timing:   negative slack or failing endpoints (setup, hold, pulse width), unrouted nets or routing errors,
              DRC errors or critical warnings, methodology violations, resource use over 80% (LUTs, block RAM,
              DSPs), in the final build's own reports;
  - board:    any R program of the bundle that does not end on the board as simulated, on each of two loads of
              the final image (its bitstream, and FCLK0 measured at 100 MHz);
  - coremark: CoreMark's official run not validated by its oracle, or not matched on the board.
It says PASS only if every check passes; otherwise "incomplete", with each failure.

  audit_phase20.py [--bundle DIR] [--board DIR] [--self-test]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import lzma
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asterbench_v12  # noqa: E402
import matrix  # noqa: E402
import matrix_ecg_overlap  # noqa: E402
import matrix_gates  # noqa: E402

BUNDLE = ROOT / "docs/results/phase20/matrix-20.5"
BOARD = ROOT / "docs/results/phase20/board-20.5"
FAMILIES = ("cpu", "coherence", "dsp", "ml", "ecg", "memory", "npu_gemm", "dma")
AREA_LIMIT = 80.0


class Failure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


# ---- the evidence, read ----

def sums_problems(directory: Path) -> list[str]:
    listed, problems = {}, []
    sums = directory / "SHA256SUMS"
    if not sums.exists():
        return [f"{directory}: no SHA256SUMS"]
    for line in sums.read_text().splitlines():
        digest, _, name = line.partition("  ")
        listed[name.removeprefix("./")] = digest        # (find's ./ prefix or none)
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        name = str(path.relative_to(directory))
        if name not in listed:
            problems.append(f"{name}: not in SHA256SUMS")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != listed[name]:
            problems.append(f"{name}: its hash is not SHA256SUMS'")
    problems += [f"{name}: listed but missing" for name in listed if not (directory / name).exists()]
    return problems


def load_manifest(bundle: Path) -> dict:
    return json.loads(lzma.open(bundle / "manifest.json.xz").read())


def load_archives(bundle: Path, manifest: dict) -> dict:
    """archive name -> {member name: text} (records and consoles; the repeats left out)."""
    out = {}
    for name in sorted({e["archive"] for e in manifest["entries"] if e.get("archive")}):
        with tarfile.open(fileobj=io.BytesIO(lzma.open(bundle / name).read())) as tar:
            out[name] = {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()
                         if m.isfile() and not m.name.startswith("repeat/")}
    return out


def entry_texts(entry: dict, archives: dict) -> tuple[str | None, str | None]:
    files = archives.get(entry.get("archive"), {})
    console = "console/" + entry["id"].replace("/", "__") + ".console"
    return files.get(entry.get("records")), files.get(console)


# ---- the checks ----

def check_sums(bundle: Path, board: Path) -> str:
    problems = sums_problems(bundle) + (sums_problems(board) if board.exists() else [])
    require(not problems, f"{len(problems)} files: {problems[:3]}")
    return "every file of the bundle and the board's evidence matches its SHA256SUMS"


def check_source(manifest: dict) -> str:
    rev, dirty = manifest["source"]["revision"], manifest["source"]["dirty"]
    require(not dirty, f"the capture's tree was dirty ({manifest['source'].get('changed', [])[:3]})")
    known = subprocess.run(["git", "cat-file", "-e", f"{rev}^{{commit}}"], cwd=ROOT,
                           stderr=subprocess.DEVNULL).returncode == 0
    require(known, f"{rev} is not a commit of this repository")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", rev, "HEAD"], cwd=ROOT,
                              stderr=subprocess.DEVNULL).returncode == 0
    require(ancestor, f"{rev} is not an ancestor of HEAD")
    return f"the capture is {rev[:7]}'s, a clean tree, an ancestor of HEAD"


def check_manifest(manifest: dict) -> str:
    families = set(manifest["families"])
    require(families == set(FAMILIES), f"families {sorted(families)}, not {sorted(FAMILIES)}")
    counts = {}
    for e in manifest["entries"]:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    require(counts.get("failed", 0) == 0 and counts.get("planned", 0) == 0, f"entries {counts}")
    captured_families = {e["family"] for e in manifest["entries"] if e["status"] == "captured"}
    require(captured_families == set(FAMILIES), f"no captured entry in {set(FAMILIES) - captured_families}")
    require(all(e.get("reason") for e in manifest["entries"] if e["status"] == "unsupported"),
            "an unsupported entry without its reason")
    return f"{len(manifest['entries'])} entries, every family: {counts}"


def parsed_records(entry: dict, archives: dict) -> tuple[list[dict], list[str]]:
    text, console = entry_texts(entry, archives)
    require(text is not None, f"{entry['id']}: its raw records are not in {entry.get('archive')}")
    require(console is not None, f"{entry['id']}: its console is not in {entry.get('archive')}")
    lines = [line for line in text.splitlines() if line.startswith("ASTERBENCH,")]
    require(lines, f"{entry['id']}: no record")
    records = []
    for line in lines:
        try:
            r = asterbench_v12.validate_line(line + "\n")
        except Exception as error:                      # (the validator's every rejection)
            raise Failure(f"{entry['id']}: {error}") from None
        require(r["status"] == "PASS", f"{entry['id']}: a record's status is {r['status']}")
        records.append(r)
    extras = [line for line in console.splitlines() if line.startswith("MATRIX_")]
    return records, extras


def check_records(manifest: dict, archives: dict) -> str:
    n = 0
    for e in manifest["entries"]:
        if e["status"] == "captured":
            n += len(parsed_records(e, archives)[0])
    require(n == manifest["records"], f"{n} records, the manifest says {manifest['records']}")
    return f"all {n} records re-validated from the raw archives, every one PASS"


def check_totals(manifest: dict, archives: dict) -> str:
    n = 0
    for e in manifest["entries"]:
        if e["status"] != "captured":
            continue
        records, extras = parsed_records(e, archives)
        try:
            matrix.reconcile_totals(records, extras)
        except Exception as error:
            raise Failure(f"{e['id']}: {error}") from None
        n += len(records)
    return f"the NPU's and the DMA's totals and bounds hold in all {n} records"


def allrec_of(manifest: dict, archives: dict) -> dict:
    out = {}
    for e in manifest["entries"]:
        if e["status"] != "captured":
            continue
        text, _ = entry_texts(e, archives)
        require(text is not None, f"{e['id']}: no raw records")
        out[e["id"]] = (e, [dict(t.split("=", 1) for t in line.strip().split(",")[1:])
                            for line in text.splitlines() if line.startswith("ASTERBENCH,")])
    return out


def check_gates(manifest: dict, archives: dict, gates: dict) -> str:
    allrec = allrec_of(manifest, archives)
    scaling, v1 = matrix_gates.scaling(allrec), matrix_gates.against_v1(allrec)
    require(json.loads(json.dumps(scaling)) == gates["scaling"], "the scaling gate differs from gates.json")
    require(json.loads(json.dumps(v1)) == gates["against_v1"], "the v1 gate differs from gates.json")
    short = [k for k, v in scaling.items() if not v["meets"]]
    require(not short, f"scaling under {matrix_gates.SCALING_GATE}x: {short}")
    slow = [k for k, v in v1.items() if not v.get("ratio") or v["ratio"] <= 1.0]
    require(not slow, f"a v1 workload without a faster v2 method: {slow}")
    low = min(min(v["e2e"]) if isinstance(v.get("e2e"), list) else 99 for v in scaling.values())
    return (f"re-derived from the raw records, as gates.json: scaling met in every pair of {len(scaling)} workloads; "
            f"{len(v1)} v1 workloads beaten ({min(v['ratio'] for v in v1.values())}x to "
            f"{max(v['ratio'] for v in v1.values())}x)" + ("" if low == 99 else ""))


def check_ecg(manifest: dict) -> str:
    rows = {}
    for e in manifest["entries"]:
        if e["status"] != "captured" or not e["case"].endswith("_stages"):
            continue
        for window in ("e2e", "kernel"):
            chunks = matrix_ecg_overlap.stages(e.get("extras", []), window)
            if chunks:
                require([c["chunk"] for c in chunks] == list(range(len(chunks))), f"{e['id']}: chunks out of order")
                rows[(e["id"], window)] = (e["case"], matrix_ecg_overlap.overlap(chunks)["label"])
    require(rows, "no stamped ECG window")
    two_hart = [label for case, label in rows.values() if case.startswith("ecg_pipeline_overlap")]
    v1 = [label for case, label in rows.values() if case.startswith("ecg_pipeline_v1")]
    require(two_hart and all(label == "overlapped" for label in two_hart),
            f"a two-hart pipeline's window not overlapped: {sorted(set(two_hart))}")
    require(all(label in ("overlapped", "sequential per chunk") for label in v1), "v1's pipeline neither")
    return (f"{len(two_hart)} two-hart pipeline windows overlapped; v1's pipeline: "
            f"{sorted(set(v1))} in its {len(v1)}")


def timing_findings(directory: Path) -> list[str]:
    problems = []
    summary = (directory / "timing_summary.rpt").read_text()
    m = re.search(r"WNS\(ns\).*?\n\s*-+.*?\n\s*(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)"
                  r"\s+(\d+)\s+(\d+)", summary)
    if not m:
        return ["the timing summary is not readable"]
    wns, _, setup_fail, _, whs, _, hold_fail, _, wpws, _, pw_fail, _ = m.groups()
    if float(wns) < 0 or int(setup_fail):
        problems.append(f"setup: WNS {wns} ns, {setup_fail} failing endpoints")
    if float(whs) < 0 or int(hold_fail):
        problems.append(f"hold: WHS {whs} ns, {hold_fail} failing endpoints")
    if float(wpws) < 0 or int(pw_fail):
        problems.append(f"pulse width: WPWS {wpws} ns, {pw_fail} failing endpoints")
    route = (directory / "route_status.rpt").read_text()
    routable = int(re.search(r"# of routable nets\.+ :\s+(\d+)", route).group(1))
    routed = int(re.search(r"# of fully routed nets\.+ :\s+(\d+)", route).group(1))
    errors = int(re.search(r"# of nets with routing errors\.+ :\s+(\d+)", route).group(1))
    if routed != routable or errors:
        problems.append(f"routing: {routed} of {routable} nets routed, {errors} with errors")
    drc = (directory / "drc.rpt").read_text()
    bad = re.findall(r"^\|\s*(\S+)\s*\|\s*(Error|Critical Warning|Advisory Error)\s*\|", drc, re.M)
    if bad:
        problems.append(f"DRC: {bad[:3]}")
    if not re.search(r"Design State\s*:\s*Fully Routed", drc):
        problems.append("DRC: not of the fully routed design")
    method = (directory / "methodology.rpt").read_text()
    found = re.search(r"Checks found:\s*(\d+)", method)
    if not found or int(found.group(1)):
        problems.append(f"methodology: {found.group(1) if found else '?'} checks found")
    use = (directory / "utilization_routed.rpt").read_text()
    for label in ("Slice LUTs", "Block RAM Tile", "DSPs"):
        row = re.search(rf"^\|\s*{re.escape(label)}\s*\|.*\|\s*([\d.]+)\s*\|\s*$", use, re.M)
        if not row:
            problems.append(f"utilization: no {label} row")
        elif float(row.group(1)) > AREA_LIMIT:
            problems.append(f"utilization: {label} at {row.group(1)}%, over {AREA_LIMIT}%")
    return problems


def check_timing(bundle: Path) -> str:
    problems = timing_findings(bundle / "timing/final-build")
    require(not problems, "; ".join(problems))
    return "setup, hold and pulse width met with no failing endpoint; every net routed; no DRC error; " \
           "no methodology violation; LUTs, block RAM and DSPs under 80%"


def board_runs(board: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(board.glob("boot*/comparison.json"))]


def check_board(manifest: dict, board: Path, final_bitstream_sha: str) -> str:
    require(board.exists(), f"no board evidence ({board})")
    runs = board_runs(board)
    require(len(runs) >= 2, f"{len(runs)} complete loads of the final image, not two")
    want = sorted(e["id"] for e in manifest["entries"] if e["status"] == "captured" and e["sim"] == "soc_dev")
    for n, run in enumerate(runs, 1):
        ids = sorted(r["id"] for r in run["rows"])
        require(ids == want, f"load {n}: its programs are not the bundle's R programs ({len(ids)} of {len(want)})")
        bad = [r["id"] for r in run["rows"] if not r["ok"]]
        require(not bad and not run["problems"], f"load {n}: {len(bad)} programs differ ({bad[:3]}); "
                                                 f"{run['problems'][:2]}")
        require(run["bitstream_sha256"] == final_bitstream_sha, f"load {n}: another bitstream")
        mhz = run["clock"].get("measured_mhz", 0)
        require(run["clock"].get("mhz") == 100.0 and 99.0 <= mhz <= 101.0, f"load {n}: FCLK0 {run['clock']}")
    return f"{len(runs)} loads of the final image: all {len(want)} R programs end as simulated on each, at 100 MHz"


def check_coremark(board: Path) -> str:
    manifest = json.loads((board / "coremark/manifest.json").read_text())
    entry = manifest["entries"][0]
    require(entry["status"] == "captured" and entry.get("oracle", "").startswith("CoreMark 1.0:"),
            f"the official run's simulation: {entry['status']} {entry.get('oracle') or entry.get('failure', '')[-120:]}")
    runs = [json.loads(p.read_text()) for p in sorted(board.glob("coremark/boot*/comparison.json"))]
    require(len(runs) >= 2, f"{len(runs)} board loads of the official run, not two")
    for n, run in enumerate(runs, 1):
        require(run["rows"] and all(r["ok"] for r in run["rows"]) and not run["problems"],
                f"load {n}: the official run differs on the board")
    return f"{entry['oracle'].split(';')[0]}, matched on the board on {len(runs)} loads"


# ---- the audit, and its planted faults ----

def run_checks(bundle: Path, board: Path) -> list[tuple[str, bool, str]]:
    results = []

    def attempt(name, fn, *args):
        try:
            results.append((name, True, fn(*args)))
        except Failure as failure:
            results.append((name, False, str(failure)))
        except Exception as error:                      # (an unreadable input fails its check, never the audit)
            results.append((name, False, f"{type(error).__name__}: {error}"))

    attempt("sums", check_sums, bundle, board)
    try:
        manifest = load_manifest(bundle)
        archives = load_archives(bundle, manifest)
        gates = json.loads((bundle / "gates.json").read_text())
    except Exception as error:
        return results + [("bundle", False, f"unreadable: {error}")]
    final_sha = (board / "bitstream.sha256").read_text().split()[0] if (board / "bitstream.sha256").exists() else ""
    attempt("source", check_source, manifest)
    attempt("manifest", check_manifest, manifest)
    attempt("records", check_records, manifest, archives)
    attempt("totals", check_totals, manifest, archives)
    attempt("gates", check_gates, manifest, archives, gates)
    attempt("ecg", check_ecg, manifest)
    attempt("timing", check_timing, bundle)
    attempt("board", check_board, manifest, board, final_sha)
    attempt("coremark", check_coremark, board)
    return results


def planted(name: str, fn) -> tuple[str, bool]:
    """A check on a planted fault: it must fail."""
    try:
        fn()
    except Failure:
        return name, True
    except Exception:
        return name, False
    return name, False


def self_test(bundle: Path, board: Path) -> list[tuple[str, bool]]:
    import shutil
    import tempfile
    manifest = load_manifest(bundle)
    archives = load_archives(bundle, manifest)
    gates = json.loads((bundle / "gates.json").read_text())
    captured = [e for e in manifest["entries"] if e["status"] == "captured"]
    npu = next(e for e in captured if e["family"] == "npu_gemm" and e["method"] == "npu")
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        copied = Path(tmp) / "bundle"
        shutil.copytree(bundle, copied)
        (copied / "gates.json").write_text((copied / "gates.json").read_text() + " ")
        out.append(planted("sums: a file changed", lambda: check_sums(copied, Path(tmp) / "none")))
        unlisted = Path(tmp) / "unlisted"
        shutil.copytree(bundle, unlisted)
        (unlisted / "extra.txt").write_text("x")
        out.append(planted("sums: a file not listed", lambda: check_sums(unlisted, Path(tmp) / "none")))
    m = copy.deepcopy(manifest)
    m["source"]["dirty"] = True
    out.append(planted("source: a dirty tree", lambda: check_source(m)))
    m = copy.deepcopy(manifest)
    m["source"]["revision"] = "0" * 40
    out.append(planted("source: not a commit", lambda: check_source(m)))
    m = copy.deepcopy(manifest)
    m["entries"].append(dict(captured[0], id="planted/failed", status="failed"))
    out.append(planted("manifest: a failed entry", lambda: check_manifest(m)))
    m = copy.deepcopy(manifest)
    m["entries"] = [e for e in m["entries"] if e["family"] != "dma"]
    out.append(planted("manifest: a family missing", lambda: check_manifest(m)))
    a = copy.deepcopy(archives)
    del a[captured[0]["archive"]][captured[0]["records"]]
    out.append(planted("records: a raw record missing", lambda: check_records(manifest, a)))
    a = copy.deepcopy(archives)
    member = captured[0]["records"]
    a[captured[0]["archive"]][member] = a[captured[0]["archive"]][member].replace(",status=PASS,", ",status=FAIL,", 1)
    out.append(planted("records: a record FAIL", lambda: check_records(manifest, a)))
    a = copy.deepcopy(archives)
    member = npu["records"]
    text = a[npu["archive"]][member]
    value = int(re.search(r",f_accepted_n=(\d+),", text).group(1))
    a[npu["archive"]][member] = text.replace(f",f_accepted_n={value},", f",f_accepted_n={value + 1},", 1)
    out.append(planted("totals: the NPU's requests misattributed", lambda: check_totals(
        dict(manifest, entries=[npu]), a)))
    g = copy.deepcopy(gates)
    first = next(iter(g["against_v1"]))
    g["against_v1"][first]["ratio"] = 0.5
    out.append(planted("gates: gates.json not the records'", lambda: check_gates(manifest, archives, g)))
    a = copy.deepcopy(archives)
    two = next(e for e in captured if e["case"] == "reduce_fill" and e["method"] == "multicore"
               and e["sim"] == "soc_dev" and e["axes"].get("cache_state") == "warm")
    text = a[two["archive"]][two["records"]]
    a[two["archive"]][two["records"]] = re.sub(r",h0_cycles=(\d+),", lambda mm: f",h0_cycles={int(mm.group(1)) * 2},",
                                               text)
    out.append(planted("gates: two workers under 1.8x", lambda: check_gates(manifest, a, gates)))
    m = copy.deepcopy(manifest)
    for e in m["entries"]:
        if e["case"] == "ecg_pipeline_overlap_stages" and e["status"] == "captured":
            lines, t = [], 0
            for line in e["extras"]:
                if line.startswith("MATRIX_STAGE,"):
                    fields = line.split(",")
                    out_fields = []
                    for f in fields:
                        if "=" in f and f.split("=")[0] in matrix_ecg_overlap.KINDS:
                            out_fields.append(f"{f.split('=')[0]}={t}-{t + 10}")
                            t += 20
                        else:
                            out_fields.append(f)
                    line = ",".join(out_fields)
                lines.append(line)
            e["extras"] = lines
            break
    out.append(planted("ecg: a two-hart pipeline sequential", lambda: check_ecg(m)))
    final = bundle / "timing/final-build"
    for label, old, new in (
            ("timing: negative slack", r"\n(\s*)0\.306(\s+)0\.000(\s+)0 ", r"\n\g<1>-0.120\g<2>-1.200\g<3>12 "),
            ("timing: an unrouted net", r"(# of fully routed nets\.+ :\s+)(\d+)", None),
            ("timing: a DRC error", r"\| DPIP-1 \| Warning ", "| DPIP-1 | Error   "),
            ("timing: a methodology violation", r"Checks found: 0", "Checks found: 3"),
            ("timing: LUTs over 80%", r"(\| Slice LUTs\s+\|.*\|\s*)72\.75(\s*\|)", r"\g<1>81.20\g<2>")):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "timing/final-build"
            shutil.copytree(final, copied)
            for path in copied.iterdir():
                text = path.read_text()
                if new is None:
                    text = re.sub(old, lambda mm: mm.group(1) + str(int(mm.group(2)) - 1), text)
                else:
                    text = re.sub(old, new, text)
                path.write_text(text)
            out.append(planted(label, lambda: check_timing(Path(tmp))))
    rows = [dict(id=e["id"], ok=True) for e in captured if e["sim"] == "soc_dev"]
    good = dict(rows=rows, problems=[], bitstream_sha256="b", clock=dict(mhz=100.0, measured_mhz=100.0))
    with tempfile.TemporaryDirectory() as tmp:
        for boot in ("boot1", "boot2"):
            (Path(tmp) / boot).mkdir()
            (Path(tmp) / boot / "comparison.json").write_text(json.dumps(good))
        sound = planted("board: (the planted runs, sound, must pass)", lambda: check_board(manifest, Path(tmp), "b"))
        out.append((sound[0], not sound[1]))
        bad = copy.deepcopy(good)
        bad["rows"][0]["ok"] = False
        (Path(tmp) / "boot2" / "comparison.json").write_text(json.dumps(bad))
        out.append(planted("board: a program differs on the board", lambda: check_board(manifest, Path(tmp), "b")))
        (Path(tmp) / "boot2" / "comparison.json").write_text(json.dumps(dict(good, bitstream_sha256="c")))
        out.append(planted("board: another bitstream", lambda: check_board(manifest, Path(tmp), "b")))
        (Path(tmp) / "boot2" / "comparison.json").unlink()
        out.append(planted("board: one load only", lambda: check_board(manifest, Path(tmp), "b")))
    with tempfile.TemporaryDirectory() as tmp:
        cm = Path(tmp) / "coremark"
        cm.mkdir()
        sound = dict(entries=[dict(status="captured", oracle="CoreMark 1.0: 218.738 (2.1874 CoreMark/MHz); ...")])
        (cm / "manifest.json").write_text(json.dumps(sound))
        run = dict(rows=[dict(id="cpu/coremark_official/scalar/soc_dev/cold", ok=True)], problems=[])
        for boot in ("boot1", "boot2"):
            (cm / boot).mkdir()
            (cm / boot / "comparison.json").write_text(json.dumps(run))
        ok = planted("coremark: (the planted runs, sound, must pass)", lambda: check_coremark(Path(tmp)))
        out.append((ok[0], not ok[1]))
        (cm / "manifest.json").write_text(json.dumps(dict(entries=[dict(status="failed", failure="ten seconds")])))
        out.append(planted("coremark: the official run not validated", lambda: check_coremark(Path(tmp))))
        (cm / "manifest.json").write_text(json.dumps(sound))
        (cm / "boot2" / "comparison.json").write_text(json.dumps(dict(run, rows=[dict(run["rows"][0], ok=False)])))
        out.append(planted("coremark: differs on the board", lambda: check_coremark(Path(tmp))))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    parser.add_argument("--board", type=Path, default=BOARD)
    parser.add_argument("--self-test", action="store_true", help="plant a fault for each check; each must fail")
    args = parser.parse_args()
    if args.self_test:
        results = self_test(args.bundle, args.board)
        for name, caught in results:
            print(f"{'caught' if caught else 'MISSED'}: {name}")
        missed = [name for name, caught in results if not caught]
        print(f"{'PASS' if not missed else 'FAIL'}: the audit's self-test, {len(results) - len(missed)} of "
              f"{len(results)} planted faults caught")
        return 0 if not missed else 1
    results = run_checks(args.bundle, args.board)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    failed = [name for name, ok, _ in results if not ok]
    print(f"{'PASS' if not failed else 'incomplete'}: Phase 20's closeout audit"
          + ("" if not failed else f" ({', '.join(failed)} failed)"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
