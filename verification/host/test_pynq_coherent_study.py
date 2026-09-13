"""Physical batch coverage, safe sequencing, failure retention and mutations.

Inner ELF/signoff/physical record gates have their own tests; mock those here
to isolate the cross-capture contract. No physical measurements are synthesized.
"""
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
sys.path.insert(0, str(ROOT/"scripts"))
import pynq_coherent_study as batch
from test_coherent_study import package


def fixtures(root):
    reference = root/"reference"; reference.mkdir(); (reference/"study.json").write_text("fixture complete study")
    overlays = {}
    for caches in (0, 1):
        folder = root/f"overlay-c{caches}"; folder.mkdir(); overlays[caches] = folder/"overlay.json"
        overlays[caches].write_text("fixture overlay "+str(caches))
    for entry in batch.study.plan(): (reference/(entry["id"]+".json")).write_text(json.dumps(package(entry)))
    return reference/"study.json", overlays


def report(entry, prior, loaded, sources):
    return dict(previous_bitstream=prior, loaded_bitstream=loaded, downloaded=prior != loaded,
                collector_revision="a"*40, collector_files={key: sources[key] for key in batch.physical.COLLECTOR_FILES},
                boots=[dict(records=rows, reference_comparison=dict(exact_counter_match=True)) for rows in package(entry)["records"]])


class PhysicalStudy(unittest.TestCase):
    def test_isolated_package_and_one_switch_schedule(self):
        entries = batch.schedule()
        self.assertEqual(len(entries), 57); self.assertEqual(len({e["id"] for e in entries}), 57)
        self.assertEqual([e["configuration"]["l1"] for e in entries], [1]*29+[0]*28)
        self.assertEqual(entries[28]["fresh_repeat_of"], "shared_mix-i64-r4-p2-c1")
        with tempfile.TemporaryDirectory(prefix="aster-physical-study-import-") as directory:
            root = Path(directory)
            for name in batch.DRIVER_FILES: shutil.copy2(ROOT/"scripts"/name, root/name)
            subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0,sys.argv[1]); import pynq_coherent_study; assert 'pynq' not in sys.modules",
                            str(root)], cwd=root, check=True, capture_output=True)

    def test_capture_failure_no_overwrite_and_pcapi_sequence(self):
        for failure in (False, True):
            with tempfile.TemporaryDirectory(prefix="aster-physical-study-run-") as directory, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                root = Path(directory); reference, overlays = fixtures(root)
                args = SimpleNamespace(output=root/"physical", reference=reference, overlay_off=overlays[0], overlay_on=overlays[1],
                    expected_loaded=str(overlays[1].parent/"aster_linux.bit"), collector_revision="a"*40, host_pause=0.2, timeout=120)
                stack.enter_context(mock.patch.object(batch, "preflight")); audited = stack.enter_context(mock.patch.object(batch, "audit"))
                sources = {name: batch.sha(ROOT/"scripts"/name) for name in batch.DRIVER_FILES}; calls = []
                def child(options):
                    calls.append(options); options.output.mkdir()
                    if failure:
                        (options.output/"partial.uart").write_bytes(b"retained partial serial"); raise RuntimeError("injected serial failure")
                    entry = next(e for e in batch.schedule() if e["id"] == options.output.name)
                    captured = report(entry, options.expected_loaded, str(options.overlay.parent/"aster_linux.bit"), sources)
                    (options.output/"physical.json").write_text(json.dumps(captured)); return captured
                stack.enter_context(mock.patch.object(batch.collector, "run", side_effect=child))
                if failure:
                    with self.assertRaises(RuntimeError): batch.run(args)
                else: batch.run(args)
                manifest = json.loads((args.output/"physical-study.json").read_text())
                self.assertEqual(manifest["status"], "failed" if failure else "complete")
                if failure:
                    audited.assert_not_called(); self.assertEqual(len(calls), 1)
                    self.assertTrue(list(args.output.glob("*/partial.uart")))
                else:
                    audited.assert_called_once(); self.assertEqual(len(calls), 57)
                    self.assertEqual([c.download for c in calls], [False]*29+[True]+[False]*27)
                    self.assertEqual((manifest["summary"]["boots"], manifest["summary"]["jobs"]), (114, 342))
                    self.assertTrue(manifest["summary"]["reference_counter_match"])
                saved = (args.output/"physical-study.json").read_bytes()
                with self.assertRaises(ValueError): batch.run(args)
                self.assertEqual((args.output/"physical-study.json").read_bytes(), saved)

    def test_batch_audit_mutations(self):
        with tempfile.TemporaryDirectory(prefix="aster-physical-study-audit-") as directory, ExitStack() as stack:
            root = Path(directory); reference, overlays = fixtures(root); output = root/"physical"; output.mkdir()
            stack.enter_context(mock.patch.object(batch, "preflight"))
            stack.enter_context(mock.patch.object(batch.physical, "audit", side_effect=lambda path, *a, **k: json.loads(path.read_text())))
            source = {name: "b"*64 for name in batch.DRIVER_FILES}; prior = str(overlays[1].parent/"aster_linux.bit")
            manifest = dict(schema=batch.SCHEMA, plan=batch.study.PLAN, status="complete", reference_sha256=batch.sha(reference),
                overlay_sha256={str(c): batch.sha(p) for c, p in overlays.items()}, overlay_paths={str(c): str(p) for c, p in overlays.items()},
                initial_bitstream=prior, collector_revision="a"*40, driver_files=source, entries=[])
            reports = {}
            for entry in batch.schedule():
                folder = output/entry["id"]; folder.mkdir(); path = folder/"physical.json"
                loaded = str(overlays[entry["configuration"]["l1"]].parent/"aster_linux.bit")
                captured = report(entry, prior, loaded, source); prior = loaded; reports[entry["id"]] = captured
                path.write_text(json.dumps(captured)); manifest["entries"].append(dict(id=entry["id"], file=entry["id"]+"/physical.json", sha256=batch.sha(path)))
            manifest["summary"] = batch.summary(reports, reference); path = output/"physical-study.json"
            def save(value): path.write_text(json.dumps(value))
            save(manifest); batch.audit(path, reference, overlays, clean=False)
            mutations = []
            for key in manifest:
                bad = copy.deepcopy(manifest); del bad[key]; mutations.append(bad)
            for key, value in (("status", "failed"), ("reference_sha256", "c"*64), ("collector_revision", "wrong")):
                bad = copy.deepcopy(manifest); bad[key] = value; mutations.append(bad)
            bad = copy.deepcopy(manifest); bad["entries"].pop(); mutations.append(bad)
            bad = copy.deepcopy(manifest); bad["entries"][0], bad["entries"][1] = bad["entries"][1], bad["entries"][0]; mutations.append(bad)
            bad = copy.deepcopy(manifest); bad["entries"][0]["file"] = "../escape.json"; mutations.append(bad)
            bad = copy.deepcopy(manifest); bad["summary"]["jobs"] = 342.0; mutations.append(bad)
            bad = copy.deepcopy(manifest); bad["summary"]["comparisons"][0]["summed_cycles"][0] += 1; mutations.append(bad)
            for index, bad in enumerate(mutations):
                save(bad)
                with self.subTest(mutation=index), self.assertRaises(ValueError): batch.audit(path, reference, overlays, clean=False)
            for key, value in (("downloaded", True), ("previous_bitstream", "/unrelated.bit"), ("collector_revision", "c"*40),
                               ("loaded_bitstream", "/unrelated.bit")):
                bad = copy.deepcopy(manifest); entry = bad["entries"][0]; target = output/entry["file"]
                saved = target.read_bytes(); captured = json.loads(saved); captured[key] = value
                target.write_text(json.dumps(captured)); entry["sha256"] = batch.sha(target); save(bad)
                with self.subTest(rehashed=key), self.assertRaises(ValueError): batch.audit(path, reference, overlays, clean=False)
                target.write_bytes(saved)
            save(manifest); extra = output/"extra"; extra.write_text("unlisted")
            with self.assertRaises(ValueError): batch.audit(path, reference, overlays, clean=False)


if __name__ == "__main__": unittest.main()
