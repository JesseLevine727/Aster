#!/usr/bin/env python3
"""Phase 20's closeout audit (docs/tuning.md §9; phase17-plus.md's rule): read-only, over the final bundle
(docs/results/phase20/matrix-20.5) and the board run's evidence (docs/results/phase20/board-20.5).

It rejects, each as a check that can fail (--self-test plants a fault for each and shows it caught, for its own
reason):
  - sums:     an evidence file whose hash is not its SHA256SUMS line, or a file not listed;
  - source:   a bundle not bound to its commit: the capture's revision not a commit of this repository, not an
              ancestor of HEAD, or made from a dirty tree;
  - manifest: missing required workloads: an entry the families' planners (scripts/matrix.py, with the bundle's
              2 and 4 KiB caches) give that the bundle lacks, or a captured entry they do not give; a failed or
              planned entry; a planned entry unsupported where its planner runs it (but for a layout that does not
              fit main memory), or not with the planner's reason; determinism or cold/warm pairs that differ;
  - records:  a captured entry whose raw records or console are missing from its archive, or a record that fails
              AsterBench v12's validator or is not PASS (every one re-validated from the archives);
  - totals:   wrong counter attribution: the NPU's bytes against the fabric's and the engines' cycle bounds
              (scripts/matrix.py's reconcile_totals, re-run on every record and its MATRIX_JOBS line from the raw
              console), with the manifest's totals;
  - gates:    the scaling and v1 gates re-derived from the raw records (scripts/matrix_gates.py), each met, and
              equal to the bundle's gates.json;
  - ecg:      soc.md §11's ECG gate re-derived from the stamped twins' raw consoles (scripts/matrix_ecg_overlap.py),
              every window of each: the two-hart pipelines overlapped, v1's sequential per chunk;
  - timing:   the final build's own reports: the final image's configuration-data hash not timing.txt's; a clock
              other than clk_fpga_0 at 10 ns, a check_timing finding,
              negative slack or failing endpoints (setup, hold, pulse width), unrouted nets or routing errors, DRC
              errors or critical warnings, methodology violations, LUTs, block RAM or DSPs over 80%;
  - board:    any R program of the bundle that does not end on the board as simulated, on each of two loads of
              the final image: its bitstream (the bundle's final-build hash), its identity (the R entries'
              configuration words), FCLK0 measured at 100 MHz; status, tohost's cycle, the console byte for byte (against the bundle's raw console), the
              console's lag, and hart 0's instructions retired at tohost and the window's cycles and instructions
              (against the reference: each program simulated as the board runs it, on the capture's simulation);
  - coremark: CoreMark's official run, its performance run (the score) and its validation run (CoreMark's run
              rules ask both to validate): its capture not from a clean ancestor commit or on another simulation,
              either run's record or report not validated by its oracle (re-run here), or either not matched on two
              loads of the final image as the board check matches.
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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asterbench_v12  # noqa: E402
import matrix  # noqa: E402
import matrix_ecg_overlap  # noqa: E402
import matrix_gates  # noqa: E402

BUNDLE = ROOT / "docs/results/phase20/matrix-20.5"
BOARD = ROOT / "docs/results/phase20/board-20.5"
FAMILIES = ("cpu", "coherence", "dsp", "ml", "ecg", "memory", "npu_gemm", "dma")
CACHES = (2, 4)
AREA_LIMIT = 80.0
CLOCKS = {"clk_fpga_0": 10.0}
LAG_LIMIT = 8192
REFERENCED = ("tohost_retired", "window_cycles", "window_retired")
COREMARK_IDS = ("cpu/coremark_official/scalar/soc_dev/cold", "cpu/coremark_validation/scalar/soc_dev/cold")


class Failure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def read_json(path: Path):
    return json.loads(lzma.open(path).read() if path.suffix == ".xz" else path.read_text())


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


def console_name(ident: str) -> str:
    return "console/" + ident.replace("/", "__") + ".console"


def entry_texts(entry: dict, archives: dict) -> tuple[str | None, str | None]:
    files = archives.get(entry.get("archive"), {})
    return files.get(entry.get("records")), files.get(console_name(entry["id"]))


def r_entries(manifest: dict) -> dict:
    return {e["id"]: e for e in manifest["entries"] if e["status"] == "captured" and e["sim"] == "soc_dev"}


def final_bitstream_sha(bundle: Path) -> str:
    return (bundle / "timing/final-build/bitstream.sha256").read_text().split()[0]


# ---- the checks ----

def check_sums(bundle: Path, board: Path) -> str:
    problems = sums_problems(bundle) + (sums_problems(board) if board.exists() else [])
    require(not problems, f"{len(problems)} files: {problems[:3]}")
    return "every file of the bundle and the board's evidence matches its SHA256SUMS"


def clean_ancestor(source: dict, what: str) -> str:
    rev, dirty = source["revision"], source["dirty"]
    require(not dirty, f"{what}: the capture's tree was dirty ({source.get('changed', [])[:3]})")
    known = subprocess.run(["git", "cat-file", "-e", f"{rev}^{{commit}}"], cwd=ROOT,
                           stderr=subprocess.DEVNULL).returncode == 0
    require(known, f"{what}: {rev} is not a commit of this repository")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", rev, "HEAD"], cwd=ROOT,
                              stderr=subprocess.DEVNULL).returncode == 0
    require(ancestor, f"{what}: {rev} is not an ancestor of HEAD")
    return rev


def check_source(manifest: dict) -> str:
    rev = clean_ancestor(manifest["source"], "the bundle")
    return f"the capture is {rev[:7]}'s, a clean tree, an ancestor of HEAD"


def planned_ids() -> dict:
    """Every entry the families' planners give, with the reason of those they mark unsupported (None: run)."""
    entries = [e for f in FAMILIES for e in matrix.FAMILIES[f]()]
    entries += matrix.with_caches(entries, list(CACHES))
    return {e.id: (e.reason if e.status == "unsupported" else None) for e in entries}


def check_manifest(manifest: dict, planned: dict) -> str:
    require(set(manifest["families"]) == set(FAMILIES), f"families {sorted(manifest['families'])}")
    counts = {}
    for e in manifest["entries"]:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    require(counts.get("failed", 0) == 0 and counts.get("planned", 0) == 0, f"entries {counts}")
    ids = {e["id"] for e in manifest["entries"]}
    missing = sorted(set(planned) - ids)
    require(not missing, f"{len(missing)} planned entries missing: {missing[:3]}")
    for e in manifest["entries"]:
        if e["id"] not in planned:
            continue
        reason = planned[e["id"]]
        if reason is not None:
            require(e["status"] == "unsupported" and e.get("reason") == reason,
                    f"{e['id']}: {e['status']}, its planner marks it unsupported ({reason})")
        elif e["status"] == "unsupported":
            require(e.get("reason") == f"layout {e['axes'].get('layout')} does not fit main memory",
                    f"{e['id']}: unsupported ({e.get('reason')}), its planner runs it")
    stray = sorted(e["id"] for e in manifest["entries"] if e["status"] == "captured" and e["id"] not in planned)
    require(not stray, f"{len(stray)} captured entries no planner gives: {stray[:3]}")
    require(all(e.get("reason") for e in manifest["entries"] if e["status"] == "unsupported"),
            "an unsupported entry without its reason")
    for key in ("determinism", "cold_warm_pairs"):
        d = manifest[key]
        require(d["checked"] > 0 and d["identical"] == d["checked"] and not d["differ"], f"{key}: {d}")
    return (f"all {len(planned)} planned entries present, each as its planner marks it; the manifest {counts}; "
            f"determinism "
            f"{manifest['determinism']['identical']}/{manifest['determinism']['checked']}, cold/warm pairs "
            f"{manifest['cold_warm_pairs']['identical']}/{manifest['cold_warm_pairs']['checked']}")


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
    sums = {}
    for e in manifest["entries"]:
        if e["status"] != "captured":
            continue
        records, extras = parsed_records(e, archives)
        try:
            got = matrix.reconcile_totals(records, extras)
        except Exception as error:
            raise Failure(f"{e['id']}: {error}") from None
        for k, v in got.items():
            sums[k] = sums.get(k, 0) + v
    require(sums == manifest["totals"], f"totals {sums}, the manifest says {manifest['totals']}")
    return f"the NPU's and the DMA's totals and bounds hold in every record: {sums}"


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


def derived_gates(manifest: dict, archives: dict) -> dict:
    allrec = allrec_of(manifest, archives)
    return json.loads(json.dumps(dict(scaling=matrix_gates.scaling(allrec), against_v1=matrix_gates.against_v1(allrec))))


def check_gates(manifest: dict, archives: dict, gates: dict) -> str:
    derived = derived_gates(manifest, archives)
    require(derived["scaling"] == gates["scaling"], "the scaling gate differs from gates.json")
    require(derived["against_v1"] == gates["against_v1"], "the v1 gate differs from gates.json")
    scaling, v1 = derived["scaling"], derived["against_v1"]
    short = [k for k, v in scaling.items() if not v["meets"]]
    require(not short, f"scaling under {matrix_gates.SCALING_GATE}x: {short}")
    slow = [k for k, v in v1.items() if not v.get("ratio") or v["ratio"] <= 1.0]
    require(not slow, f"a v1 workload without a faster v2 method: {slow}")
    return (f"re-derived from the raw records, as gates.json: scaling {min(v['e2e_min'] for v in scaling.values())}x "
            f"to {max(v['e2e_max'] for v in scaling.values())}x in {len(scaling)} workloads, every configuration; "
            f"{len(v1)} v1 workloads beaten ({min(v['ratio'] for v in v1.values())}x to "
            f"{max(v['ratio'] for v in v1.values())}x)")


def check_ecg(manifest: dict, archives: dict) -> str:
    rows = {}
    for e in manifest["entries"]:
        if e["status"] != "captured" or not e["case"].endswith("_stages"):
            continue
        text, console = entry_texts(e, archives)
        require(text is not None and console is not None, f"{e['id']}: no raw console")
        windows = {dict(t.split("=", 1) for t in line.split(",")[1:])["window"]
                   for line in text.splitlines() if line.startswith("ASTERBENCH,")}
        lines = [line for line in console.splitlines() if line.startswith("MATRIX_STAGE,")]
        stamped = {w for w in ("e2e", "kernel") if matrix_ecg_overlap.stages(lines, w)}
        require(stamped == windows, f"{e['id']}: stage lines in {sorted(stamped)}, records in {sorted(windows)}")
        for window in sorted(windows):
            chunks = matrix_ecg_overlap.stages(lines, window)
            require([c["chunk"] for c in chunks] == list(range(len(chunks))), f"{e['id']}: chunks out of order")
            rows[(e["id"], window)] = (e["case"], matrix_ecg_overlap.overlap(chunks)["label"])
    two_hart = [label for case, label in rows.values() if case.startswith("ecg_pipeline_overlap")]
    v1 = [label for case, label in rows.values() if case.startswith("ecg_pipeline_v1")]
    require(two_hart and v1, f"stamped windows: {len(two_hart)} two-hart, {len(v1)} v1")
    require(all(label == "overlapped" for label in two_hart),
            f"a two-hart pipeline's window not overlapped: {sorted(set(two_hart))}")
    require(all(label in ("overlapped", "sequential per chunk") for label in v1),
            f"v1's pipeline neither overlapped nor sequential per chunk: {sorted(set(v1))}")
    return (f"from the raw consoles, every window of every stamped twin: {len(two_hart)} two-hart pipeline windows "
            f"overlapped; v1's pipeline {sorted(set(v1))} in its {len(v1)}")


def timing_findings(directory: Path) -> list[str]:
    problems = []
    summary = (directory / "timing_summary.rpt").read_text()
    clocks = dict(re.findall(r"^\s*(\S+)\s+\{[\d. ]+\}\s+([\d.]+)\s+[\d.]+\s*$",
                             summary.split("| Clock Summary", 1)[-1].split("| Intra Clock Table", 1)[0], re.M))
    if {k: float(v) for k, v in clocks.items()} != CLOCKS:
        problems.append(f"clocks {clocks}, not {CLOCKS}")
    findings = re.findall(r"^\s*There are (\d+) (.*)$", summary, re.M)
    if len(findings) < 10:
        problems.append(f"check_timing: {len(findings)} findings read")
    problems += [f"check_timing: {n} {what}" for n, what in findings if int(n)]
    if "All user specified timing constraints are met." not in summary:
        problems.append("the summary does not say every constraint is met")
    m = re.search(r"WNS\(ns\).*?\n\s*-+.*?\n\s*(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)"
                  r"\s+(\d+)\s+(\d+)", summary)
    if not m:
        return problems + ["the timing summary is not readable"]
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


def config_hashes(bundle: Path) -> tuple[list[str], list[str]]:
    """The final image's configuration-data hash as bitstream.sha256 records it, and as timing.txt does."""
    pattern = r"configuration data[^\n]*?sha256 ([0-9a-f]{64})"
    return (re.findall(pattern, (bundle / "timing/final-build/bitstream.sha256").read_text()),
            re.findall(pattern, (bundle / "timing/timing.txt").read_text()))


def check_timing(bundle: Path) -> str:
    recorded, timing = config_hashes(bundle)
    require(len(recorded) == 1 and recorded[0] in timing,
            f"the final image's configuration data {recorded} is not timing.txt's {timing}")
    problems = timing_findings(bundle / "timing/final-build")
    require(not problems, "; ".join(problems))
    return "clk_fpga_0 at 10 ns, no check_timing finding; setup, hold and pulse width met with no failing " \
           "endpoint; every net routed; no DRC error; no methodology violation; LUTs, block RAM and DSPs under 80%"


def reference_rows(reference: dict, entries: dict) -> dict:
    """The reference's rows, checked against the capture: the same simulation, and each program's status and
    tohost's cycle as captured."""
    require(not reference["problems"], f"the reference: {reference['problems'][:2]}")
    rows = {r["id"]: r for r in reference["rows"]}
    require(set(rows) == set(entries), f"the reference has {len(rows)} programs, not the {len(entries)}")
    for ident, e in entries.items():
        require(e["sim_sha256"] == reference["sim_sha256"], f"{ident}: captured on another simulation")
        row = rows[ident]
        require((row["status"], row.get("tohost_cycles")) == (e["soc"]["status"], e["soc"]["cycles"]),
                f"{ident}: the reference ends {row['status']} at {row.get('tohost_cycles')}, captured "
                f"{e['soc']['status']} at {e['soc']['cycles']}")
    return rows


def load_problems(report: dict, entries: dict, consoles: dict, reference: dict, final_sha: str) -> list[str]:
    """One load of the final image against the programs' simulations (tuning.md §7.2)."""
    problems = []
    if report.get("status") != "complete":
        problems.append(f"the run is {report.get('status')}: {report.get('error')}")
    if report.get("bitstream_sha256") != final_sha:
        problems.append(f"another bitstream ({str(report.get('bitstream_sha256'))[:12]})")
    clock, identity, design = report.get("clock", {}), report.get("identity", {}), report.get("design", {})
    if clock.get("mhz") != 100.0 or not 99.0 <= clock.get("measured_mhz", 0) <= 101.0:
        problems.append(f"FCLK0: {clock}")
    if identity.get("clk_hz") != 100_000_000 or any(identity.get(k) != design.get(k)
                                                   for k in ("magic", "main_bytes", "npu_config", "soc_config")):
        problems.append(f"the identity {identity} against the design {design}")
    words = {(e["soc"].get("npu_config"), e["soc"].get("soc_config")) for e in entries.values()}
    if words != {(identity.get("npu_config"), identity.get("soc_config"))}:
        problems.append(f"the identity's configuration words {identity} are not the simulations' {sorted(words)}")
    got = {p["name"]: p for p in report.get("programs", [])}
    if set(got) != set(entries):
        problems.append(f"{len(set(entries) - set(got))} programs not run, {len(set(got) - set(entries))} others")
    for ident, e in entries.items():
        p = got.get(ident)
        if p is None:
            continue
        issues = []
        if p.get("status") != e["soc"]["status"] or p.get("tohost_cycles") != e["soc"]["cycles"]:
            issues.append(f"{p.get('status')} at {p.get('tohost_cycles')}, simulated {e['soc']['status']} at "
                          f"{e['soc']['cycles']}")
        if p.get("console") != consoles[ident]:
            issues.append("the console differs")
        if p.get("max_lag") is None or p["max_lag"] > LAG_LIMIT:
            issues.append(f"console lag {p.get('max_lag')}")
        issues += [f"{k} {p.get(k)}, simulated {reference[ident].get(k)}" for k in REFERENCED
                   if p.get(k) is None or p.get(k) != reference[ident].get(k)]
        problems += [f"{ident}: {i}" for i in issues]
    return problems


def check_loads(loads: list[dict], entries: dict, consoles: dict, reference: dict, final_sha: str, what: str) -> int:
    require(len(loads) >= 2, f"{what}: {len(loads)} complete loads of the final image, not two")
    for n, report in enumerate(loads, 1):
        problems = load_problems(report, entries, consoles, reference, final_sha)
        require(not problems, f"{what}, load {n}: {len(problems)} problems: {problems[:3]}")
    timings = [[p.get("load_seconds") for p in report["programs"]] for report in loads]
    require(all(a != b for i, a in enumerate(timings) for b in timings[i + 1:]),
            f"{what}: two loads' reports have the same load times: not separate loads")
    return len(loads)


def check_board(manifest: dict, archives: dict, board: Path, final_sha: str) -> str:
    require(board.exists(), f"no board evidence ({board})")
    entries = r_entries(manifest)
    require(entries, "no R entry in the bundle")
    consoles = {}
    for ident, e in entries.items():
        _, console = entry_texts(e, archives)
        require(console is not None, f"{ident}: no raw console")
        consoles[ident] = console
    reference = reference_rows(read_json(board / "reference.json.xz"), entries)
    loads = [read_json(p) for p in sorted(board.glob("boot*/report.json.xz"))]
    n = check_loads(loads, entries, consoles, reference, final_sha, "the board")
    mhz = [round(r["clock"]["measured_mhz"], 3) for r in loads]
    return (f"{n} loads of the final image (FCLK0 measured {mhz} MHz): all {len(entries)} R programs end as "
            f"simulated on each: status, tohost's cycle and hart 0's retired count, the window, the console")


def coremark_inputs(board: Path) -> tuple[dict, dict, dict, dict, list[dict]]:
    """CoreMark's capture (its manifest, and each run's console and records), its reference and its loads, as the
    board's evidence holds them."""
    capture = board / "coremark/capture"
    manifest = json.loads((capture / "manifest.json").read_text())
    consoles, records = {}, {}
    for e in manifest["entries"]:
        if (capture / console_name(e["id"])).exists():
            consoles[e["id"]] = (capture / console_name(e["id"])).read_text()
        if e.get("records") and (capture / e["records"]).exists():
            records[e["id"]] = (capture / e["records"]).read_text()
    loads = [read_json(p) for p in sorted(board.glob("coremark/boot*/report.json.xz"))]
    return manifest, consoles, records, read_json(board / "coremark/reference.json"), loads


def check_coremark(board: Path, bundle_manifest: dict, final_sha: str) -> str:
    return coremark_verdict(*coremark_inputs(board), bundle_manifest, final_sha)


def coremark_verdict(manifest: dict, consoles: dict, records_text: dict, reference: dict, loads: list[dict],
                     bundle_manifest: dict, final_sha: str) -> str:
    clean_ancestor(manifest["source"], "CoreMark's capture")
    entries = {e["id"]: e for e in manifest["entries"]}
    require(set(entries) == set(COREMARK_IDS), f"CoreMark's capture holds {sorted(entries)[:3]}, not both runs")
    r_sims = {x["sim_sha256"] for x in r_entries(bundle_manifest).values()}
    verdicts = []
    for ident in COREMARK_IDS:
        e = entries[ident]
        require(e["status"] == "captured", f"{ident}: its simulation is {e['status']}")
        require({e["sim_sha256"]} == r_sims, f"{ident}: CoreMark's simulation is not the bundle's R simulation")
        require(ident in consoles and ident in records_text, f"{ident}: its console or records are missing")
        lines = [l for l in records_text[ident].splitlines() if l.startswith("ASTERBENCH,")]
        try:
            records = [asterbench_v12.validate_line(line + "\n") for line in lines]
            verdicts.append(matrix.oracle_coremark_official(SimpleNamespace(case=e["case"]), records,
                                                            consoles[ident]))
        except Exception as error:
            raise Failure(f"{ident}: {error}") from None
        require(all(r["status"] == "PASS" for r in records), f"{ident}: its record is not PASS")
    reference = reference_rows(reference, entries)
    n = check_loads(loads, entries, consoles, reference, final_sha, "CoreMark")
    return f"{verdicts[0].split(';')[0]}; the validation run validated; both as simulated on {n} loads of the final image"


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
        final_sha = final_bitstream_sha(bundle)
    except Exception as error:
        return results + [("bundle", False, f"unreadable: {error}")]
    attempt("source", check_source, manifest)
    attempt("manifest", check_manifest, manifest, planned_ids())
    attempt("records", check_records, manifest, archives)
    attempt("totals", check_totals, manifest, archives)
    attempt("gates", check_gates, manifest, archives, gates)
    attempt("ecg", check_ecg, manifest, archives)
    attempt("timing", check_timing, bundle)
    attempt("board", check_board, manifest, archives, board, final_sha)
    attempt("coremark", check_coremark, board, manifest, final_sha)
    return results


def planted(name: str, fn, reason: str) -> tuple[str, bool, str]:
    """A check on a planted fault: it must fail, for the planted reason (its message contains `reason`)."""
    try:
        fn()
    except Failure as failure:
        return name, reason in str(failure), str(failure)[:160]
    except Exception as error:
        return name, False, f"{type(error).__name__}: {error}"[:160]
    return name, False, "passed"


def sound(name: str, fn) -> tuple[str, bool, str]:
    """A check on a sound input, made by the self-test: it must pass (so the planted faults beside it count)."""
    try:
        fn()
    except Exception as error:
        return name, False, f"{type(error).__name__}: {error}"[:160]
    return name, True, "passed"


def synthetic_loads(entries: dict, consoles: dict, sha: str) -> tuple[dict, list[dict]]:
    """A reference and two loads made to match the bundle: the self-test's sound board evidence."""
    rows = {i: dict(id=i, status=e["soc"]["status"], tohost_cycles=e["soc"]["cycles"], tohost_retired=n + 7,
                    window_cycles=n + 11, window_retired=n + 5) for n, (i, e) in enumerate(sorted(entries.items()))}
    words = sorted({(e["soc"].get("npu_config"), e["soc"].get("soc_config")) for e in entries.values()})[0]
    design = dict(magic=0x41535432, main_bytes=0x18000, npu_config=words[0], soc_config=words[1])
    loads = [dict(status="complete", bitstream_sha256=sha, clock=dict(mhz=100.0, measured_mhz=99.998),
                  identity=dict(design, clk_hz=100_000_000), design=dict(design),
                  programs=[dict(name=i, status=r["status"], tohost_cycles=r["tohost_cycles"], console=consoles[i],
                                 max_lag=100, load_seconds=0.2 + boot / 100,
                                 **{k: r[k] for k in REFERENCED}) for i, r in rows.items()])
             for boot in (1, 2)]
    return rows, loads


def self_test(bundle: Path, board: Path) -> list[tuple[str, bool, str]]:
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
        out.append(planted("sums: a file changed", lambda: check_sums(copied, Path(tmp) / "none"),
                           "gates.json: its hash"))
        unlisted = Path(tmp) / "unlisted"
        shutil.copytree(bundle, unlisted)
        (unlisted / "extra.txt").write_text("x")
        out.append(planted("sums: a file not listed", lambda: check_sums(unlisted, Path(tmp) / "none"),
                           "extra.txt: not in SHA256SUMS"))
    m = copy.deepcopy(manifest)
    m["source"]["dirty"] = True
    out.append(planted("source: a dirty tree", lambda: check_source(m), "dirty"))
    m = copy.deepcopy(manifest)
    m["source"]["revision"] = "0" * 40
    out.append(planted("source: not a commit", lambda: check_source(m), "is not a commit"))
    planned = planned_ids()
    out.append(sound("manifest: (the bundle as it is, against the planners)", lambda: check_manifest(manifest, planned)))
    m = copy.deepcopy(manifest)
    m["entries"].append(dict(captured[0], id="planted/failed", status="failed"))
    out.append(planted("manifest: a failed entry", lambda: check_manifest(m, planned), "'failed': 1"))
    m = copy.deepcopy(manifest)
    m["entries"] = [e for e in m["entries"] if e["id"] != "dsp/fir/npu/soc_dev/warm/k64"]
    out.append(planted("manifest: one sweep point missing", lambda: check_manifest(m, planned),
                       "1 planned entries missing"))
    m = copy.deepcopy(manifest)
    m["entries"].append(dict(captured[0], id="cpu/planted/scalar/soc_dev/warm"))
    out.append(planted("manifest: a captured entry no planner gives", lambda: check_manifest(m, planned),
                       "no planner gives"))
    m = copy.deepcopy(manifest)
    run = next(e for e in m["entries"] if e["status"] == "captured" and planned.get(e["id"], 0) is None)
    run.update(status="unsupported", reason="planted")
    out.append(planted("manifest: an entry its planner runs recorded unsupported", lambda: check_manifest(m, planned),
                       "its planner runs it"))
    m = copy.deepcopy(manifest)
    m["determinism"] = dict(m["determinism"], identical=m["determinism"]["checked"] - 1, differ=["planted"])
    out.append(planted("manifest: a determinism repeat differs", lambda: check_manifest(m, planned), "determinism"))
    a = copy.deepcopy(archives)
    del a[captured[0]["archive"]][captured[0]["records"]]
    out.append(planted("records: a raw record missing", lambda: check_records(manifest, a), "raw records are not in"))
    a = copy.deepcopy(archives)
    member = captured[0]["records"]
    a[captured[0]["archive"]][member] = a[captured[0]["archive"]][member].replace(",status=PASS,", ",status=FAIL,", 1)
    out.append(planted("records: a record FAIL", lambda: check_records(manifest, a), "status"))
    a = copy.deepcopy(archives)
    member = npu["records"]
    text = a[npu["archive"]][member]
    value = int(re.search(r",f_accepted_n=(\d+),", text).group(1))
    a[npu["archive"]][member] = text.replace(f",f_accepted_n={value},", f",f_accepted_n={value + 1},", 1)
    out.append(planted("totals: the NPU's requests misattributed", lambda: check_totals(
        dict(manifest, entries=[npu], totals=dict(npu_fabric=1, fabric_bounds=1, npu_one_job=1, dma_one_job=0)), a),
        "the fabric accepted"))
    m = copy.deepcopy(manifest)
    m["totals"] = dict(m["totals"], dma_one_job=m["totals"]["dma_one_job"] + 1)
    out.append(planted("totals: the manifest's totals not the records'", lambda: check_totals(m, archives),
                       "the manifest says"))
    g = copy.deepcopy(gates)
    first = next(iter(g["against_v1"]))
    g["against_v1"][first]["ratio"] = 0.5
    out.append(planted("gates: gates.json not the records'", lambda: check_gates(manifest, archives, g),
                       "the v1 gate differs"))
    a = copy.deepcopy(archives)
    two = next(e for e in captured if e["case"] == "reduce_fill" and e["method"] == "multicore"
               and e["sim"] == "soc_dev" and e["axes"].get("cache_state") == "warm")
    text = a[two["archive"]][two["records"]]
    a[two["archive"]][two["records"]] = re.sub(r",h0_cycles=(\d+),", lambda mm: f",h0_cycles={int(mm.group(1)) * 2},",
                                               text)
    out.append(planted("gates: two workers under 1.8x (gates.json re-derived alike)",
                       lambda: check_gates(manifest, a, derived_gates(manifest, a)), "scaling under"))
    stamped = next(e for e in captured if e["case"] == "ecg_pipeline_overlap_stages" and e["sim"] == "soc_dev"
                   and e["axes"].get("cache_state") == "warm")
    a = copy.deepcopy(archives)
    name = console_name(stamped["id"])
    lines, t = [], 0
    for line in a[stamped["archive"]][name].splitlines():
        if line.startswith("MATRIX_STAGE,"):
            fields = []
            for f in line.split(","):
                key = f.split("=")[0]
                if "=" in f and key in matrix_ecg_overlap.KINDS:
                    fields.append(f"{key}={t}-{t + 10}")
                    t += 20
                else:
                    fields.append(f)
            line = ",".join(fields)
        lines.append(line)
    a[stamped["archive"]][name] = "\n".join(lines) + "\n"
    out.append(planted("ecg: a two-hart pipeline sequential", lambda: check_ecg(manifest, a), "not overlapped"))
    a = copy.deepcopy(archives)
    a[stamped["archive"]][name] = "\n".join(l for l in a[stamped["archive"]][name].splitlines()
                                            if not l.startswith("MATRIX_STAGE,window=kernel")) + "\n"
    out.append(planted("ecg: a window's stage lines missing", lambda: check_ecg(manifest, a), "stage lines in"))
    final = bundle / "timing/final-build"
    timing_faults = (
        ("timing: negative slack", r"\n(\s*)0\.306(\s+)0\.000(\s+)0 ", r"\n\g<1>-0.120\g<2>-1.200\g<3>12 ", "setup"),
        ("timing: a hold violation", r"(75552\s+)0\.040(\s+)0\.000(\s+)0 ", r"\g<1>-0.010\g<2>-0.020\g<3>2 ", "hold"),
        ("timing: a pulse-width violation", r"(75552\s+)3\.750(\s+)0\.000(\s+)0 ", r"\g<1>-0.100\g<2>-0.100\g<3>1 ",
         "pulse width"),
        ("timing: another clock period", r"(clk_fpga_0\s+\{0\.000 )5\.000\}(\s+)10\.000(\s+)100\.000",
         r"\g<1>5.500}\g<2>11.000\g<3>90.909", "clocks"),
        ("timing: an unconstrained endpoint", r"There are 0 pins that are not constrained for maximum delay\.",
         "There are 4 pins that are not constrained for maximum delay.", "check_timing: 4"),
        ("timing: an unrouted net", r"(# of fully routed nets\.+ :\s+)(\d+)", None, "routing"),
        ("timing: a routing error", r"(# of nets with routing errors\.+ :\s+)0", r"\g<1>2", "routing"),
        ("timing: a DRC error", r"\| DPIP-1 \| Warning ", "| DPIP-1 | Error   ", "DRC"),
        ("timing: a DRC critical warning", r"\| DPIP-1 \| Warning ", "| DPIP-1 | Critical Warning ", "DRC"),
        ("timing: a methodology violation", r"Checks found: 0", "Checks found: 3", "methodology"),
        ("timing: LUTs over 80%", r"(\| Slice LUTs\s+\|.*\|\s*)72\.75(\s*\|)", r"\g<1>81.20\g<2>", "Slice LUTs at"),
        ("timing: block RAM over 80%", r"(\| Block RAM Tile\s+\|.*\|\s*)60\.00(\s*\|)", r"\g<1>85.00\g<2>",
         "Block RAM Tile at"),
        ("timing: DSPs over 80%", r"(\| DSPs\s+\|.*\|\s*)12\.27(\s*\|)", r"\g<1>90.00\g<2>", "DSPs at"))
    for label, old, new, reason in timing_faults:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "timing/final-build"
            shutil.copytree(final, copied)
            shutil.copyfile(bundle / "timing/timing.txt", Path(tmp) / "timing/timing.txt")
            changed = 0
            for path in copied.iterdir():
                text = path.read_text()
                if new is None:
                    edited = re.sub(old, lambda mm: mm.group(1) + str(int(mm.group(2)) - 1), text)
                else:
                    edited = re.sub(old, new, text)
                changed += edited != text
                path.write_text(edited)
            result = planted(label, lambda: check_timing(Path(tmp)), reason)
            out.append(result if changed else (label, False, "the fault was not planted"))
    with tempfile.TemporaryDirectory() as tmp:
        copied = Path(tmp) / "bundle"
        (copied / "timing/final-build").mkdir(parents=True)
        shutil.copytree(bundle / "timing/final-build", copied / "timing/final-build", dirs_exist_ok=True)
        shutil.copyfile(bundle / "timing/timing.txt", copied / "timing/timing.txt")
        out.append(sound("timing: (the final build's reports, copied)", lambda: check_timing(copied)))
        path = copied / "timing/final-build/bitstream.sha256"
        path.write_text(re.sub(r"(configuration data[^\n]*?sha256 )[0-9a-f]", r"\g<1>x", path.read_text()))
        out.append(planted("timing: the image's configuration data not timing.txt's", lambda: check_timing(copied),
                           "is not timing.txt's"))
    entries = r_entries(manifest)
    consoles = {i: entry_texts(e, archives)[1] for i, e in entries.items()}
    rows, loads = synthetic_loads(entries, consoles, "b")
    out.append(sound("board: (the planted loads, sound)", lambda: check_loads(loads, entries, consoles, rows, "b", "B")))
    first = sorted(entries)[0]

    def mutated(change):
        bad = copy.deepcopy(loads)
        change(bad[1], next(p for p in bad[1]["programs"] if p["name"] == first))
        return bad
    for label, change, reason in (
            ("board: a console byte differs", lambda r, p: p.update(console=p["console"][:-1] + "?"), "console differs"),
            ("board: tohost at another cycle", lambda r, p: p.update(tohost_cycles=p["tohost_cycles"] + 1), "simulated"),
            ("board: hart 0's retired count differs", lambda r, p: p.update(tohost_retired=p["tohost_retired"] + 1),
             "tohost_retired"),
            ("board: the console lagged too far", lambda r, p: p.update(max_lag=LAG_LIMIT + 1), "console lag"),
            ("board: a program not run", lambda r, p: r["programs"].remove(p), "not run"),
            ("board: another bitstream", lambda r, p: r.update(bitstream_sha256="c"), "another bitstream"),
            ("board: FCLK0 not 100 MHz", lambda r, p: r["clock"].update(measured_mhz=95.0), "FCLK0"),
            ("board: the run not complete", lambda r, p: r.update(status="failed"), "the run is failed"),
            ("board: another configuration's identity",
             lambda r, p: (r["identity"].update(soc_config=3), r["design"].update(soc_config=3)), "configuration words")):
        bad = mutated(change)
        out.append(planted(label, lambda: check_loads(bad, entries, consoles, rows, "b", "B"), reason))
    out.append(planted("board: one load only", lambda: check_loads(loads[:1], entries, consoles, rows, "b", "B"),
                       "not two"))
    out.append(planted("board: one load's report twice", lambda: check_loads([loads[0], copy.deepcopy(loads[0])],
                                                                             entries, consoles, rows, "b", "B"),
                       "not separate loads"))
    reference = dict(problems=[], sim_sha256=next(iter(entries.values()))["sim_sha256"], rows=list(rows.values()))
    out.append(sound("board: (the planted reference, sound)", lambda: reference_rows(reference, entries)))
    bad = copy.deepcopy(reference)
    bad["sim_sha256"] = "0" * 64
    out.append(planted("board: the reference on another simulation", lambda: reference_rows(bad, entries),
                       "another simulation"))
    bad = copy.deepcopy(reference)
    bad["rows"][0]["tohost_cycles"] += 1
    out.append(planted("board: the reference not as captured", lambda: reference_rows(bad, entries), "captured"))
    # CoreMark's: its capture as the board's evidence holds it, with loads made to match it
    try:
        cm, consoles, records, cm_reference, _ = coremark_inputs(board)
    except Exception as error:
        return out + [("coremark: (its capture, from the board's evidence)", False, f"unreadable: {error}")]
    cm_entries = {e["id"]: e for e in cm["entries"]}
    cm_rows, cm_loads = synthetic_loads(cm_entries, consoles, "b")
    cm_ref = dict(cm_reference, rows=list(cm_rows.values()))
    performance, validation = COREMARK_IDS

    def verdict(manifest=cm, consoles=consoles, records=records, reference=cm_ref, loads=cm_loads):
        return lambda: coremark_verdict(manifest, consoles, records, reference, loads, bundle_manifest, "b")
    bundle_manifest = manifest
    out.append(sound("coremark: (its capture, with planted loads, sound)", verdict()))
    bad = copy.deepcopy(cm)
    bad["source"]["dirty"] = True
    out.append(planted("coremark: its capture from a dirty tree", verdict(manifest=bad), "dirty"))
    bad = copy.deepcopy(cm)
    bad["entries"][0]["sim_sha256"] = "0" * 64
    out.append(planted("coremark: its capture on another simulation", verdict(manifest=bad),
                       "not the bundle's R simulation"))
    bad = copy.deepcopy(cm)
    bad["entries"] = [e for e in bad["entries"] if e["id"] != validation]
    out.append(planted("coremark: the validation run missing", verdict(manifest=bad), "not both runs"))
    for ident, run in ((performance, "performance"), (validation, "validation")):
        tampered = dict(consoles, **{ident: re.sub(r"(\[0\]crclist\s*: 0x)[0-9a-f]+", r"\g<1>1234", consoles[ident])})
        out.append(planted(f"coremark: the {run} run not CoreMark's known CRCs", verdict(consoles=tampered), "known")
                   if tampered[ident] != consoles[ident] else (f"coremark: the {run} run's CRCs", False, "not planted"))
        tampered = dict(consoles, **{ident: consoles[ident].replace("Correct operation validated.", "Errors detected.")})
        out.append(planted(f"coremark: the {run} run's own verdict not valid", verdict(consoles=tampered),
                           "not a validated")
                   if tampered[ident] != consoles[ident] else (f"coremark: the {run} run's verdict", False, "not planted"))
    swapped = dict(consoles, **{performance: consoles[validation]})
    out.append(planted("coremark: the validation run's report as the performance run's", verdict(consoles=swapped),
                       "not a validated 2K performance run"))
    out.append(planted("coremark: one load only", verdict(loads=cm_loads[:1]), "not two"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    parser.add_argument("--board", type=Path, default=BOARD)
    parser.add_argument("--self-test", action="store_true", help="plant a fault for each check; each must fail")
    args = parser.parse_args()
    if args.self_test:
        results = self_test(args.bundle, args.board)
        for name, ok, detail in results:
            print(f"{'ok' if ok else 'MISSED'}: {name}: {detail}")
        missed = [name for name, ok, _ in results if not ok]
        print(f"{'PASS' if not missed else 'FAIL'}: the audit's self-test, {len(results) - len(missed)} of "
              f"{len(results)} (each planted fault caught for its reason; each sound input passed)")
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
