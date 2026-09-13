"""Real compiled ELF plus explicitly synthetic RAM/logs test the auditors only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import dot8_functional as f
import dot8_functional_results as r
from coherent_elf import inspect_elf


def ram_fixture(program,caches):
    """Test data uses independent integer signs/indexing, not the production oracle."""
    symbols = program["symbols"]; raw = bytearray(65536)
    def put(name,data):
        offset = symbols[name]["address"]-0x10000000; raw[offset:offset+len(data)] = data
    a,b,y = bytearray(),bytearray(),bytearray()
    for slot in (0,1):
        seed = 0x81d7e01d if slot == 0 else (0xa47e8000 ^ (736*0x9e3779b9)) & 0xffffffff
        banks = [bytes(((seed >> ((i&3)*8)) ^ (i*73) ^ (i>>3) ^ (bank*91)) & 255 for i in range(512)) for bank in (0,1)] if slot == 0 else [bytes(512)]*2
        a.extend(banks[0]); b.extend(banks[1]); words = [(0x6d5a0000 ^ i*16843009 ^ seed) & 0xffffffff for i in range(47)]
        n,oa,ob = (32,65,66) if slot == 0 else (64,67,67)
        sa = struct.unpack("<512b",banks[0]); sb = struct.unpack("<512b",banks[1])
        for row in range(3):
            for col in range(5): words[16+5*row+col] = sum(sa[oa+row*n+j]*sb[ob+j*5+col] for j in range(n)) & 0xffffffff
        y.extend(struct.pack("<47I",*words))
    for name,data in (("input_a",a),("input_b",b),("output",y),("dma_source",a[:512]),("dma_destination",a[:512]),
                      ("directed_done",struct.pack("<2I",1,1)),("published",struct.pack("<I",3)),("consumed",struct.pack("<I",3)),
                      ("reservation_word",struct.pack("<2I",0x12345678,0x12345678))): put(name,data)
    cpu = [2000000,1000000,1100000,100,20,50,10,1000,32,16,0,3,4,12]
    if not caches: cpu[3:7] = [0]*4; cpu[11:] = [0]*3
    dma = [10000,8000,512,512,2048,512,512,0,0,0,4,0,0,0]
    counts = cpu+cpu+dma+[25393,50786,25393,25393,25689,51378,25689,25689]
    addresses = [symbols[k]["address"] for k in ("input_a","input_b","output")]
    for h in (0,1):
        meta = [0]*160
        meta[:12] = [100,736,2,64,3,3,(0xa57e8000 ^ 736*0x9e3779b9)&0xffffffff,*addresses,512,47] if h == 0 else [100,739,2,32,1,2,0x81d7e01d,*addresses,512,47]
        meta[16:21] = [736,0,25393 if h == 0 else 25689,16,3]
        if h == 0:
            meta[21:23] = [2,0x80001]
            for i,v in enumerate(counts): meta[32+2*i:34+2*i] = [v&0xffffffff,v>>32]
        put("aster_dot8_runtime_results"+str(h),struct.pack("<160I",*meta))
    return bytes(raw),counts


def fixture(build,caches):
    cmd = ["make","--no-print-directory",*r.settings(build,caches)]+[str(build/"software"/(n+".hex")) for n in r.PROGRAMS]
    result = subprocess.run(cmd,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True)
    programs = {}; data = {}; flags = None
    for name in r.PROGRAMS:
        args,cflags,ldflags = r.compile_line(result.stdout,"riscv32-unknown-elf-gcc",name)
        if name == r.PROGRAMS[0]: flags = cflags,ldflags
        elf = inspect_elf((build/"software"/(name+".elf")).read_bytes(),profile=name)
        programs[name] = dict(symbols=elf["symbols"],compile_command=args)
        for ext in (".elf",".hex",".map",".dis"): data[name+ext] = (build/"software"/(name+ext)).read_bytes()
    v = ["verilator","--assert","-DASTER_COHERENCE_ASSERT","-DASTER_DOT8_ASSERT","-DRISCV_FORMAL","--top-module","aster_coherent_soc",
         "-GHART_COUNT=2",f"-GENABLE_L1=1'b{caches}","-GENABLE_DMA=1'b1","-GENABLE_DOT8=1'b1","-GSYNC_MEMORY=1'b1","-GMEMORY_WAIT_CYCLES=1",
         "-GHOST_BOOT=1'b1","-GLINE_WORDS=4","-GLINE_COUNT=16","-CFLAGS",f"-DASTER_HART_COUNT=2 -DASTER_L1={caches} -DASTER_MEMORY_WAIT=1",
         "--Mdir",str(r.simulator(build,caches).parent/"obj"),"-o",str(r.simulator(build,caches))]+[str(ROOT/p) for p in r.RTL]
    data["build.log"] = (shlex.join(v)+"\n"+result.stdout).encode()
    raw,counts = ram_fixture(programs["dot8_runtime"],caches); lines = []; observations = []
    for boot in (1,2):
        lines += [f"OBS: hart={h} directed_pairs={n}" for n in (128,256,384,512,640) for h in (0,1)]
        lines.append(f"PASS: dot8 C runtime harts=2 cache={caches} wait=1 cycles=2100000 pairs=1472 output_methods=2947 dots=25393,25689 DMA-overlap=17; all 16 byte alignments, tails, dot/FIR/GEMM, actual output stores, full guards/RAM, LRSC, dirty DMA publication, ABI 6")
        o = dict(boot=boot,counts=counts,pairs=[736,736],methods=[1472,1475],dots=[25393,25689],dma_overlap=17)
        observations.append(o); lines.append("DOT8_FUNCTIONAL_OBS "+json.dumps(o))
        data[f"runtime.boot{boot}.ram"] = raw; data[f"runtime.boot{boot}.uart"] = b"DOT8 RUNTIME PASS\n"
    for h,p,e in [(h,p,0) for h in (0,1) for p in range(4)]+[(0,4,0)]+[(0,p,1) for p in range(4)]:
        count = 8 if p == 4 else int(e and p != 0)
        lines.append(f"PASS: dot8 warm stop hart={h} point={p} escalation={e} selective={count}; all admitted sums completed, no reset race, full retained RAM")
    lines.append("PASS: dot8 runtime closeout warm_boots=2 stopped=15 CPU-stores=3000000 DMA-stores=2000; no reset after initial POR")
    data["functional.log"] = ("\n".join(lines)+"\n").encode()
    artifacts = {}
    for name,raw in data.items():
        key = "build_log" if name == "build.log" else "simulation_log" if name == "functional.log" else name
        if name.startswith("runtime.boot"): key = name.rsplit(".",1)[1]+name.split(".")[1][-1]
        artifacts[key] = dict(file=name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    sources = {p:"0"*64 for p in r.RTL+["Makefile"]}
    t = dict(tools={n:dict(path="/synthetic/"+n,version="synthetic "+n,sha256="0"*64) for n in r.results.TOOL_NAMES},
             headers={"/synthetic/stdint.h":"0"*64,"/synthetic/stdatomic.h":"0"*64})
    meta = dict(revision="0"*40,dirty=True,source_files=sources,source_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),
        compiler="/synthetic/gcc",compiler_version="synthetic gcc",compiler_sha256="0"*64,cflags=flags[0],ldflags=flags[1],
        verilator_version="synthetic verilator",firmware_sha256=artifacts["dot8_runtime.hex"]["sha256"],elf_sha256=artifacts["dot8_runtime.elf"]["sha256"],
        simulator_sha256="0"*64,build_command=r.invocation(build,caches),platform="synthetic, not execution evidence")
    m = dict(schema=r.SCHEMA,configuration=dict(r.CONFIG,l1=caches),metadata=meta,toolchain=t,programs=programs,
             simulation_command=r.execution(build,caches,Path("/synthetic/runtime")),observations=observations,artifacts=artifacts)
    return m,data


@unittest.skipUnless(shutil.which("riscv32-unknown-elf-gcc"),"RISC-V toolchain required")
class Dot8Functional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="aster-dot8-functional-tests-"); cls.addClassCleanup(cls.temp.cleanup)
        cls.fixtures = {c:fixture(Path(cls.temp.name)/str(c),c) for c in (0,1)}

    def test_complete_package_read_only_and_every_field(self):
        for caches,(m,data) in self.fixtures.items():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); path = root/"functional.json"
                for name,raw in data.items(): (root/name).write_bytes(raw)
                path.write_text(json.dumps(m))
                with mock.patch.object(r.subprocess,"run",side_effect=AssertionError("audit executed a command")): r.load(path,clean=False)
                with self.assertRaises(ValueError): r.load(path)
                for key in m:
                    bad = deepcopy(m); del bad[key]; path.write_text(json.dumps(bad))
                    with self.subTest(cache=caches,key=key),self.assertRaises(ValueError): r.load(path,clean=False)
                mutations = [("configuration","harts",1),("configuration","l1",bool(caches)),("metadata","compiler","/bin/sh"),
                             ("metadata","cflags",m["metadata"]["cflags"]+" -O0"),("metadata","ldflags","-T wrong"),
                             ("toolchain","headers",{}),("observations",0,{}),("programs","dot8_stop_fixture",{})]
                for section,key,value in mutations:
                    bad = deepcopy(m); bad[section][key] = value; path.write_text(json.dumps(bad))
                    with self.assertRaises((ValueError,KeyError,IndexError)): r.load(path,clean=False)

    def test_all_meaningful_ram_bytes_and_independent_counters(self):
        for caches,(m,data) in self.fixtures.items():
            p = m["programs"]["dot8_runtime"]; raw = data["runtime.boot1.ram"]; observation = m["observations"][0]
            values = f.validate_ram(raw,p,caches); f.validate_observation(observation,values,1)
            offsets = []
            for name,symbol in p["symbols"].items():
                if symbol["address"] >= 0x10000000:
                    start = symbol["address"]-0x10000000; offsets += range(start,start+symbol["size"])
            for offset in offsets:
                bad = bytearray(raw); bad[offset] ^= 1
                with self.subTest(cache=caches,offset=offset),self.assertRaises(ValueError):
                    values = f.validate_ram(bytes(bad),p,caches); f.validate_observation(observation,values,1)
            for index in range(50):
                bad = deepcopy(observation); bad["counts"][index] += 1
                with self.assertRaises(ValueError): f.validate_observation(bad,f.validate_ram(raw,p,caches),1)

    def test_rehashed_rom_disassembly_map_ram_log_and_command_mutations(self):
        m,data = self.fixtures[1]
        changes = {"dot8_runtime.hex":lambda raw:b"00000000"+raw[8:],"dot8_stop_fixture.hex":lambda raw:raw[:-9],
                   "dot8_runtime.dis":lambda raw:raw.replace(b"aster_dot8_custom_dot",b"aster_dot8_wrong_dot"),
                   "dot8_runtime.map":lambda raw:raw.replace(b"aster_dot8_custom_dot",b"aster_dot8_wrong_dot"),
                   "runtime.boot1.ram":lambda raw:bytes([raw[0]^1])+raw[1:],"runtime.boot2.uart":lambda raw:raw[:-1],
                   "functional.log":lambda raw:raw.replace(b"point=3 escalation=1",b"point=2 escalation=1"),
                   "build.log":lambda raw:raw.replace(b"-GENABLE_DOT8=1",b"-GENABLE_DOT8=0")}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root/"functional.json"
            for name,raw in data.items(): (root/name).write_bytes(raw)
            for name,change in changes.items():
                raw = change(data[name]); self.assertNotEqual(raw,data[name]); (root/name).write_bytes(raw); bad = deepcopy(m)
                key = next(k for k,v in bad["artifacts"].items() if v["file"] == name)
                bad["artifacts"][key].update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                if name == "dot8_runtime.hex": bad["metadata"]["firmware_sha256"] = bad["artifacts"][key]["sha256"]
                path.write_text(json.dumps(bad))
                with self.subTest(name=name),self.assertRaises(ValueError): r.load(path,clean=False)
                (root/name).write_bytes(data[name])
            for key in ("simulation_command",):
                for index in range(len(m[key])):
                    bad = deepcopy(m); bad[key][index] += "wrong"; path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): r.load(path,clean=False)

    def test_failure_before_build_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(r.subprocess,"run",side_effect=AssertionError("unexpected execution")):
                with self.assertRaises(ValueError): r.capture(Path(directory),1)
        for invalid in (True,-1,2):
            with self.assertRaises(ValueError): f.validate_ram(b"",{},invalid)


if __name__ == "__main__": unittest.main()
