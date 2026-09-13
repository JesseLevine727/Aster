"""Compile actual RV32IMA C; mutate ELF/ROM and executed-PC claims read-only."""
from copy import deepcopy
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
import asterbench_dot8 as bench
from coherent_elf import inspect_elf
from run_dot8_sim import command
from test_asterbench_dot8 import fixture


@unittest.skipUnless(shutil.which("riscv32-unknown-elf-gcc"), "RISC-V toolchain required")
class Dot8Firmware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="aster-dot8-elf-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.images = {}
        for name, k in (("dot",0), ("dot",4096), ("fir",0), ("fir",7), ("gemm",0), ("gemm",64)):
            run = subprocess.run(["make","--no-print-directory","dot8-firmware",f"BUILD_DIR={cls.root}",
                                  f"DOT8_WORKLOAD={name}",f"DOT8_K={k}","DOT8_ALIGNMENT=unaligned"],
                                 cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if run.returncode: raise AssertionError(run.stdout)
            path = cls.root/f"software/dot8_{name}_k{k}_aunaligned_j4_s0x13570000/dot8.elf"
            cls.images[(name,k)] = path

    def inspect(self, path, name, k):
        return inspect_elf(path.read_bytes(),profile="dot8_benchmark",dot8_name=name,dot8_k=k,dot8_jobs=4)

    @staticmethod
    def offset(data, pc):
        header = struct.unpack_from("<16sHHIIIIIHHHHHH",data)
        for n in range(header[10]):
            kind, off, virtual, physical, size, memory, flags, align = struct.unpack_from("<8I",data,header[5]+32*n)
            if kind == 1 and physical <= pc < physical+size: return off+pc-physical
        raise AssertionError("instruction outside load segments")

    def test_actual_zero_tail_max_elf_rom_and_kernel_encoding(self):
        for (name,k), path in self.images.items():
            with self.subTest(name=name,k=k):
                program = self.inspect(path,name,k)
                image = b"".join(int(w,16).to_bytes(4,"little") for w in path.with_suffix(".hex").read_text().split())
                self.assertEqual(image,program["image"])
                d = bench.dimensions(name,k)
                for field, address, size in (("a",0x10001000,bench.allocation(d["a_used"])),
                                             ("b",0x10003000,bench.allocation(d["b_used"])),("y",0x10006000,192),
                                             ("results",0x10008000,4*2*132*4)):
                    self.assertEqual(program["symbols"]["aster_dot8_bench_"+field],dict(address=address,size=size))
                args = SimpleNamespace(elf=path,firmware=path.with_suffix(".hex"),simulator=Path("/not-executed/simulator"),
                    workload=name,k=k,jobs=4,alignment=1,harts=2,seed=0x13570000,l1=1,sync_memory=1,memory_wait=1,
                    line_words=4,line_count=16,boots=2,uart_seed=0,ram_prefix=self.root/"not-created")
                invocation = command(args)
                self.assertEqual(invocation[0],"/not-executed/simulator")
                self.assertIn("--custom-start",invocation)
                self.assertFalse(args.ram_prefix.exists())

    def test_elf_header_profile_load_and_opcode_mutations(self):
        path = self.images[("gemm",64)];data = path.read_bytes();program = self.inspect(path,"gemm",64)
        options = dict(profile="dot8_benchmark",dot8_name="gemm",dot8_k=64,dot8_jobs=4)
        for offset in (0,4,5,6,16,18,20,24,36,40,42,44,46,48):
            changed = bytearray(data);changed[offset] ^= 0x80
            with self.subTest(header=offset),self.assertRaises(ValueError): inspect_elf(bytes(changed),**options)
        for length in (0,51,len(data)//2):
            with self.assertRaises(ValueError): inspect_elf(data[:length],**options)
        for profile in ("benchmark","dma_benchmark","dot8_runtime","dot8_stop_fixture"):
            with self.assertRaises(ValueError): inspect_elf(data,profile=profile)
        for name,symbol in program["symbols"].items():
            if not name.startswith(("aster_dot8_custom_","aster_dot8_scalar_")): continue
            code = program["image"][symbol["address"]:symbol["address"]+symbol["size"]]
            instructions = list(struct.iter_unpack("<I",code))
            if "custom" in name:
                changed = bytearray(data)
                for n,(word,) in enumerate(instructions):
                    if word & 0xfe00707f == 0x0b: struct.pack_into("<I",changed,self.offset(data,symbol["address"]+4*n),0x13)
                with self.subTest(kernel=name,mutation="remove custom"),self.assertRaises(ValueError): inspect_elf(bytes(changed),**options)
            else:
                changed = bytearray(data);struct.pack_into("<I",changed,self.offset(data,symbol["address"]),0x0b)
                with self.subTest(kernel=name,mutation="custom scalar"),self.assertRaises(ValueError): inspect_elf(bytes(changed),**options)
        with self.assertRaises(ValueError): inspect_elf(data.replace(b"aster_dot8_bench_y",b"aster_dot8_wrong_y"),**options)

    def test_wrong_rom_and_dimensions_rejected_before_execution(self):
        path = self.images[("fir",7)]
        args = SimpleNamespace(elf=path,firmware=self.root/"changed.hex",simulator=Path("/not-executed"),
            workload="fir",k=7,jobs=4,alignment=1,harts=2,seed=0x13570000,l1=1,sync_memory=1,memory_wait=1,
            line_words=4,line_count=16,boots=2,uart_seed=0,ram_prefix=self.root/"not-created")
        raw = path.with_suffix(".hex").read_text()
        for changed in ("00000000"+raw[8:],raw[:-9],"invalid\n"+raw):
            args.firmware.write_text(changed)
            with self.assertRaises(ValueError): command(args)
        data = path.read_bytes()
        for options in (dict(dot8_name="npu",dot8_k=7,dot8_jobs=4),dict(dot8_name="fir",dot8_k=65,dot8_jobs=4),
                        dict(dot8_name="fir",dot8_k=7,dot8_jobs=True),dict(dot8_name="fir",dot8_k=7,dot8_jobs=3)):
            with self.assertRaises(ValueError): inspect_elf(data,profile="dot8_benchmark",**options)

    def test_pc_evidence_binds_actual_function_and_custom_instruction(self):
        path = self.images[("gemm",64)];program = self.inspect(path,"gemm",64)
        r = fixture(name="gemm",k=64,alignment="unaligned",pass_number=2)
        symbol = program["symbols"]["aster_dot8_custom_gemm"]
        pcs = [pc for pc in range(symbol["address"],symbol["address"]+symbol["size"],4)
               if struct.unpack_from("<I",program["image"],pc)[0] & 0xfe00707f == 0x0b]
        o = dict(boot=1,job=1,**{"pass":2},method=1,counts=[r[key] for key in bench.COUNTER_KEYS],output_stores=15,
                 kernel_retired=[0,1000],kernel_first=[0,10],kernel_last=[0,5000],kernel_pcs=[[],pcs],custom_pcs=pcs)
        bench.validate_observation(o,r,1,program)
        for key in o:
            changed = deepcopy(o);changed.pop(key)
            with self.assertRaises(ValueError): bench.validate_observation(changed,r,1,program)
        for key in ("counts","kernel_retired","kernel_first","kernel_last","kernel_pcs","custom_pcs"):
            changed = deepcopy(o);changed[key] = []
            with self.assertRaises(ValueError): bench.validate_observation(changed,r,1,program)
        for index in range(50):
            changed = deepcopy(o);changed["counts"][index] += 1
            with self.assertRaises(ValueError): bench.validate_observation(changed,r,1,program)
        changed = deepcopy(o);changed["kernel_pcs"] = [[],[symbol["address"]]];changed["custom_pcs"] = [symbol["address"]]
        with self.assertRaises(ValueError): bench.validate_observation(changed,r,1,program)


if __name__ == "__main__": unittest.main()
