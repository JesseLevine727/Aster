"""Mutate signoff gates even after recomputing the containing artifact hash."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import coherent_overlay as overlay
from test_pynq_handoff import fixture as hwh_fixture


def evidence(caches=True):
    handoff = hwh_fixture()
    parent = handoff.find(".//MODULE[@INSTANCE='aster']/PARAMETERS")
    for name, value in (("HART_COUNT", 2), ("ENABLE_COHERENCE", 1), ("COHERENT_L1", int(caches))):
        ET.SubElement(parent, "PARAMETER", NAME=name, VALUE=str(value))
    header = "Vivado v.2025.1 fixture\naster_linux_wrapper\n"
    checks = ("no_clock", "constant_clock", "pulse_width_clock", "unconstrained_internal_endpoints",
              "no_input_delay", "multiple_clock", "generated_clocks", "loops", "partial_input_delay", "partial_output_delay", "latch_loops")
    timing = header+("".join(f"checking {key} (0)\n" for key in checks)+"checking no_output_delay (5)\n")*2
    timing += "WNS(ns) ... TPWS Total Endpoints\n-----\n9.207 0.000 0 41829 0.035 0.000 0 41829 14.750 0.000 0 15645\n"
    timing += "All user specified timing constraints are met.\nclk_fpga_0 {0.000 16.000} 32.000 31.250\n"
    route = "# of routable nets.......... : 27103 :\n# of fully routed nets...... : 27103 :\n# of nets with routing errors.... : 0 :\n"
    build = f"-tclargs /fixture/source /fixture/build 2 1 {int(caches)}\n"
    build += "source /fixture/source/fpga/pynq_z1/build_linux.tcl -notrace\n"
    build += "****** Vivado v2025.1 (64-bit)\n"*2+overlay.RESET_PASS+"\n"
    build += "ASTER_SIGNOFF min slack=0.035 ns\nASTER_SIGNOFF max slack=9.207 ns\n"
    build += "Bitgen Completed Successfully.\nASTER_LINUX_BUILD complete: /fixture/build/aster_linux.bit\n"
    utilization = header+"\n".join(f"| {label} | {count} | 0 | 0 | {limit} |" for label, count, limit in
        (("Slice LUTs", 15357, 53200), ("Slice Registers", 15167, 106400), ("Block RAM Tile", 32, 140), ("DSPs", 0, 220)))
    data = {"aster_linux.bit": b"fixture only", "aster_linux.hwh": ET.tostring(handoff), "build.log": build.encode(),
            "timing_summary.rpt": timing.encode(), "route_status.rpt": route.encode(),
            "drc.rpt": (header+"Checks found: 0\n").encode(), "methodology.rpt": (header+"Checks found: 0\n").encode(),
            "utilization_routed.rpt": utilization.encode(), "reset_netlist.v": b"module aster_linux_reset_0_proc_sys_reset; endmodule",
            "reset-stage-0.log": b"fixture xvlog", "reset-stage-1.log": b"fixture elaborated glbl",
            "reset-stage-2.log": (overlay.RESET_PASS+"\n").encode()}
    return data


class CoherentOverlay(unittest.TestCase):
    def test_routed_reset_signoff_and_rehashed_failures(self):
        original = evidence(); passed = overlay.signoff(original)
        self.assertEqual(passed["setup_slack_ns"], 9.207); self.assertEqual(passed["hold_slack_ns"], 0.035)
        self.assertEqual(passed["resources"], dict(luts=15357, flip_flops=15167, bram_tiles=32, dsps=0))
        self.assertEqual(passed["asynchronous_output_ports_without_delay"], 5)
        mutations = [("build.log", b"v2025.1", b"v2025.2"), ("build.log", b"slack=0.035", b"slack=0.036"),
                     ("reset-stage-2.log", b"PASS:", b"missing:"), ("reset-stage-1.log", b"glbl", b"missing"),
                     ("reset_netlist.v", b"proc_sys_reset", b"fake_model"), ("drc.rpt", b"found: 0", b"found: 1"),
                     ("methodology.rpt", b"found: 0", b"found: 1"),
                     ("route_status.rpt", b"routed nets...... : 27103", b"routed nets...... : 27102"),
                     ("route_status.rpt", b"errors.... : 0", b"errors.... : 1"),
                     ("timing_summary.rpt", b"unconstrained_internal_endpoints (0)", b"unconstrained_internal_endpoints (1)"),
                     ("timing_summary.rpt", b"no_output_delay (5)", b"no_output_delay (6)"),
                     ("timing_summary.rpt", b"9.207 0.000", b"-1.0 -3.0"),
                     ("timing_summary.rpt", b"0.035 0.000 0", b"0.035 0.000 1"),
                     ("timing_summary.rpt", b"14.750", b"nan"),
                     ("timing_summary.rpt", b"31.250", b"50.000"),
                     ("utilization_routed.rpt", b"| 15357 |", b"| 99999 |")]
        for name, old, new in mutations:
            changed = dict(original); self.assertIn(old, changed[name]); changed[name] = changed[name].replace(old, new, 1)
            with self.subTest(artifact=name, mutation=new), self.assertRaises(ValueError): overlay.signoff(changed)
        for name in overlay.FILES-{"aster_linux.bit", "aster_linux.hwh", "reset_netlist.v"}:
            changed = dict(original); changed[name] += b"\nERROR: fixture failure\n"
            with self.subTest(error_file=name), self.assertRaises(ValueError): overlay.signoff(changed)

    def test_full_offline_package_and_manifest_mutations(self):
        for caches in (False, True):
            with self.subTest(cache=caches), tempfile.TemporaryDirectory(prefix="aster-overlay-audit-") as directory:
                root = Path(directory); data = evidence(caches)
                for name, value in data.items(): (root/name).write_bytes(value)
                sources = {"Makefile": "a"*64}
                original = dict(schema=overlay.SCHEMA, revision="b"*40, dirty=False, source_files=sources,
                    source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                    source_worktree="/fixture/source", build_directory="/fixture/build", harts=2, caches=caches,
                    files={name: dict(sha256=hashlib.sha256(value).hexdigest(), bytes=len(value)) for name, value in data.items()},
                    handoff=overlay.validate_handoff(root/"aster_linux.hwh", 2, expected_coherent=True, expected_cache=caches),
                    signoff=overlay.signoff(data))
                path = root/"overlay.json"
                def save(value): path.write_text(json.dumps(value))
                save(original); overlay.audit(path, clean=False)
                with mock.patch.object(overlay, "source_at_revision") as source:
                    overlay.audit(path); source.assert_called_once()
                mutations = []
                for key in overlay.FIELDS:
                    bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
                for key, value in (("dirty", True), ("harts", True), ("caches", int(caches)), ("source_worktree", "relative"),
                                   ("build_directory", "/different"), ("revision", "wrong")):
                    bad = copy.deepcopy(original); bad[key] = value; mutations.append(bad)
                for name in overlay.FILES:
                    bad = copy.deepcopy(original); del bad["files"][name]; mutations.append(bad)
                    bad = copy.deepcopy(original); bad["files"][name]["sha256"] = "0"*64; mutations.append(bad)
                bad = copy.deepcopy(original); bad["handoff"]["caches"] = not caches; mutations.append(bad)
                bad = copy.deepcopy(original); bad["signoff"]["setup_slack_ns"] = 100; mutations.append(bad)
                for index, bad in enumerate(mutations):
                    with self.subTest(mutation=index), self.assertRaises(ValueError): save(bad); overlay.audit(path, clean=False)
                # Hash-consistent reports still must satisfy their semantic gate.
                changed = data["drc.rpt"].replace(b"found: 0", b"found: 1")
                (root/"drc.rpt").write_bytes(changed)
                bad = copy.deepcopy(original); bad["files"]["drc.rpt"] = dict(sha256=hashlib.sha256(changed).hexdigest(), bytes=len(changed))
                with self.assertRaises(ValueError): save(bad); overlay.audit(path, clean=False)
                (root/"drc.rpt").write_bytes(data["drc.rpt"]); save(original)
                extra = root/"extra"; extra.write_bytes(b"unlisted")
                with self.assertRaises(ValueError): overlay.audit(path, clean=False)
                extra.unlink()  # generated fixture only
                bit = root/"aster_linux.bit"; bit.unlink(); bit.symlink_to(path)
                with self.assertRaises(ValueError): overlay.audit(path, clean=False)


if __name__ == "__main__": unittest.main()
