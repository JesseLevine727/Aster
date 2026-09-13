#!/usr/bin/env python3
"""Read-only, self-contained README Phase 7 requirement/evidence closeout.

Never connects to a board or executes saved commands/tool paths. Independent
inner audits verify raw semantics and Git blobs; this outer audit binds their
complete inventory, unchanged implementation and physical programming chain.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess

import asterbench_dma as bench
import audit_phase6_regressions as legacy
import audit_phase7_regressions as regressions
import dma_functional_physical as functional
import dma_overlay as overlay
import dma_physical as physical
import pynq_dma_study as study
from bench_results import sha, source_state

SCHEMA = "aster.dma.closeout.v1"
REG = {n:f"regressions/dma/{i:02d}-{n}.log" for i,n in enumerate(regressions.TARGETS,1)}
OLD = {n:f"regressions/legacy/{i:02d}-{n}.log" for i,n in enumerate(legacy.TARGETS,1)}
FUNCTIONAL = tuple(f"{k}-c{c}" for c in (0,1) for k in ("runtime","publication"))
REQUIREMENTS = {
    "01-autonomous-DMA-registers-protocol-and-driver": [REG[n] for n in ("dma-engine","dma-arbiter","dma-runtime-matrix")],
    "02-coherence-atomic-reservations-and-safe-reset": [REG[n] for n in ("dma-cache-matrix","dma-cache-boundaries","dma-atomic-fabric","dma-warm-stop","linux-dma-matrix")],
    "03-actual-RISC-V-functional-runtime-and-code-publication": [f"reference/functional-c{c}/functional.json" for c in (0,1)]+
        [f"physical/{n}/functional-physical.json" for n in FUNCTIONAL],
    "04-preserved-phase1-through-phase6": ["regressions/legacy/manifest.json"]+list(OLD.values()),
    "05-asterbench-v5-counters-size-crossover-and-reproducibility": [REG[n] for n in ("dma-counters","dma-bench-cases","dma-bench-sensitivity","linux-dma-bench-cases","linux-dma-bench-baud")]+
        ["reference/study/study.json","physical/study/physical-study.json"],
    "06-routed-FPGA-and-physical-Linux-acceptance": ["fpga/c0/overlay.json","fpga/c1/overlay.json","physical/study/physical-study.json",
        "physical/post-study-state.json","physical/final_state.json","physical/logs/study.log"]+
        [f"physical/logs/{n}.log" for n in FUNCTIONAL],
    "07-clean-source-regressions-and-fresh-checkout": ["regressions/dma/manifest.json",REG["host-tests"],"verification/manifest.json","verification/01-check.log"],
}
TOP = {"regressions","reference","fpga","physical","verification"}
EXPORTER = "verification/soc/tb_pynq_linux_coherent.cpp"


def read(path): return bench.json_record(path.read_text())


def inventory(directory):
    bench.require(directory.is_dir() and not directory.is_symlink(), "invalid Phase 7 closeout directory")
    names = {p.name for p in directory.iterdir()}
    bench.require(TOP <= names <= TOP|{"manifest.json","README.md"}, "missing/extra Phase 7 package")
    files = {}
    for path in sorted(directory.rglob("*")):
        bench.require(not path.is_symlink(), "symlink closeout evidence")
        if path.is_dir(): continue
        bench.require(path.is_file(), "non-file closeout evidence")
        name = path.relative_to(directory).as_posix()
        if name in ("manifest.json","README.md"): continue
        files[name] = dict(bytes=path.stat().st_size,sha256=sha(path))
    bench.require(all(p in files for paths in REQUIREMENTS.values() for p in paths), "missing Phase 7 requirement evidence")
    return files


def utc(value):
    bench.require(type(value) is str, "missing UTC timestamp"); date = datetime.fromisoformat(value)
    bench.require(date.utcoffset() is not None and date.utcoffset().total_seconds() == 0, "invalid UTC timestamp")
    return date


def post_study(report,loaded,fingerprint):
    bench.require(type(report) is dict and set(report) == {"observed_utc","loaded_bitstream","loaded_bitstream_sha256","fclk0_mhz","state","fifo_count"},
                  "invalid independent post-study observation")
    for key,value in dict(loaded_bitstream=loaded,loaded_bitstream_sha256=fingerprint,fclk0_mhz=31.25,state=physical.stopped_state(),fifo_count=0).items():
        bench.require(bench.typed_equal(report[key],value), "post-study CPU/DMA/serial not safely stopped")
    return utc(report["observed_utc"])


def final_state(report,loaded,fingerprint):
    fields = {"schema","observed_utc","loaded_bitstream","loaded_bitstream_sha256","fclk0_mhz","registers","dma"}
    bench.require(type(report) is dict and set(report) == fields, "invalid independent final physical observation")
    expected = dict(schema="aster.dma.final-state.v1",loaded_bitstream=loaded,loaded_bitstream_sha256=fingerprint,fclk0_mhz=31.25,
        registers=dict(magic=0x41535452,abi=0x70001,clock_hz=31250000,harts=2,features=7,dma_abi=1,dma_counter_abi=5,
                       control=0,status=0,hart_status=0,stop_status=1,fifo_count=0),dma=physical.zero_dma())
    for key,value in expected.items(): bench.require(bench.typed_equal(report[key],value), "final physical identity/clock/CPU/DMA/serial differs")
    return utc(report["observed_utc"])


def stable_implementation(files):
    # The sole hardware-harness delta after the full regression revision adds
    # optional actual UART/RAM/event export. Its exact new source is bound to
    # BOTH clean functional references and the fresh checkout below. All other
    # RTL/firmware/vendor/FPGA/build/hardware-test inputs must remain identical.
    return {p:v for p,v in files.items() if not p.startswith(("scripts/","verification/host/")) and p != EXPORTER}


def physical_logs(directory,measured,reports):
    raw = (directory/"study.log").read_text(); log = legacy.Log(raw)
    schedule = study.schedule()
    bench.require(re.findall(r"(?m)^DMA_PHYSICAL_STUDY (\d+/144) (\S+)$",raw) ==
                  [(f"{i}/144",s["id"]) for i,s in enumerate(schedule,1)], "raw physical study execution order differs")
    boots = log.rows("physical Pynq-Z1 DMA boot",r"physical Pynq-Z1 DMA boot=(\d+) methods=8 serial_bytes=(\d+) retained_RAM=65536 exact_reference_counters")
    bench.require(len(boots) == 288 and [r[0] for r in boots] == [1,2]*144 and all(r[1] > 0 for r in boots), "raw physical study boots incomplete")
    paths = log.rows("physical DMA package",r"physical DMA package audited and CPU/DMA safely STOPPED, (.+)")
    bench.require(len(paths) == 144 and [Path(r[0]).parent.name for r in paths] == [s["id"] for s in schedule], "raw physical study STOP closeouts differ")
    log.rows("complete physical DMA study",r"complete physical DMA study, 144 captures / 288 warm boots / 1152 paired jobs, (.+)")
    log.finish(433)
    for name,report in reports.items():
        raw = (directory/(name+".log")).read_text(); log = legacy.Log(raw)
        cache = int(name[-1]); kind = report["kind"]; size = 97 if kind == "runtime" else 14
        rows = log.rows("physical Pynq-Z1 DMA",rf"physical Pynq-Z1 DMA {kind} cache={cache} boot=(\d+) serial_bytes={size} retained_RAM=65536 independent_functional_oracle")
        bench.require(rows == [(1,),(2,)], "raw functional program/cache/boot differs")
        log.rows("physical DMA functional package",r"physical DMA functional package audited and CPU/DMA safely STOPPED, (.+)")
        log.finish(3)


def evaluate(directory,*,current=False):
    old = legacy.audit(directory/"regressions/legacy/manifest.json")
    reg = regressions.audit(directory/"regressions/dma/manifest.json")
    old_m = read(directory/"regressions/legacy/manifest.json"); reg_m = read(directory/"regressions/dma/manifest.json")
    baseline = stable_implementation(old_m["source_files"])
    bench.require(stable_implementation(reg_m["source_files"]) == baseline, "DMA implementation changed after legacy regressions")
    hardware = {c:overlay.audit(directory/f"fpga/c{c}/overlay.json") for c in (0,1)}
    for c,hw in hardware.items():
        bench.require(hw["caches"] is bool(c) and hw["dma"] is True and hw["revision"] == old["revision"] and
                      hw["source_files"] == old_m["source_files"], "routed FPGA differs from full legacy-regression source")
    paths = {c:directory/f"fpga/c{c}/overlay.json" for c in (0,1)}
    measured = study.audit(directory/"physical/study/physical-study.json",directory/"reference/study/study.json",paths)
    source = read(directory/"reference/study/study.json")
    bench.require(source["revision"] == old["revision"] and measured["summary"]["reference_counter_match"] is True, "fixed study source/counters differ")
    previous = str(Path(measured["overlay_paths"]["1"]).parent/"aster_linux.bit")
    previous_sha = hardware[1]["files"]["aster_linux.bit"]["sha256"]
    observed_after_study = post_study(read(directory/"physical/post-study-state.json"),previous,previous_sha)
    bench.require(observed_after_study >= utc(measured["finished_utc"]), "post-study observation predates study completion")
    reports = {}; ref_revisions = set(); exporters = set(); selected_paths = {}
    for c in (0,1):
        reference = directory/f"reference/functional-c{c}/functional.json"; ref = read(reference)
        for program in ref["programs"].values():
            meta = program["metadata"]; ref_revisions.add(meta["revision"]); exporters.add(meta["source_files"][EXPORTER])
            bench.require(stable_implementation(meta["source_files"]) == baseline, "functional RTL/firmware/build/hardware-tests drifted")
        for kind in ("runtime","publication"):
            name = f"{kind}-c{c}"; report = functional.audit(directory/f"physical/{name}/functional-physical.json",reference,paths[c])
            selected = report["loaded_bitstream"]
            if kind == "runtime":
                bench.require(selected != previous and report["downloaded"] is True, "missing controlled functional cache deployment")
                selected_paths[c] = selected
            else: bench.require(selected == selected_paths[c] and report["downloaded"] is False, "functional code test replaced runtime image")
            bench.require(report["kind"] == kind and report["previous_bitstream"] == previous and report["previous_bitstream_sha256"] == previous_sha,
                          "broken functional program/cache/PCAP hash chain")
            previous,previous_sha = selected,hardware[c]["files"]["aster_linux.bit"]["sha256"]; reports[name] = report
    bench.require(len(ref_revisions) == len(exporters) == 1 and len({r["collector_revision"] for r in reports.values()}) == 1 and
                  selected_paths[0] != selected_paths[1], "mixed functional reference/collector/harness/cache source")
    final_date = final_state(read(directory/"physical/final_state.json"),previous,previous_sha)
    bench.require(final_date > observed_after_study, "final observation predates functional sequence")
    physical_logs(directory/"physical/logs",measured,reports)
    fresh = legacy.audit(directory/"verification/manifest.json",check_only=True); fresh_m = read(directory/"verification/manifest.json")
    bench.require(stable_implementation(fresh_m["source_files"]) == baseline and fresh_m["source_files"][EXPORTER] == next(iter(exporters)),
                  "fresh implementation or functional exporter drifted")
    for name,item in old_m["toolchain"]["tools"].items():
        bench.require(item == reg_m["toolchain"]["tools"][name] == fresh_m["toolchain"]["tools"][name], "regression/fresh compiler tool identity drift")
    counts = re.findall(r"(?m)^Ran (\d+) tests in [\d.]+s$",(directory/"verification/01-check.log").read_text())
    bench.require(len(counts) == 1 and int(counts[0]) >= 148, "missing complete fresh host/audit mutation suite")
    if current: bench.require(source_state() == (fresh_m["source_files"],fresh_m["source_sha256"]), "current source differs from fresh verification")
    layout = {"regressions":{"legacy","dma"},"regressions/legacy":{"manifest.json"}|{Path(p).name for p in OLD.values()},
        "regressions/dma":{"manifest.json"}|{Path(p).name for p in REG.values()},"fpga":{"c0","c1"},
        "reference":{"study","functional-c0","functional-c1"},"physical":{"study","post-study-state.json","final_state.json","logs"}|set(FUNCTIONAL),
        "physical/logs":{"study.log"}|{n+".log" for n in FUNCTIONAL},"verification":{"manifest.json","01-check.log"}}
    for name,children in layout.items(): bench.require({p.name for p in (directory/name).iterdir()} == children, "unlisted closeout package: "+name)
    crossover = {k:{p:v for p,v in values.items() if p != "points"} | {"largest_bytes":values["points"][-1]["size"],
        "largest_cpu_over_dma":values["points"][-1]["summed_cpu_over_dma"]} for k,values in measured["summary"]["series"].items()}
    return dict(source_revisions=dict(implementation=old["revision"],dma_regressions=reg["revision"],benchmark_reference=source["revision"],
        benchmark_collector=measured["collector_revision"],functional_reference=next(iter(ref_revisions)),functional_collector=reports[FUNCTIONAL[0]]["collector_revision"],fresh_verification=fresh["revision"]),
        summary=dict(legacy_targets=22,legacy_scenarios=old["passing_scenarios"],dma_targets=14,dma_scenarios=reg["passing_scenarios"],
            dma_host_tests=reg["host_tests"],fresh_scenarios=fresh["passing_scenarios"],fresh_host_tests=int(counts[0]),
            physical_benchmark_captures=144,physical_benchmark_boots=288,physical_benchmark_pairs=1152,physical_method_records=2304,
            physical_functional_boots=8,physical_directed_copies=1280,physical_runtime_published_jobs=12,physical_selective_reset_epochs=32,
            physical_code_dma_epochs=32,exact_physical_reference_counters=True,final_safely_stopped=True,crossover=crossover,
            fpga_signoff={str(c):hw["signoff"] for c,hw in hardware.items()}))


def audit(directory,*,current=False):
    files = inventory(directory); m = read(directory/"manifest.json")
    bench.require(type(m) is dict and set(m) == {"schema","status","files","requirements","source_revisions","summary"} and
                  m["schema"] == SCHEMA and m["status"] == "complete", "incomplete Phase 7 closeout manifest")
    bench.require(bench.typed_equal(m["requirements"],REQUIREMENTS) and bench.typed_equal(m["files"],files), "omitted/changed requirement/artifact")
    actual = evaluate(directory,current=current)
    bench.require(bench.typed_equal(m["source_revisions"],actual["source_revisions"]) and bench.typed_equal(m["summary"],actual["summary"]), "invented Phase 7 provenance/summary")
    return m


def manifest(directory):
    path = directory/"manifest.json"; bench.require(not path.exists(), "never overwrite a closeout manifest")
    files = inventory(directory); actual = evaluate(directory,current=True)
    result = dict(schema=SCHEMA,status="complete",files=files,requirements=REQUIREMENTS,**actual)
    with path.open("x") as stream: stream.write(json.dumps(result,indent=2,sort_keys=True)+"\n")
    audit(directory,current=True); return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("action",choices=("audit","manifest")); parser.add_argument("directory",type=Path)
    parser.add_argument("--current",action="store_true"); args = parser.parse_args()
    try:
        result = manifest(args.directory) if args.action == "manifest" else audit(args.directory,current=args.current)
        print(json.dumps(dict(source_revisions=result["source_revisions"],summary=result["summary"]),indent=2,sort_keys=True))
        print("PASS: all seven README Phase 7 requirements, complete raw evidence, Git provenance and physical CPU/DMA STOPPED")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
