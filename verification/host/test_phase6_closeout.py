"""Outer requirement/file/identity gates; inner semantic auditors tested apart."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import audit_phase6 as closeout


def stopped():
    return dict(schema="aster.coherent.final-state.v1", observed_utc="2026-09-13T00:00:00+00:00",
        loaded_bitstream="/board/c1/aster_linux.bit", fclk0_mhz=31.25,
        registers=dict(abi=0x60001, clock_hz=31250000, control=0, features=3, fifo_count=0,
                       hart_status=0, harts=2, magic=0x41535452, status=0, stop_status=1))


def fixture(root):
    paths = set(p for files in closeout.REQUIREMENTS.values() for p in files)
    paths.add("physical/final_state.json")
    for name in paths:
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("fixture\n")
    sources = {"Makefile": "a"*64, "rtl/core.sv": "b"*64, "software/runtime.c": "c"*64}
    reg = dict(revision="1"*40, source_files=sources, source_sha256="d"*64, toolchain={"fixture": 1})
    fresh = dict(reg, revision="2"*40)
    hardware = {c: dict(reg, caches=bool(c), signoff={"fixture": c}) for c in (0, 1)}
    measured = dict(collector_revision="3"*40, summary=dict(reference_counter_match=True),
                    overlay_paths={str(c): f"/board/c{c}/overlay.json" for c in (0, 1)})
    reports = {}
    prior = "/board/c0/aster_linux.bit"
    for c in (0, 1):
        for kind in ("runtime", "lifecycle"):
            loaded = f"/board/c{c}/aster_linux.bit"
            reports[f"{kind}-c{c}"] = dict(kind=kind, previous_bitstream=prior, loaded_bitstream=loaded,
                                         downloaded=prior != loaded, collector_revision="4"*40)
            prior = loaded
    values = {"regressions/manifest.json": reg, "verification/manifest.json": fresh,
              "reference/study/study.json": dict(revision="5"*40), "physical/final_state.json": stopped()}
    for c in (0, 1):
        values[f"reference/functional-c{c}/functional.json"] = dict(programs={k: dict(metadata=dict(revision="6"*40, source_files=sources)) for k in ("runtime", "lifecycle")})
    for name, value in values.items(): (root/name).write_text(json.dumps(value))
    (root/"verification/01-check.log").write_text("Ran 84 tests in 25.0s\nOK\n")
    return reg, fresh, hardware, measured, reports


class Phase6Closeout(unittest.TestCase):
    def test_final_stopped_state_is_exact_and_typed(self):
        original = stopped(); closeout.final_state(original, original["loaded_bitstream"])
        mutations = []
        for key in original:
            bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
        for key in original["registers"]:
            bad = copy.deepcopy(original); bad["registers"][key] += 1; mutations.append(bad)
            bad = copy.deepcopy(original); bad["registers"][key] = bool(bad["registers"][key]); mutations.append(bad)
        for key, value in (("loaded_bitstream", "/other/aster_linux.bit"), ("fclk0_mhz", 100),
                           ("observed_utc", "2026-09-13T00:00:00")):
            bad = copy.deepcopy(original); bad[key] = value; mutations.append(bad)
        for index, bad in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises(ValueError): closeout.final_state(bad, original["loaded_bitstream"])

    def test_requirement_inventory_and_outer_summary_mutations(self):
        with tempfile.TemporaryDirectory(prefix="aster-closeout-outer-") as directory:
            root = Path(directory); fixture(root)
            expected = dict(source_revisions={"fixture": "a"*40}, summary={"physical_benchmark_jobs": 342})
            with mock.patch.object(closeout, "evaluate", return_value=expected):
                original = closeout.manifest(root); self.assertEqual(len(original["requirements"]), 7)
                with self.assertRaises(ValueError): closeout.manifest(root)
                path = root/"manifest.json"; mutations = []
                for key in original:
                    bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
                for key in closeout.REQUIREMENTS:
                    bad = copy.deepcopy(original); del bad["requirements"][key]; mutations.append(bad)
                    bad = copy.deepcopy(original); bad["requirements"][key] = []; mutations.append(bad)
                bad = copy.deepcopy(original); bad["files"].pop(next(iter(bad["files"]))); mutations.append(bad)
                for key, value in (("status", "running"), ("summary", {"physical_benchmark_jobs": 342.0}), ("source_revisions", {})):
                    bad = copy.deepcopy(original); bad[key] = value; mutations.append(bad)
                for index, bad in enumerate(mutations):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=index), self.assertRaises(ValueError): closeout.audit(root)
                path.write_text(json.dumps(original)); file = root/"verification/01-check.log"; file.write_text("changed\n")
                with self.assertRaises(ValueError): closeout.audit(root)
                file.unlink(); file.symlink_to(root/"physical/final_state.json")
                with self.assertRaises(ValueError): closeout.inventory(root)

    def test_cross_package_sources_and_physical_programming_chain(self):
        with tempfile.TemporaryDirectory(prefix="aster-closeout-links-") as directory:
            root = Path(directory); reg, fresh, hardware, measured, reports = fixture(root)
            def regression(path, **_):
                return dict(revision=(fresh if path.parent.name == "verification" else reg)["revision"],
                            passing_scenarios=157 if path.parent.name == "verification" else 2397)
            with mock.patch.object(closeout.regressions, "audit", side_effect=regression), \
                 mock.patch.object(closeout.overlay, "audit", side_effect=lambda path: hardware[int(path.parent.name[-1])]), \
                 mock.patch.object(closeout.study, "audit", return_value=measured), \
                 mock.patch.object(closeout.functional, "audit", side_effect=lambda path, *args: reports[path.parent.name]), \
                 mock.patch.object(closeout, "source_state", return_value=(fresh["source_files"], fresh["source_sha256"])):
                result = closeout.evaluate(root, current=True)
                self.assertEqual(result["summary"]["physical_functional_boots"], 8)
                self.assertEqual(result["summary"]["fresh_host_tests"], 84)
                for name in closeout.FUNCTIONAL:
                    for key, value in (("kind", "wrong"), ("previous_bitstream", "/wrong.bit"), ("loaded_bitstream", "/wrong.bit"),
                                       ("downloaded", not reports[name]["downloaded"]), ("collector_revision", "f"*40)):
                        old = reports[name][key]; reports[name][key] = value
                        with self.subTest(name=name, key=key), self.assertRaises(ValueError): closeout.evaluate(root)
                        reports[name][key] = old
                hardware[1]["caches"] = False
                with self.assertRaises(ValueError): closeout.evaluate(root)
                hardware[1]["caches"] = True; hardware[1]["revision"] = "f"*40
                with self.assertRaises(ValueError): closeout.evaluate(root)
                hardware[1]["revision"] = reg["revision"]; measured["summary"]["reference_counter_match"] = False
                with self.assertRaises(ValueError): closeout.evaluate(root)
                measured["summary"]["reference_counter_match"] = True
                with mock.patch.object(closeout, "source_state", return_value=({}, "f"*64)):
                    with self.assertRaises(ValueError): closeout.evaluate(root, current=True)
                changed = copy.deepcopy(fresh); changed["source_files"]["software/runtime.c"] = "e"*64
                (root/"verification/manifest.json").write_text(json.dumps(changed))
                with self.assertRaises(ValueError): closeout.evaluate(root)


if __name__ == "__main__": unittest.main()
