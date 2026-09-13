"""Mock-only PCAP/MMIO tests; generated fixtures are never board evidence."""
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
sys.path.insert(0,str(ROOT/"scripts"))
import asterbench_dot8 as bench
import dot8_physical as physical
import run_pynq_dot8 as runner
from test_coherent_bridge import MMIO
from test_coherent_overlay import evidence as old_overlay
from test_dot8_overlay import evidence as dot8_overlay
from test_asterbench_dot8 import fixture, line, ram_image


class PhysicalMMIO(MMIO):
    def __init__(self,rows,ram,scenario="complete"):
        super().__init__(); self.rows = rows; self.payload = "".join(map(line,rows)).encode(); self.cursor = 0; self.scenario = scenario
        self.ram = list(struct.unpack("<16384I",ram))
        self.registers.update({0x1c:0x80001,0x44:13+2*rows[0]["l1"],0x80:1,0x9c:5,0x110:1,0x114:6,0x160:0x0b,0x164:0xfe00707f,0x168:4})

    def read(self,address):
        if self.registers[0]:
            if address == 0x0c: return 0 if self.scenario == "timeout" else min(512,len(self.payload)-self.cursor)
            if address == 8:
                value = self.payload[self.cursor]; self.cursor += 1
                return value | (0 if self.scenario == "bad-pop" else 0x80000000)
            if address in (0x10,0x14): return len(self.payload)+(self.scenario == "trailing")
            final = self.rows[-1]
            values = {0x84:0,0x88:0,0x8c:0,0x90:0,0x94:0,0x98:0,0x118:0,0x11c:0}
            for i in range(14): values.update({0xa0+i*8:0,0xa4+i*8:0})
            for i,key in enumerate([f"h{h}_dot8_{event}" for h in range(2) for event in bench.DOT8_EVENTS]):
                value = final[key]; values.update({0x120+8*i:value & bench.U32,0x124+8*i:value >> 32})
            if address in values:
                return values[address]+(self.scenario == "bad-dma" and address == 0x88)+(self.scenario == "bad-dot8" and address == 0x120)
            if address == 0x30: return 1000000
        if address == 0x54: return 0x10000000  # Legal latched address, no fault.
        if address == 0x58: return 0x0107a52f
        return super().read(address)

    def write(self,address,value):
        if address == 0 and value: self.cursor = 0
        super().write(address,value)


def inputs(root,cache=True,jobs=4):
    records = [fixture(job=j,pass_number=p,jobs=jobs) for j in range(1,jobs+1) for p in (1,2)]
    for r in records:
        r["l1"] = int(cache)
        if not cache:
            for h in range(2):
                for event in ("i_access","i_miss","d_access","d_miss","writeback"): r[f"h{h}_{event}"] = 0
    firmware = b"00000000\n"*16384
    reference = dict(configuration=dict(name="dot",k=16,alignment="aligned",harts=2,jobs=jobs,boots=2,l1=int(cache),
        sync_memory=1,memory_wait=1,line_words=4,line_count=16,uart_seed=0,base_seed=0x13570000),
        metadata=dict(dirty=False,source_files={"Makefile":"0"*64},firmware_sha256=hashlib.sha256(firmware).hexdigest()),
        records=[copy.deepcopy(records),copy.deepcopy(records)],artifacts={"firmware":dict(file="synthetic.hex")})
    data = dict(firmware=firmware,ram1=ram_image(records),ram2=ram_image(records))
    reference["metadata"]["dirty"] = False
    (root/"synthetic.hex").write_bytes(firmware)
    (root/"reference.json").write_text("synthetic reference")
    (root/"overlay.json").write_text("synthetic overlay")
    (root/"aster_linux.bit").write_bytes(b"synthetic new bitstream")
    (root/"aster_linux.hwh").write_bytes(dot8_overlay(cache)["aster_linux.hwh"])
    previous = root/"previous.bit"; previous.write_bytes(b"synthetic old bitstream")
    previous.with_suffix(".hwh").write_bytes(old_overlay(cache)["aster_linux.hwh"])
    hardware = dict(caches=cache,dma=True,dot8=True,dirty=False,source_files=dict(reference["metadata"]["source_files"]),
                    files={"aster_linux.bit":dict(sha256=runner.sha(root/"aster_linux.bit"))})
    args = SimpleNamespace(output=root/"physical",reference=root/"reference.json",overlay=root/"overlay.json",
        collector_revision="a"*40,expected_loaded=str(previous),expected_loaded_sha256=runner.sha(previous),
        download=True,host_pause=0.0,timeout=1.0)
    return args,reference,hardware,data


def board(args,rows,ram,scenario="complete"):
    old = MMIO(); old.registers[0x44] = 1+2*rows[0]["l1"]
    new = PhysicalMMIO(rows,ram,scenario)
    if scenario == "wrong-identity": new.registers[0x1c] = 0x60001
    pl = SimpleNamespace(bitfile_name=args.expected_loaded,ip_dict={"aster":dict(phys_addr=0x40000000,addr_range=0x40000)})
    downloads = []
    class Overlay:
        def __init__(self,path,download):
            if download: raise AssertionError("implicit download")
            self.path = path
        def download(self):
            downloads.append(self.path)
            if scenario == "download-failed": raise RuntimeError("injected PCAP failure")
            pl.bitfile_name = self.path
    module = SimpleNamespace(PL=pl,Clocks=SimpleNamespace(fclk0_mhz=31.25),Overlay=Overlay,
        MMIO=lambda *_: old if pl.bitfile_name == args.expected_loaded else new,
        Device=SimpleNamespace(active_device=SimpleNamespace(name="Pynq-Z1")),__version__="synthetic")
    return module,old,new,downloads


class PynqDot8(unittest.TestCase):
    def test_no_download_reuses_only_exact_idle_dot8_image(self):
        with tempfile.TemporaryDirectory(prefix="aster-pynq8-reuse-") as directory, ExitStack() as stack:
            root = Path(directory); args,reference,hardware,data = inputs(root)
            args.download = False; args.expected_loaded = str(root/"aster_linux.bit")
            args.expected_loaded_sha256 = runner.sha(root/"aster_linux.bit")
            module,_old,new,downloads = board(args,reference["records"][0],data["ram1"])
            module.MMIO = lambda *_:new
            stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module}))
            stack.enter_context(mock.patch.object(physical,"preflight",return_value=(reference,hardware)))
            stack.enter_context(mock.patch.object(runner.time,"sleep")); stack.enter_context(redirect_stdout(io.StringIO()))
            report = runner.run(args)
            self.assertEqual(downloads,[]); self.assertFalse(report["downloaded"])
            self.assertEqual(report["boots"][0]["before_stop"]["dma"]["status"],0)  # Last balanced method is CPU.
            self.assertEqual(report["final_state"],physical.stopped_state())

    def test_changed_preflight_inputs_and_second_guard_are_nonmutating(self):
        for scenario in ("firmware","missing-download","second-loaded","second-busy","second-manifest"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-pynq8-race-") as directory, ExitStack() as stack:
                root = Path(directory); args,reference,hardware,data = inputs(root)
                module,old,new,downloads = board(args,reference["records"][0],data["ram1"])
                if scenario == "firmware": (root/reference["artifacts"]["firmware"]["file"]).write_bytes(b"changed")
                if scenario == "missing-download": args.download = False
                calls = []
                def preflight(*_args,**_kwargs):
                    calls.append(1)
                    if len(calls) == 2:
                        if scenario == "second-loaded": module.PL.bitfile_name = "/different/project.bit"
                        if scenario == "second-busy": old.registers[0] = 1
                        if scenario == "second-manifest": args.reference.write_text("changed")
                    return reference,hardware
                stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module}))
                stack.enter_context(mock.patch.object(physical,"preflight",side_effect=preflight))
                stack.enter_context(redirect_stdout(io.StringIO()))
                with self.assertRaises((ValueError,RuntimeError)): runner.run(args)
                self.assertEqual(downloads,[]); self.assertEqual(old.writes,[]); self.assertEqual(new.writes,[])
                if scenario.startswith("second"):
                    report = json.loads((args.output/"physical.json").read_text())
                    self.assertEqual(report["status"],"failed"); self.assertIsNone(report["final_state"])
                else: self.assertFalse(args.output.exists())

    def test_isolated_shipping_closure_never_imports_pynq(self):
        with tempfile.TemporaryDirectory(prefix="aster-pynq8-import-") as directory:
            root = Path(directory)
            for name in physical.COLLECTOR_FILES: shutil.copy2(ROOT/"scripts"/name,root/name)
            subprocess.run([sys.executable,"-I","-c",
                "import sys; sys.path.insert(0,sys.argv[1]); import run_pynq_dot8; assert 'pynq' not in sys.modules",str(root)],
                cwd=root,check=True,capture_output=True)

    def test_real_boot_flow_frozen_dot8_and_safe_failure_cleanup(self):
        scenarios = ("complete","bad-pop","timeout","trailing","counter-mismatch","bad-ram","bad-dma","bad-dot8","download-failed","wrong-identity")
        for cache in (False,True):
            for scenario in scenarios:
                with self.subTest(cache=cache,scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-pynq8-flow-") as directory, ExitStack() as stack:
                    root = Path(directory); args,reference,hardware,data = inputs(root,cache,jobs=3)
                    rows = copy.deepcopy(reference["records"][0]); ram = data["ram1"]
                    if scenario == "counter-mismatch": rows[0]["h0_cycles"] += 1; rows[0]["h1_cycles"] += 1
                    if scenario == "bad-ram": ram = ram[:0x8000]+bytes(528)+ram[0x8000+528:]
                    module,old,new,downloads = board(args,rows,ram,scenario)
                    stack.enter_context(redirect_stdout(io.StringIO())); stack.enter_context(mock.patch.dict(sys.modules,{"pynq":module}))
                    stack.enter_context(mock.patch.object(physical,"preflight",return_value=(reference,hardware)))
                    ticks = iter(i*0.001 for i in range(100000))
                    stack.enter_context(mock.patch.object(runner.time,"monotonic",side_effect=lambda:next(ticks)))
                    stack.enter_context(mock.patch.object(runner.time,"sleep"))
                    if scenario == "complete": runner.run(args)
                    else:
                        with self.assertRaises((RuntimeError,ValueError,TimeoutError)): runner.run(args)
                    report = json.loads((args.output/"physical.json").read_text())
                    self.assertEqual(old.writes,[]); self.assertEqual(len(downloads),1)
                    self.assertEqual(report["status"],"complete" if scenario == "complete" else "failed")
                    if scenario in ("wrong-identity","download-failed"):
                        self.assertEqual(new.writes,[]); self.assertIsNone(report["final_state"])
                    else:
                        self.assertEqual(report["final_state"],physical.stopped_state()); self.assertEqual(new.writes[-1],(0,0))
                        self.assertTrue((args.output/"boot1.uart").exists())
                    if scenario != "complete": continue
                    self.assertEqual(len(report["boots"]),2)
                    self.assertEqual(report["boots"][0]["before_stop"]["dot8"]["counters"][0],4)
                    physical.audit(args.output/"physical.json",args.reference,args.overlay,clean=False)
                    original = copy.deepcopy(report); path = args.output/"physical.json"; mutants = []
                    for key in report:
                        bad = copy.deepcopy(report); del bad[key]; mutants.append(bad)
                    for key in report["boots"][0]:
                        bad = copy.deepcopy(report); del bad["boots"][0][key]; mutants.append(bad)
                    for key in ("status","bytes_done","job_cycles","error_code","counting"):
                        bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["dma"][key] += 1; mutants.append(bad)
                    for i in range(14):
                        bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["dma"]["counters"][i] += 1; mutants.append(bad)
                    for key in ("busy","counting"):
                        bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["dot8"][key] = 1; mutants.append(bad)
                    for i in range(8):
                        bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["dot8"]["counters"][i] += 1; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["final_state"]["dot8"]["counters"][0] = 1; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["final_state"]["dma"]["bytes_done"] = 1; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["boots"].reverse(); mutants.append(bad)
                    bad = copy.deepcopy(report); bad["boots"][0]["before_stop"]["lifetime_retired"][1] = 1; mutants.append(bad)
                    bad = copy.deepcopy(report); bad["previous_state"]["control"] = 1; mutants.append(bad)
                    for n,bad in enumerate(mutants):
                        path.write_text(json.dumps(bad))
                        with self.subTest(mutation=n), self.assertRaises(ValueError): physical.audit(path,args.reference,args.overlay,clean=False)
                    path.write_text(json.dumps(original))
                    for ext in ("uart","ram"):
                        target = args.output/f"boot1.{ext}"; saved = target.read_bytes()
                        changed = bytearray(saved)
                        if ext == "ram": changed[0x8004] ^= 1
                        else: changed = bytearray(saved.replace(b"h0_cycles=40000",b"h0_cycles=40001",1))
                        self.assertNotEqual(bytes(changed),saved); target.write_bytes(changed)
                        bad = copy.deepcopy(original); bad["boots"][0][ext] = runner.artifact(target); path.write_text(json.dumps(bad))
                        with self.assertRaises(ValueError): physical.audit(path,args.reference,args.overlay,clean=False)
                        target.write_bytes(saved); path.write_text(json.dumps(original))
                    with mock.patch.object(physical.subprocess,"check_output",return_value=b"changed collector"):
                        with self.assertRaises(ValueError): physical.audit(path,args.reference,args.overlay)

    def test_previous_guard_rejects_changes_without_writes_or_download(self):
        for scenario in ("loaded","hash","hwh","map","clock","busy","uart","identity","board"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix="aster-pynq8-guard-") as directory:
                args,reference,_,data = inputs(Path(directory)); module,old,new,downloads = board(args,reference["records"][0],data["ram1"])
                if scenario == "loaded": module.PL.bitfile_name = "/different/project.bit"
                if scenario == "hash": Path(args.expected_loaded).write_bytes(b"changed")
                if scenario == "hwh": Path(args.expected_loaded).with_suffix(".hwh").write_bytes(b"<not_a_handoff/>")
                if scenario == "map": module.PL.ip_dict["aster"]["phys_addr"] = 0x50000000
                if scenario == "clock": module.Clocks.fclk0_mhz = 50
                if scenario == "busy": old.registers[0] = 1
                if scenario == "uart": old.registers[0x0c] = 1
                if scenario == "identity": old.registers[0x1c] = 0x50001
                if scenario == "board": module.Device.active_device.name = "different"
                with self.assertRaises((RuntimeError,ValueError)): runner.guard_previous(module,args.expected_loaded,args.expected_loaded_sha256)
                self.assertEqual(old.writes,[]); self.assertEqual(new.writes,[]); self.assertEqual(downloads,[])
                if scenario in ("loaded","hash","hwh","map","clock","board"): self.assertEqual(old.events,[])

    def test_invalid_inputs_do_not_import_board_api(self):
        for key,value in (("host_pause",float("nan")),("timeout",0),("collector_revision","dirty"),
                          ("expected_loaded","relative"),("expected_loaded_sha256","bad"),("download",1)):
            with tempfile.TemporaryDirectory(prefix="aster-pynq8-preflight-") as directory, mock.patch.dict(sys.modules,{"pynq":None}):
                args,_,_,_ = inputs(Path(directory)); setattr(args,key,value)
                with self.subTest(key=key), self.assertRaises(ValueError): runner.run(args)
                self.assertFalse(args.output.exists())
        with tempfile.TemporaryDirectory(prefix="aster-pynq8-preflight-") as directory, mock.patch.dict(sys.modules,{"pynq":None}):
            args,_,_,_ = inputs(Path(directory))
            with mock.patch.object(physical,"preflight",side_effect=ValueError("invalid ELF/HWH")):
                with self.assertRaises(ValueError): runner.run(args)
            self.assertFalse(args.output.exists())
            args.output.mkdir(); marker = args.output/"keep"; marker.write_text("existing data")
            with self.assertRaises(ValueError): runner.run(args)
            self.assertEqual(marker.read_text(),"existing data")

    def test_physical_configuration_and_hardware_source_contract(self):
        with tempfile.TemporaryDirectory(prefix="aster-pynq8-compatible-") as directory:
            _,reference,hardware,_ = inputs(Path(directory)); physical.compatible(reference,hardware)
            for key,value in (("harts",1),("l1",0),("sync_memory",0),("memory_wait",7),("line_count",32),("uart_seed",1)):
                bad = copy.deepcopy(reference); bad["configuration"][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError): physical.compatible(bad,hardware)
            for key in ("Makefile","rtl/dma/aster_dma_engine.sv","fpga/pynq_z1/build_linux.tcl","scripts/pynq_handoff.py","vendor/picorv32/picorv32.v"):
                bad = copy.deepcopy(reference); bad["metadata"]["source_files"][key] = "a"*64
                with self.subTest(source=key), self.assertRaises(ValueError): physical.compatible(bad,hardware)
            bad = copy.deepcopy(reference); bad["metadata"]["source_files"]["scripts/new_collector.py"] = "a"*64
            physical.compatible(bad,hardware)


if __name__ == "__main__": unittest.main()
