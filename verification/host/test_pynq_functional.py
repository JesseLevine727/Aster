"""Fake-boundary functional board collector; full RAM and metadata mutations."""
import copy
from contextlib import ExitStack, redirect_stdout
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
import coherent_functional as functional
import functional_physical as proof
import run_pynq_functional as collector
from test_coherent_bridge import MMIO
from test_coherent_functional import fixture


class FunctionalMMIO(MMIO):
    def __init__(self, kind, caches, ram, scenario):
        super().__init__(); self.kind = kind; self.payload = functional.UART[kind]; self.cursor = 0; self.scenario = scenario
        self.registers[0x44] = 1 | (caches << 1); self.ram = list(struct.unpack("<16384I", ram))
        if scenario == "wrong-identity": self.registers[0x1c] = 0x50001

    def read(self, address):
        if self.registers[0]:
            if address == 0x0c: return 0 if self.scenario == "timeout" else len(self.payload)-self.cursor
            if address == 8:
                value = self.payload[self.cursor]; self.cursor += 1
                return value | (0 if self.scenario == "bad-pop" else 0x80000000)
            if address in (0x10, 0x14): return len(self.payload)+(1 if self.scenario == "trailing" else 0)
        if address in (0x30, 0x38): return 100000
        if address == 0x54: return 0x10000000
        if address == 0x58: return 0x0107a52f
        if address == 0x50 and self.scenario == "real-fault": return 0x15
        return super().read(address)

    def write(self, address, value):
        super().write(address, value)
        if address == 0 and value:
            self.cursor = 0; self.registers[0x28] = 3 if self.kind == "runtime" else 1


class FunctionalCollector(unittest.TestCase):
    def test_isolated_functional_package(self):
        with tempfile.TemporaryDirectory(prefix="aster-functional-shipping-") as directory:
            root = Path(directory)
            for name in proof.FILES: shutil.copy2(ROOT/"scripts"/name, root/name)
            subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0,sys.argv[1]); import run_pynq_functional; assert 'pynq' not in sys.modules",
                            str(root)], cwd=root, check=True, capture_output=True)

    def test_both_programs_caches_boots_cleanup_and_failures(self):
        cases = [(kind, caches, "complete") for kind in functional.PROGRAMS for caches in (0, 1)]
        cases += [("runtime", 1, scenario) for scenario in ("bad-pop", "timeout", "trailing", "bad-ram", "wrong-identity", "download-failed", "real-fault")]
        for kind, caches, scenario in cases:
            with self.subTest(kind=kind, caches=caches, scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-functional-collector-") as directory, ExitStack() as stack:
                stack.enter_context(redirect_stdout(io.StringIO()))
                root = Path(directory); refdir = root/"reference"; refdir.mkdir(); reference = fixture(refdir, caches)
                (root/"overlay.json").write_text("fixture overlay"); (root/"aster_linux.bit").write_bytes(b"fixture bitstream")
                args = SimpleNamespace(reference=refdir/"functional.json", overlay=root/"overlay.json", output=root/"physical", kind=kind,
                    collector_revision="a"*40, expected_loaded="/fixture/previous.bit", download=True, host_pause=0.0, timeout=1.0)
                ram = bytearray(65536)
                for name, words in functional.expected_words(kind).items():
                    offset = reference["programs"][kind]["symbols"][name]["address"]-0x10000000
                    struct.pack_into("<"+"I"*len(words), ram, offset, *words)
                if scenario == "bad-ram": ram[0x8000] ^= 1
                mmio = FunctionalMMIO(kind, caches, ram, scenario); pl = SimpleNamespace(bitfile_name=args.expected_loaded)
                downloads = []
                class Overlay:
                    def __init__(self, path, download): self.path = path; self.initial = download
                    def download(self):
                        downloads.append(self.path)
                        if scenario == "download-failed": raise RuntimeError("injected PCAP failure")
                        pl.bitfile_name = self.path
                module = SimpleNamespace(PL=pl, Clocks=SimpleNamespace(fclk0_mhz=31.25), Overlay=Overlay, MMIO=lambda *_: mmio,
                    Device=SimpleNamespace(active_device=SimpleNamespace(name="Pynq-Z1")), __version__="fixture")
                stack.enter_context(mock.patch.dict(sys.modules, {"pynq": module}))
                stack.enter_context(mock.patch.object(proof, "preflight", return_value=(reference, dict(caches=bool(caches)))))
                ticks = iter(i*0.001 for i in range(20000))
                stack.enter_context(mock.patch.object(collector.time, "monotonic", side_effect=lambda: next(ticks)))
                stack.enter_context(mock.patch.object(collector.time, "sleep"))
                if scenario == "complete": collector.run(args)
                else:
                    with self.assertRaises((RuntimeError, ValueError, TimeoutError)): collector.run(args)
                path = args.output/"functional-physical.json"; report = json.loads(path.read_text())
                self.assertEqual(report["status"], "complete" if scenario == "complete" else "failed")
                self.assertEqual(len(downloads), 1)
                if scenario in ("wrong-identity", "download-failed"):
                    self.assertEqual(mmio.writes, []); self.assertIsNone(report["final_state"])
                else:
                    self.assertEqual(report["final_state"], proof.STOPPED); self.assertEqual(mmio.writes[-1], (0, 0))
                    self.assertTrue((args.output/"boot1.uart").exists())
                if scenario != "complete": continue
                proof.audit(path, args.reference, args.overlay, clean=False)
                mutants = []
                for key in report:
                    bad = copy.deepcopy(report); del bad[key]; mutants.append(bad)
                for key in report["boots"][0]:
                    bad = copy.deepcopy(report); del bad["boots"][0][key]; mutants.append(bad)
                bad = copy.deepcopy(report); bad["boots"].reverse(); mutants.append(bad)
                for key, value in (("status", 3), ("hart_status", 0), ("fifo_count", 1), ("lifetime_retired", [100000, 0])):
                    bad = copy.deepcopy(report); bad["boots"][0]["before_stop"][key] = value; mutants.append(bad)
                bad = copy.deepcopy(report); bad["collector_files"].pop("coherent_functional.py"); mutants.append(bad)
                for index, bad in enumerate(mutants):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=index), self.assertRaises(ValueError): proof.audit(path, args.reference, args.overlay, clean=False)
                path.write_text(json.dumps(report))
                for key in ("uart", "ram"):
                    target = args.output/f"boot1.{key}"; saved = target.read_bytes(); changed = bytearray(saved)
                    changed[0x8000 if key == "ram" else 0] ^= 1; target.write_bytes(changed)
                    bad = copy.deepcopy(report); bad["boots"][0][key] = collector.artifact(target); path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): proof.audit(path, args.reference, args.overlay, clean=False)
                    target.write_bytes(saved); path.write_text(json.dumps(report))

    def test_invalid_options_and_preflight_do_not_import_pynq(self):
        with tempfile.TemporaryDirectory(prefix="aster-functional-refusal-") as directory, mock.patch.dict(sys.modules, {"pynq": None}):
            root = Path(directory)
            args = SimpleNamespace(output=root/"new", kind="runtime", collector_revision="a"*40, expected_loaded="/fixture.bit", host_pause=0.2, timeout=120,
                                   reference=root/"functional.json", overlay=root/"overlay.json", download=False)
            with mock.patch.object(proof, "preflight", side_effect=ValueError("wrong ELF/signoff")):
                with self.assertRaises(ValueError): collector.run(args)
            self.assertFalse(args.output.exists())
            for key, value in (("kind", "unknown"), ("host_pause", float("nan")), ("timeout", 0), ("expected_loaded", "relative")):
                bad = copy.copy(args); setattr(bad, key, value)
                with self.subTest(option=key), self.assertRaises(ValueError): collector.run(bad)
                self.assertFalse(args.output.exists())


if __name__ == "__main__": unittest.main()
