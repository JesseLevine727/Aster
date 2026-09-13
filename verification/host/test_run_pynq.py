"""Exercise the physical collector's pure checks without importing board APIs."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from run_pynq import compare_parallel_reference, read_retirement, validate_output
from asterbench_parallel import COUNTERS


class CollectorChecks(unittest.TestCase):
    def test_exact_runtime_and_complete_parallel_streams(self):
        self.assertIsNone(validate_output(b"MULTICORE RUNTIME PASS\n", "runtime"))
        for payload in (b"", b"MULTICORE RUNTIME PASS", b"MULTICORE RUNTIME PASS\nextra"):
            with self.assertRaises(RuntimeError): validate_output(payload, "runtime")
        reference = json.loads((ROOT / "docs/results/phase5/parallel-4188064/two.json").read_text())
        payload = reference["serial_boots"][0].encode()
        records = validate_output(payload, "parallel")
        self.assertEqual(records, reference["records"][0])
        compare_parallel_reference(records, reference)
        for bad in (payload[:-1], payload + b"extra\n", payload.splitlines(keepends=True)[0]):
            with self.assertRaises(ValueError): validate_output(bad, "parallel")
        for key in records[0].keys() - set(COUNTERS):
            changed = deepcopy(records)
            changed[0][key] = "different" if isinstance(changed[0][key], str) else changed[0][key] + 1
            with self.subTest(field=key), self.assertRaises(ValueError):
                compare_parallel_reference(changed, reference)
        # Real serial backpressure changes timing; comparison intentionally
        # checks work/configuration/results, not equality to event-UART clocks.
        changed = deepcopy(records)
        changed[0]["cycles"] += 1
        compare_parallel_reference(changed, reference)
        with self.assertRaises(ValueError): compare_parallel_reference(records[:-1], reference)

    def test_lifetime_counter_rollover(self):
        class MMIO:
            def __init__(self, samples): self.samples = iter(samples)
            def read(self, offset):
                expected, value = next(self.samples)
                self_test.assertEqual(offset, expected)
                return value
        self_test = self
        for hart in (0, 1):
            base = 0x30 + hart*8
            mmio = MMIO([(base+4, 0), (base, 0xffffffff), (base+4, 1),
                         (base+4, 1), (base, 17), (base+4, 1)])
            self.assertEqual(read_retirement(mmio, hart), (1 << 32) + 17)
            samples = [(base+4, 0), (base, 0), (base+4, 1)] * 10
            with self.assertRaises(RuntimeError): read_retirement(MMIO(samples), hart)

    def test_bad_cli_preflight_never_imports_board_api(self):
        with tempfile.TemporaryDirectory(prefix="aster-collector-") as directory:
            command = [sys.executable, str(ROOT / "scripts/run_pynq.py"), "--bitstream", "missing.bit",
                       "--firmware", "missing.hex", "--revision", "test", "--output", directory + "/result.json"]
            for options in (("--kind", "parallel"), ("--kind", "runtime", "--timeout", "nan"),
                            ("--kind", "runtime", "--host-pause", "inf"),
                            ("--kind", "runtime", "--host-pause", "30")):
                result = subprocess.run(command + list(options), text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
