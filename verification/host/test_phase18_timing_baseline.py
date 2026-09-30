"""The retained Phase 18 PicoRV32 timing baselines are intact and self-consistent."""

from pathlib import Path
import hashlib
import json
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "timing"))
import sky130_summary

RESULTS = ROOT / "docs/results/phase18"
# (folder, SKY130 run folder inside it, JSON summary written for it, FPGA folders)
BASELINES = [
    ("picorv32-baseline", "asic", "asic/picorv32.json", ["fpga"]),
    ("picorv32-baseline-v2", "asic/core", "asic/core/summary.json", ["fpga/core", "fpga/core_bram"]),
]


class Phase18TimingBaselines(unittest.TestCase):
    def test_checksums_cover_every_file(self):
        folders = [path.parent.name for path in RESULTS.glob("*/SHA256SUMS")]
        self.assertTrue({name for name, *_ in BASELINES} <= set(folders))
        for name in folders:
            folder = RESULTS / name
            listed = dict(reversed(line.split(maxsplit=1)) for line in
                          (folder / "SHA256SUMS").read_text().splitlines())
            on_disk = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}
            with self.subTest(folder=name):
                self.assertEqual(set(listed) | {"SHA256SUMS"}, on_disk)
                for path, digest in listed.items():
                    self.assertEqual(hashlib.sha256((folder / path).read_bytes()).hexdigest(), digest, path)

    def test_sky130_summary_recomputes_from_retained_metrics(self):
        for name, run, summary_json, _ in BASELINES:
            with self.subTest(folder=name):
                summary = sky130_summary.summarize(RESULTS / name / run)
                retained = json.loads((RESULTS / name / summary_json).read_text())
                self.assertEqual({k: v for k, v in summary.items() if k != "run"},
                                 {k: v for k, v in retained.items() if k != "run"})

    def test_retained_probe_summaries_recompute(self):
        probes = RESULTS / "pre18.1-flow-probes/asic"
        for run in sorted(probes.iterdir()):
            with self.subTest(run=run.name):
                retained = json.loads((run / "summary.json").read_text())
                summary = sky130_summary.summarize(run)
                self.assertEqual({k: v for k, v in summary.items() if k != "run"},
                                 {k: v for k, v in retained.items() if k != "run"})

    def test_v2_uses_the_corrected_constraints_and_chosen_strategy(self):
        resolved = json.loads((RESULTS / "picorv32-baseline-v2/asic/core/resolved.json").read_text())
        self.assertEqual(resolved["SYNTH_STRATEGY"], "DELAY 1")
        self.assertTrue(resolved["RUN_POST_GRT_RESIZER_TIMING"])
        summary = sky130_summary.summarize(RESULTS / "picorv32-baseline-v2/asic/core")
        self.assertEqual(summary["corners"]["max_ss_100C_1v60"]["setup_r2r_ws_ns"], -1.9701)

    def test_fpga_summaries_agree_with_their_reports(self):
        for name, _, _, fpga_dirs in BASELINES:
            for sub in fpga_dirs:
                folder = RESULTS / name / sub
                with self.subTest(folder=f"{name}/{sub}"):
                    fields = dict(item.split("=") for item in (folder / "summary.txt").read_text().split())
                    report = (folder / "timing_summary.rpt").read_text()
                    wns, _, _, _, whs = re.search(
                        r"WNS\(ns\).*?\n.*?\n\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", report).groups()
                    self.assertEqual((fields["wns_ns"], fields["whs_ns"]), (wns, whs))
                    utilization = (folder / "utilization.rpt").read_text()
                    self.assertRegex(utilization, rf"\| Slice LUTs\s+\| {fields['luts']} \|")


if __name__ == "__main__":
    unittest.main()
