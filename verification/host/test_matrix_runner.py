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

import asterbench_v12  # noqa: E402
import matrix  # noqa: E402
import matrix_bundle  # noqa: E402
import matrix_compare  # noqa: E402
import matrix_crossover  # noqa: E402
import matrix_gates  # noqa: E402
import matrix_geometry  # noqa: E402
import matrix_judge  # noqa: E402
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
                            axes=dict(workers=workers, cache_state="warm", cache_kib=8), totals=dict(npu_fabric=1)))
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

    def test_each_size_and_layout_apart(self):
        """20.5: a 2 KiB pair that misses, and an L3 one, leave the 4 KiB gate at L0 as it was; each is judged
        at its own size and layout."""
        with tempfile.TemporaryDirectory() as tmp:
            allrec = matrix_overlap.records_of(scaling_run(Path(tmp) / "r", "coherence"))
            for key, (sim, axes) in {"2k": ("soc_l1_2k", dict(cache_kib=2)), "l3": ("soc_dev", dict(layout="L3"))}.items():
                for k, (e, recs) in matrix_overlap.records_of(scaling_run(Path(tmp) / key, "coherence", two=600)).items():
                    e = dict(e, id=e["id"].replace("/soc_dev/", f"/{sim}/") + ("/L3" if key == "l3" else ""), sim=sim,
                             axes=dict(e["axes"], **axes))
                    allrec[f"{key}:{k}"] = (e, recs)
            self.assertEqual(len(allrec), 6)
            r4 = matrix_gates.scaling(allrec)["reduce_fill"]
            self.assertEqual((r4["pairs"], r4["meets"], len(r4["r_warm"])), (1, True, 1))
            r2 = matrix_gates.scaling(allrec, kib=2)["reduce_fill"]
            self.assertEqual((r2["pairs"], r2["meets"], len(r2["r_warm"])), (1, False, 1))
            l3 = matrix_gates.scaling(allrec, layout="L3")["reduce_fill"]
            self.assertEqual((l3["pairs"], l3["meets"]), (1, False))


class DmaInvalidations(unittest.TestCase):
    """20.5: the DMA oracle's exact count up to a quarter of the cache at each size, and its bound above."""
    def check(self, kib: int, size: int, off_by: int, layout: str | None = None, symbols: dict | None = None) -> None:
        base = next(e for e in matrix.dma_entries() if e.id == f"dma/copy/dma/soc_dev/warm/b{size}s0d0c1")
        if layout:
            base = matrix.with_layout(base, layout)
        own = matrix.soc_variants.VARIANTS["soc_dev"]["CACHE_BYTES"] // 1024      # (R's: 8 KiB since 20.5)
        e = base if kib == own else matrix.with_caches([base], [kib])[0]
        a, lines = e.axes, (size + 15) // 16
        r = dict(window="e2e", name="copy", size=size, iterations=1, param=(1 << 8), seed=a["seed"], workers=1,
                 checksum=matrix.dma_model(a, False), dma_jobs=1, dma_completed_jobs=1, dma_bytes=size,
                 dma_invalidations=lines + off_by, dma_reads=(size + 7) // 8, dma_writes=(size + 7) // 8,
                 h0_dot8_retire=0, h1_dot8_retire=0, npu_jobs=0, h1_retired=0, h1_work_start=0, h1_work_end=0)
        matrix.oracle_dma(e, [r], symbols=symbols)

    def test_exact_to_a_quarter(self):
        for kib, size in ((2, 512), (4, 1024), (8, 2048)):
            self.check(kib, size, 0)
            for off_by in (-1, 1):
                with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "invalidations"):
                    self.check(kib, size, off_by)

    def test_exact_unless_the_stack_top_aliases(self):
        """20.5 step 3: a destination whose lines share a cache index with the top 64 bytes of hart 0's stack can lose
        a line (L4's, in the sweep): there the bound, elsewhere the exact count, on any layout."""
        top = 0x80017000
        # (the copy begins 64 bytes below the stack top's index, 8 KiB down: its first 4 lines alias the top 64 bytes)
        aliased = {"destination_room": top - 0x2000 - 64 - 1024 - 64, "__stack0_top": top}
        clear = {"destination_room": top - 0x2000 - 64 - 1024 - 0x400, "__stack0_top": top}
        self.assertTrue(matrix.dma_stack_aliased(matrix.with_layout(next(
            e for e in matrix.dma_entries() if e.id == "dma/copy/dma/soc_dev/warm/b128s0d0c1"), "L4"), aliased, 8192))
        self.check(8, 128, -1, "L4", aliased)
        for symbols, layout in ((clear, "L4"), (clear, None), (None, None)):
            with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "not the"):
                self.check(8, 128, -1, layout, symbols)
        with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "over the destination"):
            self.check(8, 128, 1, "L4", aliased)

    def test_bounded_above_it(self):
        self.check(2, 1024, -1)                                  # (a line aliased: 20.5's 2 KiB case)
        self.check(4, 4096, -9)
        with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "over the destination"):
            self.check(2, 1024, 1)
        with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "over the destination"):
            self.check(2, 4096, 129 - 256)                       # 129 invalidations: over 2 KiB's 128 lines


STEP2_SIM = {4: "soc_dev", 2: "soc_l1_2k", 8: "soc_l1_8k"}       # (a capture of step 2's: R's caches at 4 KiB)


class TunedVariants(unittest.TestCase):
    """20.5 step 3: the v1 gate's matching by a case's original name, tuned variants only once adopted; the judging."""
    def test_the_gate_admits_only_adopted_variants(self):
        mnist = matrix_gates.V1["MNIST MLP, per image"][1]
        self.assertTrue(matrix_gates.competes("mnist_mlp_npu", mnist))
        self.assertFalse(matrix_gates.competes("mnist_mlp_npu_batched", mnist))
        self.assertFalse(matrix_gates.competes("mnist_mlp_npu_batched__2h", mnist))   # (batched: never per image)
        self.assertFalse(matrix_gates.competes("ecg_pipeline_overlap__fir4_stages",
                                               matrix_gates.V1["streaming ECG, 16 x 64"][1]))
        saved = matrix_gates.ADOPTED
        try:
            matrix_gates.ADOPTED = frozenset()
            self.assertFalse(matrix_gates.competes("mnist_mlp_npu__2h", mnist))       # (not adopted)
            matrix_gates.ADOPTED = frozenset({"mnist_mlp_npu__2h"})
            self.assertTrue(matrix_gates.competes("mnist_mlp_npu__2h", mnist))
            self.assertFalse(matrix_gates.competes("mnist_mlp_npu_batched__2h", mnist))
        finally:
            matrix_gates.ADOPTED = saved
        ecg = matrix_gates.V1["streaming ECG, 16 x 64"][1]           # (step 3's set: the copy policy's variant left out)
        self.assertTrue(matrix_gates.competes("ecg_pipeline_overlap__fir4_split", ecg, dict(chunk=64, taps=16)))
        self.assertFalse(matrix_gates.competes("ecg_pipeline_overlap__fir4_split", ecg, dict(chunk=64, taps=32)))
        self.assertFalse(matrix_gates.competes("ecg_pipeline_overlap__fir4_split", ecg))     # (its point unknown)
        self.assertFalse(matrix_gates.competes("ecg_pipeline_overlap__fir4_copy", ecg))

    def test_the_judging(self):
        def entry(case, layout, state, cycles):
            e = dict(id=f"ml/{case}/npu/soc_dev/{state}" + (f"/{layout}" if layout != "L0" else ""), case=case,
                     axes=dict(layout=layout, cache_state=state) if layout != "L0" else dict(cache_state=state))
            return e["id"], (e, [dict(window="e2e", h0_cycles=str(cycles))])
        allrec = dict([entry("mnist_mlp_npu", "L0", "warm", 100), entry("mnist_mlp_npu__2h", "L0", "warm", 90),
                       entry("mnist_mlp_npu", "L3", "warm", 100), entry("mnist_mlp_npu__2h", "L3", "warm", 95),
                       entry("mnist_mlp_npu", "L0", "cold", 100), entry("mnist_mlp_npu__2h", "L0", "cold", 99)])
        rows, alone, _ = matrix_judge.pairs(allrec)
        self.assertEqual((list(rows), alone), (["ml/mnist_mlp_npu__2h/npu/soc_dev"], []))
        self.assertEqual(matrix_judge.verdict(rows["ml/mnist_mlp_npu__2h/npu/soc_dev"]), "wins")
        allrec.update([entry("mnist_mlp_npu__2h", "L3", "warm", 101)])
        rows, _, _ = matrix_judge.pairs(allrec)
        self.assertEqual(matrix_judge.verdict(rows["ml/mnist_mlp_npu__2h/npu/soc_dev"]), "not distinguishable")

    def test_a_stacked_change_is_judged_against_the_variant_it_extends(self):
        def entry(case, layout, state, cycles, chunk=64, taps=16):
            axes = dict(cache_state=state, chunk=chunk, taps=taps) | ({} if layout == "L0" else dict(layout=layout))
            e = dict(id=f"ecg/{case}/pipeline/soc_dev/{state}" + ("" if (chunk, taps) == (64, 16) else
                                                                 f"/c{chunk}t{taps}")
                     + ("" if layout == "L0" else f"/{layout}"), case=case, axes=axes)
            return e["id"], (e, [dict(window="e2e", h0_cycles=str(cycles))])
        allrec = dict([entry("x__fir4", "L0", "warm", 100), entry("x__fir4_split", "L0", "warm", 90),
                       entry("x__fir4", "L3", "cold", 100), entry("x__fir4_split", "L3", "cold", 101),
                       entry("x__fir4", "L0", "warm", 100, 32, 8), entry("x__fir4_split", "L0", "warm", 90, 32, 8)])
        rows, alone = matrix_judge.stacked(allrec, "x__fir4_split", "x__fir4")
        # (each configuration its own: the default point's id has no suffix, the others theirs)
        self.assertEqual({c: v["verdict"] for c, v in rows.items()},
                         {"ecg/x__fir4_split/pipeline/soc_dev": "not distinguishable",
                          "ecg/x__fir4_split/pipeline/soc_dev/c32t8": "wins"})
        self.assertEqual(alone, [])
        rows, alone = matrix_judge.stacked(allrec, "x__fir4_split", "x__none")
        self.assertEqual((rows, len(alone)), ({}, 3))

    def test_the_adopted_set_checked(self):
        def config(case, verdict, chunk=64, taps=16, sim="soc_dev", layouts=8):
            suffix = "" if (chunk, taps) == (64, 16) else f"/c{chunk}t{taps}"
            return f"ecg/{case}/pipeline/{sim}{suffix}", dict(verdict=verdict, axes=dict(chunk=chunk, taps=taps,
                                                                                        case=case),
                                                               layouts=layouts, low=-1.0, high=1.0)
        split, fir4 = "ecg_pipeline_overlap__fir4_split", "ecg_pipeline_overlap__fir4"
        points = sorted(matrix_gates.ADOPTED_POINTS[split][1])
        result = dict(config(split, "wins", c, t) for c, t in points + [(64, 32)])
        good = {(split, fir4): dict(config(split, "wins", c, t) for c, t in points)
                | dict([config(split, "loses", 64, 32)])}
        def check(stacks):                             # (the judgment holds this case alone: the others missing)
            problems, beside = matrix_judge.check_adopted(result, stacks)
            missing = [p for p in problems if p.endswith("not in the judgment")]
            self.assertEqual(len(missing), len(matrix_gates.ADOPTED) - 1)
            return [p for p in problems if p not in missing], beside
        problems, beside = check(good)
        self.assertEqual(problems, [])
        self.assertEqual(len(beside), 1)                               # (32 taps: reported, not adopted there)
        bad = {(split, fir4): {**good[(split, fir4)], **dict([config(split, "loses", 64, 16)])}}
        problems, _ = check(bad)
        self.assertEqual(len(problems), 2)                             # (adopted at 64/16 but loses; the points)
        problems, _ = check({})
        self.assertEqual(len(problems), 2)             # (stacked but not judged against __fir4; then its points)

    def test_the_staging_variants(self):
        plan = {e.case: e for e in matrix.cifar_entries() if e.sim == "soc_dev" and not e.cold}
        for method in ("npu_direct", "npu_im2col"):
            for tag, copy in (("__2h_copy", 1), ("__2h_next", 2)):
                e = plan[f"cifar_cnn_{method}{tag}"]
                self.assertIn("software/runtime/aster_copy.c", e.harness_sources)
                self.assertIn(f"-DCIFAR_COPY={copy}", e.defines)
                self.assertEqual(e.axes["workers"], 2)
            self.assertNotIn("software/runtime/aster_copy.c", plan[f"cifar_cnn_{method}__2h"].harness_sources)


class Policies(unittest.TestCase):
    """20.5 step 3: the runner reads the copy and split policies from the sources the firmware is built from."""
    def test_the_headers_values(self):
        self.assertEqual(matrix.copy_policy(), dict(aligned=113, same_offset=89, different_offsets=61, shift=15))
        split = matrix.split_policy()                  # (step 3b: one worker only where it wins, the bands two)
        self.assertEqual(split["dot_k"], 33)
        self.assertEqual(split["items"], {0: 33, 1: 17, 2: 9, 3: 0xFFFFFFFF, 4: 33, 5: 33, 8: 5})

    def test_split_variants_planned_by_the_policy(self):
        dot = {e.axes["k"]: e.axes["workers"] for e in matrix.dsp_more_entries()
               if e.case == "dot__split" and e.sim == "soc_dev" and not e.cold and "/seed" not in e.id}
        self.assertEqual(dot, {0: 1, 1: 1, 7: 1, 8: 1, 64: 2, 256: 2, 1024: 2, 4096: 2})
        lock = {e.axes["items"]: e.axes["workers"] for e in matrix.coherence_entries()
                if e.case == "lock_sum__split" and e.sim == "soc_dev" and not e.cold}
        self.assertEqual(lock, {2: 1, 129: 1, 1024: 1})


class CodePlacement(unittest.TestCase):
    """20.5 step 3b (tuning.md §4.4): the code-placement twins, their names, profile and link."""
    def test_names_and_twins(self):
        self.assertEqual((matrix.hot_case("reduce_v1"), matrix.hot_case("reduce_v1__loop")),
                         ("reduce_v1__hot", "reduce_v1__loop_hot"))
        self.assertEqual([matrix.program_case(c) for c in ("reduce_v1__hot", "reduce_v1__loop_hot", "reduce_v1__loop")],
                         ["reduce_v1", "reduce_v1__loop", "reduce_v1__loop"])
        self.assertEqual(matrix_gates.stacked_base("mnist_mlp_npu_batched__2h_hot"), "mnist_mlp_npu_batched__2h")
        self.assertIsNone(matrix_gates.stacked_base("reduce_v1__hot"))          # (judged against its original)
        ml = {e.id: e for e in matrix.FAMILIES["ml"]() if e.sim == "soc_dev" and not e.cold}
        twin = ml["ml/mnist_mlp_npu_batched__2h_hot32/npu/soc_dev/warm"]
        self.assertEqual((twin.case, twin.hot), ("mnist_mlp_npu_batched__2h_hot", True))
        self.assertIn("ml/cifar_cnn_npu_direct__hot/npu_direct/soc_dev/warm", ml)
        self.assertNotIn("ml/cifar_cnn_npu_direct__2h_next_hot/npu_direct/soc_dev/warm", ml)   # (not twinned)
        self.assertNotIn("ml/cifar_cnn_npu_im2col__2h_next_hot/npu_im2col/soc_dev/warm", ml)   # (adopted after step
        # 3b's plan: the plan is frozen, matrix_gates.TWINNED)
        ecg_ids = {e.id for e in matrix.FAMILIES["ecg"]()}
        self.assertIn("ecg/ecg_pipeline_overlap__fir4_copy_hot/pipeline/soc_dev/warm/c16t8", ecg_ids)
        self.assertNotIn("ecg/ecg_pipeline_overlap__fir4_copy_hot/pipeline/soc_dev/warm/c32t8", ecg_ids)
        ecg = [e for e in matrix.FAMILIES["ecg"]() if e.hot]
        self.assertFalse([e for e in ecg if e.case.endswith("_stages") or "stages" in e.case])
        self.assertFalse([e for e in matrix.FAMILIES["cpu"]() if e.hot])

    def test_the_map_and_the_link(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "x.map"
            m.write_text(" .text.init     0x0000000080000000       0x40 start.o\n"
                         " .text.foo\n                0x0000000080000100       0x20 /tmp/cc1.o\n"
                         " .text.bar       0x0000000080000120        0x8 /tmp/cc1.o\n"
                         " .text.empty     0x0000000080000128        0x0 /tmp/cc1.o\n")
            self.assertEqual(matrix.input_sections(m), [(0x80000000, 0x40, ".text.init"),
                                                        (0x80000100, 0x20, ".text.foo"), (0x80000120, 8, ".text.bar")])
            order = [dict(section=".text.foo", executed=9, bytes=32), dict(section=".text.bar", executed=5, bytes=8)]
            placed = matrix.hot_link(order, 33, Path(d) / "l.ld", 0x80001c00)  # (bar does not fit after foo)
            self.assertEqual([x["section"] for x in placed], [".text.foo"])
            text = (Path(d) / "l.ld").read_text()
            self.assertIn("KEEP(*(.text.init))\n        __hot_start = .;\n        *(.text.foo)\n        __hot_end = .;",
                          text)
            self.assertNotIn("ALIGN(256)", text)                               # (no padding before the rest)
            self.assertIn(".rodata 0x80001c00 : ALIGN(4)", text)              # (the data where the base's is)
            self.assertIn("ASSERT(__hot_end - __hot_start <= 33", text)


class Crossover(unittest.TestCase):
    """20.5 step 3: the copy helper's threshold, the size from which the DMA wins at every larger size."""
    def test_from_which_the_dma_wins(self):
        dma = lambda s, p: p["dma"] < p["fair"]
        by = {s: {(0, 0): dict(dma=100, fair=c)} for s, c in ((60, 90), (61, 101), (62, 99), (63, 120), (64, 130))}
        self.assertEqual(matrix_crossover.crossover(by, dma), (63, [61]))  # (61's win is isolated: not taken)
        by[64][(1, 0)] = dict(dma=200, fair=150)                           # (every offset pair must win)
        self.assertEqual(matrix_crossover.crossover(by, dma), (None, [61, 63]))
        self.assertEqual([matrix_crossover.klass(*o) for o in ((0, 0), (1, 1), (3, 6))],
                         ["aligned", "same_offset", "different_offsets"])


class Geometry(unittest.TestCase):
    """20.5: each 2 and 8 KiB entry against its 4 KiB twin, and a gate workload judged on every layout."""
    def rec(self, cycles: int) -> dict:
        return dict(name="reduce_fill", window="e2e", h0_cycles=str(cycles))

    def entry(self, sim: str, layout: str | None = None, kib: int | None = None) -> dict:
        axes = dict(workers=1, cache_state="warm", **({"layout": layout} if layout else {}),
                    **({"cache_kib": kib} if kib else {}))
        return dict(id=f"coherence/reduce_fill/scalar/{sim}/warm" + (f"/{layout}" if layout else ""),
                    family="coherence", case="reduce_fill", method="scalar", sim=sim, axes=axes)

    def test_twins_and_layouts(self):
        allrec = {}
        cycles = {(None, 4): 1000, (None, 2): 1100, (None, 8): 900, ("L3", 4): 1000, ("L3", 2): 990, ("L3", 8): 950}
        for (layout, kib), c in cycles.items():
            e = self.entry(STEP2_SIM[kib], layout, None if kib == 4 else kib)
            allrec[e["id"]] = (e, [self.rec(c)])
        lone = self.entry("soc_l1_2k", "L5", 2)
        allrec[lone["id"]] = (lone, [self.rec(5)])
        rows, alone = matrix_geometry.twins(allrec)
        self.assertEqual(alone, [lone["id"]])
        self.assertEqual(sorted((r["kib"], r["layout"], round(r["ratio"], 3)) for r in rows),
                         [(2, "L0", 1.1), (2, "L3", 0.99), (8, "L0", 0.9), (8, "L3", 0.95)])
        fam = matrix_geometry.per_family(rows)
        self.assertEqual((fam["coherence 2 KiB"]["all"]["windows"], fam["coherence 2 KiB"]["all"]["slower"]), (1, 1))
        lay = matrix_geometry.across_layouts(rows, allrec)
        self.assertEqual(lay["coherence/reduce_fill/scalar warm 2 KiB"]["verdict"], "mixed")
        self.assertEqual(lay["coherence/reduce_fill/scalar warm 8 KiB"]["verdict"], "faster on every layout")
        self.assertEqual(lay["coherence/reduce_fill/scalar warm 8 KiB"]["layouts_five"], 2)     # (L0, L3)

    def test_the_five_and_the_eight(self):
        """Faster on decision 2's five layouts but slower on L6: mixed on the eight."""
        allrec = {}
        for layout in (None, "L1", "L2", "L3", "L4", "L5", "L6", "L7"):
            for kib in (4, 8):
                e = self.entry(STEP2_SIM[kib], layout, None if kib == 4 else kib)
                other = {"L6": 1010, "L7": 1000}.get(layout, 990)
                allrec[e["id"]] = (e, [self.rec(1000 if kib == 4 else other)])
        rows, _ = matrix_geometry.twins(allrec)
        v = matrix_geometry.across_layouts(rows, allrec)["coherence/reduce_fill/scalar warm 8 KiB"]
        self.assertEqual((v["layouts"], v["verdict"], v["verdict_five"]), (8, "mixed", "faster on every layout"))
        self.assertEqual(matrix_geometry.across_layouts([r for r in rows if r["layout"] != "L6"], allrec)
                         ["coherence/reduce_fill/scalar warm 8 KiB"]["verdict"], "never slower")   # (L7 equal)

    def test_after_8k_was_adopted(self):
        """R at 8 KiB, every entry's size recorded: 2 and 4 KiB against it, the gates labelled by R's size."""
        allrec = {}
        for kib, sim, c in ((8, "soc_dev", 1000), (2, "soc_l1_2k", 1300), (4, "soc_l1_4k", 1100)):
            e = self.entry(sim, None, kib)
            allrec[e["id"]] = (e, [self.rec(c)])
        rows, alone = matrix_geometry.twins(allrec)
        self.assertEqual((alone, sorted((r["kib"], r["base_kib"], round(r["ratio"], 2)) for r in rows)),
                         ([], [(2, 8, 1.3), (4, 8, 1.1)]))
        self.assertEqual(sorted(matrix_geometry.per_family(rows)), ["coherence 2 KiB", "coherence 4 KiB"])
        self.assertTrue(matrix_gates.at(allrec["coherence/reduce_fill/scalar/soc_dev/warm"][0], None, None))
        self.assertFalse(matrix_gates.at(allrec["coherence/reduce_fill/scalar/soc_l1_4k/warm"][0], None, None))
        self.assertTrue(matrix_gates.at(allrec["coherence/reduce_fill/scalar/soc_l1_4k/warm"][0], 4, None))

    def test_a_sweep_point_at_l0_stands_apart(self):
        """A sweep point captured at L0 alone (as ECG's other chunks and taps) neither replaces the default's L0
        figure nor forms a verdict of its own."""
        allrec = {}
        for layout in (None, "L1", "L2"):
            for kib in (4, 8):
                e = self.entry(STEP2_SIM[kib], layout, None if kib == 4 else kib)
                allrec[e["id"]] = (e, [self.rec(1000 if kib == 4 else 990)])
        for kib in (4, 8):                                       # (sorted after the default)
            e = self.entry(STEP2_SIM[kib], None, None if kib == 4 else kib)
            e = dict(e, id=e["id"] + "/c64t8")
            allrec[e["id"]] = (e, [self.rec(1000 if kib == 4 else 1500)])
        rows, _ = matrix_geometry.twins(allrec)
        lay = matrix_geometry.across_layouts(rows, allrec)
        self.assertEqual(list(lay), ["coherence/reduce_fill/scalar warm 8 KiB"])
        v = lay["coherence/reduce_fill/scalar warm 8 KiB"]
        self.assertEqual((v["by_layout"]["L0"], v["verdict"]), (-1.0, "faster on every layout"))


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

    def test_a_captured_size_is_not_planned(self):
        """20.5: the scalar method captured at 2 KiB drops its placeholder; the rest stay planned."""
        with tempfile.TemporaryDirectory() as tmp:
            run = scaling_run(Path(tmp) / "a", "coherence")
            m = matrix_overlap.json.loads((run / "manifest.json").read_text())
            e = dict(m["entries"][0], id="coherence/reduce_fill/scalar/soc_l1_2k/warm", sim="soc_l1_2k")
            e["axes"] = dict(e["axes"], cache_kib=2)
            self.assertEqual(e["method"], "scalar")
            m["entries"].append(e)
            # (a cold one and a layout's at 4 KiB leave the warm L0 placeholder)
            m["entries"].append(dict(e, id="coherence/reduce_fill/scalar/soc_l1_4k/cold", sim="soc_l1_4k",
                                     axes=dict(e["axes"], cache_kib=4, cache_state="cold")))
            m["entries"].append(dict(e, id="coherence/reduce_fill/scalar/soc_l1_4k/warm/L3", sim="soc_l1_4k",
                                     axes=dict(e["axes"], cache_kib=4, layout="L3")))
            m["counts"]["captured"] = 5
            (run / "manifest.json").write_text(matrix_overlap.json.dumps(m))
            out = Path(tmp) / "out"
            argv = sys.argv
            sys.argv = ["matrix_bundle.py", str(run), "--out", str(out)]
            try:
                self.assertEqual(matrix_bundle.main(), 0)
            finally:
                sys.argv = argv
            entries = [matrix_overlap.json.loads(l.strip().rstrip(",")) for l in (out / "manifest.json").open()
                       if l.strip().startswith('{"')]
        planned = sorted(e["id"] for e in entries if e["status"] == "planned")
        self.assertEqual(planned, ["coherence/reduce_fill/multicore/soc_l1_2k/warm",
                                   "coherence/reduce_fill/multicore/soc_l1_4k/warm",
                                   "coherence/reduce_fill/scalar/soc_l1_4k/warm"])

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


class LineCount(unittest.TestCase):
    """20.5: a record's line count (the hardware's, ABI 4) against the build's CACHE_BYTES / 16."""
    def config(self, line_count: int) -> dict:
        return dict(npu_dim=8, npu_port_bytes=8, npu_strips=2, harts=2, memory_wait=1, dcache=1, line_count=line_count)

    def test_the_builds_count_passes(self):
        npu, soc = (8 << 16) | (8 << 8) | 2, 2
        for build, count in (("soc_l1_2k", 128), ("soc_l1_4k", 256), ("soc_dev", 512), ("soc_shell", 256)):
            asterbench_v12.check_config(self.config(count), npu, soc, matrix.soc_variants.VARIANTS[build]["CACHE_BYTES"] // 16)
        asterbench_v12.check_config(self.config(128), npu, soc)        # (no build count given: not checked)

    def test_another_count_fails(self):
        npu, soc = (8 << 16) | (8 << 8) | 2, 2
        for got, build in ((256, "soc_l1_2k"), (512, "soc_l1_4k"), (256, "soc_dev"), (128, "soc_dev")):
            with self.assertRaisesRegex(asterbench_v12.ValidationError, "line_count"):
                asterbench_v12.check_config(self.config(got), npu, soc, matrix.soc_variants.VARIANTS[build]["CACHE_BYTES"] // 16)


class MnistWeights(unittest.TestCase):
    def test_header_is_the_models(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts/gen_mnist_transposed.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
