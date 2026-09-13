"""The public reference programs are a pinned, unmodified vendored subset."""
import hashlib
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class UpstreamReference(unittest.TestCase):
    def test_pinned_complete_subset(self):
        root = ROOT/"vendor/riscv-tests"
        names = ("amoadd_w", "amoand_w", "amomax_w", "amomaxu_w", "amomin_w", "amominu_w", "amoor_w", "amoswap_w", "amoxor_w", "lrsc")
        wanted = {"LICENSE", "isa/macros/scalar/test_macros.h"} | {
            f"isa/{arch}/{name}.S" for arch in ("rv32ua", "rv64ua") for name in names}
        actual = {}
        for line in (root/"SHA256SUMS").read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./]+)", line)
            self.assertIsNotNone(match)
            value, name = match.groups()
            self.assertNotIn(name, actual)
            actual[name] = value
        self.assertEqual(set(actual), wanted)
        self.assertEqual({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()},
                         wanted | {"SHA256SUMS", "UPSTREAM.md"})
        for name, value in actual.items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(), value, name)
        self.assertIn("2ebecad997fa58cd9e5724340ba75aa4b59bd1d0", (root/"UPSTREAM.md").read_text())
        for name in names:
            self.assertIn(f'#include "../rv64ua/{name}.S"', (root/f"isa/rv32ua/{name}.S").read_text())

    def test_owned_environment_does_not_imply_privileged_core(self):
        env = (ROOT/"software/tests/riscv_reference/riscv_test.h").read_text()
        start = (ROOT/"software/tests/riscv_reference/start.S").read_text()
        for instruction in ("csrr", "csrw", "mret", "sret"):
            self.assertNotRegex(env+start, rf"\b{instruction}\b")
        self.assertIn("aster_reference_report", env)
        self.assertIn("REFERENCE_HART", start)
        self.assertIn("__data_load_start", start)
        self.assertIn("__bss_start", start)


if __name__ == "__main__":
    unittest.main()
