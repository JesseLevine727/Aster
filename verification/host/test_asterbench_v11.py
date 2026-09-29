"""Shared mutation corpus for the strict AsterBench v11 Python/C++ validators."""

from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v11 as bench


def make_fields(*, harts=2, dma_jobs=0, npu_jobs=0):
    fields = {
        "version": 11, "name": "conv2d_npu", "category": "dsp", "status": "PASS",
        "size": 1024, "iterations": 4, "param": 5,
        "seed": "0x13570000", "checksum": "0x07df8000",
        "clock_hz": 31_250_000, "harts": harts, "workers": 1,
        "l1": 1, "sync_memory": 0, "line_words": 4, "line_count": 16,
        "memory_wait": 0,
        "cycles": "0x00000000000003e8", "retired": "0x00000000000001f4",
        "memory_transactions": "0x0000000000000014",
        "backing_transactions": "0x0000000000000005",
        "cache_accesses": "0x0000000000000028", "cache_misses": "0x0000000000000002",
        "dma_jobs": dma_jobs, "dma_completed_jobs": dma_jobs,
        "dma_aborted_jobs": 0, "dma_error_jobs": 0,
        "dma_bytes": f"0x{64 * dma_jobs:016x}",
        "dma_job_cycles": f"0x{10 * dma_jobs:016x}",
        "h0_dot8_accept": "0x0000000000000002",
        "h0_dot8_wait": "0x0000000000000005",
        "h0_dot8_complete": "0x0000000000000002",
        "h0_dot8_retire": "0x0000000000000002",
        "h1_dot8_accept": "0x0000000000000000",
        "h1_dot8_wait": "0x0000000000000000",
        "h1_dot8_complete": "0x0000000000000000",
        "h1_dot8_retire": "0x0000000000000000",
        "npu_jobs": npu_jobs, "npu_completed_jobs": npu_jobs,
        "npu_aborted_jobs": 0, "npu_error_jobs": 0,
        "npu_bytes_read": f"0x{32 * npu_jobs:016x}",
        "npu_bytes_written": f"0x{16 * npu_jobs:016x}",
        "npu_tiles": f"0x{npu_jobs:016x}",
        "npu_job_cycles": f"0x{20 * npu_jobs:016x}",
        "npu_compute_cycles": f"0x{12 * npu_jobs:016x}",
    }
    if npu_jobs:
        fields["name"] = "conv2d_npu"
    return fields


def render(fields):
    return "ASTERBENCH," + ",".join(f"{key}={value}" for key, value in fields.items()) + "\n"


def set_value(line, key, value):
    fields = line[:-1].split(",")
    return ",".join(f"{key}={value}" if field.startswith(key + "=") else field
                    for field in fields) + "\n"


def corpus():
    default = make_fields()
    dma = make_fields(dma_jobs=2)
    npu = make_fields(npu_jobs=4)
    mixed = make_fields(dma_jobs=2, npu_jobs=4)
    valid = [render(fields) for fields in (default, dma, npu, mixed)]
    reordered = render(dict(reversed(list(default.items()))))
    valid.append(reordered)

    invalid = []
    for key in bench.ALL_FIELDS:
        fields = make_fields()
        del fields[key]
        invalid.append(render(fields))
    for key, value in {
        "version": 10, "name": "Bad Name", "category": "unknown", "status": "FAIL",
        "size": 3, "iterations": 0, "param": 0, "clock_hz": 0,
        "harts": 3, "workers": 0, "l1": 2, "sync_memory": 2,
        "line_words": 3, "line_count": 0, "memory_wait": 1025,
        "cycles": "0x0000000000000000", "retired": "0x00000000000003e9",
        "memory_transactions": "0x00000000000003e9",
        "backing_transactions": "0x00000000000003e9",
        "cache_accesses": "0x00000000000003e9",
        "cache_misses": "0x0000000000000029",
        "h0_dot8_wait": "0x00000000000003e9",
        "h0_dot8_complete": "0x0000000000000001",
        "dma_completed_jobs": 1, "dma_aborted_jobs": 1,
        "dma_bytes": "0x0000000000000001", "dma_job_cycles": "0x00000000000003e9",
        "npu_completed_jobs": 3, "npu_compute_cycles": "0x0000000000000051",
        "npu_job_cycles": "0x00000000000003e9",
        "npu_bytes_read": "0x0000000000000001",
    }.items():
        invalid.append(set_value(render(make_fields()), key, value))
    absent_hart = make_fields(harts=1)
    absent_hart["h1_dot8_wait"] = "0x0000000000000001"
    invalid.append(render(absent_hart))
    invalid.extend((
        "ASTERBENCH,\n",
        "ASTERBENCH,version=11",
        render(default)[:-1],
        render(default).replace("\n", "\r\n"),
        render(default)[:-1] + ",\n",
        " " + render(default),
        render(default) + render(default),
        set_value(render(default), "version", "011"),
        set_value(render(default), "seed", "0xABCDEF12"),
        set_value(render(default), "cycles", "0x10000000000000000"),
    ))
    body = render(default)[len("ASTERBENCH,"):-1]
    invalid.append("ASTERBENCH," + body.split(",", 1)[0] + "," + body + "\n")
    invalid.append(render(default).replace("ASTERBENCH,", "UNKNOWN,", 1))
    return [(line, True) for line in valid] + [(line, False) for line in invalid]


class AsterBenchV11(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory(prefix="asterbench-v11-")
        cls.binary = Path(cls.tempdir.name) / "parser"
        subprocess.run([
            "g++", "-std=c++17", "-O2",
            str(ROOT / "verification/host/asterbench_v11_parser_cli.cpp"),
            "-o", str(cls.binary),
        ], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_valid_records_and_cumulative_totals(self):
        parsed = bench.validate_line(render(make_fields(dma_jobs=2, npu_jobs=4)))
        self.assertEqual(parsed["dma_jobs"], 2)
        self.assertEqual(parsed["dma_bytes"], 128)
        self.assertEqual(parsed["npu_jobs"], 4)
        self.assertEqual(parsed["npu_bytes_read"], 128)
        self.assertEqual(parsed["npu_job_cycles"], 80)

    def test_python_and_cpp_share_strict_mutation_corpus(self):
        cases = corpus()
        payload = "".join(f"{len(line.encode())}\n{line}" for line, _ in cases)
        result = subprocess.run([str(self.binary)], input=payload, text=True,
                                capture_output=True, check=True)
        verdicts = result.stdout.splitlines()
        self.assertEqual(len(verdicts), len(cases))
        for (line, expected), cpp in zip(cases, verdicts):
            with self.subTest(record=line[:160]):
                try:
                    bench.validate_line(line)
                    python = True
                except ValueError:
                    python = False
                self.assertEqual(python, expected)
                self.assertEqual(cpp == "PASS", expected)


if __name__ == "__main__":
    unittest.main()
