"""Synthetic scenario logs exercise the fixed regression audit, not actual RTL evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
import audit_phase8_regressions as audit
import run_phase8_regressions as runner
from dot8_results import TOOL_NAMES
from test_asterbench_dot8 import fixture, line, observation


def log_fixture(entry):
    target,s = entry["target"],entry["settings"]
    val = lambda k:int(s[k],0)
    lines = []
    if target == "host-tests":
        return "".join(f"test_fixture_{i} (synthetic.Host.test_fixture_{i}) ... ok\n" for i in range(176))+"Ran 176 tests in 1.0s\n\nOK\n"
    if target == "dot8-unit":
        lines = [f"dot8 PCPI seed={seed} decode=131072 lane_products=262144 register_fields=32768 packed=6144 jobs=301061 reset_stages=4; exact signed sum, admission wait, captured operands, held reply, exactly-once events" for seed in (1,0xa57e8,0xc0ffee)]
    elif target == "dot8-probe":
        e,c = val("DOT8_PROBE_ENABLE"),val("DOT8_PROBE_CACHE")
        lines = [f"dot8 real hart enabled={e} icache={c} seed=0xa57e8 programs={33827 if e else 33794} dot8={426413 if e else 0} atomic={98403 if e else 0} retired={4163737 if e else 2095228} resets={32 if e else 0} prefetch_overlap={100 if e else 0} cycles=200000000 accept_to_retire_edges={4 if e else 0}..{(303 if c else 81) if e else 0}"+audit.PROBE_SUFFIX]
    elif target == "dot8-counters":
        lines = [f"dot8 ABI 6 harts={val('HART_COUNT')} eight counters, 4096 command/event cases, 20000 seeded steps, common-window priority/exclusion, full 32/64-bit wrap, absent-hart zeros, all offsets"]
    elif target == "dot8-runtime":
        h,c,w = val("HART_COUNT"),val("ENABLE_L1"),val("MEMORY_WAIT_CYCLES")
        lines = [f"dot8 C runtime harts={h} cache={c} wait={w} cycles=200000000 pairs={736*h} output_methods={1472*h+3} dots={25393 if h == 2 else 25753},{25689 if h == 2 else 0} DMA-overlap=10"+audit.RUNTIME_SUFFIX]*2
        for hart in range(h):
            for p in range(4): lines.append(f"dot8 warm stop hart={hart} point={p} escalation=0 selective={1 if h == 2 and hart == 0 else 0}"+audit.STOP_SUFFIX)
        if h == 2:
            lines.append("dot8 warm stop hart=0 point=4 escalation=0 selective=8"+audit.STOP_SUFFIX)
            for p in range(4): lines.append(f"dot8 warm stop hart=0 point={p} escalation=1 selective={0 if p == 0 else 1}"+audit.STOP_SUFFIX)
        lines.append(f"dot8 runtime closeout warm_boots=2 stopped={15 if h == 2 else 6} CPU-stores={h*1000000+1} DMA-stores=2048; no reset after initial POR")
    elif target == "linux-dot8-sim":
        e,h,c = val("DOT8_LINUX_ENABLE"),val("HART_COUNT"),val("ENABLE_L1")
        if e: lines = [f"dot8 AXI/serial runtime boot={b} harts={h} cache={c} counters=50 retained_RAM=65536; real C, ROM-write/running-RAM denial, all read-only strobes/offsets, stable replies" for b in (1,2)]
        lines.append(f"dot8 Linux bridge enabled={e} harts={h} cache={c} all AXI byte offsets/strobes, exact live banks, custom/atomic/fetch denial, final STOPPED")
        return f"synthetic model compile -GBAUD={s['DOT8_LINUX_BAUD']}\n"+"".join("PASS: "+x+"\n" for x in lines)
    elif target == "dot8-bench":
        raw = []
        for b in (1,2):
            raw.append(f"ASTERBOOT {b}\n")
            for job in (1,2,3):
                for p in (1,2):
                    r = fixture(name=s["DOT8_WORKLOAD"],k=val("DOT8_K"),alignment=s["DOT8_ALIGNMENT"],jobs=3,job=job,pass_number=p)
                    r.update(harts=1,sync_memory=0,memory_wait=7,line_words=2,line_count=2,base_seed=0xc0ffee,seed=0xc0ffee ^ ((job*0x9e3779b9)&0xffffffff))
                    r["output"] = audit.bench.reference(r["name"],r["k"],r["alignment"],r["seed"])[2].hex()
                    raw += [line(r),"DOT8_OBS "+json.dumps(observation(r,b))+"\n"]
            raw.append("ASTERSTOP "+json.dumps(dict(boot=b,records=6,ram_bytes=65536,cpu_stores=5000,dma_stores=0,lifetime_retired=[40000,0]))+"\n")
        return "".join(raw)+"PASS: dot8 benchmark boots=2 records=12; exact signed outputs/guards/RAM, actual scalar/custom PCs and 50-counter windows\n"
    return "".join("PASS: "+x+"\n" for x in lines)


class Phase8Regressions(unittest.TestCase):
    def test_runner_parallel_build_isolation_and_retained_failure(self):
        specs = runner.plan()[:2]
        for fail in (False,True):
            with self.subTest(failure=fail),tempfile.TemporaryDirectory() as directory:
                root = Path(directory)/"new"; barrier = Barrier(2)
                def fake_run(args,**kwargs):
                    entry = next(e for e in specs if e["target"] == args[-1]); barrier.wait(timeout=5)
                    raw = log_fixture(entry)
                    failed = fail and entry["target"] == "dot8-unit"
                    kwargs["stdout"].write(("FAIL: synthetic retained failure\n" if failed else raw).encode())
                    return SimpleNamespace(returncode=int(failed))
                def command(args):
                    if args[0] == "make": return "{}"
                    return "0"*40 if "rev-parse" in args else ""
                with mock.patch.object(runner,"plan",return_value=specs),mock.patch.object(runner,"command",side_effect=command),\
                     mock.patch.object(runner,"source_state",return_value=({"Makefile":"0"*64},"1"*64)),\
                     mock.patch.object(runner,"source_at_revision"),mock.patch.object(runner,"fingerprint_tools",return_value={}),\
                     mock.patch.object(runner,"identity",return_value={}),mock.patch.object(runner.platform,"platform",return_value="synthetic"),\
                     mock.patch("builtins.print"),mock.patch.object(runner.subprocess,"run",side_effect=fake_run):
                    if fail:
                        with self.assertRaises(ValueError): runner.run(root)
                    else: self.assertEqual(runner.run(root)["status"],"complete")
                    with self.assertRaises(ValueError): runner.run(root)
                m = json.loads((root/"manifest.json").read_text())
                self.assertEqual(m["status"],"failed" if fail else "complete")
                self.assertEqual([r["id"] for r in m["results"]],[e["id"] for e in specs])
                self.assertEqual(len({r["command"][3] for r in m["results"]}),2)
                self.assertTrue(all((root/r["log"]).is_file() for r in m["results"]))

    def test_plan_and_all_scenario_positive_controls(self):
        specs = runner.plan(); self.assertEqual(len(specs),56); self.assertEqual(len({e["id"] for e in specs}),56)
        self.assertEqual(len({runner.invocation(e,"/synthetic/build")[3] for e in specs}),56)
        self.assertEqual(sum(e["target"] == "dot8-runtime" for e in specs),20)
        for entry in specs:
            with self.subTest(case=entry["id"]): audit.validate_log(entry,log_fixture(entry))

    def test_missing_duplicate_reordered_truncated_or_failed_scenarios(self):
        for entry in runner.plan():
            raw = log_fixture(entry)
            for changed in (raw[:-1],raw+"PASS: invented\n","FAIL: broken\n"+raw):
                with self.subTest(case=entry["id"]),self.assertRaises(ValueError): audit.validate_log(entry,changed)
            if entry["target"] in ("host-tests","dot8-bench"): continue
            lines = raw.splitlines(keepends=True)
            for i in range(len(lines)):
                for changed in (lines[:i]+lines[i+1:],lines[:i]+[lines[i]]+lines[i:]):
                    with self.subTest(case=entry["id"],line=i),self.assertRaises(ValueError): audit.validate_log(entry,"".join(changed))
        entry = next(e for e in runner.plan() if e["id"] == "runtime-h2-c1-s1-w1")
        raw = log_fixture(entry); lines = raw.splitlines(keepends=True); lines[2],lines[3] = lines[3],lines[2]
        with self.assertRaises(ValueError): audit.validate_log(entry,"".join(lines))
        for old,new in (("pairs=1472","pairs=1471"),("dots=25393,25689","dots=25393,0"),("DMA-overlap=10","DMA-overlap=0"),
                        ("selective=8","selective=7"),("stopped=15","stopped=14"),("warm_boots=2","warm_boots=1")):
            with self.assertRaises(ValueError): audit.validate_log(entry,raw.replace(old,new))

    def package(self,root):
        sources = {"Makefile":"0"*64}
        tool = dict(path="/synthetic/tool",sha256="0"*64,version="synthetic")
        m = dict(schema=runner.SCHEMA,status="complete",revision="0"*40,dirty=False,source_files=sources,
            source_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),
            toolchain=dict(tools={name:dict(tool) for name in TOOL_NAMES},headers={"/synthetic/stdint.h":"0"*64}),
            python=dict(tool),host_compiler=dict(tool),platform="synthetic",plan=runner.plan(),parallelism=8,build_directory="/synthetic/build",results=[],
            started_utc="2026-01-01T00:00:00+00:00",finished_utc="2026-01-01T00:10:00+00:00")
        for index,entry in enumerate(runner.plan(),1):
            name = f"{index:02d}-{entry['id']}.log"; raw = log_fixture(entry).encode(); (root/name).write_bytes(raw)
            m["results"].append(dict(id=entry["id"],command=runner.invocation(entry,m["build_directory"]),exit_code=0,elapsed_seconds=1,
                log=name,log_sha256=hashlib.sha256(raw).hexdigest(),log_bytes=len(raw)))
        path = root/"manifest.json"; path.write_text(json.dumps(m)); return path,m

    def test_complete_manifest_and_rehashed_plan_provenance_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path,original = self.package(root)
            with mock.patch.object(audit.subprocess,"run",side_effect=AssertionError("audit executed command")):
                self.assertEqual(len(audit.audit(path,clean=False)["cases"]),56)
            mutations = []
            for key in original:
                changed = deepcopy(original); del changed[key]; mutations.append(changed)
            for key,value in (("dirty",True),("schema","aster.regressions.phase7.v1"),("status","running"),("plan",original["plan"][::-1]),
                              ("results",original["results"][:-1]),("parallelism",True),("parallelism",1),
                              ("finished_utc",original["started_utc"]),("source_sha256","1"*64)):
                changed = deepcopy(original); changed[key] = value; mutations.append(changed)
            for key,value in (("exit_code",True),("exit_code",1),("command",["echo","PASS"]),("elapsed_seconds",False),("log","../escape"),
                              ("log_sha256","1"*64),("log_bytes",0)):
                changed = deepcopy(original); changed["results"][0][key] = value; mutations.append(changed)
            for changed in mutations:
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError): audit.audit(path,clean=False)
            changed = deepcopy(original); item = changed["results"][1]; file = root/item["log"]
            raw = file.read_bytes().replace(b"jobs=301061",b"jobs=1"); file.write_bytes(raw)
            item.update(log_sha256=hashlib.sha256(raw).hexdigest(),log_bytes=len(raw)); path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): audit.audit(path,clean=False)


if __name__ == "__main__": unittest.main()
