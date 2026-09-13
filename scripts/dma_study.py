#!/usr/bin/env python3
"""Fixed Phase 7 CPU/DMA size, alignment, cache and fresh-rebuild study.

The complete plan is not caller-selectable. A smaller test is a development
capture, not a complete crossover experiment. All ratios retain DMA slowdowns.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import asterbench_dma as bench
import dma_results as results
from bench_results import command, sha, source_state

SCHEMA = "aster.dma.study.v1"
PLAN = "phase7-23sizes-3alignments-2caches-6freshrepeats-v1"
FIELDS = {"schema", "plan", "status", "revision", "source_sha256", "entries", "summary", "started_utc", "finished_utc"}


def plan():
    entries = []
    for caches in (0, 1):
        for alignment in bench.ALIGNMENTS:
            for size in bench.SIZES:
                c = dict(size=size, alignment=alignment, jobs=4, harts=2, base_seed=0x13570000, l1=caches,
                         sync_memory=1, memory_wait=1, line_words=4, line_count=16, boots=2, uart_seed=0)
                results.validate_configuration(c)
                entries.append(dict(id=f"b{size}-a{alignment}-c{caches}", configuration=c, fresh_repeat_of=None))
    # One independently rebuilt 1 KiB case per alignment/cache series; freeze
    # selection in advance, not after seeing the fastest transfer sizes.
    for entry in list(entries):
        if entry["configuration"]["size"] == 1024:
            entries.append(dict(id=entry["id"]+"-repeat", configuration=dict(entry["configuration"]), fresh_repeat_of=entry["id"]))
    return entries


def series_summary(points):
    """Keep any-win and all-pairs-win distinct, including later reversals."""
    first_any = next((p["size"] for p in points if p["size"] and p["max_cpu_over_dma"] > 1), None)
    first_all = next((p["size"] for p in points if p["size"] and p["min_cpu_over_dma"] > 1), None)
    reversal = [p["size"] for p in points if first_all is not None and p["size"] > first_all and p["min_cpu_over_dma"] <= 1]
    sustained = next((p["size"] for i,p in enumerate(points) if p["size"] and
                      all(later["min_cpu_over_dma"] > 1 for later in points[i:])), None)
    return dict(points=points, first_any_pair_dma_win_bytes=first_any, first_all_pairs_dma_win_bytes=first_all,
                later_not_all_pairs_win_bytes=reversal, all_pairs_win_through_largest_tested_from_bytes=sustained)


def summarize(captures):
    specs = plan(); bench.require(set(captures) == {e["id"] for e in specs}, "summary requires complete fixed plan")
    statistics, series, repeats = {}, {}, []
    for entry in specs:
        capture = captures[entry["id"]]
        pairs = results.summary(capture)["pairs"]
        ratios = [p["cpu_over_dma"] for p in pairs]
        sums = {method: {event: sum(r[event] for rows in capture["records"] for r in rows if r["method"] == method)
                         for event in sorted(bench.COUNTERS)} for method in ("cpu", "dma")}
        point = dict(size=entry["configuration"]["size"], id=entry["id"], min_cpu_over_dma=min(ratios), max_cpu_over_dma=max(ratios),
                     summed_cpu_over_dma=sums["cpu"]["h0_cycles"]/sums["dma"]["h0_cycles"])
        statistics[entry["id"]] = dict(pairs=pairs, summed_counters=sums, **point)
        if entry["fresh_repeat_of"]:
            base = captures[entry["fresh_repeat_of"]]
            repeats.append(dict(baseline=entry["fresh_repeat_of"], repeat=entry["id"],
                                identical_records=bench.typed_equal(base["records"], capture["records"]),
                                identical_observations=bench.typed_equal(base["observations"], capture["observations"])))
        else:
            c = entry["configuration"]; key = f"a{c['alignment']}-c{c['l1']}"
            series.setdefault(key, []).append(point)
    return dict(captures=len(specs), boots=sum(e["configuration"]["boots"] for e in specs),
                paired_jobs=sum(e["configuration"]["jobs"]*e["configuration"]["boots"] for e in specs),
                method_records=2*sum(e["configuration"]["jobs"]*e["configuration"]["boots"] for e in specs),
                statistics=statistics, series={key:series_summary(points) for key, points in series.items()}, repeats=repeats,
                interpretation="Simulation only; ratio below one is a DMA slowdown. Crossover is observed at sampled sizes, not universal or interpolated.",
                window="setup_copy_complete includes dispatch, descriptor setup, polling, coherent memory service and completion fences; excludes preparation/check/UART/stop",
                cache_policy="prepared_reinitialize at fixed source/destination bases; not cold-cache; balanced method order retained",
                cpu_usage="Polling occupies the CPU; no CPU-availability or overlap speedup claim.")


def build_directory(capture):
    values = [arg.split("=",1)[1] for arg in capture["metadata"]["build_command"] if arg.startswith("BUILD_DIR=")]
    bench.require(len(values) == 1 and Path(values[0]).is_absolute(), "missing build identity")
    return values[0]


def validate_repeat(base, capture, shared_build, repeat_builds):
    build = build_directory(capture)
    bench.require(build != shared_build and build not in repeat_builds, "repeat did not receive an independently fresh build root")
    for key in ("records", "observations", "stops", "symbols"):
        bench.require(bench.typed_equal(base[key], capture[key]), "fresh repeat changed measured execution: "+key)
    for key in ("firmware", "ram1", "ram2"):
        bench.require(base["artifacts"][key]["sha256"] == capture["artifacts"][key]["sha256"], "fresh repeat ROM/RAM changed")
    repeat_builds.add(build)


def audit(path, *, clean=True):
    bench.require(not path.is_symlink(), "symlink study is not self-contained")
    manifest = bench.json_record(path.read_text())
    bench.require(type(manifest) is dict and set(manifest) == FIELDS and manifest["schema"] == SCHEMA and
                  manifest["plan"] == PLAN and manifest["status"] == "complete", "incomplete/unknown fixed study")
    for key in ("started_utc", "finished_utc"):
        bench.require(type(manifest[key]) is str, "invalid study timestamp")
        instant = datetime.fromisoformat(manifest[key])
        bench.require(instant.tzinfo is not None and instant.utcoffset().total_seconds() == 0, "study timestamp must be UTC")
    bench.require(datetime.fromisoformat(manifest["finished_utc"]) >= datetime.fromisoformat(manifest["started_utc"]), "reversed study timestamps")
    specs = plan()
    bench.require(type(manifest["entries"]) is list and len(manifest["entries"]) == len(specs), "incomplete study plan coverage")
    captures, repeat_builds = {}, set(); tools = shared_build = None
    expected_files = {path.name}
    for actual, expected in zip(manifest["entries"], specs):
        bench.require(type(actual) is dict and set(actual) == {"id", "configuration", "fresh_repeat_of", "file", "sha256"}, "wrong study entry")
        bench.require(bench.typed_equal({k:actual[k] for k in expected}, expected), "changed/reordered/missing fixed study case")
        filename = expected["id"]+".json"; bench.require(actual["file"] == filename, "unsafe/mismatched capture path")
        target = path.parent/filename; results.digest(actual["sha256"])
        bench.require(not target.is_symlink() and sha(target) == actual["sha256"], "capture envelope changed")
        capture = results.load(target, clean=clean)
        bench.require(bench.typed_equal(capture["configuration"], expected["configuration"]), "capture differs from fixed study case")
        metadata = capture["metadata"]
        bench.require(metadata["dirty"] is False and metadata["revision"] == manifest["revision"] and
                      metadata["source_sha256"] == manifest["source_sha256"], "dirty/mixed-source complete study")
        if tools is None: tools = capture["toolchain"]; shared_build = build_directory(capture)
        bench.require(bench.typed_equal(tools, capture["toolchain"]), "mixed toolchain study")
        if expected["fresh_repeat_of"]:
            validate_repeat(captures[expected["fresh_repeat_of"]], capture, shared_build, repeat_builds)
        else:
            bench.require(build_directory(capture) == shared_build, "unexpected main study build root")
        captures[expected["id"]] = capture
        expected_files.update((target.name, target.with_suffix(".log").name))
        expected_files.update(item["file"] for item in capture["artifacts"].values())
    bench.require({p.name for p in path.parent.iterdir()} == expected_files, "missing/extra study files")
    bench.require(bench.typed_equal(summarize(captures), manifest["summary"]), "claimed crossover/statistics differ from audited captures")
    return manifest


def capture(output, riscv_prefix="riscv32-unknown-elf-"):
    output = output.resolve(); bench.require(not output.exists(), "study needs a new output directory; old/failed evidence is preserved")
    bench.require(not command(["git", "status", "--porcelain"]), "fixed study requires clean committed source")
    sources, fingerprint = source_state(); revision = command(["git", "rev-parse", "HEAD"])
    results.source_at_revision(dict(dirty=False, revision=revision, source_files=sources))
    manifest = dict(schema=SCHEMA, plan=PLAN, status="running", revision=revision, source_sha256=fingerprint, entries=[],
                    summary=None, started_utc=datetime.now(timezone.utc).isoformat(), finished_utc=None)
    output.mkdir(parents=True)
    def save():
        (output/"study.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n")
    save(); captures = {}; specs = plan()
    try:
        with tempfile.TemporaryDirectory(prefix="aster-dma-study-") as directory:
            for index, entry in enumerate(specs, 1):
                print(f"DMA STUDY {index}/{len(specs)}: {entry['id']}", flush=True)
                c = entry["configuration"]; target = output/(entry["id"]+".json")
                args = SimpleNamespace(**{k:v for k,v in c.items() if k != "base_seed"}, seed=c["base_seed"],
                                       output=target, riscv_prefix=riscv_prefix, allow_dirty=False)
                captured = results.capture(args, build_directory=None if entry["fresh_repeat_of"] else directory)
                bench.require(captured["metadata"]["revision"] == revision and captured["metadata"]["source_sha256"] == fingerprint,
                              "source changed between study captures")
                captures[entry["id"]] = captured
                manifest["entries"].append(dict(entry, file=target.name, sha256=sha(target))); save()
        bench.require(source_state() == (sources, fingerprint) and command(["git", "rev-parse", "HEAD"]) == revision and
                      not command(["git", "status", "--porcelain"]), "source changed before study closeout")
        manifest.update(status="complete", summary=summarize(captures), finished_utc=datetime.now(timezone.utc).isoformat()); save()
        audit(output/"study.json")
    except BaseException as error:
        manifest.update(status="failed", error=str(error), finished_utc=datetime.now(timezone.utc).isoformat()); save()
        raise
    print(f"PASS: complete independently audited DMA study: {output/'study.json'}", flush=True)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__); commands = p.add_subparsers(dest="action", required=True)
    run = commands.add_parser("capture"); run.add_argument("--output", type=Path, required=True)
    run.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    check = commands.add_parser("audit"); check.add_argument("manifest", type=Path)
    commands.add_parser("plan"); args = p.parse_args()
    try:
        if args.action == "capture": capture(args.output, args.riscv_prefix)
        elif args.action == "plan": print(json.dumps(plan(), indent=2))
        else:
            audited = audit(args.manifest)
            print(f"PASS: DMA study: {audited['summary']['captures']} captures, {audited['summary']['paired_jobs']} paired jobs")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
