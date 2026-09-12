#!/usr/bin/env python3
"""Run and audit the README Phase 4 experiments against real RTL executions."""
import argparse
import csv
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from bench_results import capture, expected_checksum, validate_result


def cases():
    experiments = {}
    def add(group, **overrides):
        settings = dict(l1=1, sync_memory=1, memory_wait=1, line_words=4, line_count=16,
                        workload="walk_sequential", words=128, repetitions=8, seed=0x13570000)
        settings.update(overrides)
        key = (f'{settings["workload"]}-b{settings["words"]*4}-r{settings["repetitions"]}'
               f'-s{settings["seed"]:08x}-l1{settings["l1"]}-sync{settings["sync_memory"]}'
               f'-wait{settings["memory_wait"]}-w{settings["line_words"]}-n{settings["line_count"]}')
        entry = experiments.setdefault(key, {"id": key, "settings": settings, "groups": []})
        if group not in entry["groups"]:
            entry["groups"].append(group)
    for workload in ("memcpy", "walk_sequential", "walk_random"):
        for l1 in (0, 1):
            for sync, wait in ((0, 0), (1, 1), (1, 4)):
                add("cache_vs_uncached", workload=workload, words=64 if workload == "memcpy" else 128,
                    l1=l1, sync_memory=sync, memory_wait=wait)
    for workload in ("walk_sequential", "walk_random"):
        for words in (16, 32, 64, 128, 256, 512, 1024, 2048):
            for l1 in (0, 1):
                add("working_sets", workload=workload, words=words, l1=l1)
                add("sequential_vs_random", workload=workload, words=words, l1=l1)
        for lines in (4, 8, 16, 32, 64):
            add("cache_sizes", workload=workload, line_count=lines)
    for seed in (0, 1, 0xa57e):
        for words in (128, 1024):
            add("random_seeds", workload="walk_random", words=words, seed=seed)
    return list(experiments.values())


def audit(output_dir):
    manifest = json.loads((output_dir / "manifest.json").read_text())
    if manifest.get("schema") != 1 or manifest.get("experiments") != cases():
        raise ValueError("experiment manifest does not cover the specified study")
    rows = []
    provenance = set()
    retirement = {}
    for experiment in manifest["experiments"]:
        result = json.loads((output_dir / (experiment["id"] + ".json")).read_text())
        record = validate_result(result)
        settings = experiment["settings"]
        expected = {key: settings[key] for key in ("l1", "sync_memory", "memory_wait", "line_words", "line_count",
                                                   "repetitions", "seed")}
        expected.update(name=settings["workload"], bytes=4*settings["words"])
        if any(record[key] != value for key, value in expected.items()):
            raise ValueError(f'wrong executing configuration: {experiment["id"]}')
        if record["checksum"] != expected_checksum(record["name"], record["bytes"]//4,
                                                   record["repetitions"], record["seed"]):
            raise ValueError("workload failed independent checksum reference")
        metadata = result["metadata"]
        provenance.add((metadata["revision"], metadata["source_sha256"], metadata["compiler_sha256"], metadata["dirty"]))
        family = "walk" if record["name"].startswith("walk_") else record["name"]
        key = (family, record["bytes"], record["repetitions"], record["seed"])
        retirement.setdefault(key, set()).add(record["retired"])
        rows.append(dict(id=experiment["id"], groups=";".join(experiment["groups"]), **record))
    if len(provenance) != 1 or any(len(counts) != 1 for counts in retirement.values()):
        raise ValueError("source/toolchain changed or retirement differed across hardware configurations")
    original = json.loads((output_dir / (manifest["repeat_of"] + ".json")).read_text())
    repeat = json.loads((output_dir / "repeat.json").read_text())
    validate_result(repeat)
    if any(original["metadata"][key] != repeat["metadata"][key] for key in
           ("revision", "source_sha256", "compiler_sha256", "verilator_version", "dirty")):
        raise ValueError("independent repetition has different source/toolchain provenance")
    if (original["record"] != repeat["record"] or
            original["metadata"]["firmware_sha256"] != repeat["metadata"]["firmware_sha256"]):
        raise ValueError("independent repetition changed record or firmware")
    return rows


def run(args):
    if args.output_dir.exists():
        raise ValueError("choose a new output directory; studies are never silently overwritten")
    args.output_dir.mkdir(parents=True)
    experiments = cases()
    with tempfile.TemporaryDirectory(prefix="aster-cache-study-") as build:
        for i, experiment in enumerate(experiments):
            print(f'EXPERIMENT {i+1}/{len(experiments)} {experiment["id"]}', flush=True)
            capture(SimpleNamespace(**experiment["settings"], riscv_prefix=args.riscv_prefix,
                                    output=args.output_dir / (experiment["id"] + ".json")), build)
        # A new simulator process resets RAM and caches; the ROM image is unchanged.
        repeated = next(item for item in experiments if item["settings"]["workload"] == "walk_random"
                        and item["settings"]["l1"] == 1 and item["settings"]["sync_memory"] == 1
                        and item["settings"]["memory_wait"] == 1)
        capture(SimpleNamespace(**repeated["settings"], riscv_prefix=args.riscv_prefix,
                                output=args.output_dir / "repeat.json"), build)
    manifest = {"schema": 1, "experiments": experiments, "repeat_of": repeated["id"]}
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = audit(args.output_dir)
    with (args.output_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"PASS: {len(rows)} configurations + repeat; all four README experiments covered", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    args = parser.parse_args()
    try:
        if args.audit_only:
            print(f"PASS: audited {len(audit(args.output_dir))} retained configurations")
        else:
            run(args)
    except (ValueError, OSError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
