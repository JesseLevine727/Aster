"""Run identical valid/malformed records against Python and C++ validators."""
import importlib.util
import copy
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bench_results
import run_pynq
spec = importlib.util.spec_from_file_location("asterbench", ROOT / "scripts/asterbench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

VALID = ("ASTERBENCH,version=2,name=memcpy,bytes=256,repetitions=4,seed=0x13570000,"
         "status=PASS,checksum=0x12345678,clock_hz=31250000,l1=1,sync_memory=0,"
         "line_words=4,line_count=16,memory_wait=0,cycles=0x0000000000001000,"
         "retired=0x0000000000000400,memory_transactions=0x0000000000000600,"
         "backing_transactions=0x0000000000000300,cache_accesses=0x0000000000000600,"
         "cache_misses=0x0000000000000100,dma_bytes=0x0000000000000000,"
         "accelerator_cycles=0x0000000000000000\n")


def replace(line, key, value):
    fields = line[:-1].split(",")
    return ",".join(f"{key}={value}" if field.startswith(key+"=") else field
                    for field in fields) + "\n"


class StrictRecords(unittest.TestCase):
    def test_common_corpus(self):
        cases = [(VALID, True)]
        fields = VALID[:-1].split(",")
        reordered = fields[1:]
        random.Random(0xa57e).shuffle(reordered)
        cases.append(("ASTERBENCH," + ",".join(reordered) + "\n", True))
        uncached = replace(replace(replace(VALID, "l1", "0"), "cache_accesses",
                                   "0x0000000000000000"), "cache_misses", "0x0000000000000000")
        cases.append((uncached, True))
        for index in range(1, len(fields)):
            cases.append((",".join(fields[:index] + fields[index+1:]) + "\n", False))
            cases.append((VALID[:-1] + "," + fields[index] + "\n", False))
        for key in bench.DECIMAL + bench.HEX32 + bench.COUNTERS:
            for bad in ("", "-1", "+1", " 1", "0xg", "0x10000000000000000", "nan"):
                cases.append((replace(VALID, key, bad), False))
        for key, value in [("version", "1"), ("version", "4294967296"), ("name", ""),
                           ("name", "mem copy"), ("name", "../memcpy"), ("status", "FAIL"),
                           ("bytes", "0"), ("bytes", "3"), ("bytes", "65540"), ("bytes", "0256"),
                           ("repetitions", "0"), ("clock_hz", "0"), ("l1", "2"),
                           ("sync_memory", "2"), ("line_words", "3"), ("line_count", "0"),
                           ("line_count", "2048"), ("memory_wait", "1025"),
                           ("cycles", "0x0000000000000000"), ("retired", "0x0000000000001001"),
                           ("memory_transactions", "0x0000000000000000"),
                           ("backing_transactions", "0x0000000000001001"),
                           ("cache_misses", "0x0000000000000601"),
                           ("dma_bytes", "0x0000000000000001"),
                           ("accelerator_cycles", "0x0000000000000001")]:
            cases.append((replace(VALID, key, value), False))
        cases += [(line, False) for line in ["", "ASTERBENCH\n", VALID[:-1], VALID+VALID,
                  VALID.replace("\n", "\r\n"), VALID[:-1]+",\n", VALID[:-1]+",extra=1\n",
                  VALID.replace("version=2", "version==2"), " " + VALID, VALID + "trailing\n",
                  replace(VALID, "sync_memory", "1"), replace(VALID, "l1", "0"),
                  VALID.replace("name=memcpy", "name=" + "x"*4096)]]
        payload = "".join(f"{len(line.encode())}\n{line}" for line, _ in cases)
        with tempfile.TemporaryDirectory(prefix="aster-parser-") as directory:
            executable = Path(directory) / "parser"
            subprocess.run(["g++", "-std=c++17", "-O2", str(ROOT / "verification/host/bench_parser_cli.cpp"),
                            "-o", str(executable)], check=True, capture_output=True)
            result = subprocess.run([str(executable)], input=payload, text=True,
                                    capture_output=True, check=True)
        verdicts = result.stdout.splitlines()
        self.assertEqual(len(verdicts), len(cases))
        for (line, expected), cpp in zip(cases, verdicts):
            with self.subTest(record=line[:160]):
                try:
                    bench.parse_record(line)
                    python = True
                except ValueError:
                    python = False
                self.assertEqual(python, expected)
                self.assertEqual(cpp == "PASS", expected)


class ResultProvenance(unittest.TestCase):
    def test_board_output_validation_uses_strict_parser(self):
        self.assertIsNone(run_pynq.validate_output(b"Hello from Aster\n", "hello"))
        stress = b"UART STRESS BEGIN\n" + bytes(33 + i % 90 for i in range(1024)) + b"\nUART STRESS PASS\n"
        self.assertIsNone(run_pynq.validate_output(stress, "stress"))
        self.assertEqual(run_pynq.validate_output(VALID.encode(), "bench"), bench.parse_record(VALID))
        for line in [VALID.replace(",retired=0x0000000000000400", ""), VALID[:-1], VALID+VALID]:
            with self.assertRaises(ValueError):
                run_pynq.validate_output(line.encode(), "bench")
        with self.assertRaises(RuntimeError):
            run_pynq.validate_output(b"Hello from Aster\nextra", "hello")

    def fixture(self):
        sources = {"rtl/fixture.sv": "1"*64}
        metadata = {key: "fixture" for key in bench_results.METADATA_FIELDS}
        for key in metadata:
            if key.endswith("sha256"):
                metadata[key] = "2"*64
        metadata.update(revision="3"*40, dirty=True, build_command=["make", "bench"],
                        source_files=sources,
                        source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest())
        return {"schema": 1, "serial_record": VALID, "record": bench.parse_record(VALID), "metadata": metadata}

    def test_required_provenance(self):
        valid = self.fixture()
        bench_results.validate_result(valid)
        for key in bench_results.METADATA_FIELDS:
            candidate = copy.deepcopy(valid)
            del candidate["metadata"][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                bench_results.validate_result(candidate)
        for key, value in [("revision", 12), ("dirty", "yes"), ("source_sha256", "0"*64),
                           ("source_files", {}), ("build_command", []), ("compiler_sha256", "bad")]:
            candidate = copy.deepcopy(valid)
            candidate["metadata"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                bench_results.validate_result(candidate)

    def test_serial_and_typed_record_must_agree(self):
        for value in [1, 4096.0, True]:
            candidate = self.fixture()
            candidate["record"]["cycles"] = value
            with self.assertRaises(ValueError):
                bench_results.validate_result(candidate)

    def test_comparison_contract(self):
        a, b = self.fixture(), self.fixture()
        self.assertEqual(bench_results.compare(a, b)["cycle_speedup"], 1)
        b["serial_record"] = replace(VALID, "cycles", "0x0000000000002000")
        b["record"] = bench.parse_record(b["serial_record"])
        self.assertEqual(bench_results.compare(a, b)["cycle_speedup"], 0.5)
        for key, value in [("bytes", "512"), ("repetitions", "5"), ("seed", "0x00000000"),
                           ("checksum", "0x00000000"), ("name", "different")]:
            b["serial_record"] = replace(VALID, key, value)
            b["record"] = bench.parse_record(b["serial_record"])
            with self.subTest(key=key), self.assertRaises(ValueError):
                bench_results.compare(a, b)


if __name__ == "__main__":
    unittest.main()
