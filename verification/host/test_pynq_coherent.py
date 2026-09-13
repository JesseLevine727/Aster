"""Physical collector safety with a fake MMIO/PCAP boundary, never real PYNQ."""
import copy
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import coherent_physical as physical
import run_pynq_coherent as runner
from test_coherent_bench import fixture, emit
from test_coherent_bridge import MMIO


class PhysicalMMIO(MMIO):
    def __init__(self, row, ram, scenario="complete"):
        super().__init__(); self.payload = emit(row).encode(); self.cursor = 0; self.scenario = scenario
        self.ram = list(struct.unpack("<16384I", ram))

    def read(self, address):
        if self.registers[0]:
            if address == 0x0c:
                return 0 if self.scenario == "timeout" else min(512, len(self.payload)-self.cursor)
            if address == 8:
                word = self.payload[self.cursor]; self.cursor += 1
                return word | (0 if self.scenario == "bad-pop" else 0x80000000)
            if address in (0x10, 0x14): return len(self.payload)+(1 if self.scenario == "trailing" else 0)
        if address in (0x30, 0x38): return 100000
        return super().read(address)

    def write(self, address, value):
        if address == 0 and value: self.cursor = 0
        return super().write(address, value)


def inputs(root):
    row = fixture(); row.update(sync_memory=1, memory_wait=1)
    reference = dict(configuration=dict(harts=2, workers=2, l1=1, sync_memory=1, memory_wait=1,
                        line_words=4, line_count=16, uart_seed=0, boots=2, jobs=1),
                     records=[[row], [row]], artifacts={"firmware": {"file": "firmware.hex"}},
                     metadata=dict(dirty=False, source_files={"Makefile": "b"*64}),
                     symbols=dict(aster_coherent_kernel=dict(address=256, size=64),
                                  aster_coherent_results=dict(address=0x10008000, size=32),
                                  aster_coherent_output=dict(address=0x10000000, size=256)))
    hardware = dict(caches=True, dirty=False, source_files={"Makefile": "b"*64})
    (root/"reference.json").write_text("fixture reference")
    (root/"overlay.json").write_text("fixture overlay")
    (root/"aster_linux.bit").write_bytes(b"fixture bitstream")
    data = b"00000000\n"*16384; (root/"firmware.hex").write_bytes(data)
    reference["metadata"]["firmware_sha256"] = hashlib.sha256(data).hexdigest()
    ram = bytearray(65536)
    struct.pack_into("<8I", ram, 0x8000, row["job"], row["seed"], row["result0"], row["result1"], row["checksum"], 0, 32, 32)
    args = SimpleNamespace(output=root/"physical", reference=root/"reference.json", overlay=root/"overlay.json",
                           collector_revision="a"*40, expected_loaded="/fixture/previous.bit", download=True, host_pause=0.0, timeout=1.0)
    return args, reference, hardware, row, bytes(ram)


class PhysicalCollector(unittest.TestCase):
    def test_isolated_shipping_manifest_contains_transitive_imports(self):
        with tempfile.TemporaryDirectory(prefix="aster-pynq6-package-") as directory:
            root = Path(directory)
            for name in physical.COLLECTOR_FILES: shutil.copy2(ROOT/"scripts"/name, root/name)
            # No repository/PYTHONPATH fallback; importing the shipped collector
            # must work without importing PYNQ or touching hardware.
            code = "import sys; sys.path.insert(0,sys.argv[1]); import run_pynq_coherent; assert 'pynq' not in sys.modules"
            subprocess.run([sys.executable, "-I", "-c", code, str(root)], cwd=root, check=True, capture_output=True)

    def test_capture_warm_boot_stop_and_failure_preservation(self):
        for scenario in ("complete", "bad-pop", "timeout", "trailing", "counter-mismatch", "bad-ram", "download-failed", "wrong-identity"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-pynq6-host-") as directory, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                root = Path(directory); args, reference, hardware, row, ram = inputs(root)
                actual = copy.deepcopy(row)
                if scenario == "counter-mismatch": actual["h0_cycles"] += 1; actual["h1_cycles"] += 1
                if scenario == "bad-ram": ram = ram[:0x8000]+bytes(32)+ram[0x8020:]
                mmio = PhysicalMMIO(actual, ram, scenario)
                if scenario == "wrong-identity": mmio.registers[0x1c] = 0x50001
                pl = SimpleNamespace(bitfile_name=args.expected_loaded); clocks = SimpleNamespace(fclk0_mhz=31.25)
                downloaded = []
                class Overlay:
                    def __init__(self, path, download): self.path = path; self.request = download
                    def download(self):
                        downloaded.append(self.path)
                        if scenario == "download-failed": raise RuntimeError("injected download failure")
                        pl.bitfile_name = self.path
                module = SimpleNamespace(PL=pl, Clocks=clocks, Overlay=Overlay, MMIO=lambda *_: mmio,
                    Device=SimpleNamespace(active_device=SimpleNamespace(name="Pynq-Z1")), __version__="fixture")
                stack.enter_context(mock.patch.dict(sys.modules, {"pynq": module}))
                stack.enter_context(mock.patch.object(physical, "preflight", return_value=(reference, hardware)))
                ticks = iter(i*0.001 for i in range(20000))
                stack.enter_context(mock.patch.object(runner.time, "monotonic", side_effect=lambda: next(ticks)))
                stack.enter_context(mock.patch.object(runner.time, "sleep"))
                if scenario == "complete": runner.run(args)
                else:
                    with self.assertRaises((RuntimeError, ValueError, TimeoutError)): runner.run(args)
                report = json.loads((args.output/"physical.json").read_text())
                self.assertEqual(report["status"], "complete" if scenario == "complete" else "failed")
                self.assertEqual(len(downloaded), 1)
                if scenario in ("wrong-identity", "download-failed"):
                    self.assertEqual(mmio.writes, []); self.assertIsNone(report["final_state"])
                else:
                    self.assertEqual(report["final_state"], dict(control=0, status=0, hart_status=0, stop_status=1))
                    self.assertEqual(mmio.writes[-1], (0, 0)); self.assertTrue((args.output/"boot1.uart").exists())
                if scenario == "complete":
                    self.assertEqual(len(report["boots"]), 2)
                    physical.audit(args.output/"physical.json", args.reference, args.overlay, clean=False)
                    # The final audit must independently reject even a rehashed
                    # changed serial stream and all envelope/observation omissions.
                    original = copy.deepcopy(report); path = args.output/"physical.json"
                    mutants = []
                    for key in report:
                        bad = copy.deepcopy(report); del bad[key]; mutants.append(bad)
                    for key in report["boots"][0]:
                        bad = copy.deepcopy(report); del bad["boots"][0][key]; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["boots"].reverse(); mutants.append(bad)
                    bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["lifetime_retired"][1] = 0; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["boots"][0]["after_stop"]["stop_status"] = 6; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["boots"][0]["reference_comparison"]["exact_counter_match"] = False; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["collector_files"].pop("coherent_bridge.py"); mutants.append(bad)
                    for index, bad in enumerate(mutants):
                        path.write_text(json.dumps(bad))
                        with self.subTest(report_mutation=index), self.assertRaises(ValueError):
                            physical.audit(path, args.reference, args.overlay, clean=False)
                    path.write_text(json.dumps(original))
                    for extension in ("uart", "ram"):
                        target = args.output/f"boot1.{extension}"; saved = target.read_bytes()
                        changed = bytearray(saved)
                        if extension == "ram": changed[0x8004] ^= 1
                        else: changed = bytearray(saved.replace(b"h0_cycles=1000000", b"h0_cycles=1000001"))
                        self.assertNotEqual(bytes(changed), saved)
                        target.write_bytes(changed)
                        bad = copy.deepcopy(original); bad["boots"][0][extension] = runner.artifact(target)
                        path.write_text(json.dumps(bad))
                        with self.assertRaises(ValueError): physical.audit(path, args.reference, args.overlay, clean=False)
                        target.write_bytes(saved); path.write_text(json.dumps(original))
                    with mock.patch.object(physical.subprocess, "check_output", return_value=b"changed collector"):
                        with self.assertRaises(ValueError): physical.audit(path, args.reference, args.overlay)

    def test_invalid_inputs_are_nonmutating_before_pynq_import(self):
        for key, value in (("host_pause", float("nan")), ("timeout", 0), ("collector_revision", "dirty"), ("expected_loaded", "relative")):
            with tempfile.TemporaryDirectory(prefix="aster-pynq6-preflight-") as directory, mock.patch.dict(sys.modules, {"pynq": None}):
                args, _, _, _, _ = inputs(Path(directory)); setattr(args, key, value)
                with self.subTest(key=key), self.assertRaises(ValueError): runner.run(args)
                self.assertFalse(args.output.exists())
        with tempfile.TemporaryDirectory(prefix="aster-pynq6-preflight-") as directory, mock.patch.dict(sys.modules, {"pynq": None}):
            args, _, _, _, _ = inputs(Path(directory))
            with mock.patch.object(physical, "preflight", side_effect=ValueError("wrong overlay/ELF")):
                with self.assertRaises(ValueError): runner.run(args)
            self.assertFalse(args.output.exists())
            args.output.mkdir(); marker = args.output/"keep"; marker.write_text("user data")
            with self.assertRaises(ValueError): runner.run(args)
            self.assertEqual(marker.read_text(), "user data")

    def test_loaded_overlay_guard_and_firmware_recheck_before_writes(self):
        for scenario in ("wrong-loaded", "missing-download", "changed-firmware"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-pynq6-loaded-") as directory, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                args, reference, hardware, _, _ = inputs(Path(directory))
                stack.enter_context(mock.patch.object(physical, "preflight", return_value=(reference, hardware)))
                pl = SimpleNamespace(bitfile_name="/unrelated/project.bit" if scenario == "wrong-loaded" else args.expected_loaded)
                forbidden = mock.Mock(side_effect=AssertionError("no PCAP/MMIO before loaded/input guard"))
                module = SimpleNamespace(PL=pl, Clocks=SimpleNamespace(fclk0_mhz=31.25), Overlay=forbidden, MMIO=forbidden)
                stack.enter_context(mock.patch.dict(sys.modules, {"pynq": module}))
                if scenario == "missing-download": args.download = False
                if scenario == "changed-firmware": (Path(directory)/"firmware.hex").write_text("changed")
                with self.assertRaises(ValueError): runner.run(args)
                forbidden.assert_not_called(); self.assertFalse(args.output.exists())

    def test_reference_hardware_compatibility(self):
        with tempfile.TemporaryDirectory(prefix="aster-pynq6-sources-") as directory:
            _, reference, hardware, _, _ = inputs(Path(directory))
            physical.compatible(reference, hardware)
            for key, value in (("harts", 1), ("l1", 0), ("sync_memory", 0), ("memory_wait", 7), ("line_words", 8), ("uart_seed", 1)):
                bad = copy.deepcopy(reference); bad["configuration"][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError): physical.compatible(bad, hardware)
            for key in ("Makefile", "rtl/soc/aster_coherent_soc.sv", "fpga/pynq_z1/build_linux.tcl", "vendor/picorv32/picorv32.v"):
                bad = copy.deepcopy(reference); bad["metadata"]["source_files"][key] = "c"*64
                with self.subTest(source=key), self.assertRaises(ValueError): physical.compatible(bad, hardware)
            bad = copy.deepcopy(reference); bad["metadata"]["source_files"]["scripts/new_host_tool.py"] = "c"*64
            physical.compatible(bad, hardware)  # Host tools can advance independently.


if __name__ == "__main__": unittest.main()
