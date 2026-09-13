"""Synthetic UART/event claims plus real compiled ELF: validator tests, not measurements."""
from copy import deepcopy
import hashlib
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
import dot8_results as results
from coherent_elf import inspect_elf
from test_asterbench_dot8 import rows, line, observation, ram_image


def fixture(directory):
    c = dict(name="dot",k=7,alignment="unaligned",jobs=4,harts=2,base_seed=0x13570000,l1=1,
             sync_memory=1,memory_wait=1,line_words=4,line_count=16,boots=2,uart_seed=0)
    settings = dict(BUILD_DIR=str(directory),RISCV_PREFIX="riscv32-unknown-elf-",HART_COUNT=2,ENABLE_L1=1,
        SYNC_MEMORY=1,MEMORY_WAIT_CYCLES=1,L1_LINE_WORDS=4,L1_LINE_COUNT=16,DOT8_WORKLOAD="dot",DOT8_K=7,
        DOT8_ALIGNMENT="unaligned",DOT8_JOBS=4,DOT8_SEED=c["base_seed"],DOT8_BOOTS=2,DOT8_UART_SEED=0,DOT8_RAM_PREFIX=str(directory/"synthetic"))
    args = ["make","--no-print-directory"]+[f"{k}={v}" for k,v in settings.items()]
    subprocess.run(args+["dot8-firmware"],cwd=ROOT,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    config = json.loads(subprocess.check_output(args+["-s","dot8-config"],cwd=ROOT))
    elf = Path(config["elf"])
    program = inspect_elf(elf.read_bytes(),profile="dot8_benchmark",dot8_name="dot",dot8_k=7,dot8_jobs=4)
    records = rows(name="dot",k=7,alignment="unaligned")
    raw = []
    for boot in (1,2):
        raw.append(f"ASTERBOOT {boot}\n")
        for r in records:
            m = int(r["method"] == "custom"); o = observation(r,boot)
            kernel = program["symbols"][f"aster_dot8_{r['method']}_dot"]
            pcs = list(range(kernel["address"],kernel["address"]+kernel["size"],4))
            sites = [pc for pc in pcs if struct.unpack_from("<I",program["image"],pc)[0] & 0xfe00707f == 0x0b]
            # The compiled aligned/unaligned branches contain distinct sites;
            # this synthetic one-group window executes only one of them.
            pcs = [pc for pc in pcs if pc not in sites[1:]]
            o["kernel_pcs"][m] = pcs; o["kernel_retired"][m] = len(pcs)
            o["custom_pcs"] = [pc for pc in pcs if struct.unpack_from("<I",program["image"],pc)[0] & 0xfe00707f == 0x0b]
            raw += [line(r),"DOT8_OBS "+json.dumps(o)+"\n"]
        raw.append("ASTERSTOP "+json.dumps(dict(boot=boot,records=8,ram_bytes=65536,cpu_stores=5000,dma_stores=0,lifetime_retired=[40000,0]))+"\n")
    raw.append("PASS: dot8 benchmark boots=2 records=16; exact signed outputs/guards/RAM, actual scalar/custom PCs and 50-counter windows\n")
    log = "".join(raw); observed = results.parse_observed(log,c,program)
    data = {"elf":elf.read_bytes(),"firmware":elf.with_suffix(".hex").read_bytes(),"map":elf.with_suffix(".map").read_bytes(),
            "disassembly":elf.with_suffix(".dis").read_bytes(),"build_log":b"synthetic build identity only\n",
            "ram1":ram_image(records),"ram2":ram_image(records)}
    artifacts = {key:dict(file=key+".bin",sha256=hashlib.sha256(value).hexdigest(),bytes=len(value)) for key,value in data.items()}
    toolchain = dict(tools={name:dict(path="/synthetic/"+name,version="synthetic "+name,sha256="0"*64) for name in results.TOOL_NAMES},
                     headers={"/synthetic/stdint.h":"0"*64})
    sources = {"Makefile":"0"*64}
    metadata = dict(revision="0"*40,dirty=True,source_files=sources,source_sha256=hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),
        compiler="/synthetic/gcc",compiler_version="synthetic gcc",compiler_sha256="0"*64,cflags=config["cflags"],ldflags=config["ldflags"],
        verilator_version="synthetic verilator",firmware_sha256=artifacts["firmware"]["sha256"],elf_sha256=artifacts["elf"]["sha256"],
        simulator_sha256="0"*64,build_command=args[:2]+["-j2"]+args[2:]+["dot8-bench-build"],platform="synthetic")
    options = SimpleNamespace(simulator=Path(config["simulator"]),elf=elf,firmware=elf.with_suffix(".hex"),ram_prefix=directory/"synthetic",
        workload="dot",alignment=1,seed=c["base_seed"],**{k:v for k,v in c.items() if k not in ("name","alignment","base_seed")})
    result = dict(schema="aster.dot8.capture.v1",configuration=c,toolchain=toolchain,metadata=metadata,symbols=program["symbols"],
        **observed,records=[deepcopy(records),deepcopy(records)],artifacts=artifacts,log_sha256=hashlib.sha256(log.encode()).hexdigest(),
        simulator_command=results.simulator_command(options))
    return result,log,data


@unittest.skipUnless(shutil.which("riscv32-unknown-elf-gcc"),"RISC-V toolchain required")
class Dot8Capture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="aster-dot8-capture-fixture-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.original,cls.log,cls.data = fixture(Path(cls.temp.name))

    def test_complete_real_executable_package_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name,data in self.data.items(): (root/(name+".bin")).write_bytes(data)
            with mock.patch.object(results.subprocess,"run",side_effect=AssertionError("audit executed a command")):
                result = results.validate_result(self.original,self.log,root,clean=False)
                self.assertEqual(len(result),2)
                self.assertTrue(all(p["scalar_over_custom"] == .5 for p in results.summary(self.original)["pairs"]))
            with self.assertRaises(ValueError): results.validate_result(self.original,self.log,root)

    def test_missing_fields_metadata_configuration_symbols_and_commands(self):
        for key in self.original:
            bad = deepcopy(self.original); del bad[key]
            with self.subTest(field=key),self.assertRaises(ValueError): results.validate_result(bad,self.log,clean=False)
        for path,value in ((("configuration","name"),"npu"),(("configuration","k"),True),(("configuration","jobs"),3),
                           (("configuration","l1"),0),(("configuration","memory_wait"),0),(("metadata","compiler"),"/bin/sh"),
                           (("metadata","cflags"),self.original["metadata"]["cflags"]+" -O0"),
                           (("metadata","cflags"),self.original["metadata"]["cflags"]+" -flto"),
                           (("symbols","aster_dot8_custom_dot","address"),256)):
            bad = deepcopy(self.original); obj = bad
            for k in path[:-1]: obj = obj[k]
            obj[path[-1]] = value
            with self.subTest(path=path),self.assertRaises(ValueError): results.validate_result(bad,self.log,clean=False)
        for key in ("simulator_command","build_command"):
            target = self.original if key == "simulator_command" else self.original["metadata"]
            for i in range(len(target[key])):
                bad = deepcopy(self.original); obj = bad if key == "simulator_command" else bad["metadata"]
                obj[key][i] += "-changed"
                with self.subTest(command=key,arg=i),self.assertRaises(ValueError): results.validate_result(bad,self.log,clean=False)

    def test_actual_artifacts_rehashed_corruption_and_path_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name,data in self.data.items(): (root/(name+".bin")).write_bytes(data)
            for name,data in self.data.items():
                if name == "map": changed = data.replace(b"aster_dot8_custom_dot",b"aster_dot8_custom_BAD")
                elif name == "disassembly": changed = data.replace(b"<aster_dot8_custom_dot>",b"<aster_dot8_custom_BAD>")
                elif name == "build_log": changed = data+b"FAIL: synthetic failure\n"
                else:
                    changed = bytearray(data); changed[0x8040 if name.startswith("ram") else 0] ^= 1; changed = bytes(changed)
                (root/(name+".bin")).write_bytes(changed)
                bad = deepcopy(self.original); item = bad["artifacts"][name]
                item.update(sha256=hashlib.sha256(changed).hexdigest(),bytes=len(changed))
                if name in ("elf","firmware"): bad["metadata"][name+"_sha256"] = item["sha256"]
                with self.subTest(name=name),self.assertRaises(ValueError): results.validate_result(bad,self.log,root,clean=False)
                (root/(name+".bin")).write_bytes(data)
                bad = deepcopy(self.original); bad["artifacts"][name]["file"] = "../escape"
                with self.assertRaises(ValueError): results.validate_result(bad,self.log,root,clean=False)
                path = root/(name+".bin"); path.unlink(); path.symlink_to(root/"elf.bin")
                with self.assertRaises(ValueError): results.validate_result(self.original,self.log,root,clean=False)
                path.unlink(); path.write_bytes(data)

    def test_raw_events_and_rehashed_sequence_still_bound(self):
        for key in bench.COUNTER_KEYS:
            bad = deepcopy(self.original); bad["records"][0][0][key] += 1
            with self.assertRaises(ValueError): results.validate_result(bad,self.log,clean=False)
        for changed in (self.log[:-1],self.log+"extra\n",self.log.replace("ASTERBOOT 2","ASTERBOOT 1"),
                        self.log.replace("DOT8_OBS ","WRONG_OBS ",1)):
            bad = deepcopy(self.original); bad["log_sha256"] = hashlib.sha256(changed.encode()).hexdigest()
            with self.assertRaises(ValueError): results.validate_result(bad,changed,clean=False)

    def test_preflight_invalid_config_or_existing_evidence_does_not_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/"capture.json"
            args = SimpleNamespace(**{k:v for k,v in self.original["configuration"].items() if k != "base_seed"},
                                   seed=0x13570000,output=output,riscv_prefix="riscv32-unknown-elf-",allow_dirty=True)
            with mock.patch.object(results.subprocess,"run",side_effect=AssertionError("premature execution")):
                args.k = -1
                with self.assertRaises(ValueError): results.capture(args)
                args.k = 7; output.with_suffix(".log").write_text("retained prior failure\n")
                with self.assertRaises(ValueError): results.capture(args)
                self.assertEqual(output.with_suffix(".log").read_text(),"retained prior failure\n")


if __name__ == "__main__": unittest.main()
