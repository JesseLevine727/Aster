"""Synthetic record fixtures test the parser; they are not capture evidence."""
import copy
from pathlib import Path
import sys
import random
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"scripts"))
import asterbench_dma as dma


def records(size=64, alignment="aligned", caches=1, jobs=4):
    result = []
    last_bytes = last_cycles = 0
    for job in range(1, jobs+1):
        for turn in (1, 2):
            method = ((job-1)&1) ^ (turn-1)
            src, dst = dma.ALIGNMENTS[alignment]
            r = {key: 0 for key in dma.NUMBERS | dma.COUNTERS}
            r.update(version=5, name="dma_memcpy", window="setup_copy_complete", policy="prepared_reinitialize", status="PASS",
                     method="dma" if method else "cpu", order="cpu_dma" if job&1 else "dma_cpu", alignment=alignment,
                     size=size, jobs=jobs, job=job, **{"pass": turn}, base_seed=0x13570000,
                     seed=0x13570000 ^ ((job*0x9e3779b9)&dma.U32), harts=2, workers=1,
                     source_offset=src, destination_offset=dst, buffer_bytes=dma.buffer_bytes(size),
                     source_addr=0x10000000+src, destination_addr=0x10004000+dst, clock_hz=31250000,
                     l1=caches, sync_memory=1, memory_wait=1, line_words=4, line_count=16,
                     cpu_abi=4, dma_abi=1, dma_counter_abi=5, h0_cycles=1000000, h1_cycles=1000000,
                     h0_retired=100, h0_memory=20)
            if caches:
                r.update(h0_i_access=100, h0_i_miss=3, h0_d_access=10, h0_d_miss=3)
            if method:
                count = dma.transactions(size, alignment)
                last_bytes, last_cycles = size, count*10
                r.update(dma_busy=last_cycles, dma_wait=count*3, dma_reads=count, dma_writes=count,
                         dma_bytes=size, dma_backing_reads=count, dma_backing_writes=count,
                         dma_success=1, raw_dma_status=2)
            r.update(raw_dma_bytes_done=last_bytes, raw_dma_job_cycles=last_cycles,
                     output=dma.reference(size, alignment, r["seed"]).hex())
            result.append(r)
    return result


def line(record):
    return "ASTERBENCH,"+",".join(f"{key}={record[key]}" for key in sorted(record))+"\n"


def stream(rows):
    return "".join(line(r) for r in rows).encode("ascii")


class DmaRecordTests(unittest.TestCase):
    def test_independent_known_bytes_and_chunk_counts(self):
        self.assertEqual(dma.source_bytes(0, 0)[:8].hex(), "004992db246db6ff")
        self.assertEqual(dma.reference(8, "aligned", 0)[63:73].hex(), "9a4881da136ca5fe37ed")
        for size, alignment, expected in ((0,"aligned",0), (1,"aligned",1), (16,"aligned",4),
                                          (16,"same_offset",7), (16,"different_offset",16),
                                          (3,"same_offset",3), (4,"same_offset",4)):
            self.assertEqual(dma.transactions(size, alignment), expected)

    def test_fixed_sizes_alignments_caches_pairs(self):
        for size in dma.SIZES:
            for alignment in dma.ALIGNMENTS:
                for caches in (0, 1):
                    rows = records(size, alignment, caches)
                    with self.subTest(size=size, alignment=alignment, caches=caches):
                        self.assertEqual(dma.parse_stream(stream(rows)), rows)
                        summary = dma.paired_summary(rows)
                        self.assertEqual(len(summary), 4)
                        self.assertEqual(summary[0]["cpu_bytes_per_second"] is None, size == 0)

    def test_missing_unknown_duplicate_and_truncated_fields(self):
        original = records()[1]
        for field in dma.FIELDS:
            broken = dict(original); del broken[field]
            with self.subTest(field=field), self.assertRaises(ValueError): dma.parse_record(line(broken))
        for bad in (line(original).rstrip("\n"), line(original)+"\n", line(original).replace("\n", "\r\n"),
                    line(original).replace("version=5", "version=5,version=5"),
                    line(original).replace("version=5", "version=5,unknown=0"),
                    line(original).replace("version=5", "version="), "x"*65537+"\n"):
            with self.assertRaises(ValueError): dma.parse_record(bad)

    def test_numeric_domain_and_typed_boolean_rejection(self):
        row = records()[1]
        for field in dma.NUMBERS | dma.COUNTERS:
            for value in (True, -1, 1 << 65, 1.5, "0"):
                bad = dict(row); bad[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): dma.validate_record(bad)
        for value in ("-1", "+1", "05", "0X5", "0xA", "5x", "nan", "1e2", "1"*10000):
            bad = line(row).replace("version=5", "version="+value)
            with self.assertRaises(ValueError): dma.parse_record(bad)

    def test_output_and_guard_corruption(self):
        for size in (0,1,8192):
            row = records(size)[1]
            for index in (0, 2*row["destination_offset"], len(row["output"])-1):
                bad = dict(row); output = list(bad["output"])
                output[index] = "0" if output[index] != "0" else "1"; bad["output"] = "".join(output)
                with self.assertRaises(ValueError): dma.validate_record(bad)
            for output in (row["output"][:-2], row["output"]+"00", "A"*len(row["output"])):
                bad = dict(row); bad["output"] = output
                with self.assertRaises(ValueError): dma.validate_record(bad)

    def test_wrong_identity_topology_abi_buffers_and_driver(self):
        row = records()[1]
        for field, value in (("version",4), ("name","memcpy"), ("window","transfer_only"), ("policy","cold_cache"),
                             ("status","FAIL"), ("method","cpu"), ("order","dma_cpu"), ("alignment","unknown"),
                             ("workers",2), ("harts",0), ("seed",0), ("source_offset",0), ("destination_offset",67),
                             ("buffer_bytes",1024), ("source_addr",0x10008040), ("destination_addr",row["source_addr"]),
                             ("clock_hz",50000000), ("cpu_abi",5), ("dma_abi",0), ("dma_counter_abi",4),
                             ("memory_wait",0), ("line_words",3), ("line_count",0), ("errors",1), ("source_errors",1),
                             ("destination_errors",1), ("driver_result",0xfffffff7)):
            bad = dict(row); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): dma.validate_record(bad)

    def test_counter_and_payload_maintenance_mutations(self):
        row = records()[1]
        for field, value in (("h1_cycles",1), ("h0_retired",0), ("h0_memory",0), ("h1_retired",1),
                             ("h0_atomic",1), ("h0_i_miss",101), ("dma_bytes",63), ("dma_reads",15),
                             ("dma_writes",15), ("dma_success",0), ("dma_success",2), ("dma_aborts",1),
                             ("dma_errors",1), ("dma_rejected",1), ("raw_dma_status",3), ("raw_dma_bytes_done",0),
                             ("raw_dma_job_cycles",1), ("dma_wait",999999), ("dma_backing_reads",15),
                             ("dma_backing_writes",17), ("dma_dirty_words",1), ("dma_invalidations",33),
                             ("h0_backing",1000000)):
            bad = dict(row); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): dma.validate_record(bad)
        cpu = records()[0]
        for event in dma.DMA_EVENTS:
            bad = dict(cpu); bad["dma_"+event] = 1
            with self.subTest(event=event), self.assertRaises(ValueError): dma.validate_record(bad)
        bad = records(caches=0)[1]; bad["dma_backing_reads"] -= 1; bad["dma_forwards"] = 1
        with self.assertRaises(ValueError): dma.validate_record(bad)

    def test_full_stream_order_configuration_and_ack_history(self):
        rows = records()
        for bad in (rows[:-1], rows+rows, rows[2:]+rows[:2], [rows[0]]+rows[2:]+[rows[1]]):
            with self.assertRaises(ValueError): dma.parse_stream(stream(bad))
        bad = copy.deepcopy(rows); bad[3]["harts"] = 1
        dma.validate_record(bad[3])
        with self.assertRaises(ValueError): dma.parse_stream(stream(bad))
        bad = copy.deepcopy(rows); bad[3]["raw_dma_job_cycles"] += 1
        dma.validate_record(bad[3])
        with self.assertRaises(ValueError): dma.parse_stream(stream(bad))
        for raw in (b"", b"\xff\n", b"\n", stream(rows).rstrip(b"\n")):
            with self.assertRaises(ValueError): dma.parse_stream(raw)

    def test_slowdowns_and_zero_bandwidth_not_hidden(self):
        rows = records()
        for row in rows:
            if row["method"] == "dma": row["h0_cycles"] = row["h1_cycles"] = 2000000
        summary = dma.paired_summary(dma.parse_stream(stream(rows)))
        self.assertTrue(all(r["cpu_over_dma"] == 0.5 for r in summary))
        self.assertTrue(all(r["dma_bytes_per_second"] is None for r in dma.paired_summary(records(0))))
        # Raising both cycle fields can be a physically possible record. The
        # later capture validator must bind it to independent actual events.
        changed = dict(rows[0]); changed["h0_cycles"] = changed["h1_cycles"] = 3000000
        dma.validate_record(changed)

    def test_independent_cpp_python_parity_mutation_corpus(self):
        cases = []
        for size in dma.SIZES:
            for alignment in dma.ALIGNMENTS:
                for cache in (0,1):
                    for row in records(size, alignment, cache): cases.append(line(row))
        original = records()[1]; valid = line(original)
        for key in dma.FIELDS:
            bad = dict(original); del bad[key]; cases.append(line(bad))
            cases.append(valid.rstrip()+f",{key}={original[key]}\n")
        for key in dma.NUMBERS | dma.COUNTERS:
            for value in ("", "-1", "+1", "01", "0xg", "true", "nan", "1.5", "18446744073709551616", 1):
                bad = dict(original); bad[key] = value; cases.append(line(bad))
        for key, values in {"l1":[0,2], "harts":[0,3], "workers":[0,2], "line_words":[0,3,1025],
                            "line_count":[0,3,1025], "memory_wait":[0,1025], "source_addr":[0,0x10008040],
                            "destination_addr":[0,original["source_addr"]], "raw_dma_status":[0,1,3],
                            "output":[original["output"][:-1],"A"*len(original["output"])]}.items():
            for value in values:
                bad = dict(original); bad[key] = value; cases.append(line(bad))
        cases += ["", valid[:-1], valid+valid, valid.replace("\n","\r\n"), valid[:-1]+",\n", " "+valid, valid+"junk"]
        fields = valid[:-1].split(",")[1:]; random.Random(0xa57e7).shuffle(fields)
        cases.append("ASTERBENCH,"+",".join(fields)+"\n")
        # Legal 64-bit cycle counts must not wrap combined backing bounds.
        high = dict(original, h0_cycles=dma.U64, h1_cycles=dma.U64); cases.append(line(high))
        payload = "".join(f"{len(item)}\n{item}" for item in cases)
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="aster-dma-parser-") as directory:
            binary = Path(directory)/"parser"
            subprocess.run(["g++","-std=c++17","-O2","-Wall","-Wextra","-Werror",
                            str(root/"verification/host/dma_parser_cli.cpp"),"-o",str(binary)], check=True, capture_output=True)
            verdicts = subprocess.run([str(binary)], input=payload, text=True, capture_output=True, check=True).stdout.splitlines()
        self.assertEqual(len(cases), len(verdicts))
        for item, verdict in zip(cases, verdicts):
            try: dma.parse_record(item); expected = True
            except ValueError: expected = False
            with self.subTest(record=item[:150]): self.assertEqual(verdict == "PASS", expected)


if __name__ == "__main__":
    unittest.main()
