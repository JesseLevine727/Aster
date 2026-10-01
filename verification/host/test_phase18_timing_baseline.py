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
    ("aster-18.1", "asic/aster", "asic/aster/summary.json", ["fpga/aster", "fpga/aster_bram", "fpga/aster_bram_reqreg"]),
    ("aster-18.1", "asic/aster_pnr_margin", "asic/aster_pnr_margin/summary.json", []),
] + [("aster-18.1-timing-work", f"asic/{run}", f"asic/{run}/summary.json",
      ["fpga/aster", "fpga/aster_bram", "fpga/aster_bram_reqreg"] if run == "final_chosen" else [])
     for run in ("grt_repair", "wt", "wt_rc", "pd", "ex", "wr", "wr_nobuf1", "wr_chosen", "wr_chosen_u38",
                 "final_chosen", "picorv32_nobuf1", "picorv32_chosen",
                 "onehot", "onehot_u38", "jt", "jt_u38", "jt_u36", "jt_u34")]


def retained_part(recomputed, retained):
    """The recomputed summary restricted to the fields the retained one records
    (sky130_summary.py has since added fields; every retained value must recur)."""
    if isinstance(retained, dict) and isinstance(recomputed, dict):
        return {key: retained_part(recomputed.get(key), value) for key, value in retained.items()}
    return recomputed


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
            with self.subTest(folder=f"{name}/{run}"):
                summary = sky130_summary.summarize(RESULTS / name / run)
                retained = json.loads((RESULTS / name / summary_json).read_text())
                retained.pop("run")
                self.assertEqual(retained_part(summary, retained), retained)

    def test_retained_probe_summaries_recompute(self):
        probes = RESULTS / "pre18.1-flow-probes/asic"
        for run in sorted(probes.iterdir()):
            with self.subTest(run=run.name):
                retained = json.loads((run / "summary.json").read_text())
                retained.pop("run")
                summary = sky130_summary.summarize(run)
                self.assertEqual(retained_part(summary, retained), retained)

    def test_v2_uses_the_corrected_constraints_and_chosen_strategy(self):
        resolved = json.loads((RESULTS / "picorv32-baseline-v2/asic/core/resolved.json").read_text())
        self.assertEqual(resolved["SYNTH_STRATEGY"], "DELAY 1")
        self.assertTrue(resolved["RUN_POST_GRT_RESIZER_TIMING"])
        summary = sky130_summary.summarize(RESULTS / "picorv32-baseline-v2/asic/core")
        self.assertEqual(summary["corners"]["max_ss_100C_1v60"]["setup_r2r_ws_ns"], -1.9701)

    def test_aster_18_1_names_the_paths_cpu_md_4_requires(self):
        summary = json.loads((RESULTS / "aster-18.1/asic/aster/summary.json").read_text())
        slow = summary["corners"]["max_ss_100C_1v60"]
        self.assertEqual(slow["setup_r2r_ws_ns"], -4.8744)
        self.assertEqual(set(slow["named_paths_slack_ns"]), {"d_rsp_valid_to_d_req_valid", "d_rsp_error_to_d_req_valid"})
        for top in ("aster_bram", "aster_bram_reqreg"):
            named = (RESULTS / "aster-18.1/fpga" / top / "named_paths.rpt").read_text()
            for label in ("d_rsp_valid_to_request", "d_rsp_error_kill", "d_rsp_valid_worst", "d_rsp_error_worst"):
                self.assertIn(f"label={label}", named)
        # In the §5 form the request is sampled by the block RAMs' write enables.
        self.assertIn("ram_reg", (RESULTS / "aster-18.1/fpga/aster_bram/named_paths.rpt").read_text())

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
