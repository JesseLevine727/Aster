"""Study orchestration mutations use synthetic captures, not performance evidence.

The study tests mock only the separately mutation-tested per-capture artifact
loader; they still enforce plan ordering, source/tool identity, independent
repeats, directory inventory and complete summary recomputation.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import asterbench_dma as b
import dma_study as study
from test_dma_results import fixture


class DmaStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captures = {}
        for entry in study.plan():
            c = entry["configuration"]
            capture, _log, _data = fixture(c["size"], c["alignment"], c["l1"])
            capture["metadata"]["dirty"] = False
            for name, artifact in capture["artifacts"].items(): artifact["file"] = entry["id"]+"-"+name+".bin"
            if entry["fresh_repeat_of"]:
                capture["metadata"]["build_command"][3] = "BUILD_DIR=/test/repeat-"+entry["id"]
            cls.captures[entry["id"]] = capture

    def test_frozen_full_coverage_and_independent_repeat_selection(self):
        entries = study.plan(); self.assertEqual(len(entries), 144)
        main = [e for e in entries if e["fresh_repeat_of"] is None]
        repeats = [e for e in entries if e["fresh_repeat_of"]]
        self.assertEqual(len(main), 138); self.assertEqual(len(repeats), 6)
        self.assertEqual({(e["configuration"]["size"], e["configuration"]["alignment"], e["configuration"]["l1"]) for e in main},
                         {(size, alignment, cache) for size in b.SIZES for alignment in b.ALIGNMENTS for cache in (0,1)})
        self.assertTrue(all(e["configuration"]["size"] == 1024 for e in repeats))
        summary = study.summarize(self.captures)
        self.assertEqual((summary["boots"], summary["paired_jobs"], summary["method_records"]), (288,1152,2304))
        with self.assertRaises(ValueError): study.summarize({})

    def test_crossover_keeps_mixed_pairs_slowdowns_ties_reversals(self):
        def point(size, low, high): return dict(size=size, min_cpu_over_dma=low, max_cpu_over_dma=high)
        points = [point(0,2,2), point(1,.5,.8), point(2,.9,1.1), point(4,1.1,1.2),
                  point(8,.9,1.2), point(16,1,1), point(32,1.3,1.5), point(64,1.2,1.4)]
        s = study.series_summary(points)
        self.assertEqual(s["first_any_pair_dma_win_bytes"], 2)
        self.assertEqual(s["first_all_pairs_dma_win_bytes"], 4)
        self.assertEqual(s["later_not_all_pairs_win_bytes"], [8,16])
        self.assertEqual(s["all_pairs_win_through_largest_tested_from_bytes"], 32)
        for points in ([point(0,2,2),point(1,.5,.8)], [point(0,2,2),point(1,1,1)]):
            s = study.series_summary(points)
            self.assertIsNone(s["first_any_pair_dma_win_bytes"])
            self.assertIsNone(s["first_all_pairs_dma_win_bytes"])
            self.assertIsNone(s["all_pairs_win_through_largest_tested_from_bytes"])

    def package(self, root):
        first = next(iter(self.captures.values()))
        manifest = dict(schema=study.SCHEMA, plan=study.PLAN, status="complete", revision=first["metadata"]["revision"],
                        source_sha256=first["metadata"]["source_sha256"], entries=[], summary=study.summarize(self.captures),
                        started_utc="2026-01-01T00:00:00+00:00", finished_utc="2026-01-01T00:01:00+00:00")
        for entry in study.plan():
            path = root/(entry["id"]+".json"); path.write_text("synthetic loader placeholder\n")
            path.with_suffix(".log").write_text("synthetic loader placeholder\n")
            for item in self.captures[entry["id"]]["artifacts"].values(): (root/item["file"]).write_bytes(b"fixture")
            manifest["entries"].append(dict(entry, file=path.name, sha256=study.sha(path)))
        path = root/"study.json"; path.write_text(json.dumps(manifest))
        return path, manifest

    def test_full_read_only_audit_and_manifest_mutations(self):
        with tempfile.TemporaryDirectory(prefix="aster-dma-study-fixture-") as directory:
            root = Path(directory); path, original = self.package(root)
            with mock.patch.object(study.results, "load", side_effect=lambda p,**kw:self.captures[p.stem]) as loader:
                study.audit(path); self.assertEqual(loader.call_count, 144)
                mutants = []
                for key in original:
                    bad = copy.deepcopy(original); del bad[key]; mutants.append(bad)
                for key, value in (("status","running"), ("plan","subset"), ("source_sha256","1"*64),
                                   ("started_utc","2026-01-01T00:00:00"), ("finished_utc","2025-01-01T00:00:00+00:00")):
                    bad = copy.deepcopy(original); bad[key] = value; mutants.append(bad)
                bad = copy.deepcopy(original); bad["entries"].pop(); mutants.append(bad)
                bad = copy.deepcopy(original); bad["entries"][0],bad["entries"][1] = bad["entries"][1],bad["entries"][0]; mutants.append(bad)
                for key, value in (("file","../escape"), ("sha256","1"*64), ("fresh_repeat_of","anything")):
                    bad = copy.deepcopy(original); bad["entries"][0][key] = value; mutants.append(bad)
                bad = copy.deepcopy(original); bad["entries"][0]["configuration"]["jobs"] = True; mutants.append(bad)
                bad = copy.deepcopy(original); bad["summary"]["series"]["aaligned-c0"]["first_any_pair_dma_win_bytes"] = 1; mutants.append(bad)
                for i, bad in enumerate(mutants):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=i), self.assertRaises(ValueError): study.audit(path)
                path.write_text(json.dumps(original)); (root/"unexpected.txt").write_text("extra")
                with self.assertRaises(ValueError): study.audit(path)

    def test_mixed_capture_source_tools_geometry_and_builds(self):
        with tempfile.TemporaryDirectory(prefix="aster-dma-study-mixed-") as directory:
            path, _ = self.package(Path(directory)); specs = study.plan()
            mutations = [(specs[1]["id"], ("metadata","dirty"), True),
                         (specs[1]["id"], ("metadata","revision"), "1"*40),
                         (specs[1]["id"], ("configuration","line_count"), 32),
                         (specs[1]["id"], ("toolchain","tools","gcc","sha256"), "1"*64),
                         (specs[1]["id"], ("metadata","build_command",3), "BUILD_DIR=/test/other"),
                         (specs[-1]["id"], ("metadata","build_command",3), "BUILD_DIR=/test/build"),
                         (specs[-1]["id"], ("records",0,0,"h0_cycles"), 123),
                         (specs[-1]["id"], ("artifacts","ram1","sha256"), "1"*64)]
            for identifier, keys, value in mutations:
                changed = copy.deepcopy(self.captures[identifier]); obj = changed
                for key in keys[:-1]: obj = obj[key]
                obj[keys[-1]] = value
                with mock.patch.object(study.results, "load", side_effect=lambda p,**kw:changed if p.stem == identifier else self.captures[p.stem]):
                    with self.subTest(identifier=identifier, keys=keys), self.assertRaises(ValueError): study.audit(path)

    def test_repeat_must_not_reuse_another_repeat_build(self):
        entry = study.plan()[-1]; repeated = self.captures[entry["id"]]; base = self.captures[entry["fresh_repeat_of"]]
        seen = set(); study.validate_repeat(base, repeated, "/test/build", seen)
        with self.assertRaises(ValueError): study.validate_repeat(base, repeated, "/test/build", seen)


if __name__ == "__main__":
    unittest.main()
