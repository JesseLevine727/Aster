"""Study coverage, option validation, references and matched instruction kernels."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bench_results
import cache_experiments
import test_asterbench as fixtures


class CacheExperiments(unittest.TestCase):
    def test_plan_covers_all_readme_experiments(self):
        cases = cache_experiments.cases()
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        groups = {group for case in cases for group in case["groups"]}
        self.assertTrue({"cache_vs_uncached", "working_sets", "sequential_vs_random", "cache_sizes"} <= groups)
        working_sets = [case["settings"] for case in cases if "working_sets" in case["groups"]]
        self.assertEqual({item["words"] for item in working_sets}, {16, 32, 64, 128, 256, 512, 1024, 2048})
        self.assertEqual(len(working_sets), 32)
        sizes = [case["settings"] for case in cases if "cache_sizes" in case["groups"]]
        self.assertEqual({item["line_count"] for item in sizes}, {4, 8, 16, 32, 64})
        self.assertEqual(len(sizes), 10)

    def test_bad_options_and_known_references(self):
        valid = dict(cache_experiments.cases()[0]["settings"])
        bench_results.validate_options(SimpleNamespace(**valid))
        for key, value in [("l1", 2), ("sync_memory", 2), ("memory_wait", -1),
                           ("memory_wait", 1025), ("line_words", 3), ("line_count", 0),
                           ("line_count", 2048), ("words", 1), ("words", 3), ("words", 8192),
                           ("repetitions", 0), ("repetitions", 65), ("seed", -1),
                           ("seed", 0x100000000), ("workload", "missing")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                bench_results.validate_options(SimpleNamespace(**(valid | {key: value})))
        with self.assertRaises(ValueError):
            bench_results.validate_options(SimpleNamespace(**(valid | {"sync_memory": 1, "memory_wait": 0})))
        self.assertEqual(bench_results.expected_checksum("memcpy", 64, 4, 0x13570000), 0xc4be3200)
        self.assertEqual(bench_results.expected_checksum("walk_random", 128, 8, 0), 0xfe00)
        self.assertEqual(bench_results.expected_checksum("walk_sequential", 128, 8, 1), 0xfe00)

    def test_missing_experiment_rejected(self):
        with tempfile.TemporaryDirectory(prefix="aster-study-invalid-") as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"schema": 1, "experiments": cache_experiments.cases()[:-1]}))
            with self.assertRaises(ValueError):
                cache_experiments.audit(root)

    def test_pattern_kernels_and_data_addresses_match(self):
        prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
        with tempfile.TemporaryDirectory(prefix="aster-kernel-compare-") as directory:
            for words in (2, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096):
                kernels = []
                for workload in ("walk_sequential", "walk_random"):
                    settings = [f"BUILD_DIR={directory}", f"RISCV_PREFIX={prefix}",
                                f"BENCH_WORKLOAD={workload}", f"BENCH_WORDS={words}", "BENCH_REPETITIONS=8"]
                    # Recursive matrix builds inherit Make's directory logging.
                    # Keep the JSON-only subprocess output machine-readable.
                    config = json.loads(subprocess.check_output(["make", "--no-print-directory", "-s", *settings, "bench-config"], cwd=ROOT))
                    subprocess.run(["make", "-s", *settings, config["elf"]], cwd=ROOT, check=True, capture_output=True)
                    bodies = []
                    for symbol in ("walk", "measure"):
                        assembly = subprocess.check_output([prefix+"objdump", "-d", "--disassemble="+symbol, config["elf"]], text=True)
                        body = re.findall(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s", assembly, re.MULTILINE)
                        self.assertTrue(body, symbol)
                        bodies.append(body)
                    symbols = subprocess.check_output([prefix+"nm", "-n", config["elf"]], text=True)
                    self.assertRegex(symbols, r"(?m)^10000000 [bB] links$")
                    kernels.append(bodies)
                with self.subTest(words=words):
                    self.assertEqual(kernels[0], kernels[1], "pattern experiment must use identical instructions/PCs")

    def test_study_audit_rejects_corruption(self):
        # Synthetic records exercise the auditor, not hardware performance.
        with tempfile.TemporaryDirectory(prefix="aster-audit-fixture-") as directory:
            root = Path(directory)
            plan = cache_experiments.cases()
            for case in plan:
                result = fixtures.ResultProvenance().fixture()
                settings = case["settings"]
                values = {key: str(settings[key]) for key in ("l1", "sync_memory", "memory_wait",
                          "line_words", "line_count", "repetitions")}
                values.update(name=settings["workload"], bytes=str(settings["words"]*4),
                              seed=f'0x{settings["seed"]:08x}',
                              checksum=f'0x{bench_results.expected_checksum(settings["workload"], settings["words"], settings["repetitions"], settings["seed"]):08x}')
                if not settings["l1"]:
                    values.update(cache_accesses="0x0000000000000000", cache_misses="0x0000000000000000")
                for key, value in values.items():
                    result["serial_record"] = fixtures.replace(result["serial_record"], key, value)
                result["record"] = bench_results.parse_record(result["serial_record"])
                (root / (case["id"] + ".json")).write_text(json.dumps(result))
            (root / "manifest.json").write_text(json.dumps({"schema": 1, "experiments": plan, "repeat_of": plan[-1]["id"]}))
            repeat = root / "repeat.json"
            original = json.dumps(result)
            repeat.write_text(original)
            self.assertEqual(len(cache_experiments.audit(root)), len(plan))
            for key, value in (("revision", "a"*40), ("compiler_sha256", "a"*64), ("dirty", False)):
                altered = json.loads(original)
                altered["metadata"][key] = value
                repeat.write_text(json.dumps(altered))
                with self.subTest(metadata=key), self.assertRaises(ValueError):
                    cache_experiments.audit(root)
            repeat.write_text(original)
            target = root / (plan[-1]["id"] + ".json")
            for key, value in (("checksum", "0x00000000"), ("retired", "0x0000000000000401"),
                               ("line_words", "8")):
                altered = json.loads(original)
                altered["serial_record"] = fixtures.replace(altered["serial_record"], key, value)
                altered["record"] = bench_results.parse_record(altered["serial_record"])
                target.write_text(json.dumps(altered))
                with self.subTest(record=key), self.assertRaises(ValueError):
                    cache_experiments.audit(root)


if __name__ == "__main__":
    unittest.main()
