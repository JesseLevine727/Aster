#!/usr/bin/env python3
"""Read-only README Phase 8 requirement, source, artifact and deployment audit.

Nested audits validate actual bytes/semantics and Git blobs, never saved command
strings. Exact reviewed Makefile/exporter hashes admit the documented capture
milestones without exempting arbitrary hardware/build/test changes.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess

import asterbench_dot8 as bench
import audit_phase6 as history6
import audit_phase7 as history7
import audit_phase6_regressions as legacy
import audit_phase7_regressions as dma
import audit_phase8_regressions as dot8
import dot8_functional_physical as functional
import dot8_functional_results as references
import dot8_overlay as overlay
import dot8_physical as physical
import pynq_dot8_study as study
from bench_results import ROOT, sha, source_state

SCHEMA = "aster.dot8.closeout.v1"
CONTRACT = "8ea59d472d0d5f52ec745ea5acf382399fd98194"
FIRST_RTL = "09afffcfa5afb68a6de124770f310336abe0aaef"
REVISIONS = dict(legacy="5b9c1757df7ad23535db6010e0b360cafe6cebe0",study="6a0449c035570a9f9d24538140bdb08a039f8651",
                dot8="0dce16b58b5c01ead9e0e690b4287bcc6666c84d",functional="7434284ac73f01c5d528b3d47725598331ac01ab")
EXPORTER = "verification/soc/tb_aster_dot8_soc.cpp"
MAKE_HASH = dict(legacy="18da8c55c65041e5f7695c6bb675378ba39cbd6d2a0b9be83033c822a012868c",
                study="94a67c7c1c1996662b92e74124d23c669b5053481ef421cc0160d345ccbb56bb",
                dot8="22aa66c688954006c033151063af206038a51416d6d7fc45e8173e310cf54bdd")
EXPORT_HASH = ("9c798568d54a1a4bce5242d0b883d55f98684fa30ccbf4c32453888eeca1d89c","8e3b451dad18858e4795437734ec94484451481cd6e105c28cf51cc97bf879ca")
HISTORY = {6:("docs/results/phase6/closeout-215b2d0","fbfb1e99019e82838fc906554d707de173be59705e70ac62426071195b832e48"),
           7:("docs/results/phase7/closeout-888c24b","45f268feae3b53b0d7ef725340e04502471346f7a51d272bc86b4bbbab166d86")}
OLD = {n:f"regressions/legacy/{i:02d}-{n}.log" for i,n in enumerate(legacy.TARGETS,1)}
DMA = {n:f"regressions/dma/{i:02d}-{n}.log" for i,n in enumerate(dma.TARGETS,1)}
DOT = {e["id"]:f"regressions/dot8/{i:02d}-{e['id']}.log" for i,e in enumerate(dot8.plan(),1)}
REQUIREMENTS = {
    "01-specified-encoding-signed-arithmetic-and-real-core-ISA": ["spec/contract-before-rtl.md"]+[p for n,p in DOT.items() if n.startswith(("arithmetic","probe"))],
    "02-RAM-backed-C-alignment-tails-LRSC-and-DMA-publication": [p for n,p in DOT.items() if n.startswith("runtime")]+
        [f"reference/functional-c{c}/functional.json" for c in (0,1)]+[f"physical/functional-c{c}/functional-physical.json" for c in (0,1)],
    "03-optional-ABI-M-A-coherence-and-safe-lifecycle": [p for n,p in DOT.items() if n.startswith(("counters","linux","probe"))]+[DMA[n] for n in ("dma-atomic-fabric","dma-warm-stop","linux-dma-matrix")],
    "04-preserve-phases1-through7-and-historical-evidence": ["regressions/legacy/manifest.json","regressions/dma/manifest.json"]+list(OLD.values())+list(DMA.values()),
    "05-fair-v6-fixed-study-outputs-events-repeats-and-sensitivity": ["reference/study/study.json","physical/study/physical-study.json"]+[p for n,p in DOT.items() if n.startswith(("sensitivity","counters"))],
    "06-clean-routed-reset-HWH-and-actual-PYNQ-execution": [f"fpga/{kind}-c{c}/overlay.json" for kind in ("study","functional") for c in (0,1)]+["physical/logs/study.log"]+
        [f"physical/logs/functional-c{c}.log" for c in (0,1)],
    "07-guarded-programming-chain-and-final-CPU-DMA-compute-STOPPED": ["physical/initial-state.json","physical/post-study-state.json","physical/final-state.json",
        "physical/logs/initial-activity.log","physical/logs/linux-available.log"],
    "08-complete-regressions-mutation-audits-and-fresh-checkout": ["regressions/dot8/manifest.json",DOT["host"],"verification/manifest.json","verification/01-check.log"],
}
TOP = {"spec","regressions","reference","fpga","physical","verification"}


def read(path): return bench.json_record(path.read_text())


def utc(value):
    bench.require(type(value) is str,"missing UTC timestamp"); date = datetime.fromisoformat(value)
    bench.require(date.utcoffset() is not None and date.utcoffset().total_seconds() == 0,"invalid UTC timestamp")
    return date


def inventory(directory):
    bench.require(directory.is_dir() and not directory.is_symlink(),"invalid Phase 8 directory")
    bench.require(TOP <= {p.name for p in directory.iterdir()} <= TOP|{"manifest.json","README.md"},"missing/extra Phase 8 package")
    files = {}
    for path in sorted(directory.rglob("*")):
        bench.require(not path.is_symlink(),"symlink Phase 8 evidence")
        if path.is_dir(): continue
        bench.require(path.is_file(),"non-file Phase 8 evidence")
        name = path.relative_to(directory).as_posix()
        if name in ("manifest.json","README.md"): continue
        files[name] = dict(bytes=path.stat().st_size,sha256=sha(path))
    bench.require(all(p in files for paths in REQUIREMENTS.values() for p in paths),"missing Phase 8 requirement evidence")
    return files


def implementation(files,role):
    bench.require(role in ("legacy","study","dot8","functional","fresh"),"unknown reviewed source milestone")
    make = MAKE_HASH[role if role in MAKE_HASH else "dot8"]
    exporter = EXPORT_HASH[int(role in ("functional","fresh"))]
    bench.require(files.get("Makefile") == make and files.get(EXPORTER) == exporter,"unreviewed Makefile or functional-exporter delta")
    return {p:v for p,v in files.items() if not p.startswith(("scripts/","verification/host/")) and p not in ("Makefile",EXPORTER)}


def post_study(m,loaded,fingerprint):
    bench.require(type(m) is dict and set(m) == {"observed_utc","loaded_bitstream","loaded_bitstream_sha256","fclk0_mhz","state","fifo_count"},"invalid post-study observation")
    for key,value in dict(loaded_bitstream=loaded,loaded_bitstream_sha256=fingerprint,fclk0_mhz=31.25,state=physical.stopped_state(),fifo_count=0).items():
        bench.require(bench.typed_equal(m[key],value),"post-study CPU/DMA/compute/serial unsafe")
    return utc(m["observed_utc"])


def final_state(m,loaded,fingerprint):
    fields = {"schema","observed_utc","loaded_bitstream","loaded_bitstream_sha256","fclk0_mhz","registers","dma","dot8"}
    bench.require(type(m) is dict and set(m) == fields,"invalid final physical observation")
    expected = dict(schema="aster.dot8.final-state.v1",loaded_bitstream=loaded,loaded_bitstream_sha256=fingerprint,fclk0_mhz=31.25,
        registers=dict(magic=0x41535452,abi=0x80001,clock_hz=31250000,harts=2,features=15,dma_abi=1,dma_counter_abi=5,dot8_abi=1,dot8_counter_abi=6,
                       dot8_value=0x0b,dot8_mask=0xfe00707f,dot8_lanes=4,control=0,status=0,hart_status=0,stop_status=1,fifo_count=0),
        dma=physical.zero_dma(),dot8=physical.zero_dot8())
    for key,value in expected.items(): bench.require(bench.typed_equal(m[key],value),"final physical identity/clock/CPU/DMA/compute differs")
    return utc(m["observed_utc"])


def programming_chain(directory,measured,hardware,reports,historical_final):
    initial = read(directory/"initial-state.json")
    fields = {"observed_utc","loaded","bit_sha256","board","clock_mhz","fifo_count","state"}
    bench.require(type(initial) is dict and set(initial) == fields,"invalid initial read-only observation")
    prior = historical_final["loaded_bitstream"]; fingerprint = historical_final["loaded_bitstream_sha256"]
    for key,value in dict(loaded=prior,bit_sha256=fingerprint,board="Pynq-Z1",clock_mhz=31.25,fifo_count=0,
                         state={k:v for k,v in physical.stopped_state().items() if k != "dot8"}).items():
        bench.require(bench.typed_equal(initial[key],value),"initial physical state differs from preserved Phase 7 handoff")
    bench.require(utc(historical_final["observed_utc"]) < utc(initial["observed_utc"]) <= utc(measured["started_utc"]) and
                  measured["initial_bitstream"] == prior and measured["initial_bitstream_sha256"] == fingerprint,"broken initial physical chronology/hash chain")
    prior = str(Path(measured["overlay_paths"]["1"]).parent/"aster_linux.bit")
    fingerprint = hardware["study"][1]["files"]["aster_linux.bit"]["sha256"]
    after = post_study(read(directory/"post-study-state.json"),prior,fingerprint)
    bench.require(after >= utc(measured["finished_utc"]),"post-study observation predates study completion")
    selected = []
    for c in (0,1):
        report = reports[c]; loaded = report["loaded_bitstream"]
        bench.require(report["previous_bitstream"] == prior and report["previous_bitstream_sha256"] == fingerprint and
                      report["downloaded"] is True and loaded != prior,"broken functional cache/PCAP programming chain")
        selected.append(loaded); prior = loaded; fingerprint = hardware["functional"][c]["files"]["aster_linux.bit"]["sha256"]
    bench.require(len(set(selected)) == 2 and len({r["collector_revision"] for r in reports.values()}) == 1,"mixed functional collector or cache image")
    bench.require(final_state(read(directory/"final-state.json"),prior,fingerprint) > after,"final observation predates functional sequence")


def physical_logs(directory,measured,reports):
    raw = (directory/"study.log").read_text(); log = legacy.Log(raw); schedule = study.schedule()
    bench.require(re.findall(r"(?m)^DOT8_PHYSICAL_STUDY (\d+/174) (\S+)$",raw) == [(f"{i}/174",e["id"]) for i,e in enumerate(schedule,1)],"raw physical study schedule differs")
    boots = log.rows("physical Pynq-Z1 dot8 boot",r"physical Pynq-Z1 dot8 boot=(\d+) methods=8 serial_bytes=(\d+) retained_RAM=65536 exact_reference_counters")
    bench.require(len(boots) == 348 and [b[0] for b in boots] == [1,2]*174 and all(b[1] > 0 for b in boots),"incomplete raw physical study boots")
    paths = log.rows("physical dot8 package",r"physical dot8 package audited and CPU/DMA/compute safely STOPPED, (.+)")
    bench.require([Path(p[0]).parent.name for p in paths] == [e["id"] for e in schedule],"raw study stopped-capture order differs")
    rows = log.rows("complete physical DOT8 study",r"complete physical DOT8 study, 174 captures / 348 warm boots / 1392 paired jobs, (.+)")
    bench.require(len(rows) == 1,"missing/duplicate physical study completion"); log.finish(523)
    for c,report in reports.items():
        raw = (directory/f"functional-c{c}.log").read_text(); log = legacy.Log(raw)
        rows = log.rows("physical Pynq-Z1 DOT8 functional",rf"physical Pynq-Z1 DOT8 functional cache={c} boot=(\d+) serial_bytes=17 retained_RAM=65536 independent_functional_oracle exact_50_reference_counters")
        bench.require(rows == [(1,),(2,)],"raw functional cache/boot differs")
        rows = log.rows("physical DOT8 functional package",r"physical DOT8 functional package audited and CPU/DMA/compute safely STOPPED, (.+)")
        bench.require(len(rows) == 1,"missing/duplicate functional closeout"); log.finish(3)
    activity = (directory/"initial-activity.log").read_text()
    bench.require("pynq" in activity and "jupyter-notebook" in activity and "ipykernel_launcher" not in activity,"missing/nonidle initial process observation")
    linux = (directory/"linux-available.log").read_text().splitlines()
    bench.require(len(linux) >= 3 and linux[0] == "pynq" and re.fullmatch(r"Linux .+ armv7l GNU/Linux",linux[1]) and linux[2].startswith("uid="),"missing independent Linux/SSH availability observation")


def evaluate(directory,*,current=False):
    original = subprocess.check_output(["git","show",CONTRACT+":docs/phase8.md"],cwd=ROOT)
    bench.require((directory/"spec/contract-before-rtl.md").read_bytes() == original,"instruction contract differs from pre-RTL commit")
    subprocess.run(["git","merge-base","--is-ancestor",CONTRACT,FIRST_RTL],cwd=ROOT,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    histories = {}
    for phase,validator in ((6,history6),(7,history7)):
        folder,fingerprint = HISTORY[phase]; path = ROOT/folder
        bench.require(sha(path/"manifest.json") == fingerprint,"preserved historical closeout changed")
        validator.audit(path); histories[str(phase)] = fingerprint
    old = legacy.audit(directory/"regressions/legacy/manifest.json"); dev = dma.audit(directory/"regressions/dma/manifest.json")
    new = dot8.audit(directory/"regressions/dot8/manifest.json")
    old_m = read(directory/"regressions/legacy/manifest.json"); dev_m = read(directory/"regressions/dma/manifest.json"); new_m = read(directory/"regressions/dot8/manifest.json")
    bench.require(old["revision"] == dev["revision"] == REVISIONS["legacy"] and new["revision"] == REVISIONS["dot8"],"wrong full regression revisions")
    baseline = implementation(old_m["source_files"],"legacy")
    bench.require(implementation(dev_m["source_files"],"legacy") == baseline and implementation(new_m["source_files"],"dot8") == baseline,"implementation changed across full regressions")
    hardware = {kind:{c:overlay.audit(directory/f"fpga/{kind}-c{c}/overlay.json") for c in (0,1)} for kind in ("study","functional")}
    for kind,pair in hardware.items():
        for c,hw in pair.items():
            bench.require(hw["caches"] is bool(c) and hw["dma"] is True and hw["dot8"] is True and hw["revision"] == REVISIONS[kind] and
                          implementation(hw["source_files"],kind) == baseline,"FPGA source differs from reviewed regression implementation")
    overlays = {c:directory/f"fpga/study-c{c}/overlay.json" for c in (0,1)}
    measured = study.audit(directory/"physical/study/physical-study.json",directory/"reference/study/study.json",overlays)
    source = read(directory/"reference/study/study.json")
    bench.require(source["revision"] == REVISIONS["study"] and measured["summary"]["reference_counter_match"] is True,"wrong study source or physical counters")
    refs = {}; reports = {}
    for c in (0,1):
        path = directory/f"reference/functional-c{c}/functional.json"; ref = references.load(path); refs[c] = ref
        bench.require(ref["metadata"]["revision"] == REVISIONS["functional"] and implementation(ref["metadata"]["source_files"],"functional") == baseline,"functional implementation/source drift")
        reports[c] = functional.audit(directory/f"physical/functional-c{c}/functional-physical.json",path,directory/f"fpga/functional-c{c}/overlay.json")
    historical_final = read(ROOT/HISTORY[7][0]/"physical/final_state.json")
    programming_chain(directory/"physical",measured,hardware,reports,historical_final)
    physical_logs(directory/"physical/logs",measured,reports)
    fresh = legacy.audit(directory/"verification/manifest.json",check_only=True); fresh_m = read(directory/"verification/manifest.json")
    bench.require(implementation(fresh_m["source_files"],"fresh") == baseline,"fresh RTL/firmware/hardware-test implementation drift")
    counts = re.findall(r"(?m)^Ran (\d+) tests in [\d.]+s$",(directory/"verification/01-check.log").read_text())
    bench.require(len(counts) == 1 and int(counts[0]) >= 206,"fresh suite lacks final functional/requirement mutation tests")
    for name,item in old_m["toolchain"]["tools"].items():
        bench.require(item == dev_m["toolchain"]["tools"][name] == new_m["toolchain"]["tools"][name] == fresh_m["toolchain"]["tools"][name] == refs[0]["toolchain"]["tools"][name] == refs[1]["toolchain"]["tools"][name],"regression/reference/fresh compiler identity drift")
    if current: bench.require(source_state() == (fresh_m["source_files"],fresh_m["source_sha256"]),"current source differs from fresh verification")
    layout = {"spec":{"contract-before-rtl.md"},"regressions":{"legacy","dma","dot8"},
        "regressions/legacy":{"manifest.json"}|{Path(p).name for p in OLD.values()},"regressions/dma":{"manifest.json"}|{Path(p).name for p in DMA.values()},
        "regressions/dot8":{"manifest.json"}|{Path(p).name for p in DOT.values()},"fpga":{f"{k}-c{c}" for k in hardware for c in (0,1)},
        "reference":{"study","functional-c0","functional-c1"},"physical":{"study","functional-c0","functional-c1","initial-state.json","post-study-state.json","final-state.json","logs"},
        "physical/logs":{"study.log","functional-c0.log","functional-c1.log","initial-activity.log","linux-available.log"},"verification":{"manifest.json","01-check.log"}}
    for name,children in layout.items(): bench.require({p.name for p in (directory/name).iterdir()} == children,"unlisted closeout package: "+name)
    crossover = {k:{n:v for n,v in s.items() if n != "points"}|{"largest_k":s["points"][-1]["k"],"largest_scalar_over_custom":s["points"][-1]["summed_scalar_over_custom"]} for k,s in measured["summary"]["series"].items()}
    return dict(source_revisions=dict(contract=CONTRACT,legacy=old["revision"],dma=dev["revision"],dot8=new["revision"],study=source["revision"],
        study_collector=measured["collector_revision"],functional=refs[0]["metadata"]["revision"],functional_collector=reports[0]["collector_revision"],fresh=fresh["revision"]),
        summary=dict(historical_manifests=histories,legacy_targets=22,legacy_scenarios=old["passing_scenarios"],dma_targets=14,dma_scenarios=dev["passing_scenarios"],
            dot8_cases=56,dot8_scenarios=new["passing_scenarios"],dot8_host_tests=new["host_tests"],fresh_scenarios=fresh["passing_scenarios"],fresh_host_tests=int(counts[0]),
            simulation_captures=174,physical_captures=174,physical_benchmark_boots=348,physical_benchmark_pairs=1392,physical_method_records=2784,
            physical_functional_boots=4,physical_directed_pairs=5888,physical_LR_dot_SC_successes=128,physical_published_GEMM_jobs=12,physical_DMA_jobs=16,
            exact_physical_reference_counters=True,final_safely_stopped=True,crossover=crossover,fpga_signoff={k:{str(c):h["signoff"] for c,h in pair.items()} for k,pair in hardware.items()}))


def audit(directory,*,current=False):
    files = inventory(directory); m = read(directory/"manifest.json")
    bench.require(type(m) is dict and set(m) == {"schema","status","files","requirements","source_revisions","summary"} and m["schema"] == SCHEMA and m["status"] == "complete","incomplete Phase 8 manifest")
    bench.require(bench.typed_equal(m["requirements"],REQUIREMENTS) and bench.typed_equal(m["files"],files),"changed/omitted Phase 8 requirement/artifact")
    actual = evaluate(directory,current=current)
    bench.require(bench.typed_equal(m["source_revisions"],actual["source_revisions"]) and bench.typed_equal(m["summary"],actual["summary"]),"invented Phase 8 source/result summary")
    return m


def manifest(directory):
    path = directory/"manifest.json"; bench.require(not path.exists(),"never overwrite a closeout manifest")
    files = inventory(directory); actual = evaluate(directory,current=True)
    result = dict(schema=SCHEMA,status="complete",files=files,requirements=REQUIREMENTS,**actual)
    with path.open("x") as handle: handle.write(json.dumps(result,indent=2,sort_keys=True)+"\n")
    audit(directory,current=True); return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("action",choices=("audit","manifest")); parser.add_argument("directory",type=Path)
    parser.add_argument("--current",action="store_true"); args = parser.parse_args()
    try:
        m = manifest(args.directory) if args.action == "manifest" else audit(args.directory,current=args.current)
        print(json.dumps(dict(source_revisions=m["source_revisions"],summary=m["summary"]),indent=2,sort_keys=True))
        print("PASS: all eight README Phase 8 requirements, complete raw evidence, exact source/deployment chain and CPU/DMA/compute STOPPED")
    except (ValueError,OSError,subprocess.CalledProcessError) as error: parser.exit(1,f"FAIL: {error}\n")
