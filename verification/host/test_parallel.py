"""Strict v3 Python/C++ parity, independent reference and capture mutation tests."""
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
import asterbench_parallel as bench
import parallel_results as results
from bench_results import METADATA_FIELDS


def fixture(workers=2, job=1, jobs=1):
    f = dict(version=3, name="parallel_mix", status="PASS", bytes=256, rounds=4, jobs=jobs, job=job,
             harts=2, workers=workers, h0_words=32 if workers == 2 else 64, h1_words=32 if workers == 2 else 0,
             clock_hz=31250000, l1=1, sync_memory=0, line_words=4, line_count=16, memory_wait=0,
             base_seed=0x13570000, seed=0x13570000 ^ ((job*0x9e3779b9) & 0xffffffff), cycles=10000)
    sums = bench.reference_checksums(64, 4, f["seed"], workers)
    f.update(h0_checksum=sums[0], h1_checksum=sums[1], checksum=sum(sums) & 0xffffffff)
    events = dict(cycles=10000, retired=800, memory_transactions=1000, cache_accesses=900,
                  cache_misses=50, backing_transactions=400, dma_bytes=0, accelerator_cycles=0)
    for h in range(2):
        for event, value in events.items():
            f[f"h{h}_{event}"] = value if h < workers or event == "cycles" else 0
    return f


def emit(f):
    return "ASTERBENCH,"+",".join(f"{k}=" + (f"0x{v:08x}" if k in bench.HEX32 else
         f"0x{v:016x}" if k in bench.COUNTERS else str(v)) for k, v in f.items())+"\n"


def replace(line, key, value):
    return ",".join(f"{key}={value}" if part.startswith(key+"=") else part
                    for part in line[:-1].split(","))+"\n"


class ParallelRecords(unittest.TestCase):
    def test_python_cpp_malformed_corpus(self):
        valid = emit(fixture())
        cases = [(valid, True), (emit(fixture(1)), True)]
        shuffled = valid[:-1].split(",")[1:]
        random.Random(0xa57e).shuffle(shuffled)
        cases += [("ASTERBENCH,"+",".join(shuffled)+"\n", True)]
        fields = valid[:-1].split(",")
        for i in range(1, len(fields)):
            cases += [(",".join(fields[:i]+fields[i+1:])+"\n", False), (valid[:-1]+","+fields[i]+"\n", False)]
        for key in bench.DECIMAL + bench.HEX32 + bench.COUNTERS:
            for bad in ("", "-1", "+1", "01", " 1", "0xgg", "0x10000000000000000", "nan"):
                cases.append((replace(valid, key, bad), False))
        for key, bad in (("version", "2"), ("workers", "0"), ("harts", "1"), ("job", "0"),
                         ("job", "2"), ("jobs", "17"), ("rounds", "65"), ("bytes", "7"),
                         ("bytes", "4100"), ("status", "FAIL"), ("name", "memcpy"),
                         ("checksum", "0x00000000"), ("h0_words", "31"), ("h1_words", "33"),
                         ("seed", "0x00000000"), ("clock_hz", "0"), ("line_words", "3"),
                         ("line_count", "2048"), ("memory_wait", "1025"), ("sync_memory", "1"),
                         ("l1", "0"), ("h1_cycles", "0x000000000000270f"),
                         ("h0_retired", "0x0000000000000000"), ("h1_memory_transactions", "0x0000000000000000"),
                         ("h0_backing_transactions", "0x0000000000002710"),
                         ("h0_dma_bytes", "0x0000000000000001"), ("h1_accelerator_cycles", "0x0000000000000001")):
            cases.append((replace(valid, key, bad), False))
        cases += [(x, False) for x in ("", "ASTERBENCH\n", valid[:-1], valid+valid, " "+valid,
                   valid.replace("\n", "\r\n"), valid[:-1]+",\n", valid[:-1]+",unknown=0\n", valid+"junk")]
        inactive = emit(fixture(1))
        cases.append((replace(inactive, "h1_retired", "0x0000000000000001"), False))
        # Lookup/acceptance counters can straddle a measurement boundary.
        cases.append((replace(valid, "h0_cache_misses", "0x0000000000000400"), True))
        uncached = fixture()
        uncached["l1"] = 0
        for h in range(2): uncached[f"h{h}_cache_accesses"] = uncached[f"h{h}_cache_misses"] = 0
        cases.append((emit(uncached), True))
        payload = "".join(f"{len(line.encode())}\n{line}" for line, _ in cases)
        with tempfile.TemporaryDirectory(prefix="aster-parallel-parser-") as directory:
            binary = Path(directory) / "parser"
            subprocess.run(["g++", "-std=c++17", "-O2", str(ROOT / "verification/host/parallel_parser_cli.cpp"),
                            "-o", str(binary)], check=True, capture_output=True)
            cpp = subprocess.run([str(binary)], input=payload, text=True, capture_output=True, check=True).stdout.splitlines()
        self.assertEqual(len(cpp), len(cases))
        for (line, expected), verdict in zip(cases, cpp):
            with self.subTest(record=line[:200]):
                try: bench.parse_record(line); accepted = True
                except ValueError: accepted = False
                self.assertEqual(accepted, expected)
                self.assertEqual(verdict == "PASS", expected)

    def test_complete_job_stream_and_independent_oracle(self):
        serial = "".join(emit(fixture(job=i, jobs=3)) for i in range(1, 4))
        self.assertEqual(len(bench.parse_stream(serial)), 3)
        lines = serial.splitlines(keepends=True)
        for bad in (serial[:-1], "".join(reversed(lines)), "".join(lines[:-1]), serial+lines[-1],
                    serial.replace("base_seed=0x13570000", "base_seed=0x13570001", 1)):
            with self.assertRaises(ValueError): bench.parse_stream(bad)
        corrupt = fixture()
        corrupt["h0_checksum"] ^= 1
        corrupt["checksum"] = (corrupt["h0_checksum"]+corrupt["h1_checksum"]) & 0xffffffff
        parsed = bench.parse_record(emit(corrupt))
        with self.assertRaises(ValueError): bench.validate_reference(parsed)
        for words in (2, 7, 64, 129, 1024):
            for seed in (0, 1, 0xffffffff, 0xa57e):
                a = bench.reference_checksums(words, 4, seed, 1)
                b = bench.reference_checksums(words, 4, seed, 2)
                self.assertEqual(a[0], sum(b) & 0xffffffff)
                self.assertEqual(a[1], 0)


class ParallelProvenance(unittest.TestCase):
    def fixture(self):
        row = fixture()
        serial = emit(row)
        metadata = {k: "fixture" for k in METADATA_FIELDS}
        for k in metadata:
            if k.endswith("sha256"): metadata[k] = "1"*64
        sources = {"rtl/fixture.sv": "2"*64}
        metadata.update(revision="3"*40, dirty=True, build_command=["make", "parallel"],
                        source_files=sources, source_sha256=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest())
        observations = [dict(boot=boot, job=1, cycles=10000, kernel_overlap_cycles=301,
                            h0_kernel_retired=256, h0_kernel_first=100, h0_kernel_last=500,
                            h1_kernel_retired=256, h1_kernel_first=200, h1_kernel_last=600) for boot in range(2)]
        log = "".join(serial+"ASTEROBS,"+json.dumps(o)+"\n" for o in observations)
        log += "PASS: parallel jobs, independent kernel retirement and exact RTL counter scoreboard (2 boots)\n"
        return dict(schema="aster.parallel.capture.v1", serial_boots=[serial, serial], records=[[row], [row]],
                    observations=observations, kernel_bounds=[256, 512], metadata=metadata,
                    log_sha256=hashlib.sha256(log.encode()).hexdigest()), log

    def test_required_fields_and_raw_log_binding(self):
        original, log = self.fixture()
        results.validate_result(original, log)
        for key in results.RESULT_FIELDS:
            candidate = copy.deepcopy(original); del candidate[key]
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(candidate, log)
        for key in METADATA_FIELDS:
            candidate = copy.deepcopy(original); del candidate["metadata"][key]
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(candidate, log)
        with self.assertRaises(ValueError): results.validate_result(original, log+"changed")
        for key, value in (("h1_kernel_retired", 0), ("job", 2), ("kernel_overlap_cycles", 0),
                           ("cycles", 1), ("h0_kernel_first", 0), ("h0_kernel_last", 10001)):
            candidate = copy.deepcopy(original); candidate["observations"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): results.validate_result(candidate)
        for value in (True, 3.0):
            candidate = copy.deepcopy(original); candidate["records"][0][0]["version"] = value
            with self.assertRaises(ValueError): results.validate_result(candidate)

    def test_comparison_refuses_different_work(self):
        original, _ = self.fixture()
        self.assertEqual(results.compare(original, original)["cycle_speedup"], 1)
        changed = copy.deepcopy(original)
        # Individually valid but different rounds/results must not compare.
        for boot in range(2):
            row = fixture(); row["rounds"] = 3
            sums = bench.reference_checksums(64, 3, row["seed"], 2)
            row.update(h0_checksum=sums[0], h1_checksum=sums[1], checksum=sum(sums) & 0xffffffff)
            changed["serial_boots"][boot] = emit(row); changed["records"][boot] = [row]
        results.validate_result(changed)
        with self.assertRaises(ValueError): results.compare(original, changed)


if __name__ == "__main__": unittest.main()
