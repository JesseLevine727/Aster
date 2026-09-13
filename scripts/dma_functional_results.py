#!/usr/bin/env python3
"""Capture/audit actual DMA functional ELF/ROM, serial, RAM and event evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shlex
import subprocess
import tempfile

import asterbench_dma as bench
import dma_functional as functional
import dma_results as results
from bench_results import ROOT, command, sha, source_state, validate_metadata
from coherent_elf import inspect_elf

SCHEMA = "aster.dma.functional-reference.v1"
CONFIG = dict(harts=2,sync_memory=1,memory_wait=1,line_words=4,line_count=16,boots=2,
              simulation_clock_hz=31250000,simulation_baud=781250)


def tools(config):
    found = results.fingerprint_tools(config)
    for name in functional.PROGRAMS.values():
        dependencies = command([config["compiler"],*shlex.split(config["cflags"]),"-M",f"software/tests/{name}.c"])
        paths = shlex.split(dependencies.replace("\\\n"," ").split(":",1)[1])
        found["headers"].update({str(Path(p).resolve()):sha(p) for p in paths if Path(p).is_absolute()})
    bench.require(any(Path(p).name == "stdatomic.h" for p in found["headers"]), "missing functional atomic compiler header")
    return found


def compile_line(log,compiler,name):
    source = f"software/tests/{name}.c"
    lines = [shlex.split(s) for s in log.replace("\\\n"," ").splitlines() if s.startswith(compiler+" ") and source in shlex.split(s)]
    bench.require(len(lines) == 1, "missing/ambiguous functional compiler command")
    args = lines[0]; split,end = args.index("-T"),args.index("-o")
    bench.require(args[end+2:] == ["software/runtime/start_multicore.S","software/drivers/aster_dma.c",source], "wrong functional compiler inputs")
    return args,shlex.join(args[1:split]),shlex.join(args[split:end])


def validate_log(log,caches):
    bench.require(type(log) is str and log.endswith("\n") and not re.search(r"(?m)(^FAIL:|^ERROR:|%Error|^Traceback|^make.*\*\*\*)",log),
                  "failed/truncated functional log")
    blocks = log.split("DMA_FUNCTIONAL_COMMAND ")
    bench.require(len(blocks) == 3, "missing functional execution commands")
    observed,commands = {},{}
    for (kind,_name),block in zip(functional.PROGRAMS.items(),blocks[1:]):
        raw_command,body = block.split("\n",1); invocation = bench.json_record(raw_command)
        bench.require(type(invocation) is list and len(invocation) == 4 and all(type(s) is str and s for s in invocation), "invalid functional execution command")
        mode = "dma" if kind == "runtime" else "publication"
        bench.require(invocation[2] == mode and all(Path(invocation[i]).is_absolute() for i in (0,1,3)), "wrong functional execution inputs")
        commands[kind] = invocation; lines = iter(body.splitlines()); observations = []
        for boot in (0,1):
            line = next(lines,"")
            pattern = rf"PASS: coherent AXI/serial {mode} harts=2 cache={caches} boot={boot} bytes={len(functional.UART[kind])} retired=([1-9]\d*),([1-9]\d*) retained_RAM=65536"
            match = re.fullmatch(pattern,line)
            bench.require(match is not None and min(map(int,match.groups())) > 100, "missing actual functional two-hart serial/RAM boot")
            line = next(lines,""); bench.require(line.startswith("DMA_FUNCTIONAL_OBS "), "missing independent event observation")
            obs = bench.json_record(line.removeprefix("DMA_FUNCTIONAL_OBS "))
            bench.require(type(obs) is dict and type(obs.get("boot")) is int and obs["boot"] == boot+1, "reordered functional observations")
            observations.append(obs)
        if kind == "runtime":
            for phase in range(3):
                pattern = rf"PASS: DMA AXI in-flight stop phase={phase} retained_RAM=65536 drained_bytes=([0-4])"
                bench.require(re.fullmatch(pattern,next(lines,"")), "missing admitted-DMA stop gate")
            bench.require(next(lines,"") == "PASS: actual-core DMA MMIO atomic and instruction-fetch denial",
                          "missing actual-core DMA MMIO access/fetch denial")
        expected = 11 if kind == "runtime" else 6
        pattern = rf"PASS: coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, full-UART stop; snapshots={expected} observed_stores=([1-9]\d*)"
        bench.require(re.fullmatch(pattern,next(lines,"")) and list(lines) == [], "missing/extra functional AXI closeout")
        observed[kind] = observations
    return observed,commands


def validate_build(program,log,caches,name):
    meta = program["metadata"]; invocation = meta["build_command"]
    bench.require(len(invocation) == 13 and invocation[:3] == ["make","--no-print-directory","-j2"],
                  "wrong functional build invocation")
    bench.require(invocation[3].startswith("BUILD_DIR="), "missing functional build directory")
    build = Path(invocation[3].split("=",1)[1]); bench.require(build.is_absolute(), "relative functional build directory")
    expected_settings = [f"BUILD_DIR={build}","HART_COUNT=2",f"ENABLE_L1={caches}","SYNC_MEMORY=1",
                         "MEMORY_WAIT_CYCLES=1","L1_LINE_WORDS=4","L1_LINE_COUNT=16"]
    simulator = build/f"linux_dma_h2_l1{caches}/aster_linux_dma_sim"
    bench.require(invocation[3:] == expected_settings+[str(simulator)]+[str(build/"software"/(n+".hex")) for n in functional.PROGRAMS.values()],
                  "wrong functional build settings/targets")
    run = program["simulation_command"]; compiler = program["compile_command"]
    bench.require(run[:2] == [str(simulator),str(build/"software"/(name+".hex"))] and
                  compiler[compiler.index("-o")+1] == str(build/"software"/(name+".elf")) and
                  shlex.split(meta["ldflags"])[-1] == "-Wl,-Map,"+str(build/"software"/(name+".map")),
                  "functional ELF/model/run paths differ")
    bench.require(meta["verilator_version"] == program["toolchain"]["tools"]["verilator"]["version"] and
                  any(Path(p).name == "stdatomic.h" for p in program["toolchain"]["headers"]),
                  "missing functional Verilator/atomic-header provenance")
    lines = [shlex.split(s) for s in log.replace("\\\n"," ").splitlines() if s.startswith("verilator ")]
    bench.require(len(lines) == 1, "missing/duplicate actual Verilator command")
    args = lines[0]
    expected_generics = ["-GHART_COUNT=2","-GENABLE_COHERENCE=1'b1",f"-GCOHERENT_L1=1'b{caches}",
                         "-GENABLE_DMA=1'b1","-GCLK_HZ=31250000","-GBAUD=781250","-GRX_DEPTH=128"]
    bench.require([s for s in args if s.startswith("-G")] == expected_generics and
                  args[args.index("--top-module")+1] == "aster_pynq_linux" and
                  args[args.index("-o")+1] == str(simulator) and
                  args[args.index("--Mdir")+1] == str(simulator.parent/"obj") and
                  shlex.split(args[args.index("-CFLAGS")+1]) == ["-DASTER_HART_COUNT=2",f"-DASTER_L1={caches}","-DASTER_DMA=1","-DASTER_CLOCK=31250000"] and
                  all(args.count(s) == 1 for s in ("--assert","-DASTER_COHERENCE_ASSERT","-DRISCV_FORMAL","--public-flat-rw")),
                  "functional model configuration/assertion flags differ")
    inputs = [p for p in args if p.endswith((".sv",".v",".cpp"))]
    bench.require(inputs and all(Path(p).is_absolute() for p in inputs) and
                  len(inputs) == len(set(inputs)) and Path(inputs[-1]).name == "tb_pynq_linux_coherent.cpp",
                  "wrong functional RTL/scoreboard inputs")
    root = str(Path(inputs[-1]).parents[2])+"/"
    names = {p.removeprefix(root) for p in inputs}
    expected = {p for p in meta["source_files"] if p.startswith("rtl/") and p.endswith(".sv")}
    expected -= {"rtl/soc/aster_pynq_z1.sv","rtl/verification/aster_smoke.sv"}
    expected |= {"vendor/picorv32/picorv32.v","verification/soc/tb_pynq_linux_coherent.cpp"}
    bench.require(names == expected, "functional model omitted/added RTL source")


def load(path,*,clean=True):
    bench.require(not path.is_symlink() and not path.with_suffix(".log").is_symlink(), "symlink functional evidence")
    m = bench.json_record(path.read_text())
    bench.require(type(m) is dict and set(m) == {"schema","configuration","programs","log_sha256"} and m["schema"] == SCHEMA,
                  "invalid functional reference envelope")
    c = m["configuration"]
    bench.require(type(c) is dict and set(c) == set(CONFIG)|{"l1"}, "wrong functional configuration fields")
    bench.integer(c["l1"],0,1); bench.require(bench.typed_equal(c,dict(CONFIG,l1=c["l1"])), "wrong functional simulation topology/timing")
    log = path.with_suffix(".log").read_text(); results.digest(m["log_sha256"])
    bench.require(sha(path.with_suffix(".log")) == m["log_sha256"], "functional log changed")
    observed,commands = validate_log(log,c["l1"])
    bench.require(type(m["programs"]) is dict and set(m["programs"]) == set(functional.PROGRAMS), "missing functional programs")
    inventory = {path.name,path.with_suffix(".log").name}; identities = []
    for kind,name in functional.PROGRAMS.items():
        program = m["programs"][kind]
        bench.require(type(program) is dict and set(program) == {"metadata","toolchain","compile_command","simulation_command","symbols","observations","artifacts"},
                      "wrong functional program fields")
        meta = program["metadata"]; validate_metadata(meta); results.validate_toolchain(program["toolchain"])
        if clean: results.source_at_revision(meta)
        identities.append((meta["revision"],meta["dirty"],meta["source_sha256"],program["toolchain"]))
        compiler = program["compile_command"]
        bench.require(type(compiler) is list and compiler and all(type(s) is str for s in compiler), "invalid compiler invocation")
        actual,cflags,ldflags = compile_line(log,compiler[0],name)
        bench.require(actual == compiler and cflags == meta["cflags"] and ldflags == meta["ldflags"], "compiler/log/flags differ")
        flags = shlex.split(cflags); ld = shlex.split(ldflags)
        bench.require([f for f in flags if f.startswith("-march=")] == ["-march=rv32ima"] and
                      [f for f in flags if f.startswith("-mabi=")] == ["-mabi=ilp32"] and
                      all(f in flags for f in ("-O2","-ffreestanding","-Isoftware/drivers")) and
                      len(ld) == 3 and ld[:2] == ["-T","software/boot/link_multicore.ld"] and ld[2].startswith("-Wl,-Map,"),
                      "wrong functional toolchain/linker contract")
        gcc = program["toolchain"]["tools"]["gcc"]
        bench.require(meta["compiler"] == gcc["path"] and meta["compiler_sha256"] == gcc["sha256"] and meta["compiler_version"] == gcc["version"] and
                      Path(compiler[0]).name == Path(gcc["path"]).name, "wrong compiler identity")
        bench.require(bench.typed_equal(commands[kind],program["simulation_command"]) and bench.typed_equal(observed[kind],program["observations"]),
                      "functional envelope differs from raw command/events")
        validate_build(program,log,c["l1"],name)
        artifacts = program["artifacts"]
        suffixes = dict(elf=".elf",firmware=".hex",map=".map",disassembly=".dis",
                        ram1=".boot1.ram",uart1=".boot1.uart",ram2=".boot2.ram",uart2=".boot2.uart")
        bench.require(type(artifacts) is dict and set(artifacts) == set(suffixes), "incomplete functional artifacts")
        contents = {}
        for key,suffix in suffixes.items():
            item = artifacts[key]; filename = (kind if key.startswith(("ram","uart")) else name)+suffix
            bench.require(type(item) is dict and set(item) == {"file","bytes","sha256"} and item["file"] == filename, "unsafe functional artifact")
            results.digest(item["sha256"]); bench.integer(item["bytes"],1,16*1024*1024)
            target = path.parent/filename; bench.require(not target.is_symlink(), "symlink functional artifact")
            raw = target.read_bytes(); contents[key] = raw; inventory.add(filename)
            bench.require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"], "functional artifact changed")
        elf = inspect_elf(contents["elf"],profile=name)
        bench.require(bench.typed_equal(elf["symbols"],program["symbols"]), "symbols differ from actual functional ELF")
        raw = contents["firmware"]
        bench.require(re.fullmatch(rb"(?:[0-9a-f]{8}\n){16384}",raw) and
                      b"".join(int(w,16).to_bytes(4,"little") for w in raw.splitlines()) == elf["image"], "ROM differs from actual functional ELF")
        for key in ("firmware","elf"): bench.require(meta[key+"_sha256"] == artifacts[key]["sha256"], "functional metadata/artifact differs")
        for index in (1,2):
            bench.require(contents[f"uart{index}"] == functional.UART[kind], "actual functional UART differs")
            values = functional.validate_ram(contents[f"ram{index}"],program,kind,c["l1"])
            functional.validate_observation(observed[kind][index-1],values,program,kind)
    bench.require(bench.typed_equal(identities[0],identities[1]), "mixed functional source/toolchain")
    runtime,publication = (m["programs"][k] for k in functional.PROGRAMS)
    bench.require(runtime["metadata"]["build_command"] == publication["metadata"]["build_command"] and
                  runtime["metadata"]["simulator_sha256"] == publication["metadata"]["simulator_sha256"] and
                  Path(runtime["simulation_command"][3]).parent == Path(publication["simulation_command"][3]).parent and
                  all(Path(m["programs"][k]["simulation_command"][3]).name == k for k in functional.PROGRAMS),
                  "mixed functional model/build/output prefixes")
    bench.require({p.name for p in path.parent.iterdir()} == inventory, "unlisted functional artifacts")
    return m


def capture(output,caches,*,allow_dirty=False):
    bench.integer(caches,0,1); bench.require(not output.is_symlink(), "symlink functional output")
    output = output.resolve(); bench.require(not output.exists(), "functional output must be new")
    dirty = bool(command(["git","status","--porcelain"]))
    bench.require(allow_dirty or not dirty, "commit functional sources before accepted capture")
    sources,fingerprint = source_state(); revision = command(["git","rev-parse","HEAD"])
    if not dirty: results.source_at_revision(dict(dirty=False,revision=revision,source_files=sources))
    output.mkdir(parents=True); log_path = output/"functional.log"
    try:
        with tempfile.TemporaryDirectory(prefix="aster-dma-functional-reference-") as directory:
            build = Path(directory); simulator = build/f"linux_dma_h2_l1{caches}/aster_linux_dma_sim"
            settings = [f"BUILD_DIR={build}","HART_COUNT=2",f"ENABLE_L1={caches}","SYNC_MEMORY=1","MEMORY_WAIT_CYCLES=1","L1_LINE_WORDS=4","L1_LINE_COUNT=16"]
            config = json.loads(command(["make","--no-print-directory","-s",*settings,"dma-config"]))
            config["cflags"] = shlex.join([f for f in shlex.split(config["cflags"]) if not f.startswith("-DDMA_")])
            before = tools(config)
            invocation = ["make","--no-print-directory","-j2",*settings,str(simulator)]+[str(build/"software"/(name+".hex")) for name in functional.PROGRAMS.values()]
            with log_path.open("xb") as log:
                result = subprocess.run(invocation,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                bench.require(result.returncode == 0, "functional model/firmware build failed")
                for kind,name in functional.PROGRAMS.items():
                    args = [str(simulator),str(build/"software"/(name+".hex")),"dma" if kind == "runtime" else "publication",str(output/kind)]
                    log.write(("DMA_FUNCTIONAL_COMMAND "+json.dumps(args)+"\n").encode()); log.flush()
                    result = subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                    bench.require(result.returncode == 0, "functional actual-core Linux scoreboard failed")
            log = log_path.read_text(); observations,commands = validate_log(log,caches); programs = {}
            for kind,name in functional.PROGRAMS.items():
                compiler,cflags,ldflags = compile_line(log,config["compiler"],name)
                toolchain = tools(dict(config,cflags=cflags)); bench.require(toolchain == before, "functional toolchain changed")
                artifacts = {}
                for key,suffix in (("elf",".elf"),("firmware",".hex"),("map",".map"),("disassembly",".dis")):
                    target = output/(name+suffix); raw = (build/"software"/(name+suffix)).read_bytes()
                    with target.open("xb") as handle: handle.write(raw)
                    artifacts[key] = dict(file=target.name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                for index in (1,2):
                    for ext in ("uart","ram"):
                        target = output/(kind+f".boot{index}.{ext}")
                        artifacts[ext+str(index)] = dict(file=target.name,bytes=target.stat().st_size,sha256=sha(target))
                meta = dict(revision=revision,dirty=dirty,source_files=sources,source_sha256=fingerprint,
                    compiler=toolchain["tools"]["gcc"]["path"],compiler_version=toolchain["tools"]["gcc"]["version"],
                    compiler_sha256=toolchain["tools"]["gcc"]["sha256"],cflags=cflags,ldflags=ldflags,
                    verilator_version=toolchain["tools"]["verilator"]["version"],firmware_sha256=artifacts["firmware"]["sha256"],
                    elf_sha256=artifacts["elf"]["sha256"],simulator_sha256=sha(simulator),build_command=invocation,platform=platform.platform())
                programs[kind] = dict(metadata=meta,toolchain=toolchain,compile_command=compiler,simulation_command=commands[kind],
                    symbols=inspect_elf((output/(name+".elf")).read_bytes(),profile=name)["symbols"],observations=observations[kind],artifacts=artifacts)
            bench.require(source_state() == (sources,fingerprint) and command(["git","rev-parse","HEAD"]) == revision and
                          bool(command(["git","status","--porcelain"])) == dirty, "source changed during functional capture")
            manifest = dict(schema=SCHEMA,configuration=dict(CONFIG,l1=caches),programs=programs,log_sha256=sha(log_path))
            path = output/"functional.json"
            with path.open("x") as handle: handle.write(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
            load(path,clean=not dirty)
    except BaseException as error:
        with (output/"failure.json").open("x") as handle: handle.write(json.dumps(dict(status="failed",error=str(error)))+"\n")
        raise
    print(f"PASS: DMA functional actual ELF/ROM/UART/RAM/event reference, {path} (dirty={dirty})",flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); actions = parser.add_subparsers(dest="action",required=True)
    run = actions.add_parser("capture"); run.add_argument("--output",type=Path,required=True)
    run.add_argument("--no-cache",action="store_true"); run.add_argument("--allow-dirty",action="store_true")
    check = actions.add_parser("audit"); check.add_argument("manifest",type=Path)
    args = parser.parse_args()
    try:
        if args.action == "capture": capture(args.output,int(not args.no_cache),allow_dirty=args.allow_dirty)
        else: load(args.manifest); print("PASS: DMA functional reference and Git audit")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
