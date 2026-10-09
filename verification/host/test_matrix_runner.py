"""20.4's runner (scripts/matrix.py): its cold/warm pair check, its totals reconciliation and its MNIST weights
header; and the overlap gate's classes (scripts/matrix_overlap.py)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import matrix  # noqa: E402
import matrix_overlap  # noqa: E402


def entry(out: Path, ident: str, state: str, image: bytes, word) -> dict:
    path = out / f"{ident}-{state}.bin"
    path.write_bytes(image)
    return dict(id=f"{ident}/{state}", family="f", case=ident, method="scalar", sim="soc_dev", status="captured",
                axes=dict(memory_wait=2, cache_state=state), firmware_bin=path.name, cold_word=word)


class ColdWarmPairs(unittest.TestCase):
    def check(self, warm: bytes, cold: bytes, warm_word=8, cold_word=8):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            results = [entry(out, "a", "warm", warm, warm_word), entry(out, "a", "cold", cold, cold_word)]
            return matrix.cold_warm_pairs(results, out), results

    def test_only_the_cold_word(self):
        base = bytes(range(32))
        cold = bytearray(base); cold[8] = 1
        pairs, results = self.check(base, bytes(cold))
        self.assertEqual(pairs, dict(checked=1, identical=1, differ=[]))
        self.assertTrue(all(r["status"] == "captured" for r in results))

    def test_rejected(self):
        base = bytes(range(32))
        outside = bytearray(base); outside[8] = 1; outside[20] = 0xFF            # a second byte elsewhere
        nothing = base                                                           # the word the same
        for cold, kw in ((bytes(outside), {}), (nothing, {}), (base + b"\0", {}),  # a size change
                         (bytes(outside[:8]) + b"\1" + base[9:], dict(cold_word=None)),
                         (bytes(outside[:8]) + b"\1" + base[9:], dict(warm_word=12))):
            pairs, results = self.check(base, cold, **kw)
            self.assertEqual((pairs["checked"], pairs["identical"]), (1, 0), (cold, kw))
            self.assertTrue(all(r["status"] == "failed" for r in results))

    def test_unpaired_entries_are_not_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            results = [entry(out, "a", "warm", b"\0" * 8, 4), entry(out, "b", "cold", b"\1" * 8, 4)]
            self.assertEqual(matrix.cold_warm_pairs(results, out), dict(checked=0, identical=0, differ=[]))


class Totals(unittest.TestCase):
    """reconcile_totals: the NPU's bytes against the fabric's, and a one-job window against its job's counters."""
    @staticmethod
    def pair():
        record = dict(window="e2e", npu_port_bytes=8, npu_bytes_read=8192, npu_bytes_written=16384, f_accepted_n=5120,
                      f_waited_n=0, npu_jobs=1, npu_job_cycles=5231, npu_active_cycles=4096, npu_macs=262144,
                      dma_jobs=1, dma_bytes=4096, dma_busy_cycles=518, f_accepted_r=256, f_waited_r=3,
                      f_accepted_w=256, f_waited_w=5)
        job = dict(npu_job_cycles=5231, npu_active_cycles=4096, npu_macs=262144, npu_bytes_read=8192,
                   npu_bytes_written=16384, dma_job_cycles=518, dma_bytes_done=4096)
        return record, job

    @staticmethod
    def line(job: dict) -> str:
        return "MATRIX_JOBS," + ",".join(f"{k}={v}" for k, v in job.items())

    def test_reconciled(self):
        record, job = self.pair()
        self.assertEqual(matrix.reconcile_totals([record], [self.line(job)]),
                         dict(npu_fabric=1, fabric_bounds=1, npu_one_job=1, dma_one_job=1))

    def test_rejected(self):
        plants = [("record", "f_accepted_n", 5121), ("record", "npu_bytes_read", 8196),   # (not a whole read)
                  ("record", "npu_bytes_written", 16388), ("record", "f_waited_n", 112),       # (past the job)
                  ("record", "f_waited_r", 263), ("record", "f_waited_w", 263)]
        plants += [("job", k, v + 1) for k, v in self.pair()[1].items()]
        for where, key, value in plants:
            record, job = self.pair()
            (record if where == "record" else job)[key] = value
            with self.assertRaises(matrix.asterbench_v12.ValidationError, msg=(where, key)):
                matrix.reconcile_totals([record], [self.line(job)])
        record, job = self.pair()
        with self.assertRaises(matrix.asterbench_v12.ValidationError):                 # a record without its line
            matrix.reconcile_totals([record, record], [self.line(job)])

    def test_several_jobs_use_the_fabric_alone(self):
        record, job = self.pair()
        record.update(npu_jobs=2, dma_jobs=3)
        job.update(npu_macs=1, dma_bytes_done=1)                                       # (the last job's alone)
        self.assertEqual(matrix.reconcile_totals([record], [self.line(job)]),
                         dict(npu_fabric=1, fabric_bounds=1, npu_one_job=0, dma_one_job=0))
        record.update(dma_bytes=8 * 518 + 1)                                           # (bounded, several jobs too)
        with self.assertRaises(matrix.asterbench_v12.ValidationError):
            matrix.reconcile_totals([record], [self.line(job)])


class Overlap(unittest.TestCase):
    """matrix_overlap.analyse: each two-worker record classed from its harts' intervals (matrix.md §10.7), and
    paired with the one-worker record of the same kernel, case, axes and window."""
    @staticmethod
    def run_dir(out: Path, rows: list) -> Path:
        entries = []
        for n, row in enumerate(rows):
            family, case, method, workers, cycles, h0, h1 = row[:7]
            dot8 = row[7] if len(row) > 7 else 0
            state = row[8] if len(row) > 8 else "warm"
            line = ("ASTERBENCH," + ",".join(f"{k}={v}" for k, v in dict(
                name=case, window="e2e", h0_cycles=cycles, h0_retired=10, h1_retired=10 if workers == 2 else 0,
                h0_work_start=h0[0], h0_work_end=h0[1], h1_work_start=h1[0], h1_work_end=h1[1],
                h0_dot8_retire=dot8, h1_dot8_retire=dot8 if workers == 2 else 0).items()))
            (out / f"{n}.record").write_text(line + "\n")
            entries.append(dict(id=f"{family}/{case}/{method}/soc_dev/{state}/{n}", family=family, case=case,
                                method=method, sim="soc_dev", status="captured", records=f"{n}.record",
                                axes=dict(workers=workers, cache_state=state)))
        (out / "manifest.json").write_text(matrix_overlap.json.dumps(dict(entries=entries, source=dict(revision="r"))))
        return out

    def classes(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            result = matrix_overlap.analyse([self.run_dir(Path(tmp), rows)])
        return result, {r["id"].split("/")[1]: r for r in result["rows"]}

    def test_classes_and_twins(self):
        result, by = self.classes([
            ("f", "gemm", "dot8", 1, 1000, (0, 1000), (0, 0), 5), ("f", "gemm", "scalar", 1, 4000, (0, 4000), (0, 0)),
            ("f", "gemm", "multicore", 2, 520, (0, 500), (20, 510), 5),      # stamped; against DOT8: 1.92x
            ("f", "small", "multicore", 2, 300, (0, 60), (200, 290)),        # hart 0 done before hart 1 starts
            ("f", "tiny", "multicore", 2, 200, (0, 100), (70, 130)),         # 30 cycles: within hart 1's stamps
            ("f", "red", "scalar", 1, 1000, (0, 1000), (0, 0)),
            ("f", "red", "multicore", 2, 600, (0, 600), (10, 500)),          # spans hart 0's wait; 1.67x
            ("f", "slow", "scalar", 1, 500, (0, 500), (0, 0)),
            ("f", "slow", "multicore", 2, 600, (0, 600), (10, 500)),         # spans; slower than one worker
            ("f", "pipe", "pipeline", 2, 700, (0, 700), (30, 680)),          # spans; no twin
            ("dma", "overlap_h1", "dma", 2, 900, (0, 0), (100, 800)), ("dma", "overlap_serial_h1", "dma", 2, 1500,
                                                                           (0, 0), (800, 1400))])
        self.assertEqual(result["classes"], {"overlap, stamped": 1, "no overlap": 2, "overlap, by speedup": 1,
                                             "not shown, spans hart 0's wait": 2, "engine overlap": 1,
                                             "serial by design": 1})
        self.assertEqual((by["gemm"]["twin"], by["gemm"]["speedup"], by["gemm"]["overlap"]), ("dot8", 1.9231, 480))
        self.assertEqual((by["red"]["cls"], by["red"]["speedup"]), ("overlap, by speedup", 1.6667))
        self.assertEqual(by["overlap_h1"]["speedup"], 1.6667)
        self.assertEqual(result["faults"], [])

    def test_a_twin_must_match_every_axis(self):
        _, by = self.classes([("f", "red", "scalar", 1, 1000, (0, 1000), (0, 0), 0, "cold"),
                              ("f", "red", "multicore", 2, 600, (0, 600), (10, 500))])
        self.assertEqual((by["red"]["cls"], by["red"]["speedup"]), ("not shown, spans hart 0's wait", None))

    def test_faults(self):
        result, _ = self.classes([("f", "a", "multicore", 2, 600, (0, 0), (10, 500)),     # hart 0 unstamped
                                  ("f", "b", "multicore", 2, 600, (0, 300), (0, 0))])     # hart 1 unstamped
        self.assertEqual(len(result["faults"]), 2)


class MnistWeights(unittest.TestCase):
    def test_header_is_the_models(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts/gen_mnist_transposed.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
