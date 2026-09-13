"""Study coverage/aggregation/fresh rebuild and strict manifest mutation gates.

Capture/ELF/raw-log/RAM validation is independently tested in test_coherent_bench;
these tests mock that boundary to isolate batch-level omissions and mutations.
"""
import copy
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import coherent_study as study
import asterbench_coherent as bench


def package(entry):
    c = entry["configuration"]
    cycles = 12000 if c["workers"] == 1 else 15000  # real slowdowns must survive
    rows = [[dict({key: cycles if key.endswith("_cycles") else 5 for key in bench.COUNTERS},
                  job=job, items=c["items"]) for job in range(1, 4)] for _ in range(2)]
    return dict(configuration=copy.deepcopy(c), records=rows, observations=[1, 2], stops=[3, 4], symbols={"fixture": 1},
                metadata=dict(revision="a"*40, source_sha256="b"*64, dirty=False,
                              build_command=["make", "BUILD_DIR=/fixture/"+("repeat" if entry["fresh_repeat_of"] else "batch")]),
                toolchain={"fixture": 1}, artifacts={key: dict(file=entry["id"]+"."+key, sha256="c"*64)
                    for key in ("firmware", "elf", "map", "disassembly", "ram1", "ram2")})


def fixture(root):
    packages = {}; entries = []
    for entry in study.plan():
        result = package(entry); packages[entry["id"]] = result
        target = root/(entry["id"]+".json"); target.write_text(json.dumps(result))
        target.with_suffix(".log").write_text("fixture capture; raw validation belongs to inner auditor\n")
        for artifact in result["artifacts"].values(): (root/artifact["file"]).write_bytes(b"fixture")
        entries.append(dict(entry, file=target.name, sha256=study.sha(target)))
    manifest = dict(schema=study.SCHEMA, plan=study.PLAN, status="complete", revision="a"*40,
                    source_sha256="b"*64, entries=entries, summary=study.summarize(packages),
                    started_utc="2026-09-12T00:00:00+00:00", finished_utc="2026-09-12T01:00:00+00:00")
    return manifest, packages


class CoherentStudy(unittest.TestCase):
    def test_fixed_plan_and_honest_slowdown_aggregation(self):
        plan = study.plan()
        self.assertEqual(len(plan), 57); self.assertEqual(len({e["id"] for e in plan}), 57)
        self.assertEqual(sum(e["fresh_repeat_of"] is not None for e in plan), 1)
        for name in bench.NAMES:
            default = [e["configuration"] for e in plan if e["configuration"]["name"] == name and
                       e["configuration"]["items"] == 64 and not e["fresh_repeat_of"]]
            self.assertEqual({(c["workers"], c["l1"]) for c in default}, {(1, 0), (2, 0), (1, 1), (2, 1)})
        self.assertEqual({e["configuration"]["items"] for e in plan if e["configuration"]["name"] == "shared_mix"}, {2, 64, 129, 1024})
        for name in ("ping_pong", "spsc_queue"):
            self.assertEqual(sum(e["configuration"]["name"] == name and e["configuration"]["items"] == 1024 for e in plan), 4)
        summary = study.summarize({e["id"]: package(e) for e in plan})
        self.assertEqual((summary["captures"], summary["boots"], summary["jobs"]), (57, 114, 342))
        comparisons = summary["comparisons"]
        self.assertEqual(len(comparisons), 61)
        self.assertEqual({kind: sum(c["kind"] == kind for c in comparisons) for kind in
                          ("worker_scaling", "cache_effect", "padding_effect", "fresh_rebuild_repeat")},
                         dict(worker_scaling=28, cache_effect=28, padding_effect=4, fresh_rebuild_repeat=1))
        for comparison in comparisons:
            expected = 0.8 if comparison["kind"] == "worker_scaling" else 1.0
            self.assertEqual(comparison["cycle_ratio_baseline_over_candidate"], expected)
            self.assertEqual(comparison["per_boot_job_ratios"], [[expected]*3]*2)

    def test_readonly_audit_rejects_batch_mutations(self):
        with tempfile.TemporaryDirectory(prefix="aster-study-audit-") as directory:
            root = Path(directory); path = root/"study.json"; original, packages = fixture(root)

            def write(value): path.write_text(json.dumps(value))

            def inner(target): return json.loads(target.read_text())

            write(original)
            with mock.patch.object(study.results, "load", side_effect=inner), \
                 mock.patch.object(study.subprocess, "run", side_effect=AssertionError("saved paths must not execute")):
                study.audit(path)
                mutations = []
                for key in study.FIELDS:
                    bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
                for key, value in (("status", "running"), ("plan", "subset"), ("revision", "d"*40),
                                   ("source_sha256", "d"*64), ("started_utc", "2026-09-13T00:00:00+00:00"),
                                   ("finished_utc", "2026-09-12T02:00:00")):
                    bad = copy.deepcopy(original); bad[key] = value; mutations.append(bad)
                bad = copy.deepcopy(original); bad["entries"].pop(); mutations.append(bad)
                bad = copy.deepcopy(original); bad["entries"][0], bad["entries"][1] = bad["entries"][1], bad["entries"][0]; mutations.append(bad)
                for key, value in (("id", "wrong"), ("file", "../escape.json"), ("sha256", "0"*64), ("fresh_repeat_of", "wrong")):
                    bad = copy.deepcopy(original); bad["entries"][0][key] = value; mutations.append(bad)
                bad = copy.deepcopy(original); bad["entries"][0]["configuration"]["boots"] = 1; mutations.append(bad)
                bad = copy.deepcopy(original); bad["entries"][0]["configuration"]["l1"] = False; mutations.append(bad)
                bad = copy.deepcopy(original); bad["summary"]["comparisons"][0]["cycle_ratio_baseline_over_candidate"] = 100; mutations.append(bad)
                bad = copy.deepcopy(original); bad["summary"]["jobs"] = 342.0; mutations.append(bad)
                for index, bad in enumerate(mutations):
                    with self.subTest(manifest_mutation=index), self.assertRaises(ValueError): write(bad); study.audit(path)
                # Rehash envelope-level changes: the outer hash alone cannot hide
                # a config/source/tool mix or pretend a shared build is fresh.
                for index, keys, value in ((0, ("configuration", "items"), 2), (0, ("metadata", "revision"), "d"*40),
                    (1, ("toolchain", "fixture"), 2), (-1, ("metadata", "build_command"), ["make", "BUILD_DIR=/fixture/batch"]),
                    (-1, ("observations",), [9]), (-1, ("stops",), [9]), (-1, ("symbols",), {"changed": 1}),
                    (-1, ("artifacts", "firmware", "sha256"), "d"*64), (-1, ("artifacts", "ram2", "sha256"), "d"*64)):
                    manifest = copy.deepcopy(original); entry = manifest["entries"][index]; target = root/entry["file"]
                    saved = target.read_bytes(); mutated = json.loads(saved); selected = mutated
                    for key in keys[:-1]: selected = selected[key]
                    selected[keys[-1]] = value; target.write_text(json.dumps(mutated)); entry["sha256"] = study.sha(target)
                    with self.subTest(capture_mutation=keys), self.assertRaises(ValueError): write(manifest); study.audit(path)
                    target.write_bytes(saved)
                write(original); extra = root/"unlisted"; extra.write_bytes(b"extra")
                with self.assertRaises(ValueError): study.audit(path)
                extra.unlink()  # generated fixture only
                log = root/(original["entries"][0]["id"]+".log")
                saved = log.read_bytes(); log.unlink(); log.symlink_to(path)
                with self.assertRaises(ValueError): study.audit(path)
                log.unlink(); log.write_bytes(saved); study.audit(path)

    def test_capture_fresh_repeat_failure_and_no_overwrite(self):
        for scenario in ("complete", "child-failed", "source-changed"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-study-run-") as directory, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                output = Path(directory)/"new"
                stack.enter_context(mock.patch.object(study, "command", side_effect=lambda args:
                    "" if args[:2] == ["git", "status"] else "a"*40))
                stack.enter_context(mock.patch.object(study, "source_state", return_value=({"Makefile": "f"*64}, "b"*64)))
                stack.enter_context(mock.patch.object(study.results, "source_at_revision"))
                audited = stack.enter_context(mock.patch.object(study, "audit"))
                invocations = []

                def child(args, build_directory):
                    invocations.append(build_directory)
                    args.output.with_suffix(".log").write_text("fixture retained log")
                    if scenario == "child-failed": raise ValueError("injected child failure")
                    entry = next(e for e in study.plan() if e["id"] == args.output.stem)
                    result = package(entry)
                    if scenario == "source-changed": result["metadata"]["revision"] = "d"*40
                    args.output.write_text(json.dumps(result)); return result

                stack.enter_context(mock.patch.object(study.results, "capture", side_effect=child))
                if scenario == "complete": study.capture(output)
                else:
                    with self.assertRaises(ValueError): study.capture(output)
                manifest = json.loads((output/"study.json").read_text())
                self.assertEqual(manifest["status"], "complete" if scenario == "complete" else "failed")
                self.assertTrue(list(output.glob("*.log")))
                if scenario == "complete":
                    self.assertEqual(len(invocations), 57); self.assertEqual(len(set(invocations[:-1])), 1)
                    self.assertIsNone(invocations[-1]); audited.assert_called_once()
                else: audited.assert_not_called(); self.assertEqual(len(invocations), 1)
                prior = (output/"study.json").read_bytes()
                with self.assertRaises(ValueError): study.capture(output)
                self.assertEqual((output/"study.json").read_bytes(), prior)
        with tempfile.TemporaryDirectory(prefix="aster-study-dirty-") as directory, mock.patch.object(study, "command", return_value=" M source"):
            output = Path(directory)/"new"
            with self.assertRaises(ValueError): study.capture(output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
