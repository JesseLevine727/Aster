"""The ACT4 configuration of the Aster core (verification/core/act4) agrees
with itself and with the CPU shell: the ELF layout and the Sail model's memory,
the macros' device addresses and the shell's devices (docs/phase18.md, "ACT4")."""

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "verification/core/act4/aster-rv32ima"
SHELL = (ROOT / "verification/core/tb_core_ports.cpp").read_text()


def sail():
    # sail.json carries a // header (pyjson5 reads it in the framework)
    text = "".join(line + "\n" for line in (CONFIG / "sail.json").read_text().splitlines()
                   if not line.lstrip().startswith("//"))
    return json.loads(text)


def macro(name):
    match = re.search(rf"^#define {name}\s+(0x[0-9A-Fa-f]+|\d+)", (CONFIG / "rvmodel_macros.h").read_text(), re.M)
    return int(match.group(1), 0)


def shell_constant(name):
    return int(re.search(rf"{name} = (0x[0-9A-Fa-f]+)u", SHELL).group(1), 16)


class Act4Config(unittest.TestCase):
    def test_framework_config_names_its_files(self):
        text = (CONFIG / "test_config.yaml").read_text()
        for name in ("aster-rv32ima.yaml", "link.ld"):
            self.assertIn(name, text)
            self.assertTrue((CONFIG / name).exists(), name)

    def test_ram_matches_the_sail_region(self):
        link = (CONFIG / "link.ld").read_text()
        origin = int(re.search(r"RAM_ORIGIN = (0x[0-9a-fA-F]+);", link).group(1), 16)
        length = int(re.search(r"RAM_LENGTH = (0x[0-9a-fA-F]+);", link).group(1), 16)
        self.assertIn("TEST_BASE = 0x80000000;", link)          # the core's reset vector
        ram = [r for r in sail()["memory"]["regions"] if r["attributes"]["mem_type"] == "MainMemory"]
        self.assertEqual(len(ram), 1)
        self.assertEqual((int(ram[0]["base"]["value"], 16), int(ram[0]["size"]["value"], 16)), (origin, length))

    def test_timer_addresses_match_sail_and_the_shell(self):
        clint = sail()["platform"]["clint"]["base"]
        self.assertEqual(macro("RVMODEL_MTIMECMP_ADDRESS"), clint + 0x4000)
        self.assertEqual(macro("RVMODEL_MTIME_ADDRESS"), clint + 0xBFF8)
        self.assertEqual(macro("RVMODEL_MTIMECMP_ADDRESS"), shell_constant("kMtimecmp"))
        self.assertEqual(macro("RVMODEL_MTIME_ADDRESS"), shell_constant("kMtime"))

    def test_the_timer_rate_is_the_shells(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import run_core_tests
        self.assertEqual(macro("RVMODEL_MAX_CYCLES_PER_TIMER_TICK"), run_core_tests.ACT4_TIMER_DIVIDER)

    def test_interrupt_macros_use_the_shell_device(self):
        macros = (CONFIG / "rvmodel_macros.h").read_text()
        device = shell_constant("kIrqDevice")
        for name in ("SET_MEXT", "CLR_MEXT", "SET_MSW", "CLR_MSW"):
            body = re.search(rf"#define RVMODEL_{name}_INT\(_R1, _R2\)(.*?)(?=\n#|\n\n)", macros, re.S).group(1)
            self.assertIn(f"0x{device:08x}", body.lower(), name)

    def test_the_access_fault_address_faults_in_sail_and_the_shell(self):
        address = macro("RVMODEL_ACCESS_FAULT_ADDRESS")
        for region in sail()["memory"]["regions"]:
            base, size = int(region["base"]["value"], 16), int(region["size"]["value"], 16)
            self.assertFalse(base <= address < base + size, region)
        self.assertLess(address, 0x80000000)                     # below the shell's memory
        self.assertNotEqual(address & ~3, shell_constant("kIrqDevice"))

    def test_sail_extensions_are_the_cores(self):
        enabled = {name for name, value in sail()["extensions"].items()
                   if isinstance(value, dict) and value.get("supported")}
        # Zihpm: Sail then implements mhpmcounter3-31/mhpmevent3-31 (read-only zero
        # here), as the core does.
        self.assertEqual(enabled, {"M", "A", "Zaamo", "Zalrsc", "Zmmul", "Zicsr", "Zicntr", "Zifencei", "Zihpm"})


if __name__ == "__main__":
    unittest.main()
