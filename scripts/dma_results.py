#!/usr/bin/env python3
"""Capture and read-only audit real paired AsterBench v5 RTL executions.

Audits read local artifact bytes/Git blobs. They never execute commands or tool
paths taken from a saved capture. --allow-dirty is development-only; published
acceptance defaults to complete clean-source inventory validation.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile

import asterbench_dma as bench
from bench_results import ROOT, command, sha, source_state, validate_metadata
from coherent_elf import inspect_elf
from coherent_results import source_at_revision

FIELDS = {"schema", "metadata", "toolchain", "symbols", "configuration", "serial_boots", "records",
          "observations", "stops", "artifacts", "log_sha256"}
CONFIG_FIELDS = {"size", "alignment", "jobs", "harts", "base_seed", "l1", "sync_memory", "memory_wait",
                 "line_words", "line_count", "boots", "uart_seed"}
TOOL_NAMES = {"gcc", "cc1", "as", "ld", "collect2", "nm", "objdump", "verilator", "verilator_bin", "host_cxx", "host_cc1plus", "make"}
SETTING_KEYS = {"BUILD_DIR", "RISCV_PREFIX", "HART_COUNT", "ENABLE_L1", "SYNC_MEMORY", "MEMORY_WAIT_CYCLES",
                "L1_LINE_WORDS", "L1_LINE_COUNT", "DMA_BYTES", "DMA_ALIGNMENT", "DMA_JOBS", "DMA_SEED",
                "DMA_BOOTS", "DMA_UART_SEED", "DMA_RAM_PREFIX"}


def digest(value):
    bench.require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "invalid SHA-256")


def validate_configuration(c):
    bench.require(type(c) is dict and set(c) == CONFIG_FIELDS, "wrong DMA configuration fields")
    bench.integer(c["size"], 0, 8192); bench.require(type(c["alignment"]) is str and c["alignment"] in bench.ALIGNMENTS, "unknown alignment")
    bench.integer(c["jobs"], 1, 8); bench.integer(c["harts"], 1, 2); bench.integer(c["base_seed"], 0, bench.U32)
    bench.integer(c["boots"], 1, 16); bench.integer(c["uart_seed"], 0, bench.U32)
    bench.integer(c["l1"], 0, 1); bench.integer(c["sync_memory"], 0, 1)
    bench.integer(c["memory_wait"], c["sync_memory"], 1024)
    for key in ("line_words", "line_count"):
        # Device-cache unit supports 1x1, but the actual CPU instruction cache
        # retains its existing minimum of two words and two lines.
        bench.integer(c[key], 2 if c["l1"] else 1, 1024); bench.require(not c[key] & (c[key]-1), "invalid cache geometry")


def validate_toolchain(t):
    bench.require(type(t) is dict and set(t) == {"tools", "headers"} and type(t["tools"]) is dict and
                  set(t["tools"]) == TOOL_NAMES and type(t["headers"]) is dict and t["headers"], "incomplete DMA toolchain")
    for tool in t["tools"].values():
        bench.require(type(tool) is dict and set(tool) == {"path", "sha256", "version"}, "wrong tool fields")
        bench.require(type(tool["path"]) is str and Path(tool["path"]).is_absolute() and
                      type(tool["version"]) is str and tool["version"], "invalid tool identity")
        digest(tool["sha256"])
    for path, value in t["headers"].items():
        bench.require(type(path) is str and Path(path).is_absolute(), "invalid system header path"); digest(value)
    bench.require(any(Path(path).name == "stdint.h" for path in t["headers"]), "missing compiler integer header")


def fingerprint_tools(config):
    def resolve(path):
        actual = Path(shutil.which(path) or path).resolve()
        bench.require(actual.is_file(), "missing tool: "+path)
        return str(actual)
    compiler = resolve(config["compiler"]); host_cxx = resolve(config["host_cxx"])
    verilator = resolve(config["verilator"])
    paths = {"gcc": compiler, "nm": config["nm"], "objdump": config["objdump"], "verilator": verilator,
             "verilator_bin": str(Path(verilator).with_name("verilator_bin")), "host_cxx": host_cxx, "make": "make",
             "host_cc1plus": command([host_cxx, "-print-prog-name=cc1plus"])}
    for name in ("cc1", "as", "ld", "collect2"):
        paths[name] = command([compiler, "-print-prog-name="+name])
    tools = {}
    for name, path in paths.items():
        actual = resolve(path)
        version_args = ["-version", "-o", "/dev/null"] if name in ("cc1", "host_cc1plus") else ["--version"]
        process = subprocess.run([actual, *version_args], input="", text=True, cwd=ROOT, check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        version = ((process.stderr+process.stdout) if name in ("cc1", "collect2", "host_cc1plus") else
                   (process.stdout+process.stderr)).strip()
        bench.require(version, "tool did not report identity: "+name)
        tools[name] = dict(path=actual, sha256=sha(actual), version=version.splitlines()[0])
    headers = {}
    for source in ("software/benchmarks/dma.c", "software/drivers/aster_dma.c", "software/runtime/start_multicore.S"):
        dependencies = command([compiler, *shlex.split(config["cflags"]), "-M", source])
        names = shlex.split(dependencies.replace("\\\n", " ").split(":", 1)[1])
        headers.update({str(Path(path).resolve()): sha(path) for path in names if Path(path).is_absolute()})
    result = dict(tools=tools, headers=headers); validate_toolchain(result)
    return result


def validate_build(metadata, toolchain, c):
    flags = shlex.split(metadata["cflags"])
    for prefix, expected in (("-march=", "rv32ima"), ("-mabi=", "ilp32")):
        bench.require([f for f in flags if f.startswith(prefix)] == [prefix+expected], "wrong CPU ISA/ABI")
    for flag in ("-O2", "-ffreestanding", "-fno-builtin", "-fno-tree-loop-distribute-patterns", "-Isoftware/drivers"):
        bench.require(flags.count(flag) == 1, "missing controlled CPU copy compiler option: "+flag)
    for define, value in {"BYTES": c["size"], "ALIGNMENT": list(bench.ALIGNMENTS).index(c["alignment"]),
                          "JOBS": c["jobs"], "SEED": c["base_seed"]}.items():
        matches = [f.split("=", 1)[1] for f in flags if f.startswith("-DDMA_"+define+"=")]
        bench.require(len(matches) == 1 and int(matches[0], 0) == value, "compiler DMA define mismatch")
    ldflags = shlex.split(metadata["ldflags"])
    bench.require(len(ldflags) == 3 and ldflags[:2] == ["-T", "software/boot/link_dma_bench.ld"] and
                  ldflags[2].startswith("-Wl,-Map,"), "wrong fixed-layout benchmark linker contract")
    for key, field in (("compiler", "path"), ("compiler_version", "version"), ("compiler_sha256", "sha256")):
        bench.require(metadata[key] == toolchain["tools"]["gcc"][field], "compiler identity mismatch")
    bench.require(metadata["verilator_version"] == toolchain["tools"]["verilator"]["version"], "Verilator identity mismatch")
    invocation = metadata["build_command"]
    bench.require(invocation[:3] == ["make", "--no-print-directory", "-j2"] and invocation[-1] == "dma-bench",
                  "wrong recorded build target/invocation")
    settings = {}
    for value in invocation[3:-1]:
        bench.require("=" in value, "unexpected build option")
        key, setting = value.split("=", 1)
        bench.require(key in SETTING_KEYS and key not in settings and setting, "unknown/duplicate/empty build setting")
        settings[key] = setting
    bench.require(set(settings) == SETTING_KEYS, "incomplete build settings")
    for key, field in {"HART_COUNT":"harts", "ENABLE_L1":"l1", "SYNC_MEMORY":"sync_memory", "MEMORY_WAIT_CYCLES":"memory_wait",
                       "L1_LINE_WORDS":"line_words", "L1_LINE_COUNT":"line_count", "DMA_BYTES":"size", "DMA_JOBS":"jobs",
                       "DMA_SEED":"base_seed", "DMA_BOOTS":"boots", "DMA_UART_SEED":"uart_seed"}.items():
        bench.require(int(settings[key], 0) == c[field], "recorded build configuration mismatch")
    bench.require(settings["DMA_ALIGNMENT"] == c["alignment"] and Path(settings["BUILD_DIR"]).is_absolute() and
                  Path(settings["DMA_RAM_PREFIX"]).is_absolute(), "wrong alignment/build evidence paths")


def validate_result(result, log, directory=None, *, clean=True):
    bench.require(type(result) is dict and set(result) == FIELDS and result["schema"] == "aster.dma.capture.v1", "wrong v5 capture envelope")
    validate_metadata(result["metadata"]); validate_toolchain(result["toolchain"])
    c = result["configuration"]; validate_configuration(c)
    metadata = result["metadata"]; validate_build(metadata, result["toolchain"], c)
    if clean: source_at_revision(metadata)
    digest(result["log_sha256"])
    bench.require(hashlib.sha256(log.encode()).hexdigest() == result["log_sha256"], "raw simulation log changed")
    observed = bench.simulation_log(log, c["boots"], c["jobs"])
    for key in observed:
        bench.require(bench.typed_equal(observed[key], result[key]), "envelope differs from raw observation log")
    rows = [bench.parse_stream(serial.encode("ascii")) for serial in observed["serial_boots"]]
    bench.require(bench.typed_equal(rows, result["records"]), "typed records differ from raw UART")
    for boot in rows:
        bench.require(all(row[key] == value for row in boot for key, value in c.items() if key not in ("boots", "uart_seed")),
                      "executing configuration differs from requested build")
    bench.validate_symbols(result["symbols"], c["size"], c["jobs"])
    artifacts = result["artifacts"]
    expected = {"firmware", "elf", "map", "disassembly"} | {f"ram{boot}" for boot in range(1, c["boots"]+1)}
    bench.require(type(artifacts) is dict and set(artifacts) == expected, "missing/extra DMA artifacts")
    names, contents = set(), {}
    for key, item in artifacts.items():
        bench.require(type(item) is dict and set(item) == {"file", "sha256", "bytes"}, "wrong artifact fields")
        filename = item["file"]
        bench.require(type(filename) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", filename) and filename not in names,
                      "unsafe/duplicate artifact basename")
        names.add(filename); digest(item["sha256"]); bench.integer(item["bytes"], 1, 16*1024*1024)
        if key.startswith("ram"): bench.require(item["bytes"] == 65536, "incomplete stopped RAM")
        if key in ("firmware", "elf"):
            bench.require(item["sha256"] == metadata[key+"_sha256"], "artifact/provenance mismatch")
        if directory is not None:
            path = Path(directory)/filename
            bench.require(not path.is_symlink(), "symlink artifact is not self-contained")
            data = path.read_bytes(); contents[key] = data
            bench.require(len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"], "artifact bytes/hash changed")
    if directory is not None:
        elf = inspect_elf(contents["elf"], profile="dma_benchmark", dma_size=c["size"], dma_jobs=c["jobs"])
        bench.require(bench.typed_equal(elf["symbols"], result["symbols"]), "symbol claims differ from actual ELF")
        firmware = contents["firmware"]
        bench.require(re.fullmatch(rb"(?:[0-9a-f]{8}\n){16384}", firmware), "noncanonical/incomplete ROM")
        image = b"".join(int(word, 16).to_bytes(4, "little") for word in firmware.splitlines())
        bench.require(image == elf["image"], "ROM bytes differ from actual ELF loads")
        for boot in range(1, c["boots"]+1):
            bench.validate_ram(contents[f"ram{boot}"], rows[boot-1], elf["symbols"])
    return rows


def capture(args, build_directory=None):
    c = {key: getattr(args, "seed" if key == "base_seed" else key) for key in CONFIG_FIELDS}
    if c["memory_wait"] is None: c["memory_wait"] = c["sync_memory"]
    validate_configuration(c)
    bench.require(re.fullmatch(r"[A-Za-z0-9_./+-]+", args.riscv_prefix), "unsupported compiler prefix")
    bench.require(not args.output.is_symlink(), "output must not alias another capture through a symlink")
    output = args.output.resolve()
    bench.require(output.suffix == ".json" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", output.stem), "use a safe .json capture basename")
    suffixes = [".json", ".log", ".hex", ".elf", ".map", ".dis"]+[f".boot{i}.ram" for i in range(1, c["boots"]+1)]
    bench.require(not any(output.with_suffix(s).exists() or output.with_suffix(s).is_symlink() for s in suffixes), "capture would overwrite evidence")
    sources, fingerprint = source_state(); revision = command(["git", "rev-parse", "HEAD"])
    dirty = bool(command(["git", "status", "--porcelain"]))
    bench.require(args.allow_dirty or not dirty, "commit before reproducible capture; --allow-dirty is development only")
    # Standalone captures always rebuild in a fresh directory. Only a study
    # orchestrator may share its own freshly-created model root across sizes.
    context = nullcontext(str(build_directory)) if build_directory else tempfile.TemporaryDirectory(prefix="aster-dma-capture-")
    output.parent.mkdir(parents=True, exist_ok=True)
    with context as directory:
        build = Path(directory).resolve()
        settings = [f"BUILD_DIR={build}", f"RISCV_PREFIX={args.riscv_prefix}", f"HART_COUNT={c['harts']}",
                    f"ENABLE_L1={c['l1']}", f"SYNC_MEMORY={c['sync_memory']}", f"MEMORY_WAIT_CYCLES={c['memory_wait']}",
                    f"L1_LINE_WORDS={c['line_words']}", f"L1_LINE_COUNT={c['line_count']}", f"DMA_BYTES={c['size']}",
                    f"DMA_ALIGNMENT={c['alignment']}", f"DMA_JOBS={c['jobs']}", f"DMA_SEED={c['base_seed']}",
                    f"DMA_BOOTS={c['boots']}", f"DMA_UART_SEED={c['uart_seed']}", f"DMA_RAM_PREFIX={output.with_suffix('')}"]
        config = bench.json_record(command(["make", "--no-print-directory", "-s", *settings, "dma-config"]))
        toolchain = fingerprint_tools(config)
        invocation = ["make", "--no-print-directory", "-j2", *settings, "dma-bench"]
        # Stream directly to an exclusive log so failures/interruption preserve
        # partial progress; the simulator itself exclusively writes RAM files.
        with output.with_suffix(".log").open("xb") as log_file:
            process = subprocess.run(invocation, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)
        log = output.with_suffix(".log").read_text()
        bench.require(process.returncode == 0, "build/RTL scoreboard failed; partial log/RAM retained")
        observed = bench.simulation_log(log, c["boots"], c["jobs"])
        elf = Path(config["elf"]).read_bytes()
        found = inspect_elf(elf, profile="dma_benchmark", dma_size=c["size"], dma_jobs=c["jobs"])
        contents = {"elf": (".elf", elf), "firmware": (".hex", Path(config["firmware"]).read_bytes()),
                    "map": (".map", Path(config["elf"]).with_suffix(".map").read_bytes()),
                    "disassembly": (".dis", Path(config["elf"]).with_suffix(".dis").read_bytes())}
        artifacts = {}
        for key, (suffix, data) in contents.items():
            path = output.with_suffix(suffix)
            with path.open("xb") as f: f.write(data)
            artifacts[key] = dict(file=path.name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        for boot in range(1, c["boots"]+1):
            path = output.with_suffix(f".boot{boot}.ram")
            artifacts[f"ram{boot}"] = dict(file=path.name, sha256=sha(path), bytes=path.stat().st_size)
        metadata = dict(revision=revision, dirty=dirty, source_sha256=fingerprint, source_files=sources,
                        compiler=toolchain["tools"]["gcc"]["path"], compiler_version=toolchain["tools"]["gcc"]["version"],
                        compiler_sha256=toolchain["tools"]["gcc"]["sha256"], cflags=config["cflags"], ldflags=config["ldflags"],
                        verilator_version=toolchain["tools"]["verilator"]["version"], firmware_sha256=sha(config["firmware"]),
                        elf_sha256=sha(config["elf"]), simulator_sha256=sha(config["simulator"]), build_command=invocation,
                        platform=platform.platform())
        result = dict(schema="aster.dma.capture.v1", metadata=metadata, toolchain=toolchain, symbols=found["symbols"],
                      configuration=c, **observed, records=[bench.parse_stream(s.encode("ascii")) for s in observed["serial_boots"]],
                      artifacts=artifacts, log_sha256=hashlib.sha256(log.encode()).hexdigest())
        bench.require(source_state() == (sources, fingerprint) and command(["git", "rev-parse", "HEAD"]) == revision and
                      bool(command(["git", "status", "--porcelain"])) == dirty, "source changed during capture; retain as failed development")
        bench.require(fingerprint_tools(config) == toolchain, "toolchain changed during capture")
        validate_result(result, log, output.parent, clean=not args.allow_dirty)
        with output.open("x") as f:
            json.dump(result, f, indent=2, sort_keys=True); f.write("\n")
    print(f"PASS: DMA capture {output} (dirty={dirty})", flush=True)
    return result


def load(path, *, clean=True):
    bench.require(not path.is_symlink() and not path.with_suffix(".log").is_symlink(), "symlink capture/log is not self-contained")
    result = bench.json_record(path.read_text())
    validate_result(result, path.with_suffix(".log").read_text(), path.parent, clean=clean)
    return result


def summary(result):
    return dict(configuration=result["configuration"], revision=result["metadata"]["revision"],
                pairs=[dict(boot=boot, **pair) for boot, rows in enumerate(result["records"], 1) for pair in bench.paired_summary(rows)],
                interpretation="CPU/DMA cycle ratio below one is a DMA slowdown; includes setup, polling and completion, not CPU availability")


def main():
    p = argparse.ArgumentParser(description=__doc__); commands = p.add_subparsers(dest="action", required=True)
    run = commands.add_parser("capture"); run.add_argument("--output", type=Path, required=True)
    run.add_argument("--alignment", choices=bench.ALIGNMENTS, default="aligned")
    for key, default in (("size", 64), ("jobs", 4), ("harts", 2), ("seed", 0x13570000), ("l1", 1), ("sync-memory", 1),
                         ("memory-wait", None), ("line-words", 4), ("line-count", 16), ("boots", 2), ("uart-seed", 0)):
        run.add_argument("--"+key, type=lambda value: int(value, 0), default=default)
    run.add_argument("--riscv-prefix", default="riscv32-unknown-elf-"); run.add_argument("--allow-dirty", action="store_true")
    audit = commands.add_parser("audit"); audit.add_argument("capture", type=Path); audit.add_argument("--allow-dirty", action="store_true")
    args = p.parse_args()
    try:
        if args.action == "capture": capture(args)
        else: print(json.dumps(summary(load(args.capture, clean=not args.allow_dirty)), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        p.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
