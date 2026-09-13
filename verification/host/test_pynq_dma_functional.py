"""Mocked physical boundary tests; fixtures never become acceptance evidence."""
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
sys.path.insert(0,str(ROOT/"scripts"))
import dma_functional as f
import dma_functional_physical as proof
import dma_physical as physical
import run_pynq_dma_functional as runner
from test_coherent_bridge import MMIO
from test_dma_functional import fixture
from test_dma_overlay import evidence as dma_overlay
from test_coherent_overlay import evidence as old_overlay


class FunctionalMMIO(MMIO):
    def __init__(self,kind,caches,ram,scenario):
        super().__init__(); self.kind = kind; self.scenario = scenario; self.cursor = 0; self.payload = f.UART[kind]
        self.counts = list(struct.unpack_from("<14Q",ram,0x8040)); self.ram = list(struct.unpack("<16384I",ram))
        self.registers.update({0x1c:0x70001,0x44:5+2*caches,0x80:1,0x9c:5})
        if scenario == "wrong-identity": self.registers[0x1c] = 0x60001
        if scenario == "bad-ram": self.ram[0x8000//4] ^= 1

    def read(self,address):
        if self.registers[0]:
            if address == 0x0c: return 0 if self.scenario == "timeout" else len(self.payload)-self.cursor
            if address == 8:
                value = self.payload[self.cursor]; self.cursor += 1
                return value | (0 if self.scenario == "bad-pop" else 0x80000000)
            if address in (0x10,0x14): return len(self.payload)+(self.scenario == "trailing")
            values = {0x84:2,0x88:1024 if self.kind == "runtime" else 8,0x8c:0,0x90:54,0x94:0,0x98:0}
            for i,value in enumerate(self.counts): values.update({0xa0+8*i:value&0xffffffff,0xa4+8*i:value>>32})
            if address in values: return values[address]+(self.scenario == "bad-dma" and address == 0xa0)
        if address in (0x30,0x38): return 2000000
        if address == 0x50 and self.scenario == "fault": return 0x15
        return super().read(address)

    def write(self,address,value):
        super().write(address,value)
        if address == 0 and value: self.cursor = 0; self.registers[0x28] = 3


def inputs(root,kind,caches):
    refdir = root/"reference"; refdir.mkdir(); reference = fixture(refdir,caches)
    (root/"overlay.json").write_text("synthetic overlay")
    bit = root/"aster_linux.bit"; bit.write_bytes(b"synthetic new bitstream")
    bit.with_suffix(".hwh").write_bytes(dma_overlay(bool(caches))["aster_linux.hwh"])
    previous = root/"previous.bit"; previous.write_bytes(b"synthetic old bitstream")
    previous.with_suffix(".hwh").write_bytes(old_overlay(bool(caches))["aster_linux.hwh"])
    hardware = dict(caches=bool(caches),dma=True,dirty=False,source_files=reference["programs"][kind]["metadata"]["source_files"],
                    files={"aster_linux.bit":dict(sha256=runner.sha(bit))})
    args = SimpleNamespace(reference=refdir/"functional.json",overlay=root/"overlay.json",output=root/"physical",kind=kind,
        collector_revision="a"*40,expected_loaded=str(previous),expected_loaded_sha256=runner.sha(previous),download=True,host_pause=0.0,timeout=1.0)
    return args,reference,hardware


def board(args,reference,scenario):
    caches = reference["configuration"]["l1"]; old = MMIO(); old.registers[0x44] = 1+2*caches
    new = FunctionalMMIO(args.kind,caches,(args.reference.parent/f"{args.kind}.boot1.ram").read_bytes(),scenario)
    pl = SimpleNamespace(bitfile_name=args.expected_loaded,ip_dict={"aster":dict(phys_addr=0x40000000,addr_range=0x40000)})
    downloads = []
    class Overlay:
        def __init__(self,path,download):
            if download: raise AssertionError("implicit PCAP download")
            self.path = path
        def download(self):
            downloads.append(self.path)
            if scenario == "download-failed": raise RuntimeError("injected PCAP failure")
            pl.bitfile_name = self.path
    module = SimpleNamespace(PL=pl,Clocks=SimpleNamespace(fclk0_mhz=31.25),Overlay=Overlay,
        MMIO=lambda *_:old if pl.bitfile_name == args.expected_loaded else new,
        Device=SimpleNamespace(active_device=SimpleNamespace(name="Pynq-Z1")),__version__="synthetic")
    return module,old,new,downloads


class PhysicalDmaFunctional(unittest.TestCase):
    def test_both_programs_cache_modes_boots_and_failure_cleanup(self):
        cases = [(k,c,"complete") for k in f.PROGRAMS for c in (0,1)]+[("runtime",1,s) for s in (
            "bad-pop","timeout","trailing","bad-dma","bad-ram","fault","download-failed","wrong-identity")]
        for kind,caches,scenario in cases:
            with self.subTest(kind=kind,cache=caches,scenario=scenario), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory); args,reference,hardware = inputs(root,kind,caches)
                module,old,new,downloads = board(args,reference,scenario)
                stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module}))
                stack.enter_context(mock.patch.object(proof,"preflight",return_value=(reference,hardware)))
                stack.enter_context(redirect_stdout(io.StringIO())); stack.enter_context(mock.patch.object(runner.time,"sleep"))
                ticks = iter(i*0.001 for i in range(30000)); stack.enter_context(mock.patch.object(runner.time,"monotonic",side_effect=lambda:next(ticks)))
                if scenario == "complete": runner.run(args)
                else:
                    with self.assertRaises((ValueError,RuntimeError,TimeoutError)): runner.run(args)
                path = args.output/"functional-physical.json"; report = json.loads(path.read_text())
                self.assertEqual(old.writes,[]); self.assertEqual(len(downloads),1)
                self.assertEqual(report["status"],"complete" if scenario == "complete" else "failed")
                if scenario in ("download-failed","wrong-identity"):
                    self.assertEqual(new.writes,[]); self.assertIsNone(report["final_state"])
                else:
                    self.assertEqual(report["final_state"],physical.stopped_state()); self.assertEqual(new.writes[-1],(0,0))
                    self.assertTrue((args.output/"boot1.uart").exists())
                    # Host writes only lifecycle and stopped boot ROM, no RAM or DMA programming.
                    self.assertTrue(all(a == 0 or 0x10000 <= a < 0x20000 for a,_ in new.writes))
                if scenario != "complete": continue
                proof.audit(path,args.reference,args.overlay,clean=False); self.assertEqual(len(report["boots"]),2)
                mutants = []
                for key in report:
                    bad = copy.deepcopy(report); del bad[key]; mutants.append(bad)
                for key in report["boots"][0]:
                    bad = copy.deepcopy(report); del bad["boots"][0][key]; mutants.append(bad)
                for key,value in (("hart_status",1),("lifetime_retired",[2000000,0]),("fifo_count",1),("tx_bytes",0)):
                    bad = copy.deepcopy(report); bad["boots"][0]["before_stop"][key] = value; mutants.append(bad)
                for i in range(14):
                    bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["dma"]["counters"][i] += 1; mutants.append(bad)
                bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["dma"]["job_cycles"] = 0; mutants.append(bad)
                bad = copy.deepcopy(report); bad["final_state"]["dma"]["bytes_done"] = 1; mutants.append(bad)
                bad = copy.deepcopy(report); bad["previous_state"]["control"] = 1; mutants.append(bad)
                bad = copy.deepcopy(report); bad["boots"].reverse(); mutants.append(bad)
                for i,bad in enumerate(mutants):
                    path.write_text(json.dumps(bad))
                    with self.subTest(mutation=i), self.assertRaises(ValueError): proof.audit(path,args.reference,args.overlay,clean=False)
                path.write_text(json.dumps(report))
                for ext in ("uart","ram"):
                    target = args.output/f"boot1.{ext}"; original = target.read_bytes(); badbytes = bytearray(original)
                    badbytes[0x8000 if ext == "ram" else 0] ^= 1; target.write_bytes(badbytes)
                    bad = copy.deepcopy(report); bad["boots"][0][ext] = runner.artifact(target); path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): proof.audit(path,args.reference,args.overlay,clean=False)
                    target.write_bytes(original); path.write_text(json.dumps(report))
                with mock.patch.object(proof.subprocess,"check_output",return_value=b"not the committed collector"):
                    with self.assertRaises(ValueError): proof.audit(path,args.reference,args.overlay)

    def test_guard_and_recheck_prevent_unrelated_writes(self):
        for scenario in ("loaded","hash","busy","fifo","second-loaded","second-busy","second-manifest","firmware","missing-download"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                args,reference,hardware = inputs(Path(directory),"runtime",1); module,old,new,downloads = board(args,reference,"complete")
                if scenario == "loaded": module.PL.bitfile_name = "/unrelated.bit"
                if scenario == "hash": args.expected_loaded_sha256 = "f"*64
                if scenario == "busy": old.registers[0] = 1
                if scenario == "fifo": old.registers[0x0c] = 1
                if scenario == "firmware": (args.reference.parent/"dma_runtime.hex").write_text("changed")
                if scenario == "missing-download": args.download = False
                calls = []
                def preflight(*_args,**_kwargs):
                    calls.append(1)
                    if len(calls) == 2:
                        if scenario == "second-loaded": module.PL.bitfile_name = "/unrelated.bit"
                        if scenario == "second-busy": old.registers[0] = 1
                        if scenario == "second-manifest": args.reference.write_text("changed")
                    return reference,hardware
                stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module})); stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(proof,"preflight",side_effect=preflight))
                with self.assertRaises((ValueError,RuntimeError)): runner.run(args)
                self.assertEqual(old.writes,[]); self.assertEqual(new.writes,[]); self.assertEqual(downloads,[])

    def test_reuse_same_idle_image_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            args,reference,hardware = inputs(Path(directory),"publication",0)
            args.download = False; args.expected_loaded = str(args.overlay.parent/"aster_linux.bit")
            args.expected_loaded_sha256 = runner.sha(Path(args.expected_loaded))
            module,old,new,downloads = board(args,reference,"complete"); module.MMIO = lambda *_:new
            stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module})); stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(mock.patch.object(proof,"preflight",return_value=(reference,hardware)))
            stack.enter_context(mock.patch.object(runner.time,"sleep"))
            runner.run(args); self.assertEqual(downloads,[])
            original = (args.output/"functional-physical.json").read_bytes()
            with self.assertRaises(ValueError): runner.run(args)
            self.assertEqual((args.output/"functional-physical.json").read_bytes(),original)

    def test_offline_preflight_and_isolated_source_closure(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules,{"pynq":None}):
            args,reference,hardware = inputs(Path(directory),"runtime",1)
            with mock.patch.object(proof,"preflight",side_effect=ValueError("invalid ELF/HWH")):
                with self.assertRaises(ValueError): runner.run(args)
            self.assertFalse(args.output.exists())
            for key,value in (("kind","wrong"),("host_pause",float("nan")),("timeout",0),("expected_loaded","relative"),
                              ("expected_loaded_sha256","wrong"),("download",1),("collector_revision","dirty")):
                bad = copy.copy(args); setattr(bad,key,value)
                with self.subTest(option=key), self.assertRaises(ValueError): runner.run(bad)
                self.assertFalse(args.output.exists())
            with mock.patch.object(proof.reference_tools,"load",return_value=reference), mock.patch.object(proof.overlay,"audit",return_value=hardware):
                proof.preflight(args.reference,args.overlay)
                hardware["dma"] = False
                with self.assertRaises(ValueError): proof.preflight(args.reference,args.overlay)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in proof.FILES: shutil.copy2(ROOT/"scripts"/name,root/name)
            subprocess.run([sys.executable,"-I","-c",
                "import sys; sys.path.insert(0,sys.argv[1]); import run_pynq_dma_functional; assert 'pynq' not in sys.modules",str(root)],
                cwd=root,check=True,capture_output=True)


if __name__ == "__main__": unittest.main()
