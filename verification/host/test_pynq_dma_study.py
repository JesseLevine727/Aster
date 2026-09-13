"""Mocked batch boundaries test full coverage and PCAP sequencing, not FPGA results."""
import copy
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import pynq_dma_study as batch
from test_asterbench_dma import records


def inputs(root):
    reference = root/"reference"; reference.mkdir(); (reference/"study.json").write_text("synthetic complete study")
    overlays,hardware = {},{}
    for c in (0,1):
        folder = root/f"overlay-c{c}"; folder.mkdir(); overlays[c] = folder/"overlay.json"
        overlays[c].write_text("synthetic overlay "+str(c))
        hardware[c] = dict(files={"aster_linux.bit":dict(sha256=str(c+1)*64)})
    return reference/"study.json",overlays,hardware


def report(entry,prior,prior_sha,loaded,sources):
    c = entry["configuration"]; rows = records(c["size"],c["alignment"],c["l1"])
    return dict(previous_bitstream=prior,previous_bitstream_sha256=prior_sha,loaded_bitstream=loaded,downloaded=prior != loaded,
        collector_revision="a"*40,collector_files={k:sources[k] for k in batch.physical.COLLECTOR_FILES},
        boots=[dict(boot=i,records=copy.deepcopy(rows),reference_comparison=dict(exact_counter_match=True)) for i in (1,2)])


class PhysicalDmaStudy(unittest.TestCase):
    def test_full_plan_and_isolated_imports(self):
        entries = batch.schedule()
        self.assertEqual(len(entries),144); self.assertEqual(len({e["id"] for e in entries}),144)
        self.assertEqual([e["configuration"]["l1"] for e in entries],[0]*72+[1]*72)
        self.assertEqual([e["configuration"]["size"] for e in entries if e["fresh_repeat_of"]],[1024]*6)
        with tempfile.TemporaryDirectory(prefix="aster-pynq7-study-import-") as directory:
            root = Path(directory)
            for name in batch.DRIVER_FILES: shutil.copy2(ROOT/"scripts"/name,root/name)
            subprocess.run([sys.executable,"-I","-c",
                "import sys; sys.path.insert(0,sys.argv[1]); import pynq_dma_study; assert 'pynq' not in sys.modules",str(root)],
                cwd=root,check=True,capture_output=True)

    def test_capture_all_cases_hash_chain_and_partial_failure_retention(self):
        for failure in (False,True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(prefix="aster-pynq7-study-flow-") as directory, ExitStack() as stack:
                root = Path(directory); reference,overlays,hardware = inputs(root)
                args = SimpleNamespace(output=root/"physical",reference=reference,overlay_off=overlays[0],overlay_on=overlays[1],
                    expected_loaded=str(overlays[0].parent/"aster_linux.bit"),expected_loaded_sha256="1"*64,
                    collector_revision="a"*40,host_pause=0.2,timeout=120)
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(batch,"preflight",return_value=(None,hardware)))
                audited = stack.enter_context(mock.patch.object(batch,"audit"))
                sources = {name:batch.sha(ROOT/"scripts"/name) for name in batch.DRIVER_FILES}; calls = []
                def child(options):
                    calls.append(options); options.output.mkdir()
                    if failure:
                        (options.output/"partial.uart").write_bytes(b"retained failure"); raise RuntimeError("injected serial failure")
                    entry = next(e for e in batch.schedule() if e["id"] == options.output.name)
                    captured = report(entry,options.expected_loaded,options.expected_loaded_sha256,str(options.overlay.parent/"aster_linux.bit"),sources)
                    (options.output/"physical.json").write_text(json.dumps(captured)); return captured
                stack.enter_context(mock.patch.object(batch.collector,"run",side_effect=child))
                if failure:
                    with self.assertRaises(RuntimeError): batch.run(args)
                else: batch.run(args)
                path = args.output/"physical-study.json"; m = json.loads(path.read_text())
                self.assertEqual(m["status"],"failed" if failure else "complete")
                if failure:
                    self.assertEqual(len(calls),1); audited.assert_not_called()
                    self.assertTrue(list(args.output.glob("*/partial.uart")))
                else:
                    self.assertEqual(len(calls),144); audited.assert_called_once()
                    self.assertEqual([c.download for c in calls],[False]*72+[True]+[False]*71)
                    self.assertEqual([c.expected_loaded_sha256 for c in calls],["1"*64]*73+["2"*64]*71)
                    self.assertEqual((m["summary"]["boots"],m["summary"]["paired_jobs"],m["summary"]["method_records"]),(288,1152,2304))
                    self.assertTrue(all(r["identical_records"] for r in m["summary"]["repeats"]))
                    # All fixture methods tie; no fabricated crossover.
                    self.assertTrue(all(s["first_any_pair_dma_win_bytes"] is None for s in m["summary"]["series"].values()))
                saved = path.read_bytes()
                with self.assertRaises(ValueError): batch.run(args)
                self.assertEqual(path.read_bytes(),saved)

    def test_audit_coverage_summary_hashes_and_rehashed_sequence_mutations(self):
        with tempfile.TemporaryDirectory(prefix="aster-pynq7-study-audit-") as directory, ExitStack() as stack:
            root = Path(directory); reference,overlays,hardware = inputs(root); output = root/"physical"; output.mkdir()
            stack.enter_context(mock.patch.object(batch,"preflight",return_value=(None,hardware)))
            stack.enter_context(mock.patch.object(batch.physical,"audit",side_effect=lambda path,*a,**kw:json.loads(path.read_text())))
            sources = {name:"b"*64 for name in batch.DRIVER_FILES}; prior = str(overlays[0].parent/"aster_linux.bit"); prior_sha = "1"*64
            m = dict(schema=batch.SCHEMA,plan=batch.study.PLAN,status="complete",reference_sha256=batch.sha(reference),
                overlay_sha256={str(c):batch.sha(p) for c,p in overlays.items()},overlay_paths={str(c):str(p) for c,p in overlays.items()},
                initial_bitstream=prior,initial_bitstream_sha256=prior_sha,collector_revision="a"*40,driver_files=sources,entries=[],
                started_utc="2026-01-01T00:00:00+00:00",finished_utc="2026-01-01T00:01:00+00:00")
            reports = {}
            for entry in batch.schedule():
                folder = output/entry["id"]; folder.mkdir(); path = folder/"physical.json"
                c = entry["configuration"]["l1"]; loaded = str(overlays[c].parent/"aster_linux.bit")
                captured = report(entry,prior,prior_sha,loaded,sources); prior = loaded; prior_sha = hardware[c]["files"]["aster_linux.bit"]["sha256"]
                reports[entry["id"]] = captured; path.write_text(json.dumps(captured))
                m["entries"].append(dict(id=entry["id"],file=entry["id"]+"/physical.json",sha256=batch.sha(path)))
            m["summary"] = batch.summary(reports); path = output/"physical-study.json"
            def save(value): path.write_text(json.dumps(value))
            save(m); batch.audit(path,reference,overlays,clean=False)
            mutations = []
            for key in m:
                bad = copy.deepcopy(m); del bad[key]; mutations.append(bad)
            for key,value in (("status","failed"),("reference_sha256","c"*64),("collector_revision","wrong"),
                              ("initial_bitstream_sha256","c"*64),("started_utc","2026-01-01T00:00:00")):
                bad = copy.deepcopy(m); bad[key] = value; mutations.append(bad)
            bad = copy.deepcopy(m); bad["entries"].pop(); mutations.append(bad)
            bad = copy.deepcopy(m); bad["entries"][0],bad["entries"][1] = bad["entries"][1],bad["entries"][0]; mutations.append(bad)
            bad = copy.deepcopy(m); bad["entries"][0]["file"] = "../escape"; mutations.append(bad)
            bad = copy.deepcopy(m); bad["summary"]["method_records"] = 2304.0; mutations.append(bad)
            bad = copy.deepcopy(m); bad["summary"]["series"]["aaligned-c0"]["first_any_pair_dma_win_bytes"] = 1; mutations.append(bad)
            for i,bad in enumerate(mutations):
                save(bad)
                with self.subTest(mutation=i), self.assertRaises(ValueError): batch.audit(path,reference,overlays,clean=False)
            for key,value in (("downloaded",True),("previous_bitstream","/other.bit"),("previous_bitstream_sha256","c"*64),
                              ("collector_revision","c"*40),("loaded_bitstream","/other.bit")):
                bad = copy.deepcopy(m); entry = bad["entries"][0]; target = output/entry["file"]; saved = target.read_bytes()
                captured = json.loads(saved); captured[key] = value; target.write_text(json.dumps(captured))
                entry["sha256"] = batch.sha(target); save(bad)
                with self.subTest(rehashed=key), self.assertRaises(ValueError): batch.audit(path,reference,overlays,clean=False)
                target.write_bytes(saved)
            save(m); (output/"extra").write_text("unlisted")
            with self.assertRaises(ValueError): batch.audit(path,reference,overlays,clean=False)


if __name__ == "__main__": unittest.main()
