#!/usr/bin/env python3
"""Clean ELF/ROM/reference evidence for full-A C runtime and reset lifecycle.

Uses existing firmware and the actual Linux/serial RTL testbench; no Makefile,
RTL or pinned-core changes. Final RAM oracles use real audited ELF symbols.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shlex
import struct
import subprocess
import tempfile

import asterbench_coherent as bench
import coherent_results as results
from bench_results import ROOT, command, sha, source_state, validate_metadata
from coherent_elf import inspect_elf

SCHEMA = "aster.coherent.functional-reference.v1"
PROGRAMS = {"runtime": "atomic_runtime", "lifecycle": "coherent_lifecycle"}
UART = {"runtime": b"RV32A DIRECTED PASS\n"+b"RV32A JOB PASS\n"*3,
        "lifecycle": b"COHERENT LIFECYCLE PASS\n"}
CONFIG = dict(harts=2, sync_memory=1, memory_wait=1, line_words=4, line_count=16, boots=2,
              simulation_clock_hz=400, simulation_baud=10)


def validate_log(log, caches):
    bench.require(not re.search(r"(?m)(FAIL:|ERROR:|%Error|^Traceback|^make.*\*\*\*)", log), "failed functional simulation")
    expected = {(kind, boot) for kind in PROGRAMS for boot in (0, 1)}; found = set()
    pattern = r"^PASS: coherent AXI/serial (runtime|lifecycle) harts=(\d+) cache=(\d+) boot=(\d+) bytes=(\d+) retired=(\d+),(\d+) retained_RAM=65536$"
    for line in log.splitlines():
        if not line.startswith("PASS: coherent AXI/serial"): continue
        match = re.fullmatch(pattern, line); bench.require(match is not None, "malformed functional serial proof")
        kind, harts, cache, boot, count, h0, h1 = match.groups(); key = kind, int(boot)
        bench.require(key in expected and key not in found and int(harts) == 2 and int(cache) == caches and
                      int(count) == len(UART[kind]) and int(h0) > 100 and int(h1) > 100, "incomplete/wrong functional execution")
        found.add(key)
    bench.require(found == expected, "missing functional Linux serial boots")
    terminals = [line for line in log.splitlines() if line.startswith("PASS: coherent AXI stop/boot/RAM gates")]
    pattern = r"PASS: coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, full-UART stop; snapshots=6 observed_stores=([1-9][0-9]*)"
    bench.require(len(terminals) == 2 and all(re.fullmatch(pattern, line) for line in terminals), "missing functional lifecycle/AXI/RAM guards")


def expected_words(kind):
    bench.require(kind in PROGRAMS, "unknown functional program")
    if kind == "runtime":
        return dict(probe_results=[3, 256, 256, 256, 256, 24768, 1, 2], directed_word=[9],
                    counter=[256], cas_counter=[256], lrsc_counter=[256], protected_counter=[256],
                    protected_sum=[24768], lock_word=[0], start_epoch=[3], done=[3, 3])
    pattern = lambda index: 0x57a60000 ^ (8*0x10309) ^ (index*0x10101)
    return dict(requested_epoch=[8], done_epoch=[8], generations=[8], worker_error=[0],
                primary_reservation=[8], secondary_reservation=[0], payload=[pattern(i) for i in range(64)],
                primary_private=[0x13570000+i for i in range(16)], secondary_private=[pattern(i) for i in range(16)])


def validate_ram(data, program, kind):
    bench.require(type(data) is bytes and len(data) == 65536, "incomplete functional RAM snapshot")
    for name, values in expected_words(kind).items():
        symbol = program["symbols"][name]; offset = symbol["address"]-0x10000000
        bench.require(symbol["size"] == len(values)*4 and 0 <= offset <= 65536-len(values)*4, "wrong functional symbol bounds")
        actual = struct.unpack_from("<"+"I"*len(values), data, offset)
        bench.require(actual == tuple(values), "independent functional RAM mismatch: "+name)


def compile_line(log, compiler, name):
    source = f"software/tests/{name}.c"
    candidates = []
    for line in log.replace("\\\n", " ").splitlines():
        if not line.startswith(compiler+" "): continue
        args = shlex.split(line)
        if source in args: candidates.append(args)
    bench.require(len(candidates) == 1, "missing/ambiguous actual firmware compiler command")
    args = candidates[0]; split, output = args.index("-T"), args.index("-o")
    bench.require(args[output+2:] == ["software/runtime/start_multicore.S", source], "unexpected compiler input source")
    return args, shlex.join(args[1:split]), shlex.join(args[split:output])


def load(path, *, clean=True):
    bench.require(not path.is_symlink(), "symlink functional manifest")
    m = bench.json_record(path.read_text())
    bench.require(type(m) is dict and set(m) == {"schema", "configuration", "programs", "log_sha256"} and m["schema"] == SCHEMA,
                  "invalid functional reference envelope")
    c = m["configuration"]; bench.require(type(c) is dict and set(c) == set(CONFIG) | {"l1"}, "wrong functional configuration")
    bench.integer(c["l1"], 0, 1)
    bench.require(results.typed_equal(c, dict(CONFIG, l1=c["l1"])), "wrong functional reference timing/topology")
    log_path = path.with_suffix(".log"); bench.require(not log_path.is_symlink(), "symlink functional log")
    log = log_path.read_text(); results.digest(m["log_sha256"])
    bench.require(sha(log_path) == m["log_sha256"], "functional raw log changed"); validate_log(log, c["l1"])
    bench.require(type(m["programs"]) is dict and set(m["programs"]) == set(PROGRAMS), "missing functional firmware")
    files = {path.name, log_path.name}; identities = []
    for kind, name in PROGRAMS.items():
        program = m["programs"][kind]
        bench.require(type(program) is dict and set(program) == {"metadata", "toolchain", "compile_command", "symbols", "artifacts"}, "wrong program fields")
        meta = program["metadata"]; validate_metadata(meta); results.validate_toolchain(program["toolchain"])
        bench.require(meta["dirty"] is False, "functional acceptance requires clean source")
        if clean: results.source_at_revision(meta)
        identities.append((meta["revision"], meta["source_sha256"], program["toolchain"]["tools"]))
        compiler = program["compile_command"]
        bench.require(type(compiler) is list and compiler and all(type(s) is str for s in compiler), "invalid compiler invocation")
        actual, cflags, ldflags = compile_line(log, compiler[0], name)
        bench.require(actual == compiler and cflags == meta["cflags"] and ldflags == meta["ldflags"], "compiler command/log/flags mismatch")
        flags = shlex.split(cflags)
        bench.require([f for f in flags if f.startswith("-march=")] == ["-march=rv32ima"] and
                      [f for f in flags if f.startswith("-mabi=")] == ["-mabi=ilp32"], "wrong functional ISA/ABI")
        gcc = program["toolchain"]["tools"]["gcc"]
        bench.require(meta["compiler"] == gcc["path"] and meta["compiler_sha256"] == gcc["sha256"] and
                      meta["compiler_version"] == gcc["version"] and Path(compiler[0]).name == Path(gcc["path"]).name,
                      "compiler/tool provenance differs")
        artifacts = program["artifacts"]; bench.require(type(artifacts) is dict and set(artifacts) == {"elf", "firmware", "map", "disassembly"}, "missing functional artifacts")
        data = {}
        for key, suffix in (("elf", ".elf"), ("firmware", ".hex"), ("map", ".map"), ("disassembly", ".dis")):
            item = artifacts[key]; filename = name+suffix
            bench.require(type(item) is dict and set(item) == {"file", "sha256", "bytes"} and item["file"] == filename, "invalid functional artifact")
            results.digest(item["sha256"]); bench.integer(item["bytes"], 1)
            target = path.parent/filename; bench.require(not target.is_symlink(), "symlink functional artifact")
            data[key] = target.read_bytes(); files.add(filename)
            bench.require(len(data[key]) == item["bytes"] and hashlib.sha256(data[key]).hexdigest() == item["sha256"], "functional artifact changed")
        elf = inspect_elf(data["elf"], profile=kind)
        bench.require(results.typed_equal(elf["symbols"], program["symbols"]), "functional symbols differ from actual ELF")
        firmware = data["firmware"]
        bench.require(re.fullmatch(rb"(?:[0-9a-f]{8}\n){16384}", firmware) and
                      b"".join(int(w, 16).to_bytes(4, "little") for w in firmware.splitlines()) == elf["image"], "functional ROM differs from ELF")
        for key in ("firmware", "elf"):
            bench.require(meta[key+"_sha256"] == artifacts[key]["sha256"], "functional provenance/artifact mismatch")
    bench.require(identities[0] == identities[1], "mixed functional source/toolchain")
    bench.require({p.name for p in path.parent.iterdir()} == files, "unlisted functional reference artifacts")
    return m


def capture(output, caches):
    output = output.resolve(); bench.integer(caches, 0, 1)
    bench.require(not output.exists(), "functional output must be a new directory")
    bench.require(not command(["git", "status", "--porcelain"]), "functional capture needs clean committed source")
    sources, fingerprint = source_state(); revision = command(["git", "rev-parse", "HEAD"])
    results.source_at_revision(dict(dirty=False, revision=revision, source_files=sources))
    with tempfile.TemporaryDirectory(prefix="aster-functional-reference-") as directory:
        build = Path(directory)
        settings = [f"BUILD_DIR={build}", "HART_COUNT=2", f"ENABLE_L1={caches}", "SYNC_MEMORY=1", "MEMORY_WAIT_CYCLES=1", "L1_LINE_WORDS=4", "L1_LINE_COUNT=16"]
        config = json.loads(command(["make", "-s", "--no-print-directory", *settings, "coherent-config"]))
        config["cflags"] = shlex.join([f for f in shlex.split(config["cflags"]) if not f.startswith("-DCOHERENT_")])
        before = {kind: results.fingerprint_tools(config, source=f"software/tests/{name}.c") for kind, name in PROGRAMS.items()}
        invocation = ["make", "--no-print-directory", "-j2", *settings, "linux-coherent-sim"]
        process = subprocess.run(invocation, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output.mkdir(parents=True); (output/"functional.log").write_text(process.stdout)
        bench.require(process.returncode == 0, "functional Linux/serial scoreboard failed; raw log retained")
        validate_log(process.stdout, caches); programs = {}
        simulator = build/f"linux_coherent_h2_l1{caches}"/"aster_linux_coherent_sim"
        for kind, name in PROGRAMS.items():
            elf = build/"software"/(name+".elf"); firmware = elf.with_suffix(".hex")
            compiler, cflags, ldflags = compile_line(process.stdout, config["compiler"], name)
            actual_config = dict(config, cflags=cflags)
            toolchain = results.fingerprint_tools(actual_config, source=f"software/tests/{name}.c")
            bench.require(toolchain == before[kind], "functional toolchain changed during build")
            contents = {"elf": (".elf", elf.read_bytes()), "firmware": (".hex", firmware.read_bytes()),
                        "map": (".map", elf.with_suffix(".map").read_bytes()),
                        "disassembly": (".dis", subprocess.check_output([toolchain["tools"]["objdump"]["path"], "-d", str(elf)]))}
            artifacts = {}
            for key, (suffix, data) in contents.items():
                target = output/(name+suffix); target.write_bytes(data)
                artifacts[key] = dict(file=target.name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            meta = dict(revision=revision, dirty=False, source_sha256=fingerprint, source_files=sources,
                compiler=toolchain["tools"]["gcc"]["path"], compiler_version=toolchain["tools"]["gcc"]["version"],
                compiler_sha256=toolchain["tools"]["gcc"]["sha256"], cflags=cflags, ldflags=ldflags,
                verilator_version=toolchain["tools"]["verilator"]["version"], firmware_sha256=sha(firmware), elf_sha256=sha(elf),
                simulator_sha256=sha(simulator), build_command=invocation, platform=platform.platform())
            programs[kind] = dict(metadata=meta, toolchain=toolchain, compile_command=compiler,
                                 symbols=inspect_elf(contents["elf"][1], profile=kind)["symbols"], artifacts=artifacts)
        bench.require(source_state() == (sources, fingerprint) and command(["git", "rev-parse", "HEAD"]) == revision and
                      not command(["git", "status", "--porcelain"]), "source changed during functional capture")
        manifest = dict(schema=SCHEMA, configuration=dict(CONFIG, l1=caches), programs=programs,
                        log_sha256=hashlib.sha256(process.stdout.encode()).hexdigest())
        path = output/"functional.json"; path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n"); load(path)
    print(f"PASS: clean functional ELF/ROM/Linux-serial reference, {path}", flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("capture"); run.add_argument("--output", type=Path, required=True); run.add_argument("--caches", type=int, choices=(0, 1), required=True)
    check = actions.add_parser("audit"); check.add_argument("reference", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "capture": capture(args.output, args.caches)
        else: load(args.reference); print("PASS: functional reference/ELF/source audit")
    except (ValueError, OSError, subprocess.CalledProcessError) as error: parser.exit(1, f"FAIL: {error}\n")
