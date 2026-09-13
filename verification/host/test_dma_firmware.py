"""Actual GCC-produced functional ELF/ROM audit, separate from synthetic fixtures."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))
from coherent_elf import inspect_elf
from run_dma_linux import command as linux_command


class DmaFirmware(unittest.TestCase):
    def test_linux_runner_uses_audited_elf_and_canonical_rom(self):
        with tempfile.TemporaryDirectory(prefix="aster-dma-linux-elf-") as directory:
            build = Path(directory)
            image = build/"software/dma_b64_aaligned_j4_s0x13570000/dma.hex"
            subprocess.run(["make", "--no-print-directory", f"BUILD_DIR={build}", str(image)],
                           cwd=ROOT, check=True, capture_output=True)
            args = SimpleNamespace(simulator=Path("/fixture/simulator"), elf=image.with_suffix(".elf"),
                                   firmware=image, size=64, alignment=0, jobs=4, boots=2, seed=0x13570000)
            found = inspect_elf(args.elf.read_bytes(), profile="dma_benchmark", dma_size=64, dma_jobs=4)["symbols"]
            kernel = found["aster_dma_cpu_memcpy"]
            expected = [args.simulator, image, 64, 0, 4, 2, 0x13570000, kernel["address"], kernel["address"]+kernel["size"],
                        found["aster_dma_bench_results"]["address"], found["aster_dma_bench_source"]["address"],
                        found["aster_dma_bench_destination"]["address"]]
            self.assertEqual(linux_command(args), list(map(str, expected)))
            for key, bad in (("size", True), ("size", 8193), ("alignment", 3), ("jobs", 0), ("jobs", 3),
                             ("boots", 0), ("boots", 17), ("seed", -1), ("seed", 1 << 32)):
                changed = SimpleNamespace(**vars(args)); setattr(changed, key, bad)
                with self.subTest(key=key, value=bad), self.assertRaises(ValueError): linux_command(changed)
            original = image.read_bytes()
            for bad in (original[:-9], original+b"00000000\n", original.upper(),
                        b"ffffffff\n"+original[9:], original.replace(b"\n", b"\r\n")):
                image.write_bytes(bad)
                with self.assertRaises(ValueError): linux_command(args)
            image.write_bytes(original)
            args.elf.write_bytes(args.elf.read_bytes()[:64])
            with self.assertRaises(ValueError): linux_command(args)

    def test_actual_functional_layout_and_distinct_profiles(self):
        with tempfile.TemporaryDirectory(prefix="aster-dma-firmware-audit-") as directory:
            build = Path(directory)
            targets = [build/"software"/(name+".hex") for name in ("dma_runtime", "dma_publication", "dma_stop_fixture")]
            subprocess.run(["make","--no-print-directory",f"BUILD_DIR={build}", *map(str,targets)], cwd=ROOT, check=True, capture_output=True)
            for path in targets:
                with self.subTest(profile=path.stem):
                    data = path.with_suffix(".elf").read_bytes(); found = inspect_elf(data, profile=path.stem)
                    rom = b"".join(int(word,16).to_bytes(4,"little") for word in path.read_text().splitlines())
                    self.assertEqual(found["image"],rom)
                    for wrong in ("runtime","lifecycle", *({"dma_runtime","dma_publication","dma_stop_fixture"}-{path.stem})):
                        with self.assertRaises(ValueError): inspect_elf(data,profile=wrong)
                    for options in (dict(dma_size=64),dict(dma_jobs=4)):
                        with self.assertRaises(ValueError): inspect_elf(data,profile=path.stem,**options)
                    if path.stem == "dma_publication":
                        self.assertEqual(found["symbols"]["publication_results"],dict(address=0x10008000,size=512))
                        for name in ("code","staging"): self.assertEqual(found["symbols"][name]["address"]%64,0)
                        self.assertGreater(found["symbols"]["execute_code"]["size"],0)


if __name__ == "__main__":
    unittest.main()
