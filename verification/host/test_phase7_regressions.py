"""Synthetic scenario/envelope mutations; not RTL execution evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import audit_phase7_regressions as a
from test_asterbench_dma import records, stream
from test_dma_results import fixture as bench_fixture, log_fixture


def unit_fixture(target):
    if target == "dma-engine":
        return "".join(f"PASS: DMA engine seed={s} delay={d} cases=7489 jobs=3098 reads=300001 writes=300000 success=1966 aborted=1062 errors=20 rejected=5000 stalls=100000 paused=10000{a.ENGINE_SUFFIX}\n"
                       for s,d in a.product(a.SEEDS,(0,7,-1)))
    if target == "dma-arbiter":
        return "".join(f"PASS: DMA arbiter seed={s} delay={d} cpu_groups=1600 device_groups=1500 operations=3200 stalls=4000 gaps=33000{a.ARBITER_SUFFIX}\n"
                       for s,d in a.product(a.SEEDS,(0,1,31,-1)))
    if target in ("dma-cache-matrix","dma-cache-boundaries"):
        configs = a.product((0,1),(1,4,8),(1,4,16)) if target.endswith("matrix") else [(1,1,1024),(1,1024,1)]
        return "".join(f"PASS: DMA cache enabled={c} geometry={w}x{n} seed={s} delay={d} cpu=33000 device=1400 forward={c*90} "
            f"invalidated={c*100} dirty_words={c*w*100} commits=1300 flushes=136 stalls={0 if d == 0 else 65000}{a.CACHE_SUFFIX}\n"
            for c,w,n in configs for s,d in a.product(a.SEEDS,(0,1,17,-1)))
    if target == "dma-atomic-fabric":
        return "".join(f"PASS: RV32A DMA-enabled fabric seed={s}: 62000 operations, 70000 backing transfers, 36000 committed stores, SC=4500/400 success/failure, 1400000 stalled cycles\n" for s in a.SEEDS)
    if target == "dma-counters": return "PASS: DMA ABI 5 fourteen-counter increments, exact common-window command priority/exclusion, frozen reads, metadata, all offsets and 32/64-bit carry/rollover\n"
    if target == "dma-warm-stop": return "PASS: warm-stop sequencing cases=58; drain, response settlement, held flush, selective/global stop, escalation/restart\n"
    raise ValueError(target)


def runtime_fixture():
    raw = ""
    for h,c,(_s,w) in a.product((1,2),(0,1),a.TIMINGS):
        line = (f"PASS: real DMA runtime harts={h} cache={c} wait={w} cycles=150000000 bytes={61313 if h == 2 else 53121} "
                f"jobs=3 directed=320 selective={8 if h == 2 else 0} retired=11600000,{200000 if h == 2 else 0}{a.RUNTIME_SUFFIX}\n")
        raw += line*2
        raw += "".join(f"PASS: real DMA warm stop point={p} full acknowledged RAM retained; no destructive reset\n" for p in range(5))
        if h == 2: raw += "".join(f"PASS: real DMA selective/global escalation point={p} transient STOP latched, separate flushes, complete retained RAM\n" for p in range(4))
        raw += line+f"PASS: DMA SoC closeout stops={12 if h == 2 else 8} CPU stores=2300000 DMA stores=150000 reads=150010 writes=150000 reservation-clears=18\n"
    return raw


def linux_fixture():
    raw = ""
    for h,c in a.product((1,2),(0,1)):
        for kind,size in (("dma",97),("publication",14)):
            raw += "".join(f"PASS: coherent AXI/serial {kind} harts={h} cache={c} boot={b} bytes={size} retired=100000,{10000 if h == 2 else 0} retained_RAM=65536\n" for b in (0,1))
            if kind == "dma":
                raw += "".join(f"PASS: DMA AXI in-flight stop phase={p} retained_RAM=65536 drained_bytes=4\n" for p in range(3))
                raw += "PASS: actual-core DMA MMIO atomic and instruction-fetch denial\n"
            raw += f"PASS: {a.AXI_CLOSE}{11 if kind == 'dma' else 6} observed_stores=1000000\n"
    return raw


def benchmark_fixture(target):
    raw = ""
    if target.startswith("linux-"):
        baud = 115200 if target.endswith("baud") else 781250
        raw += (f"synthetic verilator -GBAUD={baud}\n")*2
    for config in a.benchmark_plan(target):
        rows = records(config["size"],config["alignment"],config["l1"],config["jobs"])
        for row in rows:
            row.update(config); row["seed"] = config["base_seed"] ^ ((row["job"]*0x9e3779b9)&0xffffffff)
            row["output"] = a.bench.reference(row["size"],row["alignment"],row["seed"]).hex()
        if not target.startswith("linux-"): raw += log_fixture(rows); continue
        serial = stream(rows).decode()
        for boot in (1,2):
            raw += serial+f"PASS: DMA AXI/serial benchmark boot={boot} size={config['size']} alignment={list(a.bench.ALIGNMENTS).index(config['alignment'])} cache={config['l1']} records=8 serial_bytes={len(serial)} retained_RAM=65536\n"
        raw += f"PASS: {a.LINUX_CLOSE}1000000 DMA stores={8*a.bench.transactions(config['size'],config['alignment'])}\n"
    return raw


class DmaRegressionAudit(unittest.TestCase):
    def test_unit_seed_geometry_protocol_and_carry_gates(self):
        for target in a.TARGETS[1:8]:
            raw = unit_fixture(target); a.validate_log(target,raw)
            for changed in (raw+"PASS: invented\n",raw+"FAIL: hidden\n",raw.rstrip("\n"),raw.split("\n",1)[1]):
                with self.subTest(target=target), self.assertRaises(ValueError): a.validate_log(target,changed)
            replacements = [("seed=1 ","seed=2 "),("delay=-1","delay=0"),("geometry=1x1024","geometry=1x512"),
                            ("cases=7489","cases=7488"),("jobs=3098","jobs=3097"),("success=1966","success=1965"),
                            ("device_groups=1500","device_groups=0"),("flushes=136","flushes=135"),("SC=4500/400","SC=4500/0"),
                            ("ABI 5","ABI 4"),("cases=58","cases=34"),("dirty_words=0","dirty_words=1")]
            for old,new in replacements:
                if old not in raw: continue
                with self.subTest(target=target,old=old), self.assertRaises(ValueError): a.validate_log(target,raw.replace(old,new,1))

    def test_real_hart_runtime_reset_sequence_and_totals(self):
        raw = runtime_fixture(); self.assertEqual(a.validate_log("dma-runtime-matrix",raw)["passing_scenarios"],176)
        for old,new in (("harts=2","harts=1"),("cache=1","cache=0"),("wait=7","wait=1"),("selective=8","selective=0"),
            ("directed=320","directed=319"),("bytes=61313","bytes=60000"),("retired=11600000,200000","retired=11600000,0"),
            ("point=4","point=3"),("escalation point=3","escalation point=2"),("stops=12","stops=8"),
            ("DMA stores=150000","DMA stores=150001"),("reservation-clears=18","reservation-clears=17")):
            with self.subTest(old=old), self.assertRaises(ValueError): a.validate_log("dma-runtime-matrix",raw.replace(old,new,1))
        lines = raw.splitlines(keepends=True); lines[1],lines[2] = lines[2],lines[1]
        with self.assertRaises(ValueError): a.validate_log("dma-runtime-matrix","".join(lines))

    def test_linux_runtime_programs_serial_boots_and_denials(self):
        raw = linux_fixture(); self.assertEqual(a.validate_log("linux-dma-matrix",raw)["passing_scenarios"],40)
        for old,new in (("harts=2","harts=1"),("cache=1","cache=0"),("bytes=97","bytes=96"),("boot=1","boot=0"),
                        ("phase=2","phase=1"),("MMIO atomic and instruction-fetch denial","MMIO denial"),("snapshots=11","snapshots=6")):
            with self.subTest(old=old), self.assertRaises(ValueError): a.validate_log("linux-dma-matrix",raw.replace(old,new,1))

    def test_raw_paired_benchmarks_and_fixed_plans(self):
        for target,count in (("dma-bench-cases",42),("dma-bench-sensitivity",9),("linux-dma-bench-cases",10),("linux-dma-bench-baud",2)):
            plan = a.benchmark_plan(target); self.assertEqual(len(plan),count)
            raw = benchmark_fixture(target); a.validate_log(target,raw)
            changes = [("base_seed=324468736","base_seed=324468737"),("ASTERBENCH,","MISSING,"),("cache=1","cache=0"),
                       ("DMA_OBS ","MISSING_OBS "),("ASTERBOOT 2","ASTERBOOT 1"),("retained_RAM=65536","retained_RAM=65532"),
                       ("-GBAUD=115200","-GBAUD=781250"),("-GBAUD=781250","-GBAUD=115200")]
            for old,new in changes:
                if old not in raw: continue
                with self.subTest(target=target,old=old), self.assertRaises(ValueError): a.validate_log(target,raw.replace(old,new,1))
            with self.assertRaises(ValueError): a.validate_log(target,raw+"PASS: invented\n")

    def test_host_test_names_counts_no_skips_and_completion(self):
        raw = "".join(f"test_{i} (fixture.Tests.test_{i}) ... ok\n" for i in range(130))+"\nRan 130 tests in 1.0s\n\nOK\n"
        self.assertEqual(a.validate_log("host-tests",raw)["host_tests"],130)
        for old,new in (("Ran 130","Ran 129"),("test_1 (fixture.Tests.test_1)","test_0 (fixture.Tests.test_0)"),("OK","FAILED"),("... ok","... skipped")):
            with self.assertRaises(ValueError): a.validate_log("host-tests",raw.replace(old,new,1))

    def test_envelope_source_tools_hashes_plan_and_no_saved_command_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); old,_,_ = bench_fixture(); sources = {"Makefile":"c"*64}; tool = dict(path="/fixture/tool",sha256="a"*64,version="synthetic")
            m = dict(schema=a.SCHEMA,status="complete",revision="b"*40,dirty=False,source_files=sources,
                source_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),toolchain=old["toolchain"],python=tool,
                host_compiler=tool,platform="synthetic",targets=list(a.TARGETS),build_directory="/fixture/build",results=[],
                started_utc="2026-09-13T00:00:00+00:00",finished_utc="2026-09-13T01:00:00+00:00")
            for i,target in enumerate(a.TARGETS,1):
                name = f"{i:02d}-{target}.log"; raw = ("synthetic "+target+"\n").encode(); (root/name).write_bytes(raw)
                m["results"].append(dict(target=target,command=["make","--no-print-directory","-j2","BUILD_DIR=/fixture/build",target],
                    exit_code=0,elapsed_seconds=1.0,log=name,log_bytes=len(raw),log_sha256=hashlib.sha256(raw).hexdigest()))
            path = root/"manifest.json"
            with mock.patch.object(a,"validate_log",side_effect=lambda target,raw:dict(target=target,passing_scenarios=1,host_tests=130 if target == "host-tests" else 0)), mock.patch.object(a,"source_at_revision") as git:
                path.write_text(json.dumps(m)); self.assertEqual(a.audit(path)["passing_scenarios"],14); git.assert_called_once()
                mutants = []
                for key in m:
                    bad = copy.deepcopy(m); del bad[key]; mutants.append(bad)
                for key,value in (("dirty",True),("status","running"),("revision","HEAD"),("source_sha256","0"*64),("toolchain",{}),
                                  ("build_directory","relative"),("finished_utc","2026-09-13T01:00:00")):
                    bad = copy.deepcopy(m); bad[key] = value; mutants.append(bad)
                for key,value in (("command",["touch","/must-not-execute"]),("exit_code",False),("elapsed_seconds",float("inf")),
                    ("elapsed_seconds",10000),("log","../escape"),("log_bytes",1),("log_sha256","f"*64)):
                    bad = copy.deepcopy(m); bad["results"][0][key] = value; mutants.append(bad)
                bad = copy.deepcopy(m); bad["results"].reverse(); mutants.append(bad)
                bad = copy.deepcopy(m); bad["targets"].pop(); mutants.append(bad)
                for i,bad in enumerate(mutants):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=i), self.assertRaises(ValueError): a.audit(path,clean=False)
                path.write_text(json.dumps(m)); (root/"unlisted").write_text("preserve")
                with self.assertRaises(ValueError): a.audit(path,clean=False)


if __name__ == "__main__": unittest.main()
