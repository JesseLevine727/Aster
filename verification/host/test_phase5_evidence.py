"""Reject corrupted physical Phase 5 reports; never substitute simulator bytes."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_phase5 import NAMES, validate_board_report
from parallel_results import load


class Phase5Evidence(unittest.TestCase):
    def test_retained_physical_reports_and_mutations(self):
        directory = ROOT / "docs/results/phase5/closeout-71e2570"
        manifest = json.loads((directory / "manifest.json").read_text())
        for name in ["runtime"] + NAMES:
            report = json.loads((directory / f"{name}.json").read_text())
            reference = load(directory / f"reference_{name}.json") if name != "runtime" else None
            validate_board_report(report, name, manifest, reference)
            mutations = []
            for key in ("schema", "board", "hart_count", "bridge_version", "source_revision", "bitstream_sha256",
                        "hwh_sha256", "firmware_sha256", "clock_mhz", "baud", "transport", "external_pmod_loopback",
                        "host_pause_seconds", "handoff_preflight", "pynq_version", "kernel"):
                changed = deepcopy(report)
                changed.pop(key)
                mutations.append(changed)
            for key, value in (("boot", True), ("status", "FAIL"), ("bridge_status", 3), ("hart_status", 3),
                               ("tx_bytes", True), ("rx_bytes", 0), ("uart_output", "partial"),
                               ("counter_record", {}), ("lifetime_retired", [1, False]),
                               ("lifetime_retired", [0, 0]), ("elapsed_seconds", float("nan"))):
                changed = deepcopy(report)
                changed["boots"][0][key] = value
                mutations.append(changed)
            changed = deepcopy(report)
            changed["boots"].pop()
            mutations.append(changed)
            if reference:
                for key, value in (("host_build_provenance", {}), ("host_reference_sha256", "0"*64)):
                    changed = deepcopy(report)
                    changed[key] = value
                    mutations.append(changed)
                changed = deepcopy(report)
                changed["boots"][0]["lifetime_retired"][1] = 0 if name.endswith("2") else 1
                mutations.append(changed)
                changed = deepcopy(report)
                changed["boots"][0]["counter_record"][0]["h0_cycles"] = True
                mutations.append(changed)
                bad_reference = deepcopy(reference)
                bad_reference["metadata"]["dirty"] = True
                with self.assertRaises(ValueError): validate_board_report(report, name, manifest, bad_reference)
            for changed in mutations:
                with self.subTest(name=name, report=changed.get("kind")), self.assertRaises((ValueError, RuntimeError)):
                    validate_board_report(changed, name, manifest, reference)


if __name__ == "__main__":
    unittest.main()
