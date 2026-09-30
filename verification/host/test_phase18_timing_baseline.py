"""The retained Phase 18 PicoRV32 timing baseline is intact and self-consistent."""

from pathlib import Path
import hashlib
import json
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "timing"))
import sky130_summary

BASELINE = ROOT / "docs/results/phase18/picorv32-baseline"


class Phase18TimingBaseline(unittest.TestCase):
    def test_checksums_cover_every_file(self):
        listed = dict(reversed(line.split(maxsplit=1)) for line in
                      (BASELINE / "SHA256SUMS").read_text().splitlines())
        on_disk = {str(p.relative_to(BASELINE)) for p in BASELINE.rglob("*") if p.is_file()}
        self.assertEqual(set(listed) | {"SHA256SUMS"}, on_disk)
        for path, digest in listed.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((BASELINE / path).read_bytes()).hexdigest(), digest)

    def test_sky130_summary_recomputes_from_retained_metrics(self):
        summary = sky130_summary.summarize(BASELINE / "asic")
        retained = json.loads((BASELINE / "asic/picorv32.json").read_text())
        self.assertEqual({k: v for k, v in summary.items() if k != "run"},
                         {k: v for k, v in retained.items() if k != "run"})
        self.assertEqual(summary["corners"]["max_ss_100C_1v60"]["setup_r2r_ws_ns"], -4.3254)

    def test_fpga_summary_agrees_with_its_reports(self):
        fields = dict(item.split("=") for item in (BASELINE / "fpga/summary.txt").read_text().split())
        report = (BASELINE / "fpga/timing_summary.rpt").read_text()
        wns, _, _, _, whs = re.search(r"WNS\(ns\).*?\n.*?\n\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)",
                                      report).groups()
        self.assertEqual((fields["wns_ns"], fields["whs_ns"]), (wns, whs))
        utilization = (BASELINE / "fpga/utilization.rpt").read_text()
        self.assertIn(f"| Slice LUTs                 | {fields['luts']} |", utilization)


if __name__ == "__main__":
    unittest.main()
