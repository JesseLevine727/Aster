"""Pure host checks for the generic AsterBench v10 workload contract."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v10 as bench


def make_fields(name="strided", category="memory", size=4096, iterations=4,
                seed=0x13570000, checksum=0x12345678):
    return {
        "name": name, "category": category, "status": "PASS", "version": 10,
        "size": size, "iterations": iterations, "param": 16, "clock_hz": 31_250_000,
        "l1": 1, "sync_memory": 0, "line_words": 4, "line_count": 16, "memory_wait": 0,
        "seed": f"0x{seed:08x}", "checksum": f"0x{checksum:08x}",
        "cycles": f"0x{100000:016x}", "retired": f"0x{50000:016x}",
        "memory_transactions": f"0x{40000:016x}", "backing_transactions": f"0x{1000:016x}",
        "cache_accesses": f"0x{500:016x}", "cache_misses": f"0x{50:016x}",
        "dma_bytes": "0x" + "0" * 16, "accelerator_cycles": "0x" + "0" * 16,
    }


def render(fields):
    return "ASTERBENCH," + ",".join(f"{key}={fields[key]}" for key in fields) + "\n"


class AsterBenchV10(unittest.TestCase):
    def test_valid_record(self):
        result = bench.validate_line(render(make_fields()), name="strided", category="memory")
        self.assertEqual(result["name"], "strided")

    def test_parser_rejects_truncation_and_unknown_fields(self):
        for line in ("ASTERBENCH,\n", "ASTERBENCH,unknown=0\n", "ASTERBENCH,version=10"):
            with self.subTest(line=line), self.assertRaises(bench.ValidationError):
                bench._parse_fields(line)

    def test_validator_rejects_mutations(self):
        mutations = {
            "version": 9, "status": "FAIL", "name": "Bad Name", "category": "bogus",
            "size": 3, "iterations": 0, "param": 0, "l1": 7, "line_words": 3,
            "retired": f"0x{200000:016x}", "cache_misses": f"0x{999:016x}",
            "memory_wait": 2000,
        }
        for key, value in mutations.items():
            fields = make_fields()
            fields[key] = value
            with self.subTest(key=key), self.assertRaises(bench.ValidationError):
                bench.validate_line(render(fields))

    def test_duplicate_and_missing_fields(self):
        fields = make_fields()
        body = render(fields)[len("ASTERBENCH,"):-1]
        first = body.split(",", 1)[0]
        with self.assertRaises(bench.ValidationError):
            bench.validate_line("ASTERBENCH," + first + "," + body + "\n")
        trimmed = render({key: value for key, value in fields.items() if key != "cycles"})
        with self.assertRaises(bench.ValidationError):
            bench.validate_line(trimmed)


if __name__ == "__main__":
    unittest.main()
