#!/usr/bin/env python3
"""Capture/audit clean actual-core DOT8 functional RAM/UART/ELF/event evidence.

Saved commands are compared as data, never executed by the read-only auditor.
The optional evidence harness retains its complete two-boot and 13-boundary
scoreboards. This is distinct from the fixed AsterBench performance study.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shlex
import subprocess
import tempfile

import asterbench_dot8 as bench
import dot8_functional as functional
import dot8_results as results
from bench_results import ROOT, command, sha, source_state, validate_metadata
from coherent_elf import inspect_elf

SCHEMA = "aster.dot8.functional-reference.v1"
CONFIG = dict(harts=2,sync_memory=1,memory_wait=1,line_words=4,line_count=16,boots=2)
PROGRAMS = ("dot8_runtime","dot8_stop_fixture")
FLAGS = "-mabi=ilp32 -nostdlib -nostartfiles -nodefaultlibs -ffreestanding -Wall -Wextra -Werror -O2 -fno-pic -fno-stack-protector -msmall-data-limit=0 -Isoftware/runtime -march=rv32ima -Isoftware/drivers".split()
EXTRA = "-Isoftware/benchmarks -fno-builtin -fno-tree-loop-distribute-patterns".split()
RTL = "rtl/core/aster_picorv32.sv vendor/picorv32/picorv32.v rtl/cache/aster_l1_cache.sv rtl/memory/aster_rom.sv rtl/memory/aster_ram.sv rtl/core/aster_pcpi_atomic.sv rtl/core/aster_pcpi_dot8.sv rtl/core/aster_atomic_hart.sv rtl/cache/aster_coherent_cache.sv rtl/interconnect/aster_atomic_fabric.sv rtl/soc/aster_warm_stop.sv rtl/peripherals/aster_uart.sv rtl/peripherals/aster_coherent_perf.sv rtl/dma/aster_dma_engine.sv rtl/interconnect/aster_dma_arbiter.sv rtl/peripherals/aster_dma_perf.sv rtl/peripherals/aster_dot8_perf.sv rtl/soc/aster_coherent_soc.sv verification/soc/tb_aster_dot8_soc.cpp".split()


def settings(build,caches):
    return [f"BUILD_DIR={build}","HART_COUNT=2",f"ENABLE_L1={caches}","SYNC_MEMORY=1","MEMORY_WAIT_CYCLES=1","L1_LINE_WORDS=4","L1_LINE_COUNT=16"]


def simulator(build,caches):
    return build/f"dot8_soc_h2_l1{caches}_sync1_wait1_w4_n16/aster_dot8_soc_sim"


def invocation(build,caches):
    return ["make","--no-print-directory","-j2",*settings(build,caches),str(simulator(build,caches))]+[str(build/"software"/(n+".hex")) for n in PROGRAMS]


def execution(build,caches,prefix):
    return [str(simulator(build,caches)),"+rom="+str(build/"software/dot8_runtime.hex"),"+ram_fill=a5a5a5a5",
            "--stop-rom="+str(build/"software/dot8_stop_fixture.hex"),"--evidence-prefix="+str(prefix)]


def tools(config):
    found = results.fingerprint_tools(dict(config,cflags=shlex.join(FLAGS+EXTRA)))
    for name in PROGRAMS:
        dependencies = command([config["compiler"],*(FLAGS+EXTRA),"-M",f"software/tests/{name}.c"])
        names = shlex.split(dependencies.replace("\\\n"," ").split(":",1)[1])
        found["headers"].update({str(Path(p).resolve()):sha(p) for p in names if Path(p).is_absolute()})
    bench.require(any(Path(p).name == "stdatomic.h" for p in found["headers"]), "missing functional atomic compiler header")
    return found


def validate_log(raw,caches):
    bench.require(type(raw) is str and raw.endswith("\n") and "\x00" not in raw, "truncated functional log")
    lines = iter(raw.splitlines()); observations = []
    for boot in (1,2):
        progress = {0:[],1:[]}; line = next(lines,"")
        while line.startswith("OBS: "):
            match = re.fullmatch(r"OBS: hart=([01]) directed_pairs=(\d+)",line)
            bench.require(match is not None,"unknown functional progress")
            progress[int(match[1])].append(int(match[2])); line = next(lines,"")
        bench.require(progress == {h:[128,256,384,512,640] for h in (0,1)}, "incomplete/reordered directed progress")
        suffix = "; all 16 byte alignments, tails, dot/FIR/GEMM, actual output stores, full guards/RAM, LRSC, dirty DMA publication, ABI 6"
        match = re.fullmatch(rf"PASS: dot8 C runtime harts=2 cache={caches} wait=1 cycles=([1-9]\d*) pairs=1472 output_methods=2947 dots=25393,25689 DMA-overlap=([1-9]\d*)"+re.escape(suffix),line)
        bench.require(match is not None and int(match[1]) > 1000000,"incomplete actual functional runtime")
        line = next(lines,""); bench.require(line.startswith("DOT8_FUNCTIONAL_OBS "),"missing functional independent events")
        observation = bench.json_record(line.removeprefix("DOT8_FUNCTIONAL_OBS "))
        bench.require(type(observation) is dict and type(observation.get("boot")) is int and observation["boot"] == boot and
                      type(observation.get("dma_overlap")) is int and observation["dma_overlap"] == int(match[2]),"reordered/inconsistent functional observation")
        observations.append(observation)
    expected = [(h,p,0) for h in (0,1) for p in range(4)]+[(0,4,0)]+[(0,p,1) for p in range(4)]
    for h,p,e in expected:
        match = re.fullmatch(rf"PASS: dot8 warm stop hart={h} point={p} escalation={e} selective=(\d+); all admitted sums completed, no reset race, full retained RAM",next(lines,""))
        bench.require(match is not None,"missing/reordered admitted-compute stop boundary")
        count = int(match[1])
        bench.require(count == 0 if h == 1 else count >= 8 if p == 4 else count == (0 if p == 0 else 1) if e else count <= 1,
                      "wrong selective/global reset history")
    match = re.fullmatch(r"PASS: dot8 runtime closeout warm_boots=2 stopped=15 CPU-stores=([1-9]\d*) DMA-stores=([1-9]\d*); no reset after initial POR",next(lines,""))
    bench.require(match is not None and int(match[1]) > 2000000 and int(match[2]) >= 1024 and list(lines) == [],"incomplete/extra full-RAM functional closeout")
    return observations


def compile_line(log,compiler,name):
    source = f"software/tests/{name}.c"
    lines = [shlex.split(s) for s in log.replace("\\\n"," ").splitlines() if s.startswith(compiler+" ") and source in shlex.split(s)]
    bench.require(len(lines) == 1,"missing/ambiguous functional compiler command")
    args = lines[0]; split,end = args.index("-T"),args.index("-o")
    inputs = ["software/runtime/start_multicore.S","software/drivers/aster_dma.c"]
    if name == PROGRAMS[0]: inputs += ["software/benchmarks/dot8_kernels.c"]
    bench.require(args[1:split] == FLAGS+(EXTRA if name == PROGRAMS[0] else []) and args[end+2:] == inputs+[source],"wrong fixed functional compiler recipe/inputs")
    return args,shlex.join(args[1:split]),shlex.join(args[split:end])


def validate_build(m,log):
    bench.require(log.endswith("\n") and not re.search(r"(?m)(^FAIL:|^ERROR:|^FAILED\b|^Traceback|%Error|make[^\n]*\*\*\*)",log),"failed/truncated functional build")
    meta = m["metadata"]; cmd = meta["build_command"]; caches = m["configuration"]["l1"]
    bench.require(len(cmd) == 13 and cmd[3].startswith("BUILD_DIR="),"wrong functional build invocation")
    build = Path(cmd[3].split("=",1)[1]); bench.require(build.is_absolute() and cmd == invocation(build,caches),"wrong functional build settings/targets")
    run = m["simulation_command"]
    bench.require(type(run) is list and len(run) == 5 and type(run[-1]) is str and run[-1].startswith("--evidence-prefix="),"invalid functional execution command")
    prefix = Path(run[-1].split("=",1)[1]); bench.require(prefix.is_absolute() and prefix.name == "runtime" and run == execution(build,caches,prefix),"functional model/firmware/evidence paths differ")
    for name,program in m["programs"].items():
        gcc = program["compile_command"][0]
        bench.require(gcc == "riscv32-unknown-elf-gcc" or gcc == m["toolchain"]["tools"]["gcc"]["path"],"unbound compiler prefix")
        args,cflags,ldflags = compile_line(log,gcc,name)
        bench.require(args == program["compile_command"] and args[args.index("-o")+1] == str(build/"software"/(name+".elf")) and
                      shlex.split(ldflags) == ["-T","software/boot/link_multicore.ld","-Wl,-Map,"+str(build/"software"/(name+".map"))],"compiler/ELF/linker map paths differ")
        if name == PROGRAMS[0]: bench.require((cflags,ldflags) == (meta["cflags"],meta["ldflags"]),"compiler flags differ from metadata")
    lines = [shlex.split(s) for s in log.replace("\\\n"," ").splitlines() if s.startswith("verilator ")]
    bench.require(len(lines) == 1,"missing/duplicate Verilator build")
    args = lines[0]
    generics = ["-GHART_COUNT=2",f"-GENABLE_L1=1'b{caches}","-GENABLE_DMA=1'b1","-GENABLE_DOT8=1'b1","-GSYNC_MEMORY=1'b1",
                "-GMEMORY_WAIT_CYCLES=1","-GHOST_BOOT=1'b1","-GLINE_WORDS=4","-GLINE_COUNT=16"]
    bench.require([s for s in args if s.startswith("-G")] == generics and args[args.index("--top-module")+1] == "aster_coherent_soc" and
                  args[args.index("-o")+1] == str(simulator(build,caches)) and args[args.index("--Mdir")+1] == str(simulator(build,caches).parent/"obj") and
                  shlex.split(args[args.index("-CFLAGS")+1]) == ["-DASTER_HART_COUNT=2",f"-DASTER_L1={caches}","-DASTER_MEMORY_WAIT=1"] and
                  all(args.count(s) == 1 for s in ("--assert","-DASTER_COHERENCE_ASSERT","-DASTER_DOT8_ASSERT","-DRISCV_FORMAL")),"functional model configuration/assertions differ")
    inputs = [p for p in args if p.endswith((".sv",".v",".cpp"))]
    bench.require(inputs and all(Path(p).is_absolute() for p in inputs),"relative functional model inputs")
    root = Path(inputs[-1]).parents[2]
    bench.require(inputs == [str(root/p) for p in RTL] and all(p in meta["source_files"] for p in RTL),"functional model source inventory differs")


def load(path,*,clean=True):
    bench.require(not path.is_symlink(),"symlink functional manifest")
    m = bench.json_record(path.read_text())
    fields = {"schema","configuration","metadata","toolchain","programs","simulation_command","observations","artifacts"}
    bench.require(type(m) is dict and set(m) == fields and m["schema"] == SCHEMA,"invalid functional envelope")
    c = m["configuration"]; bench.require(type(c) is dict and set(c) == set(CONFIG)|{"l1"},"wrong functional configuration")
    bench.integer(c["l1"],0,1); bench.require(bench.typed_equal(c,dict(CONFIG,l1=c["l1"])),"nonphysical functional topology/timing")
    meta = m["metadata"]; validate_metadata(meta); results.validate_toolchain(m["toolchain"])
    if clean: results.source_at_revision(meta)
    for key,field in (("compiler","path"),("compiler_sha256","sha256"),("compiler_version","version")):
        bench.require(meta[key] == m["toolchain"]["tools"]["gcc"][field],"functional compiler identity differs")
    bench.require(meta["verilator_version"] == m["toolchain"]["tools"]["verilator"]["version"] and
                  any(Path(p).name == "stdatomic.h" for p in m["toolchain"]["headers"]),"functional tool/header identity missing")
    bench.require(type(m["programs"]) is dict and set(m["programs"]) == set(PROGRAMS),"missing functional ROM/program")
    expected = {"build_log":"build.log","simulation_log":"functional.log"}
    expected.update({n+ext:n+ext for n in PROGRAMS for ext in (".elf",".hex",".map",".dis")})
    expected.update({f"{ext}{i}":f"runtime.boot{i}.{ext}" for i in (1,2) for ext in ("ram","uart")})
    bench.require(type(m["artifacts"]) is dict and set(m["artifacts"]) == set(expected),"incomplete functional artifacts")
    contents = {}
    for key,name in expected.items():
        item = m["artifacts"][key]
        bench.require(type(item) is dict and set(item) == {"file","bytes","sha256"} and item["file"] == name,"unsafe functional artifact")
        results.digest(item["sha256"]); bench.integer(item["bytes"],1,16777216)
        target = path.parent/name; bench.require(not target.is_symlink(),"symlink functional artifact")
        raw = target.read_bytes(); contents[key] = raw
        bench.require(len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],"functional artifact changed")
    validate_build(m,contents["build_log"].decode())
    observed = validate_log(contents["simulation_log"].decode(),c["l1"])
    bench.require(bench.typed_equal(observed,m["observations"]),"functional event envelope differs from raw log")
    for name in PROGRAMS:
        p = m["programs"][name]
        bench.require(type(p) is dict and set(p) == {"symbols","compile_command"},"invalid functional program fields")
        elf = inspect_elf(contents[name+".elf"],profile=name)
        bench.require(bench.typed_equal(p["symbols"],elf["symbols"]),"functional symbols differ from actual ELF")
        rom = contents[name+".hex"]
        bench.require(re.fullmatch(rb"(?:[0-9a-f]{8}\n){16384}",rom) and b"".join(int(w,16).to_bytes(4,"little") for w in rom.splitlines()) == elf["image"],"functional ROM differs from executable ELF")
        results.validate_disassembly(contents[name+".dis"],elf)
        # GNU maps omit file-local BSS symbols; those are independently bound
        # through the ELF symbol/section inspector, not invented map entries.
        public = {k:v for k,v in elf["symbols"].items() if k.startswith("aster_") or k == "main"}
        results.validate_map(contents[name+".map"],dict(symbols=public))
    for key,suffix in (("firmware",".hex"),("elf",".elf")):
        bench.require(meta[key+"_sha256"] == m["artifacts"][PROGRAMS[0]+suffix]["sha256"],"functional firmware metadata differs")
    for i in (1,2):
        bench.require(contents[f"uart{i}"] == functional.UART,"actual functional UART differs")
        values = functional.validate_ram(contents[f"ram{i}"],m["programs"][PROGRAMS[0]],c["l1"])
        functional.validate_observation(observed[i-1],values,i)
    bench.require({p.name for p in path.parent.iterdir()} == {path.name}|set(expected.values()),"unlisted functional artifacts")
    return m


def capture(output,caches,*,allow_dirty=False):
    bench.integer(caches,0,1); bench.require(not output.is_symlink(),"symlink functional output")
    output = output.resolve(); bench.require(not output.exists(),"functional output must be new")
    dirty = bool(command(["git","status","--porcelain"]))
    bench.require(allow_dirty or not dirty,"commit functional sources before accepted capture")
    sources,fingerprint = source_state(); revision = command(["git","rev-parse","HEAD"])
    output.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix="aster-dot8-functional-") as directory:
            build = Path(directory); config = bench.json_record(command(["make","--no-print-directory","-s",*settings(build,caches),"dot8-config"]))
            before = tools(config); cmd = invocation(build,caches)
            with (output/"build.log").open("xb") as log:
                process = subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            bench.require(process.returncode == 0,"functional build failed; evidence retained")
            run = execution(build,caches,output/"runtime")
            with (output/"functional.log").open("xb") as log:
                process = subprocess.run(run,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            bench.require(process.returncode == 0,"functional actual-core scoreboard failed; evidence retained")
            programs = {}; log = (output/"build.log").read_text()
            for name in PROGRAMS:
                args,cflags,ldflags = compile_line(log,config["compiler"],name)
                if name == PROGRAMS[0]: runtime_flags = (cflags,ldflags)
                for ext in (".elf",".hex",".map",".dis"):
                    with (output/(name+ext)).open("xb") as handle: handle.write((build/"software"/(name+ext)).read_bytes())
                programs[name] = dict(compile_command=args,symbols=inspect_elf((output/(name+".elf")).read_bytes(),profile=name)["symbols"])
            artifacts = {}
            for p in output.iterdir():
                key = "build_log" if p.name == "build.log" else "simulation_log" if p.name == "functional.log" else p.name
                match = re.fullmatch(r"runtime.boot([12]).(ram|uart)",p.name)
                if match: key = match[2]+match[1]
                artifacts[key] = dict(file=p.name,bytes=p.stat().st_size,sha256=sha(p))
            bench.require(tools(config) == before and source_state() == (sources,fingerprint) and command(["git","rev-parse","HEAD"]) == revision and
                          bool(command(["git","status","--porcelain"])) == dirty,"source/tool changed during functional capture")
            gcc = before["tools"]["gcc"]
            meta = dict(revision=revision,dirty=dirty,source_files=sources,source_sha256=fingerprint,compiler=gcc["path"],compiler_version=gcc["version"],
                        compiler_sha256=gcc["sha256"],cflags=runtime_flags[0],ldflags=runtime_flags[1],verilator_version=before["tools"]["verilator"]["version"],
                        firmware_sha256=artifacts["dot8_runtime.hex"]["sha256"],elf_sha256=artifacts["dot8_runtime.elf"]["sha256"],
                        simulator_sha256=sha(simulator(build,caches)),build_command=cmd,platform=platform.platform())
            m = dict(schema=SCHEMA,configuration=dict(CONFIG,l1=caches),metadata=meta,toolchain=before,programs=programs,simulation_command=run,
                     observations=validate_log((output/"functional.log").read_text(),caches),artifacts=artifacts)
            path = output/"functional.json"
            with path.open("x") as handle: handle.write(json.dumps(m,indent=2,sort_keys=True)+"\n")
            load(path,clean=not dirty)
    except BaseException as error:
        with (output/"failure.json").open("x") as handle: handle.write(json.dumps(dict(status="failed",error=str(error)))+"\n")
        raise
    print(f"PASS: DOT8 functional actual-core ELF/ROM/UART/RAM/events, {path} (dirty={dirty})",flush=True)
    return m


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="action",required=True)
    cap = commands.add_parser("capture"); cap.add_argument("output",type=Path); cap.add_argument("--l1",type=int,choices=(0,1),required=True); cap.add_argument("--allow-dirty",action="store_true")
    aud = commands.add_parser("audit"); aud.add_argument("manifest",type=Path); args = parser.parse_args()
    try:
        if args.action == "capture": capture(args.output,args.l1,allow_dirty=args.allow_dirty)
        else: load(args.manifest); print("PASS: complete DOT8 functional reference and Git audit")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
