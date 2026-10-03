"""The firmware regression (milestone 18.6): the shell's layouts
(verification/core/firmware) are v1's (software/boot/link.ld, link_multicore.ld)
with only the declared differences — their MEMORY regions in the shell's
memory, the harness's .tohost section and shell_memory_end — their regions fit
the shell's 96 KiB at 0x8000_0000, and the runner's programs exist."""

from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "verification/core/firmware"
V1 = ROOT / "software/boot"
sys.path.insert(0, str(ROOT / "scripts"))


def sections(path: Path) -> list[str]:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    text = re.sub(r"MEMORY\s*\{.*?\}", "", text, flags=re.S)
    text = re.sub(r"^\s*\.tohost\b.*$", "", text, flags=re.M)
    text = re.sub(r"^\s*shell_memory_end = .*$", "", text, flags=re.M)
    return [line.strip() for line in text.splitlines() if line.strip()]


def regions(path: Path) -> list[tuple[int, int]]:
    memory = re.search(r"MEMORY\s*\{(.*?)\}", path.read_text(), re.S).group(1)
    return [(int(origin, 16), int(length) * 1024)
            for origin, length in re.findall(r"ORIGIN = (0x[0-9a-fA-F]+), LENGTH = (\d+)K", memory)]


class FirmwareLayouts(unittest.TestCase):
    def test_sections_are_v1s(self):
        for name in ("link.ld", "link_multicore.ld"):
            self.assertEqual(sections(SHELL / name), sections(V1 / name), name)

    def test_regions_tile_the_shell_memory(self):
        for name in ("link.ld", "link_multicore.ld"):
            spans = sorted(regions(SHELL / name))
            self.assertEqual(spans[0][0], 0x80000000, name)
            for (origin, length), (following, _) in zip(spans, spans[1:]):
                self.assertEqual(origin + length, following, name)
            self.assertEqual(spans[-1][0] + spans[-1][1], 0x80000000 + 96 * 1024, name)

    def test_programs_exist(self):
        import run_core_tests
        for name, (sources, multicore, _, _) in run_core_tests.FIRMWARE.items():
            for source in sources:
                self.assertTrue((ROOT / source).exists(), f"{name}: {source}")
        for start in ("start_aster.S", "start_multicore_aster.S", "aster_trap.S"):
            self.assertTrue((ROOT / "software/runtime" / start).exists(), start)


if __name__ == "__main__":
    unittest.main()
