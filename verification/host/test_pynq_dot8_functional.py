"""Mock-only physical functional flows; synthetic data is never board evidence."""
from copy import deepcopy
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
sys.path.insert(0,str(ROOT/"scripts"))
import dot8_functional as f
import dot8_functional_physical as proof
import dot8_physical as physical
import run_pynq_dot8_functional as runner
from test_coherent_bridge import MMIO
from test_coherent_overlay import evidence as old_overlay
from test_dot8_overlay import evidence as new_overlay
from test_dot8_functional import fixture


class FunctionalMMIO(MMIO):
    def __init__(self,reference,ram,scenario):
        super().__init__(); self.scenario = scenario; self.cursor = 0; self.payload = b"DOT8 RUNTIME PASS\n"
        self.counts = reference["observations"][0]["counts"]; c = reference["configuration"]["l1"]
        self.ram = list(struct.unpack("<16384I",ram))
        self.registers.update({0x1c:0x80001,0x44:13+2*c,0x80:1,0x9c:5,0x110:1,0x114:6,0x160:0x0b,0x164:0xfe00707f,0x168:4})
        if scenario == "wrong-identity": self.registers[0x114] = 5

    def read(self,address):
        if self.registers[0]:
            if address == 0x0c: return 0 if self.scenario == "timeout" else len(self.payload)-self.cursor
            if address == 8:
                value = self.payload[self.cursor]; self.cursor += 1
                return value | (0 if self.scenario == "bad-pop" else 0x80000000)
            if address in (0x10,0x14): return len(self.payload)+(self.scenario == "trailing")
            if address == 0x28: return 3
            if address in (0x30,0x38): return 2000000
            # DMA ACK clears terminal status but retains the completed-byte
            # prefix and last-job cycle accounting, per the driver contract.
            values = {0x84:0,0x88:512,0x8c:0,0x90:1,0x94:0,0x98:0,0x118:0,0x11c:0}
            for base,counts in ((0xa0,self.counts[28:42]),(0x120,self.counts[42:])):
                for i,value in enumerate(counts): values.update({base+8*i:value&0xffffffff,base+8*i+4:value>>32})
            if address in values:
                return values[address]+(self.scenario == "bad-dma" and address == 0x88)+(self.scenario == "bad-dot8" and address == 0x120)
        return super().read(address)

    def write(self,address,value):
        if address == 0 and value: self.cursor = 0
        super().write(address,value)


def inputs(root,original,data,scenario="complete"):
    reference = deepcopy(original); reference["metadata"]["dirty"] = False
    (root/"dot8_runtime.hex").write_bytes(data["dot8_runtime.hex"])
    (root/"reference.json").write_text("synthetic reference"); (root/"overlay.json").write_text("synthetic overlay")
    (root/"aster_linux.bit").write_bytes(b"synthetic DOT8 bitstream")
    caches = bool(reference["configuration"]["l1"])
    (root/"aster_linux.hwh").write_bytes(new_overlay(caches)["aster_linux.hwh"])
    previous = root/"previous.bit"; previous.write_bytes(b"synthetic prior overlay")
    previous.with_suffix(".hwh").write_bytes(old_overlay(caches)["aster_linux.hwh"])
    hardware = dict(caches=caches,dma=True,dot8=True,dirty=False,source_files=reference["metadata"]["source_files"],
                    files={"aster_linux.bit":dict(sha256=runner.sha(root/"aster_linux.bit"))})
    args = SimpleNamespace(output=root/"physical",reference=root/"reference.json",overlay=root/"overlay.json",collector_revision="a"*40,
        expected_loaded=str(previous),expected_loaded_sha256=runner.sha(previous),download=True,host_pause=0.0,timeout=1.0)
    raw = data["runtime.boot1.ram"]
    if scenario == "bad-ram": raw = bytes([raw[0]^1])+raw[1:]
    if scenario == "counter-mismatch":
        changed = bytearray(raw); struct.pack_into("<Q",changed,0x8080+8,1000001); raw = bytes(changed)
    old = MMIO(); old.registers[0x44] = 1+2*int(caches); new = FunctionalMMIO(reference,raw,scenario)
    pl = SimpleNamespace(bitfile_name=args.expected_loaded,ip_dict={"aster":dict(phys_addr=0x40000000,addr_range=0x40000)})
    downloads = []
    class Overlay:
        def __init__(self,path,download):
            if download: raise AssertionError("implicit PCAP")
            self.path = path
        def download(self):
            downloads.append(self.path)
            if scenario == "download-failed": raise RuntimeError("injected PCAP failure")
            pl.bitfile_name = self.path
    module = SimpleNamespace(PL=pl,Clocks=SimpleNamespace(fclk0_mhz=31.25),Overlay=Overlay,MMIO=lambda *_:old if pl.bitfile_name == args.expected_loaded else new,
        Device=SimpleNamespace(active_device=SimpleNamespace(name="Pynq-Z1")),__version__="synthetic")
    return args,reference,hardware,module,old,new,downloads


@unittest.skipUnless(shutil.which("riscv32-unknown-elf-gcc"),"RISC-V toolchain required")
class PynqDot8Functional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="aster-pynq-dot8-functional-fixtures-"); cls.addClassCleanup(cls.temp.cleanup)
        cls.fixtures = {c:fixture(Path(cls.temp.name)/str(c),c) for c in (0,1)}

    def test_offline_closure_and_incompatible_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in proof.FILES: shutil.copy2(ROOT/"scripts"/name,root/name)
            subprocess.run([sys.executable,"-I","-c","import sys; sys.path.insert(0,sys.argv[1]); import run_pynq_dot8_functional; assert 'pynq' not in sys.modules",str(root)],cwd=root,check=True,capture_output=True)
            args,reference,hardware,*_ = inputs(root,*self.fixtures[1])
            with mock.patch.object(proof.references,"load",return_value=reference),mock.patch.object(proof.overlay,"audit",return_value=hardware):
                proof.preflight(args.reference,args.overlay)
                for key,value in (("dma",False),("dot8",False),("caches",False),("dirty",True)):
                    bad = deepcopy(hardware); bad[key] = value
                    with mock.patch.object(proof.overlay,"audit",return_value=bad),self.assertRaises(ValueError): proof.preflight(args.reference,args.overlay)
                bad = deepcopy(hardware); bad["source_files"]["Makefile"] = "f"*64
                with mock.patch.object(proof.overlay,"audit",return_value=bad),self.assertRaises(ValueError): proof.preflight(args.reference,args.overlay)

    def test_complete_both_cache_modes_and_safe_failure_cleanup(self):
        for c in (0,1):
            for scenario in ("complete","bad-pop","timeout","trailing","bad-ram","bad-dma","bad-dot8","counter-mismatch","wrong-identity","download-failed"):
                with self.subTest(cache=c,scenario=scenario),tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
                    args,ref,hardware,module,old,new,downloads = inputs(Path(directory),*self.fixtures[c],scenario)
                    stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module})); stack.enter_context(mock.patch.object(proof,"preflight",return_value=(ref,hardware)))
                    stack.enter_context(mock.patch.object(runner.time,"sleep")); stack.enter_context(redirect_stdout(io.StringIO()))
                    ticks = iter(i*.001 for i in range(100000)); stack.enter_context(mock.patch.object(runner.time,"monotonic",side_effect=lambda:next(ticks)))
                    if scenario == "complete": report = runner.run(args)
                    else:
                        with self.assertRaises((ValueError,RuntimeError,TimeoutError)): runner.run(args)
                    report = json.loads((args.output/"functional-physical.json").read_text())
                    self.assertEqual(old.writes,[]); self.assertEqual(len(downloads),1)
                    self.assertEqual(report["status"],"complete" if scenario == "complete" else "failed")
                    if scenario in ("wrong-identity","download-failed"):
                        self.assertIsNone(report["final_state"]); self.assertEqual(new.writes,[])
                    else:
                        self.assertEqual(report["final_state"],physical.stopped_state()); self.assertEqual(new.writes[-1],(0,0))
                        self.assertTrue(all(a == 0 or 0x10000 <= a < 0x20000 for a,_ in new.writes))
                    if scenario == "complete":
                        self.assertEqual(len(report["boots"]),2); self.assertTrue(all(b["reference_comparison"]["exact_counter_match"] for b in report["boots"]))
                        proof.audit(args.output/"functional-physical.json",args.reference,args.overlay,clean=False)

    def test_rehashed_physical_manifest_fields_and_every_live_counter(self):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            args,ref,hardware,module,old,new,downloads = inputs(Path(directory),*self.fixtures[1])
            stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module})); stack.enter_context(mock.patch.object(proof,"preflight",return_value=(ref,hardware)))
            stack.enter_context(mock.patch.object(runner.time,"sleep")); stack.enter_context(redirect_stdout(io.StringIO()))
            original = runner.run(args); path = args.output/"functional-physical.json"; mutations = []
            for key in original:
                bad = deepcopy(original); del bad[key]; mutations.append(bad)
            for key in original["boots"][0]:
                bad = deepcopy(original); del bad["boots"][0][key]; mutations.append(bad)
            for bank,n in (("dma",14),("dot8",8)):
                for i in range(n):
                    bad = deepcopy(original); bad["boots"][0]["before_stop"][bank]["counters"][i] += 1; mutations.append(bad)
            for key,value in (("bytes_done",511),("job_cycles",0)):
                bad = deepcopy(original); bad["boots"][0]["before_stop"]["dma"][key] = value; mutations.append(bad)
            for i in range(50):
                bad = deepcopy(original); bad["boots"][0]["reference_comparison"]["counter_deltas"][i] = 1; mutations.append(bad)
            for key in ("control","status","hart_status","stop_status"):
                bad = deepcopy(original); bad["final_state"][key] ^= 1; mutations.append(bad)
            for bad in mutations:
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError): proof.audit(path,args.reference,args.overlay,clean=False)
            ram = args.output/"boot1.ram"; data = ram.read_bytes(); ram.write_bytes(bytes([data[0]^1])+data[1:])
            bad = deepcopy(original); bad["boots"][0]["ram"]["sha256"] = runner.sha(ram); path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): proof.audit(path,args.reference,args.overlay,clean=False)

    def test_second_guard_and_bad_inputs_never_touch_another_project(self):
        for scenario in ("firmware","missing-download","second-loaded","second-busy","second-manifest"):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
                root = Path(directory); args,ref,hardware,module,old,new,downloads = inputs(root,*self.fixtures[1])
                if scenario == "firmware": (root/"dot8_runtime.hex").write_bytes(b"changed")
                if scenario == "missing-download": args.download = False
                calls = []
                def preflight(*_args,**_kwargs):
                    calls.append(1)
                    if len(calls) == 2:
                        if scenario == "second-loaded": module.PL.bitfile_name = "/another/project.bit"
                        if scenario == "second-busy": old.registers[0] = 1
                        if scenario == "second-manifest": args.reference.write_text("changed")
                    return ref,hardware
                stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module})); stack.enter_context(mock.patch.object(proof,"preflight",side_effect=preflight))
                stack.enter_context(redirect_stdout(io.StringIO()))
                with self.assertRaises((ValueError,RuntimeError)): runner.run(args)
                self.assertEqual(downloads,[]); self.assertEqual(old.writes,[]); self.assertEqual(new.writes,[])


if __name__ == "__main__": unittest.main()
