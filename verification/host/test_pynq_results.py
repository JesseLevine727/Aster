"""Mutation checks for independently auditing actual retained board records."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_pynq_results import validate_board_report


class PhysicalResults(unittest.TestCase):
    def test_captured_reports_and_rejected_mutations(self):
        directory = ROOT / "docs/results/phase2"
        manifest = json.loads((directory / "manifest.json").read_text())
        for name in ("hello", "stress", "memcpy", "walk_sequential", "walk_random"):
            report = json.loads((directory / f"{name}.json").read_text())
            reference = (json.loads((directory / f"reference_{name}.json").read_text())
                         if name not in ("hello", "stress") else None)
            validate_board_report(report, name, manifest, reference)
            mutations = []
            for key in ("source_revision", "firmware_sha256", "bitstream_sha256", "hwh_sha256",
                        "clock_mhz", "baud", "external_pmod_loopback", "transport", "handoff_preflight"):
                changed = deepcopy(report)
                changed.pop(key)
                mutations.append(changed)
            for key, value in (("tx_bytes", 1), ("rx_bytes", True), ("bridge_status", 3),
                               ("status", "FAIL"), ("uart_output", "partial"),
                               ("counter_record", {}), ("boot", 1), ("elapsed_seconds", float("nan"))):
                changed = deepcopy(report)
                changed["boots"][0][key] = value
                mutations.append(changed)
            changed = deepcopy(report)
            changed["boots"].pop()
            mutations.append(changed)
            if reference is not None:
                changed = deepcopy(report)
                changed["boots"][0]["counter_record"]["dma_bytes"] = False
                mutations.append(changed)
            for changed in mutations:
                with self.subTest(name=name, mutation=changed), self.assertRaises((ValueError, RuntimeError)):
                    validate_board_report(changed, name, manifest, reference)
