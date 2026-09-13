"""Synthetic fixtures test validators only; never used as execution evidence."""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import dma_functional as f
import dma_functional_results as results
from coherent_elf import inspect_elf
from test_dma_results import fixture as bench_fixture


def elf_fixture(kind):
    functions = ["main","aster_secondary_main","aster_dma_copy","aster_dma_submit","aster_dma_poll",
                 "aster_dma_abort_and_wait" if kind == "runtime" else "execute_code"]
    entries = [(name,128+32*i,32,2,1) for i,name in enumerate(functions)]
    objects = (("source",0x10000040,1152,2),("destination",0x10000500,1152,2),
               ("independent_work",0x10000000,4,2),("dma_results",0x10008000,512,3)) if kind == "runtime" else (
               ("code",0x10000000,64,2),("staging",0x10000080,64,2),("publication_results",0x10008000,512,3))
    entries += [(n,a,s,1,section) for n,a,s,section in objects]
    names = b"\0"+b"".join(n.encode()+b"\0" for n,*_ in entries)
    rom = bytes(512); strings = 84+len(rom); symoffset = strings+len(names)
    symbols = bytes(16)+b"".join(struct.pack("<IIIBBH",names.index(n.encode()+b"\0"),a,s,16+typ,0,section)
                              for n,a,s,typ,section in entries)
    section_offset = symoffset+len(symbols)
    header = struct.pack("<16sHHIIIIIHHHHHH",b"\x7fELF\x01\x01\x01"+bytes(9),2,243,1,0,52,section_offset,0,52,32,1,40,6,0)
    program = struct.pack("<8I",1,84,0,0,512,512,5,4)
    sections = [bytes(40)]+[struct.pack("<10I",*values) for values in (
        (0,1,6,0,84,512,0,0,4,0),(0,8,3,0x10000000,0,0x8000,0,0,64,0),
        (0,8,3,0x10008000,0,0x3000,0,0,4,0),(0,3,0,0,strings,len(names),0,0,1,0),
        (0,2,0,0,symoffset,len(symbols),4,0,4,16))]
    return header+program+rom+names+symbols+b"".join(sections)


def ram_fixture(kind,caches,program):
    data = bytearray(65536); symbols = program["symbols"]
    cpu = [[2000000,1000000,1100000,100,20,50,10,1000,0,0,0,3,4,12] for _ in range(2)]
    if not caches:
        for row in cpu: row[3:7] = [0]*4; row[11:] = [0]*3
    if kind == "runtime":
        # Independently specified known totals, not calls to runtime_totals().
        device = [1000000,700000,52292,52292,61313,52292,52292,0,0,0,339,1,6,0]
        source,dest = (symbols[k]["address"] for k in ("source","destination"))
        meta = [3,320,768239,source,dest,1001,2,1024,5,31250000,6+caches,0,0,0,0,0]
        pattern = lambda i: ((i*73) ^ (i >> 3) ^ (2003*41)) & 255
        data[source-0x10000000:source-0x10000000+1152] = bytes(pattern(i) for i in range(1152))
        data[dest-0x10000000:dest-0x10000000+1152] = bytes(pattern(i+1) if 64 <= i < 1088 else (0xa5^i)&255 for i in range(1152))
        struct.pack_into("<I",data,symbols["independent_work"]["address"]-0x10000000,1001)
        cpu[0][8:11] = [12,2,4]; cpu[1][8] = 1001
    else:
        device = [432,368,16,16,64,16,16,0,0,0,8,0,0,0]
        code,staging = (symbols[k]["address"] for k in ("code","staging"))
        meta = [8,0,288,312,312,code,2,8,5,31250000,6+caches,staging,0,0,0,0]
        for address,guard in ((code,0xc0010000),(staging,0xa57e0000)):
            struct.pack_into("<16I",data,address-0x10000000,0x13800513,0x8067,*[guard^i for i in range(2,16)])
    struct.pack_into("<16I",data,0x8000,*meta)
    struct.pack_into("<42Q",data,0x8040,*device,*cpu[0],*cpu[1])
    return bytes(data)


def fixture(root,caches):
    old,_,_ = bench_fixture(); toolchain = old["toolchain"]
    toolchain["headers"]["/test/stdatomic.h"] = "0"*64
    sources = {"Makefile":"0"*64,"rtl/soc/aster_pynq_linux.sv":"0"*64,
               "vendor/picorv32/picorv32.v":"0"*64,"verification/soc/tb_pynq_linux_coherent.cpp":"0"*64}
    build = Path("/test/build"); simulator = build/f"linux_dma_h2_l1{caches}/aster_linux_dma_sim"
    settings = [f"BUILD_DIR={build}","HART_COUNT=2",f"ENABLE_L1={caches}","SYNC_MEMORY=1","MEMORY_WAIT_CYCLES=1","L1_LINE_WORDS=4","L1_LINE_COUNT=16"]
    invocation = ["make","--no-print-directory","-j2",*settings,str(simulator)]+[str(build/"software"/(n+".hex")) for n in f.PROGRAMS.values()]
    verilator = ["verilator","--assert","-DASTER_COHERENCE_ASSERT","-DRISCV_FORMAL","--public-flat-rw","--top-module","aster_pynq_linux",
                 "-GHART_COUNT=2","-GENABLE_COHERENCE=1'b1",f"-GCOHERENT_L1=1'b{caches}","-GENABLE_DMA=1'b1","-GCLK_HZ=31250000","-GBAUD=781250","-GRX_DEPTH=128",
                 "-CFLAGS",f"-DASTER_HART_COUNT=2 -DASTER_L1={caches} -DASTER_DMA=1 -DASTER_CLOCK=31250000",
                 "--Mdir",str(simulator.parent/"obj"),"-o",str(simulator)]+["/test/source/"+s for s in sources if s != "Makefile"]
    lines = [shlex.join(verilator)]; executions = []; programs = {}
    for kind,name in f.PROGRAMS.items():
        elf = elf_fixture(kind); program = dict(symbols=inspect_elf(elf,profile=name)["symbols"])
        ram = ram_fixture(kind,caches,program)
        raw = dict(elf=elf,firmware=b"00000000\n"*16384,map=b"synthetic map\n",disassembly=b"synthetic disassembly\n",
                   ram1=ram,ram2=ram,uart1=f.UART[kind],uart2=f.UART[kind])
        suffixes = dict(elf=".elf",firmware=".hex",map=".map",disassembly=".dis",ram1=".boot1.ram",ram2=".boot2.ram",uart1=".boot1.uart",uart2=".boot2.uart")
        artifacts = {}
        for key,value in raw.items():
            file = (kind if key.startswith(("ram","uart")) else name)+suffixes[key]
            (root/file).write_bytes(value); artifacts[key] = dict(file=file,bytes=len(value),sha256=hashlib.sha256(value).hexdigest())
        meta = copy.deepcopy(old["metadata"]); meta.update(dirty=False,source_files=sources,
            source_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),build_command=invocation,
            cflags="-march=rv32ima -mabi=ilp32 -O2 -ffreestanding -Isoftware/drivers",
            ldflags=f"-T software/boot/link_multicore.ld -Wl,-Map,{build}/software/{name}.map",
            elf_sha256=artifacts["elf"]["sha256"],firmware_sha256=artifacts["firmware"]["sha256"])
        compiler = [meta["compiler"],*shlex.split(meta["cflags"]),*shlex.split(meta["ldflags"]),"-o",str(build/"software"/(name+".elf")),
                    "software/runtime/start_multicore.S","software/drivers/aster_dma.c",f"software/tests/{name}.c"]
        lines.append(shlex.join(compiler)); mode = "dma" if kind == "runtime" else "publication"
        run = [str(simulator),str(build/"software"/(name+".hex")),mode,f"/test/output/{kind}"]
        executions.append("DMA_FUNCTIONAL_COMMAND "+json.dumps(run)); observations = []
        values = f.validate_ram(ram,program,kind,caches)
        for boot in (1,2):
            code = program["symbols"].get("code",{}).get("address")
            obs = dict(boot=boot,kind=mode,cpu=values["cpu"],dma=values["dma"],job_cycles=54,
                       ram_retired=[0,0] if kind == "runtime" else [32,16],ram_pcs=[[],[]] if kind == "runtime" else [[code,code+4]]*2)
            observations.append(obs)
            executions += [f"PASS: coherent AXI/serial {mode} harts=2 cache={caches} boot={boot-1} bytes={len(f.UART[kind])} retired=1000000,1000000 retained_RAM=65536",
                           "DMA_FUNCTIONAL_OBS "+json.dumps(obs)]
        if kind == "runtime":
            executions += [f"PASS: DMA AXI in-flight stop phase={p} retained_RAM=65536 drained_bytes=1" for p in range(3)]
            executions.append("PASS: actual-core DMA MMIO atomic and instruction-fetch denial")
        executions.append("PASS: coherent AXI stop/boot/RAM gates, split channels, held replies, fault diagnostics, full-UART stop; "
                          f"snapshots={11 if kind == 'runtime' else 6} observed_stores=10000")
        program.update(metadata=meta,toolchain=toolchain,compile_command=compiler,simulation_command=run,observations=observations,artifacts=artifacts)
        programs[kind] = program
    log = "\n".join(lines+executions)+"\n"; (root/"functional.log").write_text(log)
    manifest = dict(schema=results.SCHEMA,configuration=dict(results.CONFIG,l1=caches),programs=programs,log_sha256=hashlib.sha256(log.encode()).hexdigest())
    (root/"functional.json").write_text(json.dumps(manifest)); return manifest


class DmaFunctional(unittest.TestCase):
    def test_independent_totals_and_profiles(self):
        self.assertEqual(f.runtime_totals(),dict(bytes=61244,writes=52223,checks=768239,success=339,aborts=1,errors=6))
        for kind in f.PROGRAMS:
            elf = elf_fixture(kind); inspect_elf(elf,profile=f.PROGRAMS[kind])
            with self.assertRaises(ValueError): inspect_elf(elf,profile="dma_publication" if kind == "runtime" else "dma_runtime")

    def test_every_payload_guard_metadata_and_event_word(self):
        for kind,name in f.PROGRAMS.items():
            program = inspect_elf(elf_fixture(kind),profile=name)
            for caches in (0,1):
                raw = ram_fixture(kind,caches,program); values = f.validate_ram(raw,program,kind,caches)
                code = program["symbols"].get("code",{}).get("address")
                observation = dict(boot=1,kind="dma" if kind == "runtime" else kind,cpu=values["cpu"],dma=values["dma"],job_cycles=54,
                    ram_retired=[0,0] if kind == "runtime" else [32,16],ram_pcs=[[],[]] if kind == "runtime" else [[code,code+4]]*2)
                f.validate_observation(observation,values,program,kind)
                offsets = list(range(0x8000,0x8200,4))
                for symbol in ("source","destination","independent_work") if kind == "runtime" else ("code","staging"):
                    s = program["symbols"][symbol]; offsets += list(range(s["address"]-0x10000000,s["address"]-0x10000000+s["size"]))
                for offset in offsets:
                    bad = bytearray(raw); bad[offset] ^= 1
                    with self.subTest(kind=kind,cache=caches,offset=offset), self.assertRaises(ValueError):
                        changed = f.validate_ram(bytes(bad),program,kind,caches); f.validate_observation(observation,changed,program,kind)
                snapshot = dict(status=2,bytes_done=1024 if kind == "runtime" else 8,error_code=0,counting=0,job_cycles=54,counters=values["dma"])
                f.validate_host_dma(snapshot,values,kind)
                for key in snapshot:
                    bad = copy.deepcopy(snapshot)
                    if key == "counters": bad[key][0] += 1
                    elif key == "job_cycles": bad[key] = 0
                    else: bad[key] += 1
                    with self.assertRaises(ValueError): f.validate_host_dma(bad,values,kind)

    def test_complete_package_and_rehashed_mutations(self):
        for caches in (0,1):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); path = root/"functional.json"; original = fixture(root,caches)
                results.load(path,clean=False)
                with mock.patch.object(results.results,"source_at_revision") as git:
                    results.load(path); self.assertEqual(git.call_count,2)
                mutations = []
                for kind in f.PROGRAMS:
                    for key in original["programs"][kind]:
                        bad = copy.deepcopy(original); del bad["programs"][kind][key]; mutations.append(bad)
                    for key,value in (("compiler_sha256","f"*64),("simulator_sha256","f"*64),("verilator_version","wrong")):
                        bad = copy.deepcopy(original); bad["programs"][kind]["metadata"][key] = value; mutations.append(bad)
                    for index in range(3,13):
                        bad = copy.deepcopy(original); bad["programs"][kind]["metadata"]["build_command"][index] += "wrong"; mutations.append(bad)
                for key in original:
                    bad = copy.deepcopy(original); del bad[key]; mutations.append(bad)
                bad = copy.deepcopy(original); bad["configuration"]["l1"] = bool(caches); mutations.append(bad)
                for i,bad in enumerate(mutations):
                    path.write_text(json.dumps(bad))
                    with self.subTest(cache=caches,mutation=i), self.assertRaises(ValueError): results.load(path,clean=False)
                path.write_text(json.dumps(original)); logpath = root/"functional.log"; raw = logpath.read_text()
                for old,new in (("boot=1","boot=0"),("snapshots=11","snapshots=10"),("phase=2","phase=1"),
                    ("MMIO atomic and instruction-fetch denial","MMIO denial"),("-GCLK_HZ=31250000","-GCLK_HZ=62500000"),
                    ("-GENABLE_DMA=1","-GENABLE_DMA=0"),("--assert ",""),("DMA_FUNCTIONAL_OBS ","MISSING ")):
                    changed = raw.replace(old,new,1); self.assertNotEqual(raw,changed); logpath.write_text(changed)
                    bad = copy.deepcopy(original); bad["log_sha256"] = hashlib.sha256(changed.encode()).hexdigest(); path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): results.load(path,clean=False)
                logpath.write_text(raw)
                for kind in f.PROGRAMS:
                    for key in ("elf","firmware","ram1","ram2","uart1","uart2"):
                        item = original["programs"][kind]["artifacts"][key]; target = root/item["file"]; data = target.read_bytes()
                        changed = bytearray(data); changed[0x8000 if key.startswith("ram") else 84 if key == "elf" else 0] ^= 1
                        target.write_bytes(changed); bad = copy.deepcopy(original); digest = hashlib.sha256(changed).hexdigest()
                        bad["programs"][kind]["artifacts"][key]["sha256"] = digest
                        if key in ("elf","firmware"): bad["programs"][kind]["metadata"][key+"_sha256"] = digest
                        path.write_text(json.dumps(bad))
                        with self.subTest(kind=kind,artifact=key), self.assertRaises(ValueError): results.load(path,clean=False)
                        target.write_bytes(data)
                path.write_text(json.dumps(original)); results.load(path,clean=False)
                (root/"unlisted").write_text("preserve")
                with self.assertRaises(ValueError): results.load(path,clean=False)

    def test_capture_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); marker = root/"keep"; marker.write_text("user data")
            with self.assertRaises(ValueError): results.capture(root,1)
            with mock.patch.object(results,"command",return_value=" M source.c"):
                with self.assertRaises(ValueError): results.capture(root/"new",1)
            self.assertFalse((root/"new").exists()); self.assertEqual(marker.read_text(),"user data")


if __name__ == "__main__": unittest.main()
