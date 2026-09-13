"""Strict regression provenance and semantic gate mutations (synthetic fixtures).

Fixtures test the auditor, never stand in for RTL or physical acceptance. The
actual complete 22-target raw run is audited separately by the same functions.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import audit_phase6_regressions as audit
import test_coherent_bench as fixtures


def manifest(root):
    old, _ = fixtures.CoherentProvenance().fixture()
    tool = dict(path="/fixture/tool", sha256="a"*64, version="synthetic fixture")
    m = dict(schema="aster.regressions.phase6.v1", status="complete", revision="b"*40,
             dirty=False, source_files={"Makefile": "c"*64}, toolchain=old["toolchain"],
             python=tool, host_compiler=tool, platform="fixture host", targets=list(audit.TARGETS),
             build_directory="/fixture/build", results=[], started_utc="2026-09-13T00:00:00+00:00",
             finished_utc="2026-09-13T01:00:00+00:00")
    m["source_sha256"] = hashlib.sha256(json.dumps(m["source_files"], sort_keys=True).encode()).hexdigest()
    for i, target in enumerate(audit.TARGETS, 1):
        name = f"{i:02d}-{target}.log"; raw = ("fixture for "+target+"\n").encode(); (root/name).write_bytes(raw)
        m["results"].append(dict(target=target, command=["make", "--no-print-directory", "-j2", "BUILD_DIR=/fixture/build", target],
            exit_code=0, elapsed_seconds=1.0, log=name, log_bytes=len(raw), log_sha256=hashlib.sha256(raw).hexdigest()))
    return m


def litmus_fixture(seeds, epochs):
    lines = []
    for seed in seeds:
        for boot in (1, 2):
            for mode in range(8):
                histogram = f"0,{epochs},0,0" if mode in (0, 1, 5) else f"{epochs},0,0,0"
                failures = "2,3" if mode == 7 else "0,0"
                lines.append(f"PASS: coherent litmus boot={boot} mode={mode} epochs={epochs} seed={seed} histogram={histogram} sc_failure={failures} exact per-hart counters")
        lines.append(f"PASS: coherent litmus closeout trials={epochs*16} contended_sc_failures=10 warm_boots=2 complete_ram=65536; no destructive reset after POR")
    return "\n".join(lines)+"\n"


def v4_fixture():
    lines = []
    for boot in (1, 2):
        lines.append(f"ASTERBOOT {boot}\n")
        for job in (1, 2, 3):
            row = fixtures.fixture(job=job, jobs=3)
            lines.extend((fixtures.emit(row), "COHERENT_OBS "+json.dumps(fixtures.observation(row, boot))+"\n"))
        lines.append("ASTERSTOP "+json.dumps(dict(boot=boot, jobs=3, ram_bytes=65536, stores=1000, lifetime_retired=[100000, 100000]))+"\n")
    lines.append("PASS: coherent benchmark boots=2 jobs=6; exact 14-counter/hart windows, independent full outputs, retained RAM\n")
    return "".join(lines)


class Phase6Regressions(unittest.TestCase):
    def test_fresh_check_only_cannot_pass_as_complete_regressions(self):
        with tempfile.TemporaryDirectory(prefix="aster-fresh-check-") as directory:
            root = Path(directory); original = manifest(root)
            for entry in original["results"][1:]: (root/entry["log"]).unlink()
            original["results"] = original["results"][:1]; original["targets"] = ["check"]
            path = root/"manifest.json"; path.write_text(json.dumps(original))
            with mock.patch.object(audit, "validate_log", return_value=dict(target="check", passing_scenarios=157)), mock.patch.object(audit, "source_at_revision"):
                self.assertEqual(audit.audit(path, check_only=True)["passing_scenarios"], 157)
                with self.assertRaises(ValueError): audit.audit(path)

    def test_manifest_mutations_and_default_git_validation(self):
        with tempfile.TemporaryDirectory(prefix="aster-regression-audit-") as directory:
            root = Path(directory); path = root/"manifest.json"; original = manifest(root)
            def write(m): path.write_text(json.dumps(m))
            def inner(target, raw):
                self.assertEqual(raw, "fixture for "+target+"\n")
                return dict(target=target, passing_scenarios=1)
            # Only the semantic-log boundary is mocked here. Independent tests
            # below exercise real scenario parsing and hash-consistent damage.
            with mock.patch.object(audit, "validate_log", side_effect=inner), mock.patch.object(audit, "source_at_revision") as source:
                write(original); self.assertEqual(audit.audit(path)["passing_scenarios"], 22); source.assert_called_once()
                mutations = []
                for key in original:
                    bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
                for key, value in (("dirty", True), ("dirty", 0), ("status", "running"), ("revision", "HEAD"),
                    ("source_sha256", "0"*64), ("source_files", {}), ("toolchain", {}), ("python", {}),
                    ("host_compiler", {}), ("build_directory", "../build"), ("platform", ""),
                    ("started_utc", "2026-09-14T00:00:00+00:00"), ("finished_utc", "2026-09-13T01:00:00")):
                    bad = copy.deepcopy(original); bad[key] = value; mutations.append(bad)
                bad = copy.deepcopy(original); bad["targets"].reverse(); mutations.append(bad)
                bad = copy.deepcopy(original); bad["results"].reverse(); mutations.append(bad)
                bad = copy.deepcopy(original); bad["results"].pop(); mutations.append(bad)
                for key in original["results"][0]:
                    bad = copy.deepcopy(original); del bad["results"][0][key]; mutations.append(bad)
                for key, value in (("target", "wrong"), ("command", ["touch", "/must-not-execute"]), ("exit_code", 1),
                    ("exit_code", False), ("elapsed_seconds", 0), ("elapsed_seconds", True), ("elapsed_seconds", 10000),
                    ("log", "../escaped.log"), ("log_sha256", "0"*64), ("log_bytes", 1)):
                    bad = copy.deepcopy(original); bad["results"][0][key] = value; mutations.append(bad)
                for i, bad in enumerate(mutations):
                    with self.subTest(mutation=i), self.assertRaises(ValueError): write(bad); audit.audit(path, clean=False)
                write(original); file = root/original["results"][0]["log"]; raw = file.read_bytes(); file.write_bytes(raw+b"changed")
                with self.assertRaises(ValueError): audit.audit(path, clean=False)
                file.write_bytes(raw); extra = root/"extra"; extra.write_text("unlisted")
                with self.assertRaises(ValueError): audit.audit(path, clean=False)
                extra.unlink(); file.unlink(); file.symlink_to(root/original["results"][1]["log"])
                with self.assertRaises(ValueError): audit.audit(path, clean=False)

    def test_all_reference_operations_and_original_negative_control(self):
        lines = []
        for _ in range(8):
            for name, (cases, retired) in audit.REFERENCE.items():
                for hart in (0, 1):
                    for boot in (1, 2):
                        lines.append(f"PASS: pinned RV32UA test={name} hart={hart} boot={boot} cases={cases} body_retired={retired} cycles=200000 stores=2000 retained_ram=65536")
            for hart in (0, 1): lines.append(f"PASS: upstream negative comparison hart={hart}, original test_2 fail path detected; vendor unchanged")
        raw = "\n".join(lines)+"\n"; audit.validate_log("riscv-reference-matrix", raw)
        mutations = [("test=amoadd_w", "test=amoand_w"), ("hart=1", "hart=0"), ("boot=2", "boot=1"),
                     ("cases=4", "cases=3"), ("body_retired=6202", "body_retired=6201"), ("original test_2", "original test_3"),
                     ("retained_ram=65536", "retained_ram=65532")]
        for old, new in mutations:
            with self.subTest(old=old), self.assertRaises(ValueError): audit.validate_log("riscv-reference-matrix", raw.replace(old, new, 1))
        with self.assertRaises(ValueError): audit.validate_log("riscv-reference-matrix", "\n".join(lines[:-1])+"\n")

    def test_litmus_histogram_forbidden_outcomes_progress_and_retry_totals(self):
        raw = litmus_fixture([0, 1, 0xc0ffee]*8, 128); audit.validate_log("coherent-litmus-matrix", raw)
        for old, new in (("mode=0", "mode=1"), ("boot=2", "boot=1"), ("epochs=128", "epochs=127"),
                         ("seed=1 ", "seed=2 "), ("histogram=0,128,0,0", "histogram=1,127,0,0"),
                         ("histogram=128,0,0,0", "histogram=127,0,0,1"), ("sc_failure=0,0", "sc_failure=1,0"),
                         ("contended_sc_failures=10", "contended_sc_failures=11"), ("trials=2048", "trials=2047")):
            with self.subTest(old=old), self.assertRaises(ValueError): audit.validate_log("coherent-litmus-matrix", raw.replace(old, new, 1))
        raw = litmus_fixture([0xffffffff]*4, 2).replace("sc_failure=2,3", "sc_failure=0,0").replace("contended_sc_failures=10", "contended_sc_failures=0")
        audit.validate_log("coherent-litmus-boundaries", raw)  # tiny trials need not contend

    def test_benchmark_plan_and_raw_paired_observers(self):
        for target, count in (("coherent-bench-matrix", 216), ("coherent-bench-boundaries", 40), ("coherent-bench-sizes", 108)):
            plan = audit.bench_plan(target); self.assertEqual(len(plan), count); self.assertEqual(len(set(plan)), count)
        for target, count in (("parallel-matrix", 24), ("parallel-workloads", 10)):
            self.assertEqual(len(audit.bench_plan(target, False)), count)
        raw = v4_fixture(); log = audit.Log(raw); audit.benchmarks(log, "check"); log.finish(1)
        for old, new in (("ASTERBOOT 2", "ASTERBOOT 1"), ("jobs=3", "jobs=2"), ("line_count=16", "line_count=8"),
                         ('"ram_bytes": 65536', '"ram_bytes": 65532'), ('"boot": 1', '"boot": 2'),
                         ("COHERENT_OBS ", "MISSING_OBS "), ("h0_retired=10000", "h0_retired=9999")):
            with self.subTest(old=old), self.assertRaises(ValueError): audit.benchmarks(audit.Log(raw.replace(old, new, 1)), "check")
        for tail in ("FAIL: hidden failure\n", "PASS: invented proof\n", "ASTERBOOT 3\n"):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                log = audit.Log(raw+tail); audit.benchmarks(log, "check"); log.finish(1)

    def test_atomic_runtime_and_cache_cross_product(self):
        lines = []
        for h in (1, 2):
            for latency in (0, 1, 19, -1):
                for boot in (0, 1):
                    lines.append(f"PASS: compiled RV32IMA runtime harts={h} cache=1 latency={latency} boot={boot} jobs=3 cycles=200000 retired=33000,{13000 if h == 2 else 0} A-retired=3605,{2307 if h == 2 else 0} SC={1537 if h == 2 else 769}/2")
        raw = "\n".join(lines)+"\n"; audit.validate_log("coherent-runtime-matrix", raw)
        for old, new in (("cache=1", "cache=0"), ("harts=2", "harts=1"), ("latency=19", "latency=1"),
                         ("boot=1", "boot=0"), ("A-retired=3605", "A-retired=3604"), ("SC=1537", "SC=1536")):
            with self.subTest(old=old), self.assertRaises(ValueError): audit.validate_log("coherent-runtime-matrix", raw.replace(old, new, 1))
        lines = [f"PASS: coherent cache enabled={c} geometry={w}x{n} seed={s} requests=12428 backing=20000/20000 read/write stalled=90000 flushes=140 interventions={100 if c else 0}"
                 for c, w, n in audit.product((0, 1), (1, 4, 8), (1, 4, 16)) for s in (1, 0xc06e6, 0xc0ffee)]
        raw = "\n".join(lines)+"\n"; audit.validate_log("coherent-cache-matrix", raw)
        for old, new in (("geometry=1x1", "geometry=4x1"), ("seed=788198", "seed=1"), ("requests=12428", "requests=12427"),
                         ("flushes=140", "flushes=139"), ("interventions=0", "interventions=1"), ("interventions=100", "interventions=0")):
            with self.subTest(old=old), self.assertRaises(ValueError): audit.validate_log("coherent-cache-matrix", raw.replace(old, new, 1))


if __name__ == "__main__": unittest.main()
