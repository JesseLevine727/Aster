"""Synthetic outer-audit fixtures, never physical or regression evidence."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import audit_phase8 as a


def stopped(loaded="/board/functional-c1/aster_linux.bit",fingerprint="4"*64):
    return dict(schema="aster.dot8.final-state.v1",observed_utc="2026-09-13T21:40:00+00:00",loaded_bitstream=loaded,
        loaded_bitstream_sha256=fingerprint,fclk0_mhz=31.25,dma=a.physical.zero_dma(),dot8=a.physical.zero_dot8(),
        registers=dict(magic=0x41535452,abi=0x80001,clock_hz=31250000,harts=2,features=15,dma_abi=1,dma_counter_abi=5,dot8_abi=1,dot8_counter_abi=6,
                       dot8_value=11,dot8_mask=0xfe00707f,dot8_lanes=4,control=0,status=0,hart_status=0,stop_status=1,fifo_count=0))


def fixture(root):
    for name in {p for files in a.REQUIREMENTS.values() for p in files}:
        path = root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("synthetic\n")
    (root/"spec/contract-before-rtl.md").write_bytes(subprocess.check_output(["git","show",a.CONTRACT+":docs/phase8.md"],cwd=ROOT))
    def source(role):
        return {"Makefile":a.MAKE_HASH[role if role in a.MAKE_HASH else "dot8"],a.EXPORTER:a.EXPORT_HASH[int(role in ("functional","fresh"))],
                "rtl/core.sv":"a"*64,"software/runtime.c":"b"*64,"scripts/auditor.py":"c"*64}
    toolchain = dict(tools={"gcc":dict(path="/synthetic/gcc",sha256="0"*64,version="synthetic")})
    def meta(role): return dict(revision=a.REVISIONS[role] if role in a.REVISIONS else "f"*40,source_files=source(role),source_sha256="0"*64,toolchain=toolchain)
    old,new,fresh = meta("legacy"),meta("dot8"),meta("fresh")
    hardware = {kind:{c:dict(meta(kind),caches=bool(c),dma=True,dot8=True,signoff={"synthetic":c},
                    files={"aster_linux.bit":dict(sha256=str(c+(1 if kind == "study" else 3))*64)}) for c in (0,1)} for kind in ("study","functional")}
    historical = a.read(ROOT/a.HISTORY[7][0]/"physical/final_state.json")
    prior,fingerprint = historical["loaded_bitstream"],historical["loaded_bitstream_sha256"]
    initial = dict(observed_utc="2026-09-13T20:31:19+00:00",loaded=prior,bit_sha256=fingerprint,board="Pynq-Z1",clock_mhz=31.25,fifo_count=0,
                   state={k:v for k,v in a.physical.stopped_state().items() if k != "dot8"})
    measured = dict(collector_revision="e"*40,started_utc="2026-09-13T21:00:00+00:00",finished_utc="2026-09-13T21:30:00+00:00",
        initial_bitstream=prior,initial_bitstream_sha256=fingerprint,overlay_paths={str(c):f"/board/study-c{c}/overlay.json" for c in (0,1)},
        summary=dict(reference_counter_match=True,series={"dot-aaligned-c1":dict(first_all_pairs_custom_win_k=4,points=[dict(k=4096,summed_scalar_over_custom=4.9)])}))
    prior,fingerprint = "/board/study-c1/aster_linux.bit","2"*64
    post = dict(observed_utc="2026-09-13T21:31:00+00:00",loaded_bitstream=prior,loaded_bitstream_sha256=fingerprint,fclk0_mhz=31.25,state=a.physical.stopped_state(),fifo_count=0)
    reports = {}; refs = {}
    for c in (0,1):
        loaded = f"/board/functional-c{c}/aster_linux.bit"
        reports[c] = dict(previous_bitstream=prior,previous_bitstream_sha256=fingerprint,loaded_bitstream=loaded,downloaded=True,collector_revision="d"*40)
        prior,fingerprint = loaded,str(c+3)*64
        refs[c] = dict(metadata=meta("functional"),toolchain=toolchain)
    values = {"regressions/legacy/manifest.json":old,"regressions/dma/manifest.json":old,"regressions/dot8/manifest.json":new,
        "verification/manifest.json":fresh,"reference/study/study.json":dict(revision=a.REVISIONS["study"]),
        "physical/initial-state.json":initial,"physical/post-study-state.json":post,"physical/final-state.json":stopped()}
    for c in (0,1): values[f"reference/functional-c{c}/functional.json"] = refs[c]
    for name,value in values.items(): (root/name).write_text(json.dumps(value))
    (root/"verification/01-check.log").write_text("Ran 206 tests in 1.0s\nOK\n")
    return old,new,fresh,hardware,measured,reports,refs,historical


class Phase8Closeout(unittest.TestCase):
    def test_every_final_state_field_and_live_bank(self):
        m = stopped(); a.final_state(m,m["loaded_bitstream"],m["loaded_bitstream_sha256"]); mutants = []
        for key in m:
            bad = deepcopy(m); del bad[key]; mutants.append(bad)
        for key,value in m["registers"].items():
            for changed in (value+1,bool(value)):
                bad = deepcopy(m); bad["registers"][key] = changed; mutants.append(bad)
        for bank in ("dma","dot8"):
            for key in m[bank]:
                bad = deepcopy(m)
                if key == "counters": bad[bank][key][-1] = 1
                else: bad[bank][key] = 1
                mutants.append(bad)
        for key,value in (("observed_utc","2026-09-13T21:40:00"),("fclk0_mhz",50),("loaded_bitstream","/wrong.bit"),("loaded_bitstream_sha256","f"*64)):
            bad = deepcopy(m); bad[key] = value; mutants.append(bad)
        for bad in mutants:
            with self.assertRaises(ValueError): a.final_state(bad,m["loaded_bitstream"],m["loaded_bitstream_sha256"])

    def test_exact_reviewed_build_and_exporter_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            old,new,fresh,hardware,*_ = fixture(Path(directory))
            baseline = a.implementation(old["source_files"],"legacy")
            for role,files in (("dot8",new["source_files"]),("fresh",fresh["source_files"]),("study",hardware["study"][0]["source_files"]),("functional",hardware["functional"][0]["source_files"])):
                self.assertEqual(a.implementation(files,role),baseline)
                for key in ("Makefile",a.EXPORTER):
                    bad = dict(files); bad[key] = "f"*64
                    with self.assertRaises(ValueError): a.implementation(bad,role)
                changed = dict(files); changed["rtl/core.sv"] = "f"*64
                self.assertNotEqual(a.implementation(changed,role),baseline)

    def test_outer_inventory_requirements_summary_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root); result = dict(source_revisions={"synthetic":"a"*40},summary=dict(physical_captures=174))
            with mock.patch.object(a,"evaluate",return_value=result):
                original = a.manifest(root); self.assertEqual(len(original["requirements"]),8)
                with self.assertRaises(ValueError): a.manifest(root)
                path = root/"manifest.json"; mutants = []
                for key in original:
                    bad = deepcopy(original); del bad[key]; mutants.append(bad)
                for key in a.REQUIREMENTS:
                    bad = deepcopy(original); bad["requirements"][key] = []; mutants.append(bad)
                for key,value in (("summary",dict(physical_captures=174.0)),("status","running"),("source_revisions",{}),("files",{})):
                    bad = deepcopy(original); bad[key] = value; mutants.append(bad)
                for bad in mutants:
                    path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): a.audit(root)
                path.write_text(json.dumps(original)); changed = root/"physical/final-state.json"; changed.write_text("changed")
                with self.assertRaises(ValueError): a.audit(root)
                changed.unlink(); changed.symlink_to(root/"physical/initial-state.json")
                with self.assertRaises(ValueError): a.inventory(root)

    def test_nested_composition_source_drift_chain_and_current_source(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root = Path(directory); old,new,fresh,hardware,measured,reports,refs,historical = fixture(root)
            for validator in (a.history6,a.history7): stack.enter_context(mock.patch.object(validator,"audit",return_value={}))
            def legacy(path,**_): return dict(revision=(fresh if path.parent.name == "verification" else old)["revision"],passing_scenarios=157 if path.parent.name == "verification" else 2397)
            stack.enter_context(mock.patch.object(a.legacy,"audit",side_effect=legacy))
            stack.enter_context(mock.patch.object(a.dma,"audit",return_value=dict(revision=old["revision"],passing_scenarios=569)))
            stack.enter_context(mock.patch.object(a.dot8,"audit",return_value=dict(revision=new["revision"],passing_scenarios=400,host_tests=180)))
            stack.enter_context(mock.patch.object(a.overlay,"audit",side_effect=lambda p:hardware[p.parent.name.split("-c")[0]][int(p.parent.name[-1])]))
            stack.enter_context(mock.patch.object(a.study,"audit",return_value=measured))
            stack.enter_context(mock.patch.object(a.references,"load",side_effect=lambda p:refs[int(p.parent.name[-1])]))
            stack.enter_context(mock.patch.object(a.functional,"audit",side_effect=lambda p,*_:reports[int(p.parent.name[-1])]))
            stack.enter_context(mock.patch.object(a,"physical_logs"))
            stack.enter_context(mock.patch.object(a,"source_state",return_value=(fresh["source_files"],fresh["source_sha256"])))
            result = a.evaluate(root,current=True); self.assertEqual(result["summary"]["physical_directed_pairs"],5888)
            self.assertEqual(result["summary"]["physical_LR_dot_SC_successes"],128)
            for c in (0,1):
                for key,value in (("previous_bitstream","/wrong.bit"),("previous_bitstream_sha256","f"*64),("loaded_bitstream","/wrong.bit"),("downloaded",False),("collector_revision","f"*40)):
                    saved = reports[c][key]; reports[c][key] = value
                    with self.subTest(cache=c,key=key),self.assertRaises(ValueError): a.evaluate(root)
                    reports[c][key] = saved
            for file in ("regressions/dma/manifest.json","regressions/dot8/manifest.json","verification/manifest.json"):
                path = root/file; saved = path.read_text(); bad = json.loads(saved); bad["source_files"]["software/runtime.c"] = "f"*64; path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): a.evaluate(root)
                path.write_text(saved)
            with mock.patch.object(a,"source_state",return_value=({},"f"*64)):
                with self.assertRaises(ValueError): a.evaluate(root,current=True)
            initial = root/"physical/initial-state.json"; bad = json.loads(initial.read_text()); bad["state"]["dma"]["status"] = 2; initial.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): a.programming_chain(root/"physical",measured,hardware,reports,historical)

    def test_raw_physical_schedule_boots_functional_and_linux_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = ""; reports = {c:{} for c in (0,1)}
            for i,s in enumerate(a.study.schedule(),1):
                raw += f"DOT8_PHYSICAL_STUDY {i}/174 {s['id']}\n"
                raw += "".join(f"PASS: physical Pynq-Z1 dot8 boot={b} methods=8 serial_bytes=20712 retained_RAM=65536 exact_reference_counters\n" for b in (1,2))
                raw += f"PASS: physical dot8 package audited and CPU/DMA/compute safely STOPPED, /board/{s['id']}/physical.json\n"
            raw += "PASS: complete physical DOT8 study, 174 captures / 348 warm boots / 1392 paired jobs, /board/physical-study.json\n"
            path = root/"study.log"; path.write_text(raw)
            for c in (0,1):
                log = "".join(f"PASS: physical Pynq-Z1 DOT8 functional cache={c} boot={b} serial_bytes=17 retained_RAM=65536 independent_functional_oracle exact_50_reference_counters\n" for b in (1,2))
                log += f"PASS: physical DOT8 functional package audited and CPU/DMA/compute safely STOPPED, /board/functional-c{c}/functional-physical.json\n"
                (root/f"functional-c{c}.log").write_text(log)
            (root/"initial-activity.log").write_text("pynq jupyter-notebook\n")
            (root/"linux-available.log").write_text("pynq\nLinux 5.15 armv7l GNU/Linux\nuid=1000(xilinx)\n")
            a.physical_logs(root,{},reports)
            for before,after in (("STUDY 1/174","STUDY 2/174"),("boot=2","boot=1"),("methods=8","methods=7"),("RAM=65536","RAM=65532"),("/dot-k0-aaligned-c0/","/wrong/")):
                changed = raw.replace(before,after,1); self.assertNotEqual(changed,raw); path.write_text(changed)
                with self.assertRaises(ValueError): a.physical_logs(root,{},reports)
            path.write_text(raw+"FAIL: late failure\n")
            with self.assertRaises(ValueError): a.physical_logs(root,{},reports)


if __name__ == "__main__": unittest.main()
