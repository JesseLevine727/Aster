"""Host-side checks for the independent ISA oracle and firmware layout."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


vectors = module("gen_rv32im_vectors")
images = module("elf_to_hex")


class ArithmeticReference(unittest.TestCase):
    def test_known_corners(self):
        for op, a, b, expected in [
            ("div", -7, 3, -2), ("rem", -7, 3, -1),
            ("div", 7, -3, -2), ("rem", 7, -3, 1),
            ("div", -0x80000000, -1, 0x80000000),
            ("rem", -0x80000000, -1, 0),
            ("div", 7, 0, 0xffffffff), ("rem", -7, 0, -7),
            ("divu", 0xffffffff, 2, 0x7fffffff),
            ("divu", 0, 0, 0xffffffff), ("remu", 0xffffffff, 0, 0xffffffff),
            ("mulh", 0x80000000, 0x80000000, 0x40000000),
            ("mulhsu", 0xffffffff, 0xffffffff, 0xffffffff),
            ("mulhu", 0xffffffff, 0xffffffff, 0xfffffffe),
            ("sll", 1, 32, 1), ("sra", 0x80000000, 31, 0xffffffff),
            ("sltu", 0x7fffffff, -1, 1),
        ]:
            with self.subTest(op=op, a=a, b=b):
                self.assertEqual(vectors.evaluate(op, a, b), expected & 0xffffffff)

    def test_seed_reproducibility(self):
        self.assertEqual(vectors.generate(1), vectors.generate(1))
        self.assertNotEqual(vectors.generate(1), vectors.generate(2))


class FirmwareLayout(unittest.TestCase):
    def build(self, source, expected_success):
        compiler = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-") + "gcc"
        with tempfile.TemporaryDirectory(prefix="aster-link-") as work:
            output = Path(work) / "image.elf"
            result = subprocess.run([
                compiler, "-march=rv32im", "-mabi=ilp32", "-nostdlib",
                "-ffreestanding", "-msmall-data-limit=0", "-x", "c", "-",
                "-T", str(ROOT / "software/boot/link.ld"), "-o", str(output),
            ], input=source, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, expected_success, result.stderr)
            if expected_success:
                return output.read_bytes()
            return result.stderr

    def test_stack_reservation(self):
        self.build("volatile char data[61440]; void _start(void) { data[0]=1; for(;;){} }", True)
        error = self.build("volatile char data[61441]; void _start(void) { data[0]=1; for(;;){} }", False)
        self.assertIn("reserved stack", error)

    def test_rom_overflow(self):
        self.build("const char image[65536]={1}; void _start(void) { for(;;){} }", False)

    def test_initialized_byte_segment(self):
        elf = self.build("volatile unsigned char byte=0xa5; void _start(void) { byte++; for(;;){} }", True)
        image = images.elf_to_image(elf, 65536)
        header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf)
        loads = []
        for index in range(header[10]):
            segment = struct.unpack_from("<IIIIIIII", elf, header[5] + index * header[9])
            if segment[0] == 1 and segment[2] == 0x10000000:
                loads.append(segment)
        self.assertEqual(len(loads), 1)
        segment = loads[0]
        self.assertEqual(segment[4] % 4, 0)
        self.assertEqual(segment[3] % 4, 0)
        self.assertLess(segment[3], 65536)
        self.assertEqual(image[segment[3]], 0xa5)


if __name__ == "__main__":
    unittest.main()
