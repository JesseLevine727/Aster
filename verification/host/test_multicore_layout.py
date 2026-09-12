"""Negative link tests for the dual-hart ownership and runtime boundaries."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MulticoreLayout(unittest.TestCase):
    def build(self, declarations, success=True, stack_size=None):
        prefix = os.environ.get("RISCV_PREFIX", "riscv32-unknown-elf-")
        with tempfile.TemporaryDirectory(prefix="aster-dual-link-") as work:
            output = Path(work) / "layout.elf"
            args = [prefix + "gcc", "-march=rv32im", "-mabi=ilp32", "-nostdlib",
                    "-ffreestanding", "-msmall-data-limit=0", "-x", "c", "-",
                    "-T", str(ROOT / "software/boot/link_multicore.ld"), "-o", str(output)]
            if stack_size is not None:
                args += [f"-Wl,--defsym,__stack_size={stack_size}"]
            result = subprocess.run(args, input=declarations + "\nvoid _start(void) { for (;;) {} }",
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            if not success:
                return result.stderr
            symbols = subprocess.check_output([prefix + "nm", "--defined-only", str(output)], text=True)
            symbols = {parts[2]: int(parts[0], 16) for line in symbols.splitlines()
                       if len(parts := line.split()) == 3}
            return output.read_bytes(), symbols

    def test_shared_size_and_no_stack_alias(self):
        _, symbols = self.build("volatile char shared[32768];")
        self.assertEqual(symbols["__bss_end"], 0x10008000)
        self.assertEqual(symbols["__stack0_top"], 0x1000c000)
        self.assertEqual(symbols["__stack1_top"], 0x10010000)
        self.assertEqual(symbols["__stack0_bottom"], 0x1000b000)
        self.assertEqual(symbols["__stack1_bottom"], 0x1000f000)
        self.build("volatile char shared[32769];", False)

    def test_each_private_region_cannot_overflow_its_stack(self):
        for hart in (0, 1):
            with self.subTest(hart=hart):
                declaration = f'volatile char private[SIZE] __attribute__((section(".private{hart}")));'
                _, symbols = self.build(declaration.replace("SIZE", "12288"))
                self.assertEqual(symbols[f"__private{hart}_end"], symbols[f"__stack{hart}_bottom"])
                error = self.build(declaration.replace("SIZE", "12289"), False)
                self.assertIn(f"hart {hart} private RAM overflow", error)

    def test_stack_alignment_and_minimum(self):
        for size in (0, 8, 4097):
            with self.subTest(size=size):
                self.assertIn("stacks must be 16-byte aligned", self.build("", False, size))
        self.assertIn("private RAM overflow", self.build("", False, 16400))

    def test_rom_limit(self):
        self.build("const unsigned char image[65536] = {1};", False)

    def test_shared_initialized_odd_bytes_have_rom_load_image(self):
        elf, symbols = self.build("volatile unsigned char odd[3] = {0x12, 0x56, 0x9a};")
        self.assertEqual(symbols["__data_start"], 0x10000000)
        self.assertEqual(symbols["__data_end"], 0x10000004)
        self.assertLess(symbols["__data_load_start"], 65536)
        header = struct.unpack_from("<16sHHIIIIIHHHHHH", elf)
        segments = [struct.unpack_from("<IIIIIIII", elf, header[5] + i * header[9])
                    for i in range(header[10])]
        data = [s for s in segments if s[0] == 1 and s[2] == 0x10000000]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0][3], symbols["__data_load_start"])
        self.assertEqual(data[0][4], 4)
        self.assertEqual(elf[data[0][1]:data[0][1]+3], bytes([0x12, 0x56, 0x9a]))


if __name__ == "__main__":
    unittest.main()
