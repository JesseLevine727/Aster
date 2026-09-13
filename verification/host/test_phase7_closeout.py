"""Outer requirement/source/programming-chain fixtures; no physical evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import audit_phase7 as a


def stopped():
    return dict(schema="aster.dma.final-state.v1",observed_utc="2026-09-13T02:00:00+00:00",loaded_bitstream="/board/functional-c1/aster_linux.bit",
        loaded_bitstream_sha256="2"*64,fclk0_mhz=31.25,dma=a.physical.zero_dma(),
        registers=dict(magic=0x41535452,abi=0x70001,clock_hz=31250000,harts=2,features=7,dma_abi=1,dma_counter_abi=5,
                       control=0,status=0,hart_status=0,stop_status=1,fifo_count=0))


def fixture(root):
    for name in {p for files in a.REQUIREMENTS.values() for p in files}:
        path = root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("synthetic\n")
    source = {"Makefile":"a"*64,"rtl/core.sv":"b"*64,"software/dma.c":"c"*64,a.EXPORTER:"d"*64}
    toolchain = dict(tools={"gcc":dict(path="/fixture/gcc",sha256="a"*64,version="synthetic")})
    old = dict(revision="1"*40,source_files=source,source_sha256="a"*64,toolchain=toolchain)
    reg = dict(old,revision="2"*40); changed = dict(source); changed[a.EXPORTER] = "e"*64
    fresh = dict(old,revision="3"*40,source_files=changed)
    hardware = {c:dict(old,caches=bool(c),dma=True,signoff={"fixture":c},files={"aster_linux.bit":dict(sha256=str(c+1)*64)}) for c in (0,1)}
    measured = dict(collector_revision="4"*40,finished_utc="2026-09-13T00:00:00+00:00",
        overlay_paths={str(c):f"/board/study-c{c}/overlay.json" for c in (0,1)},
        summary=dict(reference_counter_match=True,series={"aligned-c1":dict(first_all_pairs_dma_win_bytes=255,points=[dict(size=8192,summed_cpu_over_dma=1.1)])}))
    previous,previous_sha = "/board/study-c1/aster_linux.bit","2"*64; reports = {}
    post = dict(observed_utc="2026-09-13T01:00:00+00:00",loaded_bitstream=previous,loaded_bitstream_sha256=previous_sha,
                fclk0_mhz=31.25,state=a.physical.stopped_state(),fifo_count=0)
    for c in (0,1):
        for kind in ("runtime","publication"):
            loaded = f"/board/functional-c{c}/aster_linux.bit"
            reports[f"{kind}-c{c}"] = dict(kind=kind,previous_bitstream=previous,previous_bitstream_sha256=previous_sha,loaded_bitstream=loaded,
                                          downloaded=loaded != previous,collector_revision="5"*40)
            previous,previous_sha = loaded,str(c+1)*64
    values = {"regressions/legacy/manifest.json":old,"regressions/dma/manifest.json":reg,"verification/manifest.json":fresh,
              "reference/study/study.json":dict(revision=old["revision"]),"physical/final_state.json":stopped(),"physical/post-study-state.json":post}
    for c in (0,1): values[f"reference/functional-c{c}/functional.json"] = dict(programs={k:dict(metadata=dict(revision="6"*40,source_files=changed)) for k in ("runtime","publication")})
    for name,value in values.items(): (root/name).write_text(json.dumps(value))
    (root/"verification/01-check.log").write_text("Ran 148 tests in 1.0s\nOK\n")
    return old,reg,fresh,hardware,measured,reports


class Phase7Closeout(unittest.TestCase):
    def test_exact_final_cpu_dma_clock_identity_and_post_study(self):
        original = stopped(); a.final_state(original,original["loaded_bitstream"],original["loaded_bitstream_sha256"])
        mutants = []
        for key in original:
            bad = copy.deepcopy(original); del bad[key]; mutants.append(bad)
        for key in original["registers"]:
            for value in (original["registers"][key]+1,bool(original["registers"][key])):
                bad = copy.deepcopy(original); bad["registers"][key] = value; mutants.append(bad)
        for key in original["dma"]:
            bad = copy.deepcopy(original)
            if key == "counters": bad["dma"][key][13] = 1
            else: bad["dma"][key] = 1
            mutants.append(bad)
        for key,value in (("loaded_bitstream","/other.bit"),("loaded_bitstream_sha256","f"*64),("fclk0_mhz",50),
                          ("observed_utc","2026-09-13T02:00:00")):
            bad = copy.deepcopy(original); bad[key] = value; mutants.append(bad)
        for i,bad in enumerate(mutants):
            with self.subTest(mutation=i), self.assertRaises(ValueError): a.final_state(bad,original["loaded_bitstream"],original["loaded_bitstream_sha256"])
        post = dict(observed_utc=original["observed_utc"],loaded_bitstream=original["loaded_bitstream"],loaded_bitstream_sha256=original["loaded_bitstream_sha256"],
                    fclk0_mhz=31.25,state=a.physical.stopped_state(),fifo_count=0)
        a.post_study(post,post["loaded_bitstream"],post["loaded_bitstream_sha256"])
        post["state"]["dma"]["status"] = 2
        with self.assertRaises(ValueError): a.post_study(post,post["loaded_bitstream"],post["loaded_bitstream_sha256"])

    def test_requirement_inventory_manifest_summary_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root); expected = dict(source_revisions={"fixture":"a"*40},summary={"physical_benchmark_pairs":1152})
            with mock.patch.object(a,"evaluate",return_value=expected):
                original = a.manifest(root); self.assertEqual(len(original["requirements"]),7)
                with self.assertRaises(ValueError): a.manifest(root)
                path = root/"manifest.json"; mutants = []
                for key in original:
                    bad = copy.deepcopy(original); del bad[key]; mutants.append(bad)
                for key in a.REQUIREMENTS:
                    bad = copy.deepcopy(original); bad["requirements"][key] = []; mutants.append(bad)
                bad = copy.deepcopy(original); bad["files"].pop(next(iter(bad["files"]))); mutants.append(bad)
                for key,value in (("status","running"),("source_revisions",{}),("summary",{"physical_benchmark_pairs":1152.0})):
                    bad = copy.deepcopy(original); bad[key] = value; mutants.append(bad)
                for i,bad in enumerate(mutants):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=i), self.assertRaises(ValueError): a.audit(root)
                path.write_text(json.dumps(original)); file = root/"physical/final_state.json"; file.write_text("changed\n")
                with self.assertRaises(ValueError): a.audit(root)
                file.unlink(); file.symlink_to(root/"physical/post-study-state.json")
                with self.assertRaises(ValueError): a.inventory(root)

    def test_exact_implementation_reference_exporter_and_physical_programming_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); old,reg,fresh,hardware,measured,reports = fixture(root)
            def legacy(path,**_): return dict(revision=(fresh if path.parent.name == "verification" else old)["revision"],passing_scenarios=157 if path.parent.name == "verification" else 2397)
            with mock.patch.object(a.legacy,"audit",side_effect=legacy), mock.patch.object(a.regressions,"audit",return_value=dict(revision=reg["revision"],passing_scenarios=540,host_tests=130)), \
                 mock.patch.object(a.overlay,"audit",side_effect=lambda p:hardware[int(p.parent.name[-1])]), mock.patch.object(a.study,"audit",return_value=measured), \
                 mock.patch.object(a.functional,"audit",side_effect=lambda p,*_:reports[p.parent.name]), mock.patch.object(a,"physical_logs"), \
                 mock.patch.object(a,"source_state",return_value=(fresh["source_files"],fresh["source_sha256"])):
                result = a.evaluate(root,current=True); self.assertEqual(result["summary"]["physical_functional_boots"],8)
                self.assertEqual(result["summary"]["physical_directed_copies"],1280)
                for name in a.FUNCTIONAL:
                    for key,value in (("kind","wrong"),("previous_bitstream","/wrong.bit"),("previous_bitstream_sha256","f"*64),
                                      ("loaded_bitstream","/wrong.bit"),("downloaded",not reports[name]["downloaded"]),("collector_revision","f"*40)):
                        saved = reports[name][key]; reports[name][key] = value
                        with self.subTest(name=name,key=key), self.assertRaises(ValueError): a.evaluate(root)
                        reports[name][key] = saved
                for name in ("regressions/dma/manifest.json","reference/functional-c1/functional.json","verification/manifest.json"):
                    path = root/name; saved = path.read_text(); changed = json.loads(saved)
                    sources = changed["programs"]["runtime"]["metadata"]["source_files"] if "programs" in changed else changed["source_files"]
                    sources["software/dma.c"] = "f"*64; path.write_text(json.dumps(changed))
                    with self.subTest(source=name), self.assertRaises(ValueError): a.evaluate(root)
                    path.write_text(saved)
                changed = copy.deepcopy(fresh); changed["source_files"][a.EXPORTER] = "f"*64
                (root/"verification/manifest.json").write_text(json.dumps(changed))
                with self.assertRaises(ValueError): a.evaluate(root)
                (root/"verification/manifest.json").write_text(json.dumps(fresh))
                with mock.patch.object(a,"source_state",return_value=({},"f"*64)):
                    with self.assertRaises(ValueError): a.evaluate(root,current=True)
                hardware[1]["dma"] = False
                with self.assertRaises(ValueError): a.evaluate(root)

    def test_raw_board_log_schedule_boots_stops_and_missing_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); reports = {}; raw = ""
            for i,spec in enumerate(a.study.schedule(),1):
                raw += f"DMA_PHYSICAL_STUDY {i}/144 {spec['id']}\n"
                raw += "".join(f"PASS: physical Pynq-Z1 DMA boot={b} methods=8 serial_bytes=1000 retained_RAM=65536 exact_reference_counters\n" for b in (1,2))
                raw += f"PASS: physical DMA package audited and CPU/DMA safely STOPPED, /board/physical/{spec['id']}/physical.json\n"
            raw += "PASS: complete physical DMA study, 144 captures / 288 warm boots / 1152 paired jobs, /board/physical/physical-study.json\n"
            path = root/"study.log"; path.write_text(raw)
            for c in (0,1):
                for kind,size in (("runtime",97),("publication",14)):
                    name = f"{kind}-c{c}"; reports[name] = dict(kind=kind)
                    log = "".join(f"PASS: physical Pynq-Z1 DMA {kind} cache={c} boot={b} serial_bytes={size} retained_RAM=65536 independent_functional_oracle\n" for b in (1,2))
                    log += f"PASS: physical DMA functional package audited and CPU/DMA safely STOPPED, /board/{name}/functional-physical.json\n"
                    (root/(name+".log")).write_text(log)
            a.physical_logs(root,{},reports)
            for old,new in (("STUDY 1/144","STUDY 2/144"),("boot=2","boot=1"),("methods=8","methods=7"),
                            ("retained_RAM=65536","retained_RAM=65532"),("/b0-aaligned-c0/","/wrong/")):
                changed = raw.replace(old,new,1); self.assertNotEqual(changed,raw); path.write_text(changed)
                with self.subTest(old=old), self.assertRaises(ValueError): a.physical_logs(root,{},reports)
            path.write_text(raw+"FAIL: late failure\n")
            with self.assertRaises(ValueError): a.physical_logs(root,{},reports)


if __name__ == "__main__": unittest.main()
