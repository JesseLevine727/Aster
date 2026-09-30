"""The vendored riscv-arch-test subset is complete and unmodified (SHA256SUMS)."""

from pathlib import Path
import hashlib
import unittest

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor/riscv-arch-test"


class ArchTestVendor(unittest.TestCase):
    def test_checksums_cover_every_file_and_match(self):
        listed = {}
        for line in (VENDOR / "SHA256SUMS").read_text().splitlines():
            digest, path = line.split(maxsplit=1)
            listed[path] = digest
        on_disk = {str(path.relative_to(VENDOR)) for path in VENDOR.rglob("*") if path.is_file()}
        self.assertEqual(set(listed) | {"SHA256SUMS", "UPSTREAM.md"}, on_disk)
        for path, digest in listed.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((VENDOR / path).read_bytes()).hexdigest(), digest)

    def test_suites_used_by_the_shell_are_present(self):
        for suite, count in (("I", 39), ("M", 8), ("A", 9), ("Zifencei", 1)):
            with self.subTest(suite=suite):
                self.assertEqual(len(list((VENDOR / "riscv-test-suite/rv32i_m" / suite / "src").glob("*.S"))), count)


if __name__ == "__main__":
    unittest.main()
