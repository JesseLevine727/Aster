"""20.4's runner (scripts/matrix.py): its cold/warm pair check, its totals reconciliation and its MNIST weights
header; the overlap gate's classes (scripts/matrix_overlap.py); two captures compared (scripts/matrix_compare.py);
the scaling gate (scripts/matrix_gates.py) and the bundle's refusals (scripts/matrix_bundle.py)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import matrix  # noqa: E402
import matrix_bundle  # noqa: E402
import matrix_compare  # noqa: E402
import matrix_gates  # noqa: E402
import matrix_golden  # noqa: E402
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

    def test_shares_and_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_dir(Path(tmp), [
                ("f", "red", "scalar", 1, 1000, (0, 1000), (0, 0)),
                ("f", "red", "multicore", 2, 600, (0, 600), (10, 500)),           # spans; its shares overlap
                ("f", "cnn", "npu", 1, 900, (0, 900), (0, 0)),
                ("f", "cnn__2h", "npu", 2, 600, (0, 600), (10, 590))])            # a tuned variant, spans
            manifest = matrix_overlap.json.loads((out / "manifest.json").read_text())
            manifest["entries"][1]["extras"] = ["MATRIX_SHARE,window=e2e,h0_begin=100,h0_end=400,h1_begin=120,h1_end=420"]
            manifest["entries"][3]["extras"] = ["MATRIX_SHARE,window=e2e,h0_begin=100,h0_end=130,h1_begin=120,h1_end=420"]
            (out / "manifest.json").write_text(matrix_overlap.json.dumps(manifest))
            result = matrix_overlap.analyse([out])
        by = {r["id"].split("/")[1]: r for r in result["rows"]}
        self.assertEqual((by["red"]["cls"], by["red"]["overlap"]), ("overlap, stamped shares", 280))
        self.assertEqual(by["cnn__2h"]["cls"], "overlap, by speedup")       # (its shares overlap 10: not shown)
        self.assertEqual((by["cnn__2h"]["twin"], by["cnn__2h"]["speedup"]), ("cnn", 1.5))

    def test_a_variant_of_a_two_worker_original_is_no_overlap_proof(self):
        _, by = self.classes([("f", "red", "multicore", 2, 900, (0, 900), (10, 800)),
                              ("f", "red__place", "multicore", 2, 600, (0, 600), (10, 500))])   # (faster: tuning won)
        self.assertEqual((by["red__place"]["cls"], by["red__place"]["speedup"], by["red__place"]["tuned_speedup"]),
                         ("not shown, spans hart 0's wait", None, 1.5))

    def test_a_twin_must_match_every_axis(self):
        _, by = self.classes([("f", "red", "scalar", 1, 1000, (0, 1000), (0, 0), 0, "cold"),
                              ("f", "red", "multicore", 2, 600, (0, 600), (10, 500))])
        self.assertEqual((by["red"]["cls"], by["red"]["speedup"]), ("not shown, spans hart 0's wait", None))

    def test_faults(self):
        result, _ = self.classes([("f", "a", "multicore", 2, 600, (0, 0), (10, 500)),     # hart 0 unstamped
                                  ("f", "b", "multicore", 2, 600, (0, 300), (0, 0))])     # hart 1 unstamped
        self.assertEqual(len(result["faults"]), 2)


class Compare(unittest.TestCase):
    """matrix_compare.identical: two captures of one build, entry by entry."""
    @staticmethod
    def capture(out: Path, cycles: int, firmware: str = "f0") -> Path:
        out.mkdir()
        (out / "a.record").write_text(f"ASTERBENCH,name=a,window=e2e,h0_cycles={cycles}\n")
        entries = [dict(id="f/a/scalar/soc_dev/warm", status="captured", records="a.record", firmware_sha256=firmware,
                        sim_sha256="s0"), dict(id="f/b/scalar/soc_h1/warm", status="unsupported")]
        (out / "manifest.json").write_text(matrix_overlap.json.dumps(dict(entries=entries)))
        return out

    def test_identical_and_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self.capture(Path(tmp) / "a", 100)
            self.assertEqual(matrix_compare.identical(a, self.capture(Path(tmp) / "b", 100)), (1, []))
            self.assertEqual(len(matrix_compare.identical(a, self.capture(Path(tmp) / "c", 101))[1]), 1)
            self.assertEqual(len(matrix_compare.identical(a, self.capture(Path(tmp) / "d", 100, "f1"))[1]), 1)
            self.assertIn("e2e 100 -> 101", matrix_compare.moved(a, Path(tmp) / "c"))


def scaling_run(out: Path, family: str, revision: str = "r", dirty: bool = False, one: int = 1000,
                two: int = 520) -> Path:
    """A run of the gate's reduction on one and two workers, as the runner writes it."""
    (out / "records").mkdir(parents=True)
    (out / "console").mkdir()
    entries = []
    for workers, method, cycles in ((1, "scalar", one), (2, "multicore", two)):
        name = f"{family}__reduce_fill__{method}"
        line = (f"ASTERBENCH,name=reduce_fill,window=e2e,h0_cycles={cycles},h0_retired=10,h1_retired="
                f"{10 if workers == 2 else 0},h0_work_start=0,h0_work_end={cycles},h1_work_start=5,"
                f"h1_work_end={cycles - 20},h0_dot8_retire=0,h1_dot8_retire=0,npu_jobs=0\n")
        (out / "records" / f"{name}.record").write_text(line)
        (out / "console" / f"{name}.console").write_text(line)
        entries.append(dict(id=f"{family}/reduce_fill/{method}/soc_dev/warm", family=family, case="reduce_fill",
                            method=method, sim="soc_dev", status="captured", records=f"records/{name}.record",
                            axes=dict(workers=workers, cache_state="warm"), totals=dict(npu_fabric=1)))
    sample = dict(checked=1, identical=1, differ=[])
    (out / "manifest.json").write_text(matrix_overlap.json.dumps(dict(
        schema=matrix_bundle.SCHEMA, created="t", source=dict(revision=revision, dirty=dirty, changed=[]),
        toolchain=dict(gcc="g"), families=[family], counts=dict(captured=2, failed=0, unsupported=0, planned=0),
        determinism=sample, cold_warm_pairs=sample, seconds=1, entries=entries)))
    return out


class Gates(unittest.TestCase):
    def test_scaling(self):
        with tempfile.TemporaryDirectory() as tmp:
            allrec = matrix_overlap.records_of(scaling_run(Path(tmp), "coherence"))
            s = matrix_gates.scaling(allrec)["reduce_fill"]
            self.assertEqual((s["pairs"], s["e2e_min"], s["meets"]), (1, 1.9231, True))
            allrec = matrix_overlap.records_of(scaling_run(Path(tmp) / "slow", "coherence", two=600))
            self.assertFalse(matrix_gates.scaling(allrec)["reduce_fill"]["meets"])             # 1.67x


class Bundle(unittest.TestCase):
    def bundle(self, *runs) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            argv = sys.argv
            sys.argv = ["matrix_bundle.py", *map(str, runs), "--out", str(Path(tmp) / "out")]
            try:
                return matrix_bundle.main()
            finally:
                sys.argv = argv

    def test_one_clean_capture_or_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            self.assertEqual(self.bundle(scaling_run(t / "a", "coherence"), scaling_run(t / "b", "dsp")), 0)
            self.assertEqual(self.bundle(scaling_run(t / "c", "coherence"), scaling_run(t / "d", "dsp", "r2")), 1)
            self.assertEqual(self.bundle(scaling_run(t / "e", "coherence", dirty=True)), 1)
            self.assertEqual(self.bundle(scaling_run(t / "f", "coherence"), scaling_run(t / "g", "coherence")), 1)

    def test_section_7_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            argv = sys.argv
            sys.argv = ["matrix_bundle.py", str(scaling_run(Path(tmp) / "a", "coherence")), "--out", str(out)]
            try:
                self.assertEqual(matrix_bundle.main(), 0)
            finally:
                sys.argv = argv
            entries = [matrix_overlap.json.loads(l.strip().rstrip(",")) for l in (out / "manifest.json").open()
                       if l.strip().startswith('{"')]
        unsupported = sorted(e["id"] for e in entries if e["status"] == "unsupported")
        # the three configurations for each of the two captured methods, and the DMA for the case; no NPU ran
        self.assertEqual(unsupported, sorted([f"coherence/reduce_fill/{m}/{sim}/warm" for m in ("multicore", "scalar")
                                              for sim in ("soc_icache_off", "soc_l2", "soc_zero_wait")]
                                             + ["coherence/reduce_fill/dma/soc_dev/warm"]))
        self.assertEqual(sum(e["status"] == "planned" for e in entries), 4)       # 2 and 8 KiB, each method
        self.assertEqual(matrix_bundle.METHOD_SUFFIX.sub("", "conv2d_im2col_npu"), "conv2d_im2col")

    def test_npu_exclusions_where_the_npu_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = scaling_run(Path(tmp) / "a", "coherence")
            record = run / "records" / "coherence__reduce_fill__multicore.record"
            record.write_text(record.read_text().replace("npu_jobs=0", "npu_jobs=1"))   # (as if it ran the NPU)
            out = Path(tmp) / "out"
            argv = sys.argv
            sys.argv = ["matrix_bundle.py", str(run), "--out", str(out)]
            try:
                self.assertEqual(matrix_bundle.main(), 0)
            finally:
                sys.argv = argv
            ids = {matrix_overlap.json.loads(l.strip().rstrip(","))["id"] for l in (out / "manifest.json").open()
                   if l.strip().startswith('{"')}
        self.assertLessEqual({"coherence/reduce_fill/multicore/soc_n2x2/warm",
                              "coherence/reduce_fill/multicore/soc_n8p4/warm"}, ids)
        self.assertNotIn("coherence/reduce_fill/scalar/soc_n2x2/warm", ids)

    def test_reproducible_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = scaling_run(Path(tmp) / "a", "coherence")
            matrix_bundle.pack(run, Path(tmp) / "1.tar.xz")
            matrix_bundle.pack(run, Path(tmp) / "2.tar.xz")
            self.assertEqual((Path(tmp) / "1.tar.xz").read_bytes(), (Path(tmp) / "2.tar.xz").read_bytes())


class Golden(unittest.TestCase):
    def test_nothing_to_replay_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir()
            (run / "manifest.json").write_text(matrix_overlap.json.dumps(dict(entries=[
                dict(id="f/a/scalar/soc_w1/warm", status="captured", sim="soc_w1")])))
            argv = sys.argv
            sys.argv = ["matrix_golden.py", str(run), "--out", str(Path(tmp) / "out")]
            try:
                self.assertEqual(matrix_golden.main(), 1)                         # (no R image: no golden build)
            finally:
                sys.argv = argv


class MnistWeights(unittest.TestCase):
    def test_header_is_the_models(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts/gen_mnist_transposed.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
