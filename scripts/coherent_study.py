#!/usr/bin/env python3
"""Capture and independently audit the fixed AsterBench v4 Phase 6 study.

The plan is deliberately not caller-selectable: a subset cannot claim complete
coverage. Each capture retains its own clean-source, ELF, UART and RAM proof.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import asterbench_coherent as bench
import coherent_results as results
from bench_results import command, sha, source_state

SCHEMA = "aster.coherent.study.v1"
PLAN = "phase6-default-size-communication-repeat-v1"
FIELDS = {"schema", "plan", "status", "revision", "source_sha256", "entries", "summary",
          "started_utc", "finished_utc"}


def plan():
    blocks = [(name, 64, 4, 0x13570000) for name in bench.NAMES]
    blocks += [("shared_mix", 2, 1, 0), ("shared_mix", 129, 16, 1),
               ("shared_mix", 1024, 64, 0xc0ffee)]
    blocks += [(name, 1024, 4, 0xffffffff) for name in ("ping_pong", "spsc_queue")]
    entries = []
    for name, items, rounds, seed in blocks:
        for caches in (0, 1):
            for workers in (1, 2):
                identifier = f"{name}-i{items}-r{rounds}-p{workers}-c{caches}"
                config = dict(name=name, items=items, rounds=rounds, jobs=3, workers=workers, harts=2,
                              base_seed=seed, l1=caches, sync_memory=1, memory_wait=1,
                              line_words=4, line_count=16, boots=2, uart_seed=0)
                results.validate_configuration(config)
                entries.append(dict(id=identifier, configuration=config, fresh_repeat_of=None))
    repeated = next(e for e in entries if e["id"] == "shared_mix-i64-r4-p2-c1")
    entries.append(dict(id=repeated["id"]+"-repeat", configuration=dict(repeated["configuration"]),
                        fresh_repeat_of=repeated["id"]))
    return entries


def ratios(baseline, candidate):
    left = [[row["h0_cycles"] for row in boot] for boot in baseline["records"]]
    right = [[row["h0_cycles"] for row in boot] for boot in candidate["records"]]
    sums = [sum(sum(boot) for boot in runs) for runs in (left, right)]
    return dict(cycle_ratio_baseline_over_candidate=sums[0]/sums[1], summed_cycles=sums,
                per_boot_job_ratios=[[x/y for x, y in zip(a, b)] for a, b in zip(left, right)])


def summarize(captures):
    """Only call with every plan entry already fully audited and config-matched."""
    specs = plan(); comparisons = []; statistics = {}
    for entry in specs:
        identifier = entry["id"]; capture = captures[identifier]
        c = entry["configuration"]; rows = [row for boot in capture["records"] for row in boot]
        sums = {key: sum(row[key] for row in rows) for key in sorted(bench.COUNTERS)}
        statistics[identifier] = dict(per_boot_job_cycles=[[r["h0_cycles"] for r in boot] for boot in capture["records"]],
            summed_counters=sums, cycles_per_item=sums["h0_cycles"]/(len(rows)*c["items"]),
            work_unit="request/reply handoff" if c["name"] == "ping_pong" else
                      "queue handoff" if c["name"] == "spsc_queue" else
                      "element (all configured rounds)" if c["name"] == "shared_mix" else "update",
            summed_window_seconds=sums["h0_cycles"]/31250000)
        if entry["fresh_repeat_of"]:
            base = entry["fresh_repeat_of"]
            comparisons.append(dict(kind="fresh_rebuild_repeat", baseline=base, candidate=identifier,
                                    **ratios(captures[base], capture)))
            continue
        for kind, key, value in (("worker_scaling", "workers", 2), ("cache_effect", "l1", 1)):
            if c[key] == value:
                continue
            other = dict(c); other[key] = value
            peer = next(e["id"] for e in specs if e["fresh_repeat_of"] is None and e["configuration"] == other)
            # Standard comparisons additionally enforce source/tool equality.
            results.compare(capture, captures[peer])
            comparisons.append(dict(kind=kind, baseline=identifier, candidate=peer,
                                    **ratios(capture, captures[peer])))
        if c["name"] == "false_shared":
            other = dict(c); other["name"] = "padded"
            peer = next(e["id"] for e in specs if e["configuration"] == other)
            comparisons.append(dict(kind="padding_effect", baseline=identifier, candidate=peer,
                                    **ratios(capture, captures[peer])))
    return dict(captures=len(specs), boots=sum(e["configuration"]["boots"] for e in specs),
                jobs=sum(e["configuration"]["boots"]*e["configuration"]["jobs"] for e in specs),
                statistics=statistics, comparisons=comparisons,
                interpretation="Simulation only. Ratio below 1 is a slowdown. No cross-workload aggregate speedup.",
                window="dispatch_work_join includes secondary startup and synchronization; excludes initialization, UART and stop/flush",
                cache_state="primary reflects initialization/prior work; secondary cold-started each job; serialized private D-cache hits")


def build_directory(capture):
    values = [arg[10:] for arg in capture["metadata"]["build_command"] if arg.startswith("BUILD_DIR=")]
    bench.require(len(values) == 1 and Path(values[0]).is_absolute(), "missing/ambiguous build directory")
    return values[0]


def audit(path, *, clean=True):
    """Read-only; never invokes executables or paths claimed by saved evidence."""
    bench.require(not path.is_symlink(), "study manifest is not self-contained")
    manifest = bench.json_record(path.read_text())
    bench.require(type(manifest) is dict and set(manifest) == FIELDS and manifest["schema"] == SCHEMA and
                  manifest["plan"] == PLAN and manifest["status"] == "complete", "incomplete/unknown study envelope")
    for key in ("started_utc", "finished_utc"):
        bench.require(type(manifest[key]) is str, "invalid study timestamp")
        instant = datetime.fromisoformat(manifest[key])
        bench.require(instant.tzinfo is not None and instant.utcoffset().total_seconds() == 0, "timestamp must be UTC")
    bench.require(datetime.fromisoformat(manifest["finished_utc"]) >= datetime.fromisoformat(manifest["started_utc"]), "reversed timestamps")
    specs = plan()
    bench.require(type(manifest["entries"]) is list and len(manifest["entries"]) == len(specs), "incomplete study plan")
    captures = {}; tools = None; shared_build = None
    expected_files = {path.name}
    for actual, expected in zip(manifest["entries"], specs):
        bench.require(type(actual) is dict and set(actual) == {"id", "configuration", "fresh_repeat_of", "file", "sha256"}, "invalid study entry")
        bench.require(results.typed_equal({k: actual[k] for k in expected}, expected), "missing/reordered/changed study case")
        filename = expected["id"]+".json"
        bench.require(actual["file"] == filename, "unsafe/mismatched capture path")
        target = path.parent/filename
        bench.require(not target.is_symlink() and not target.with_suffix(".log").is_symlink(), "symlink capture/log")
        results.digest(actual["sha256"])
        bench.require(sha(target) == actual["sha256"], "capture envelope changed")
        capture = results.load(target, clean=clean)
        bench.require(results.typed_equal(capture["configuration"], expected["configuration"]), "capture differs from study configuration")
        meta = capture["metadata"]
        bench.require(meta["dirty"] is False, "dirty capture cannot enter the complete study, including offline board preflight")
        bench.require(meta["revision"] == manifest["revision"] and meta["source_sha256"] == manifest["source_sha256"], "mixed source study")
        if tools is None:
            tools = capture["toolchain"]; shared_build = build_directory(capture)
        bench.require(results.typed_equal(tools, capture["toolchain"]), "mixed toolchain study")
        if expected["fresh_repeat_of"] is None:
            bench.require(build_directory(capture) == shared_build, "unexpected study build directory")
        else:
            base = captures[expected["fresh_repeat_of"]]
            bench.require(build_directory(capture) != shared_build, "repeat was not independently rebuilt")
            for key in ("records", "observations", "stops", "symbols"):
                bench.require(results.typed_equal(base[key], capture[key]), "fresh repeat differs: "+key)
            for key in ("firmware", "ram1", "ram2"):
                bench.require(base["artifacts"][key]["sha256"] == capture["artifacts"][key]["sha256"], "repeat ROM/RAM changed")
        captures[expected["id"]] = capture
        expected_files.update((filename, target.with_suffix(".log").name))
        expected_files.update(a["file"] for a in capture["artifacts"].values())
    bench.require({p.name for p in path.parent.iterdir()} == expected_files, "missing/extra study files")
    summary = summarize(captures)
    bench.require(results.typed_equal(summary, manifest["summary"]), "claimed study results differ from audited captures")
    return manifest


def capture(output, riscv_prefix="riscv32-unknown-elf-"):
    output = output.resolve()
    bench.require(not output.exists(), "study output must be a new directory; existing evidence is never overwritten")
    bench.require(not command(["git", "status", "--porcelain"]), "study requires clean committed source")
    sources, fingerprint = source_state(); revision = command(["git", "rev-parse", "HEAD"])
    results.source_at_revision(dict(dirty=False, revision=revision, source_files=sources))
    manifest = dict(schema=SCHEMA, plan=PLAN, status="running", revision=revision, source_sha256=fingerprint,
                    entries=[], summary=None, started_utc=datetime.now(timezone.utc).isoformat(), finished_utc=None)
    output.mkdir(parents=True)

    def save():
        (output/"study.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")

    save(); captures = {}
    try:
        with tempfile.TemporaryDirectory(prefix="aster-coherent-study-") as directory:
            for index, entry in enumerate(plan(), 1):
                c = entry["configuration"]; target = output/(entry["id"]+".json")
                print(f"STUDY {index}/{len(plan())}: {entry['id']}", flush=True)
                args = SimpleNamespace(**{k: v for k, v in c.items() if k not in ("name", "base_seed")},
                                       workload=c["name"], seed=c["base_seed"], output=target,
                                       riscv_prefix=riscv_prefix, allow_dirty=False)
                build = None if entry["fresh_repeat_of"] else directory
                captured = results.capture(args, build_directory=build)
                bench.require(captured["metadata"]["revision"] == revision and
                              captured["metadata"]["source_sha256"] == fingerprint, "source changed between captures")
                captures[entry["id"]] = captured
                manifest["entries"].append(dict(entry, file=target.name, sha256=sha(target))); save()
        bench.require(source_state() == (sources, fingerprint) and command(["git", "rev-parse", "HEAD"]) == revision and
                      not command(["git", "status", "--porcelain"]), "source changed before study closeout")
        manifest.update(status="complete", summary=summarize(captures), finished_utc=datetime.now(timezone.utc).isoformat()); save()
        audit(output/"study.json")
    except BaseException as error:
        manifest.update(status="failed", error=str(error), finished_utc=datetime.now(timezone.utc).isoformat()); save()
        raise
    print(f"PASS: audited complete AsterBench v4 study, {output/'study.json'}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("capture"); run.add_argument("--output", type=Path, required=True)
    run.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    check = actions.add_parser("audit"); check.add_argument("manifest", type=Path)
    actions.add_parser("plan")
    args = parser.parse_args()
    try:
        if args.action == "capture": capture(args.output, args.riscv_prefix)
        elif args.action == "plan": print(json.dumps(plan(), indent=2))
        else:
            audited = audit(args.manifest)
            print(f"PASS: AsterBench v4 study ({audited['summary']['captures']} captures, {audited['summary']['jobs']} jobs)")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
