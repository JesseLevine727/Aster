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
      {"final_chosen": ["fpga/aster", "fpga/aster_bram", "fpga/aster_bram_reqreg"],
       "rr": ["fpga/rr_aster", "fpga/rr_aster_bram", "fpga/rr_aster_bram_reqreg"]}.get(run, []))
     for run in ("grt_repair", "wt", "wt_rc", "pd", "ex", "wr", "wr_nobuf1", "wr_chosen", "wr_chosen_u38",
                 "final_chosen", "picorv32_nobuf1", "picorv32_chosen",
                 "onehot", "onehot_u38", "jt", "jt_u38", "jt_u36", "jt_u34",
                 "rr", "rr_u38", "rr_u36", "rr_u34", "keepcopy_u38")
] + [("aster-18.1-delay-cells", f"asic/{run}", f"asic/{run}/summary.json", [])
     for run in ("nodly", "nodly_u38", "nodly_u36", "nodly_u34", "picorv32_nodly",
                 "picorv32_nodly_u38", "picorv32_nodly_u36", "picorv32_nodly_u34",
                 "norebuf_u38", "norebuf_u36", "inv_u38")
] + [("aster-18.1-select-copies", f"asic/{run}", f"asic/{run}/summary.json",
      ["fpga/aster", "fpga/aster_bram", "fpga/aster_bram_reqreg"] if run == "invc" else [])
     for run in ("invc", "invc_u38", "invc_u36", "invc_u34")
] + [("aster-18.2", f"asic/{run}", f"asic/{run}/summary.json",
      {"m_u38": ["fpga/m_aster", "fpga/m_aster_bram", "fpga/m_aster_bram_reqreg"],
       "m4_u40": ["fpga/m4_aster", "fpga/m4_aster_bram", "fpga/m4_aster_bram_reqreg"],
       "m5_u36": ["fpga/m5_aster", "fpga/m5_aster_bram", "fpga/m5_aster_bram_reqreg"],
       "m5_u38": ["fpga/m7_aster", "fpga/m7_aster_bram", "fpga/m7_aster_bram_reqreg"]}.get(run, []))
     for run in ("m_u38", "m2_u34", "m2_u36", "m2_u38", "m2_u40", "m2nb_u38", "m2nb_u40",
                 "m3_u34", "m3_u36", "m3_u38", "m3_u40", "m3ss_u38", "m4_u34", "m4_u36", "m4_u38", "m4_u40",
                 "m4_u42", "m4_u44", "m4_u46", "m5_u34", "m5_u36", "m5_u38", "m5_u40", "m5_u44",
                 "m6_u34", "m6_u36", "m6_u38", "m6_u40", "m6_u44",
                 "c80_u34", "c80_u36", "c80_u38", "c80_u40", "c80_u44", "picorv32_c80")]
# FPGA-only evidence (SKY130 was dropped on 1 October 2026): (folder, FPGA folders).
FPGA_ONLY = [(name, ["fpga/aster", "fpga/aster_bram", "fpga/aster_bram_reqreg"])
             for name in ("aster-18.3", "aster-18.3-time", "aster-18.4")]


def retained_part(recomputed, retained):
    """The recomputed summary restricted to the fields the retained one records
    (sky130_summary.py has since added fields; every retained value must recur)."""
    if isinstance(retained, dict) and isinstance(recomputed, dict):
        return {key: retained_part(recomputed.get(key), value) for key, value in retained.items()}
    return recomputed


class Phase18TimingBaselines(unittest.TestCase):
    def test_checksums_cover_every_file(self):
        folders = [path.parent.name for path in RESULTS.glob("*/SHA256SUMS")]
        self.assertTrue({name for name, *_ in BASELINES + FPGA_ONLY} <= set(folders))
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

    def test_both_cores_use_one_chosen_flow_without_delay_buffers(self):
        # The 18.7 comparison needs PicoRV32 and the Aster core in the same flow;
        # with buf_1 excluded, the resizer otherwise buffers with delay cells.
        configs = [json.loads((ROOT / "asic/sky130" / name).read_text())
                   for name in ("config.core_aster.json", "config.core_picorv32_rc.json")]
        # Everything but the design, its sources, its utilization and the Aster
        # core's named-path report is shared.
        own = {"DESIGN_NAME", "VERILOG_FILES", "STA_EXTRA_CORNER_TCL_FILE"}
        shared = [{key: value for key, value in config.items() if key not in own} for config in configs]
        for config in shared:
            config["pdk::sky130*"] = {k: v for k, v in config["pdk::sky130*"].items() if k != "FP_CORE_UTIL"}
        self.assertEqual(shared[0], shared[1])
        excluded = [set(config["EXTRA_EXCLUDED_CELLS"]) for config in configs]
        for cell in ("sky130_fd_sc_hd__buf_1", "sky130_fd_sc_hd__dlygate4sd*", "sky130_fd_sc_hd__dlymetal6s*"):
            self.assertIn(cell, excluded[0])
        for name in ("aster-18.1-delay-cells/asic/" + run for run in (
                "nodly", "nodly_u38", "nodly_u36", "nodly_u34", "picorv32_nodly",
                "picorv32_nodly_u38", "picorv32_nodly_u36", "picorv32_nodly_u34")):
            resolved = json.loads((RESULTS / name / "resolved.json").read_text())
            self.assertEqual(set(resolved["EXTRA_EXCLUDED_CELLS"]), excluded[0], name)
        for name in ("aster-18.1-select-copies/asic/" + run for run in ("invc", "invc_u38", "invc_u36", "invc_u34")):
            resolved = json.loads((RESULTS / name / "resolved.json").read_text())
            self.assertEqual(set(resolved["EXTRA_EXCLUDED_CELLS"]), excluded[0], name)

    def test_fpga_summaries_agree_with_their_reports(self):
        for name, fpga_dirs in [(name, dirs) for name, _, _, dirs in BASELINES] + FPGA_ONLY:
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
