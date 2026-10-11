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
import matrix_gates  # noqa: E402
import matrix_geometry  # noqa: E402
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
    def check(self, kib: int, size: int, off_by: int) -> None:
        base = next(e for e in matrix.dma_entries() if e.id == f"dma/copy/dma/soc_dev/warm/b{size}s0d0c1")
        own = matrix.soc_variants.VARIANTS["soc_dev"]["CACHE_BYTES"] // 1024      # (R's: 8 KiB since 20.5)
        e = base if kib == own else matrix.with_caches([base], [kib])[0]
        a, lines = e.axes, (size + 15) // 16
        r = dict(window="e2e", name="copy", size=size, iterations=1, param=(1 << 8), seed=a["seed"], workers=1,
                 checksum=matrix.dma_model(a, False), dma_jobs=1, dma_completed_jobs=1, dma_bytes=size,
                 dma_invalidations=lines + off_by, dma_reads=(size + 7) // 8, dma_writes=(size + 7) // 8,
                 h0_dot8_retire=0, h1_dot8_retire=0, npu_jobs=0, h1_retired=0, h1_work_start=0, h1_work_end=0)
        matrix.oracle_dma(e, [r])

    def test_exact_to_a_quarter(self):
        for kib, size in ((2, 512), (4, 1024), (8, 2048)):
            self.check(kib, size, 0)
            for off_by in (-1, 1):
                with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "invalidations"):
                    self.check(kib, size, off_by)

    def test_bounded_above_it(self):
        self.check(2, 1024, -1)                                  # (a line aliased: 20.5's 2 KiB case)
        self.check(4, 4096, -9)
        with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "over the destination"):
            self.check(2, 1024, 1)
        with self.assertRaisesRegex(matrix.asterbench_v12.ValidationError, "over the destination"):
            self.check(2, 4096, 129 - 256)                       # 129 invalidations: over 2 KiB's 128 lines


STEP2_SIM = {4: "soc_dev", 2: "soc_l1_2k", 8: "soc_l1_8k"}       # (a capture of step 2's: R's caches at 4 KiB)


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
