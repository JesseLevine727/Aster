#!/usr/bin/env python3
"""Capture/audit AsterBench v4 with clean source, toolchain, UART and RAM evidence."""
import argparse
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile

import asterbench_coherent as bench
from bench_results import ROOT, command, sha, source_state, validate_metadata
from run_coherent_sim import symbols
from coherent_elf import inspect_elf

FIELDS = {"schema", "metadata", "toolchain", "symbols", "configuration", "serial_boots", "records",
          "observations", "stops", "artifacts", "log_sha256"}
CONFIG_FIELDS = {"name", "items", "rounds", "jobs", "workers", "harts", "base_seed", "l1", "sync_memory",
                 "memory_wait", "line_words", "line_count", "boots", "uart_seed"}
TOOL_NAMES = {"gcc", "cc1", "as", "ld", "collect2", "nm", "objdump", "verilator"}
SOURCE_PREFIXES = ("rtl/", "software/", "vendor/", "scripts/", "verification/", "fpga/")


def typed_equal(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def digest(value):
    bench.require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "invalid SHA-256")


def source_at_revision(metadata):
    """Require the COMPLETE relevant source tree, not an arbitrary hashed subset."""
    bench.require(metadata["dirty"] is False, "clean source is required for reproducible acceptance")
    listing = subprocess.check_output(["git", "ls-tree", "-rz", metadata["revision"]], cwd=ROOT)
    entries = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        head, name = entry.split(b"\t", 1)
        mode, kind, oid = head.decode().split()
        name = name.decode()
        if name == "Makefile" or name.startswith(SOURCE_PREFIXES):
            bench.require(kind == "blob" and mode in ("100644", "100755"), "non-file build source")
            entries.append((name, oid))
    bench.require(entries, "missing revision source tree")
    batch = subprocess.run(["git", "cat-file", "--batch"], input="".join(oid+"\n" for _, oid in entries).encode(),
                           cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    stream = io.BytesIO(batch); sources = {}
    for name, oid in entries:
        header = stream.readline().decode().split()
        bench.require(len(header) == 3 and header[:2] == [oid, "blob"], "unavailable source blob")
        size = int(header[2]); data = stream.read(size)
        bench.require(len(data) == size and stream.read(1) == b"\n", "truncated source blob")
        sources[name] = hashlib.sha256(data).hexdigest()
    bench.require(sources == metadata["source_files"], "omitted/extra/changed source file in provenance")


def validate_configuration(c):
    bench.require(type(c) is dict and set(c) == CONFIG_FIELDS, "wrong configuration fields")
    bench.reference(c["name"], c["items"], c["rounds"], c["workers"], c["base_seed"])
    bench.integer(c["harts"], c["workers"], 2); bench.integer(c["jobs"], 1, 16)
    bench.integer(c["boots"], 1, 16); bench.integer(c["uart_seed"], 0, bench.U32)
    bench.integer(c["l1"], 0, 1); bench.integer(c["sync_memory"], 0, 1)
    bench.integer(c["memory_wait"], c["sync_memory"], 1024)
    for key in ("line_words", "line_count"):
        bench.integer(c[key], 2 if c["l1"] else 1, 1024); bench.require(not c[key] & (c[key]-1), "invalid geometry")


def validate_toolchain(t):
    bench.require(type(t) is dict and set(t) == {"tools", "headers"} and type(t["tools"]) is dict and
                  set(t["tools"]) == TOOL_NAMES and type(t["headers"]) is dict and t["headers"], "incomplete toolchain")
    for tool in t["tools"].values():
        bench.require(type(tool) is dict and set(tool) == {"path", "sha256", "version"}, "wrong tool fields")
        bench.require(type(tool["path"]) is str and Path(tool["path"]).is_absolute() and
                      type(tool["version"]) is str and tool["version"], "invalid tool identity")
        digest(tool["sha256"])
    for path, value in t["headers"].items():
        bench.require(type(path) is str and Path(path).is_absolute(), "invalid system-header identity"); digest(value)
    bench.require(any(Path(path).name == "stdatomic.h" for path in t["headers"]), "missing atomic compiler header")


def validate_result(result, log, directory=None, *, clean=True):
    bench.require(type(result) is dict and set(result) == FIELDS and result["schema"] == "aster.coherent.capture.v1", "invalid capture envelope")
    validate_metadata(result["metadata"]); validate_toolchain(result["toolchain"])
    c = result["configuration"]; validate_configuration(c)
    metadata = result["metadata"]
    flags = shlex.split(metadata["cflags"])
    bench.require([f for f in flags if f.startswith("-march=")] == ["-march=rv32ima"] and
                  [f for f in flags if f.startswith("-mabi=")] == ["-mabi=ilp32"], "wrong ISA/ABI compiler flags")
    for flag, value in {"KIND": bench.NAMES.index(c["name"]), "ITEMS": c["items"], "ROUNDS": c["rounds"],
                        "JOBS": c["jobs"], "WORKERS": c["workers"], "SEED": c["base_seed"]}.items():
        matches = [f.split("=", 1)[1] for f in flags if f.startswith(f"-DCOHERENT_{flag}=")]
        bench.require(len(matches) == 1 and int(matches[0], 0) == value, "compiler workload define mismatch")
    bench.require(metadata["compiler_sha256"] == result["toolchain"]["tools"]["gcc"]["sha256"] and
                  metadata["compiler"] == result["toolchain"]["tools"]["gcc"]["path"], "compiler identity mismatch")
    if clean:
        source_at_revision(metadata)
    digest(result["log_sha256"])
    bench.require(hashlib.sha256(log.encode()).hexdigest() == result["log_sha256"], "raw log changed")
    observed = bench.simulation_log(log, c["boots"], c["jobs"])
    for key in ("serial_boots", "observations", "stops"):
        bench.require(typed_equal(observed[key], result[key]), "envelope differs from raw log")
    records = [bench.parse_stream(serial) for serial in observed["serial_boots"]]
    bench.require(typed_equal(records, result["records"]), "typed records differ from raw serial")
    for rows in records:
        bench.require(all(row["clock_hz"] == 31250000 for row in rows) and
                      all(row[key] == value for row in rows for key, value in c.items() if key not in ("boots", "uart_seed")),
                      "running configuration differs from requested build")
    artifacts = result["artifacts"]
    expected = {"firmware", "elf", "map", "disassembly"} | {f"ram{boot}" for boot in range(1, c["boots"]+1)}
    bench.require(type(artifacts) is dict and set(artifacts) == expected, "missing/extra artifacts")
    names = set()
    artifact_data = {}
    for key, artifact in artifacts.items():
        bench.require(type(artifact) is dict and set(artifact) == {"file", "sha256", "bytes"}, "wrong artifact fields")
        filename = artifact["file"]
        bench.require(type(filename) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", filename) and filename not in names,
                      "unsafe/duplicate artifact path")
        names.add(filename); digest(artifact["sha256"]); bench.integer(artifact["bytes"], 1)
        if key.startswith("ram"):
            bench.require(artifact["bytes"] == 65536, "wrong RAM snapshot size")
        if key in ("firmware", "elf"):
            bench.require(artifact["sha256"] == metadata[key+"_sha256"], "artifact/provenance mismatch")
        if directory is not None:
            path = Path(directory)/filename
            bench.require(not path.is_symlink(), "symlink evidence is not self-contained")
            data = path.read_bytes()
            artifact_data[key] = data
            bench.require(len(data) == artifact["bytes"] and hashlib.sha256(data).hexdigest() == artifact["sha256"], "artifact changed")
            if key.startswith("ram"):
                bench.validate_ram(data, records[int(key[3:])-1], result["symbols"])
    if directory is not None:
        elf = inspect_elf(artifact_data["elf"])
        bench.require(typed_equal(elf["symbols"], result["symbols"]), "claimed symbol addresses differ from actual ELF")
        firmware = artifact_data["firmware"]
        bench.require(len(firmware) == 16384*9 and re.fullmatch(rb"(?:[0-9a-f]{8}\n){16384}", firmware), "noncanonical/incomplete firmware")
        image = b"".join(int(word, 16).to_bytes(4, "little") for word in firmware.splitlines())
        bench.require(image == elf["image"], "firmware differs from actual ELF ROM load segments")
    # Even envelope-only validation must not accept missing/ill-typed symbols.
    # A synthetic snapshot cannot validate results; use structural bounds here.
    s = result["symbols"]
    bench.require(type(s) is dict and set(s) == {"aster_coherent_kernel", "aster_coherent_results", "aster_coherent_output"}, "missing ELF symbols")
    for key, (low, high, size) in {"aster_coherent_kernel": (0, 65536, None),
            "aster_coherent_results": (0x10008000, 0x1000b000, c["jobs"]*32),
            "aster_coherent_output": (0x10000000, 0x10008000, c["items"]*4)}.items():
        bench.require(type(s[key]) is dict and set(s[key]) == {"address", "size"}, "wrong symbol fields")
        address = bench.integer(s[key]["address"], low, high-1); count = bench.integer(s[key]["size"], 1, high-low)
        bench.require(not address % 4 and address+count <= high and (size is None or count == size), "wrong symbol range")
    return records


def fingerprint_tools(config, *, source="software/benchmarks/coherent.c"):
    compiler = Path(shutil.which(config["compiler"]) or "").resolve()
    bench.require(compiler.is_file(), "compiler unavailable")
    paths = {"gcc": str(compiler), "nm": config["nm"], "objdump": config["nm"][:-2]+"objdump", "verilator": config["verilator"]}
    for name in ("cc1", "as", "ld", "collect2"):
        paths[name] = command([str(compiler), "-print-prog-name="+name])
    tools = {}
    for name, path in paths.items():
        actual = Path(shutil.which(path) or path).resolve()
        bench.require(actual.is_file(), "missing compiler component: "+name)
        # cc1's --version can silently emit nothing; -version compiles empty
        # stdin while reporting its identity on stderr. Never leave an a.s in
        # the source tree. collect2 likewise reports its own version on stderr.
        version_args = ["-version", "-o", "/dev/null"] if name == "cc1" else ["--version"]
        process = subprocess.run([str(actual), *version_args], input="", text=True, cwd=ROOT,
                                 check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        version = (process.stderr+process.stdout).strip() if name in ("cc1", "collect2") else (process.stdout+process.stderr).strip()
        bench.require(version, "tool did not report its version: "+name)
        tools[name] = {"path": str(actual), "sha256": sha(actual), "version": version.splitlines()[0]}
    dependencies = command([str(compiler), *shlex.split(config["cflags"]), "-M", source])
    paths = shlex.split(dependencies.replace("\\\n", " ").split(":", 1)[1])
    headers = {str(Path(path).resolve()): sha(path) for path in paths if Path(path).is_absolute()}
    result = {"tools": tools, "headers": headers}; validate_toolchain(result)
    return result


def capture(args, build_directory=None):
    c = {"name": args.workload, "items": args.items, "rounds": args.rounds, "jobs": args.jobs,
         "workers": args.workers, "harts": args.harts, "base_seed": args.seed, "l1": args.l1,
         "sync_memory": args.sync_memory, "memory_wait": args.sync_memory if args.memory_wait is None else args.memory_wait,
         "line_words": args.line_words, "line_count": args.line_count, "boots": args.boots, "uart_seed": args.uart_seed}
    validate_configuration(c)
    bench.require(args.output.suffix == ".json" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.output.stem), "output must have safe basename and .json suffix")
    sources, fingerprint = source_state(); revision = command(["git", "rev-parse", "HEAD"])
    dirty = bool(command(["git", "status", "--porcelain"]))
    bench.require(args.allow_dirty or not dirty, "commit source before a reproducible capture (or explicitly --allow-dirty for development)")
    suffixes = [".json", ".log", ".hex", ".elf", ".map", ".dis"]+[f".boot{i}.ram" for i in range(1, c["boots"]+1)]
    bench.require(not any(args.output.with_suffix(suffix).exists() for suffix in suffixes), "capture would overwrite existing evidence")
    context = nullcontext(str(build_directory)) if build_directory else tempfile.TemporaryDirectory(prefix="aster-coherent-capture-")
    with context as directory:
        build = Path(directory).resolve()
        settings = [f"BUILD_DIR={build}", f"RISCV_PREFIX={args.riscv_prefix}", f"HART_COUNT={c['harts']}",
                    f"ENABLE_L1={c['l1']}", f"SYNC_MEMORY={c['sync_memory']}", f"MEMORY_WAIT_CYCLES={c['memory_wait']}",
                    f"L1_LINE_WORDS={c['line_words']}", f"L1_LINE_COUNT={c['line_count']}"]
        for variable, key in {"WORKLOAD": "name", "ITEMS": "items", "ROUNDS": "rounds", "JOBS": "jobs", "WORKERS": "workers",
                              "SEED": "base_seed", "BOOTS": "boots", "UART_SEED": "uart_seed"}.items():
            settings.append(f"COHERENT_{variable}={c[key]}")
        config = json.loads(command(["make", "--no-print-directory", "-s", *settings, "coherent-config"]))
        toolchain = fingerprint_tools(config)
        invocation = ["make", "--no-print-directory", "-j2", *settings, "coherent-bench"]
        process = subprocess.run(invocation, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.with_suffix(".log").open("x") as f:
            f.write(process.stdout)
        bench.require(process.returncode == 0, "build/RTL scoreboard failed; retained raw log")
        observed = bench.simulation_log(process.stdout, c["boots"], c["jobs"])
        record_symbols = symbols(config["elf"], config["nm"], c["items"], c["jobs"])
        disassembly = subprocess.check_output([toolchain["tools"]["objdump"]["path"], "-d", config["elf"]])
        prefix = Path(config["firmware"]).parent / f"h{c['harts']}_l1{c['l1']}_sync{c['sync_memory']}_wait{c['memory_wait']}_w{c['line_words']}_n{c['line_count']}_u{c['uart_seed']}"
        contents = {"firmware": (".hex", Path(config["firmware"]).read_bytes()), "elf": (".elf", Path(config["elf"]).read_bytes()),
                    "map": (".map", Path(config["elf"]).with_suffix(".map").read_bytes()), "disassembly": (".dis", disassembly)}
        for boot in range(1, c["boots"]+1):
            contents[f"ram{boot}"] = (f".boot{boot}.ram", Path(str(prefix)+f".boot{boot}.ram").read_bytes())
        artifacts = {}
        for key, (suffix, data) in contents.items():
            path = args.output.with_suffix(suffix)
            with path.open("xb") as f:
                f.write(data)
            artifacts[key] = {"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        metadata = dict(revision=revision, dirty=dirty, source_sha256=fingerprint, source_files=sources,
                        compiler=toolchain["tools"]["gcc"]["path"], compiler_version=toolchain["tools"]["gcc"]["version"],
                        compiler_sha256=toolchain["tools"]["gcc"]["sha256"], cflags=config["cflags"], ldflags=config["ldflags"],
                        verilator_version=toolchain["tools"]["verilator"]["version"], firmware_sha256=sha(config["firmware"]),
                        elf_sha256=sha(config["elf"]), simulator_sha256=sha(config["simulator"]), build_command=invocation,
                        platform=platform.platform())
        result = dict(schema="aster.coherent.capture.v1", metadata=metadata, toolchain=toolchain, symbols=record_symbols,
                      configuration=c, **observed, records=[bench.parse_stream(s) for s in observed["serial_boots"]],
                      artifacts=artifacts, log_sha256=hashlib.sha256(process.stdout.encode()).hexdigest())
        bench.require(source_state() == (sources, fingerprint) and command(["git", "rev-parse", "HEAD"]) == revision and
                      bool(command(["git", "status", "--porcelain"])) == dirty, "source changed during capture")
        bench.require(fingerprint_tools(config) == toolchain, "toolchain changed during capture")
        validate_result(result, process.stdout, args.output.parent, clean=not args.allow_dirty)
        with args.output.open("x") as f:
            json.dump(result, f, indent=2, sort_keys=True); f.write("\n")
    print(f"PASS: coherent capture {args.output} (dirty={dirty})")
    return result


def load(path, *, clean=True):
    result = bench.json_record(path.read_text())
    validate_result(result, path.with_suffix(".log").read_text(), path.parent, clean=clean)
    return result


def compare(a, b):
    # Caller must load/audit both complete artifact packages first.
    aa, bb = a["configuration"], b["configuration"]
    allowed = {"workers", "l1"}
    bench.require(all(aa[k] == bb[k] for k in CONFIG_FIELDS-allowed), "incomparable workloads/platforms")
    bench.require(a["metadata"]["source_sha256"] == b["metadata"]["source_sha256"] and
                  a["toolchain"] == b["toolchain"], "source/toolchain changed in controlled comparison")
    records = [x["records"] for x in (a, b)]
    cycles = [sum(row["h0_cycles"] for boot in runs for row in boot) for runs in records]
    return {"cycle_ratio_baseline_over_candidate": cycles[0]/cycles[1], "summed_cycles": cycles,
            "configurations": [aa, bb], "per_boot_job_ratios": [[x["h0_cycles"]/y["h0_cycles"] for x, y in zip(left, right)]
                for left, right in zip(records[0], records[1])],
            "interpretation": "ratio below 1 is a slowdown; communication jobs retain N handoffs (single worker executes both endpoints)",
            "window": "includes secondary startup, dispatch, work and join; excludes initialization, secondary stop, oracle, UART and global flush"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="action", required=True)
    run = commands.add_parser("capture")
    run.add_argument("--output", type=Path, required=True); run.add_argument("--workload", choices=bench.NAMES, default="atomic_add")
    for key, default in (("items", 64), ("rounds", 4), ("jobs", 3), ("workers", 2), ("harts", 2), ("l1", 1),
                         ("sync-memory", 0), ("line-words", 4), ("line-count", 16), ("boots", 2)):
        run.add_argument("--"+key, type=int, default=default)
    run.add_argument("--memory-wait", type=int)
    run.add_argument("--seed", type=lambda s: int(s, 0), default=0x13570000)
    run.add_argument("--uart-seed", type=lambda s: int(s, 0), default=0)
    run.add_argument("--riscv-prefix", default="riscv32-unknown-elf-")
    run.add_argument("--allow-dirty", action="store_true", help="development only; cannot pass clean acceptance audit")
    audit = commands.add_parser("audit"); audit.add_argument("capture", type=Path); audit.add_argument("--allow-dirty", action="store_true")
    diff = commands.add_parser("compare"); diff.add_argument("baseline", type=Path); diff.add_argument("candidate", type=Path)
    args = p.parse_args()
    try:
        if args.action == "capture": capture(args)
        elif args.action == "audit": load(args.capture, clean=not args.allow_dirty); print("PASS: coherent capture/artifact audit")
        else: print(json.dumps(compare(load(args.baseline), load(args.candidate)), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
