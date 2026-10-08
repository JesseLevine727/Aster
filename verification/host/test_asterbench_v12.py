"""Shared corpus for the AsterBench v12 validators (docs/asterbench-v12.md): the Python
validator (scripts/asterbench_v12.py) and the C++ one (verification/common/asterbench_v12_record.h)
must agree on every record: the valid ones from each family pass; every field deleted,
duplicated, mis-encoded or past its width fails; and each invariant, broken, fails."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v12 as bench  # noqa: E402


def base(*, family="dsp", method="dot8", window="e2e", cache_state="warm", harts=2, workers=1,
         dma_jobs=0, npu_jobs=0):
    r = {k: 0 for k in bench.FIELD_ORDER}
    r.update({
        "version": 12, "name": f"{family}_case", "family": family, "method": method, "window": window,
        "status": "PASS", "size": 1024, "iterations": 4, "param": 5, "seed": 0x13570000, "checksum": 0x07DF8000,
        "clock_hz": 100_000_000, "harts": harts, "workers": workers, "dcache": 1, "cache_state": cache_state,
        "line_words": 4, "line_count": 256, "memory_wait": 1, "npu_dim": 8, "npu_port_bytes": 8, "npu_strips": 2,
    })
    for h in range(harts):
        p = f"h{h}_"
        r.update({p + "cycles": 100_000, p + "retired": 60_000, p + "memory_transactions": 20_000,
                  p + "icache_accesses": 60_000, p + "icache_misses": 40, p + "dcache_accesses": 20_000,
                  p + "dcache_misses": 300, p + "backing_transactions": 1_500, p + "amos": 4, p + "sc_success": 1,
                  p + "sc_failure": 1, p + "invalidations": 3, p + "work_start": 100, p + "work_end": 99_000})
        if method == "dot8":
            r.update({p + "dot8_accept": 512, p + "dot8_complete": 512, p + "dot8_retire": 512})
    r["h1_cycles"] = r["h0_cycles"]
    if harts == 1:
        r["workers"] = 1
    for q, a in (("i0", 600), ("d0", 1_200), ("i1", 500 if harts == 2 else 0), ("d1", 900 if harts == 2 else 0)):
        r[f"f_accepted_{q}"] = a
        r[f"f_waited_{q}"] = a // 10
        r[f"f_longest_{q}"] = 3 if a else 0
    if dma_jobs:
        units = 64 * dma_jobs
        r.update({"dma_jobs": dma_jobs, "dma_completed_jobs": dma_jobs, "dma_bytes": 8 * units,
                  "dma_busy_cycles": 70 * dma_jobs, "dma_wait_cycles": 5, "dma_reads": units, "dma_writes": units,
                  "dma_backing_reads": units, "dma_backing_writes": units, "dma_invalidations": 2,
                  "f_accepted_r": units, "f_accepted_w": units, "f_waited_r": 3, "f_waited_w": 2,
                  "f_longest_r": 1, "f_longest_w": 1, "f_snoops_c0p2": units, "f_invalidations_c0p2": 2})
    if npu_jobs:
        r.update({"npu_jobs": npu_jobs, "npu_completed_jobs": npu_jobs, "npu_job_cycles": 5_000 * npu_jobs,
                  "npu_active_cycles": 4_000 * npu_jobs, "npu_macs": 200_000 * npu_jobs,
                  "npu_bytes_read": 9_000 * npu_jobs, "npu_bytes_written": 16_384 * npu_jobs, "npu_tiles": 64 * npu_jobs,
                  "f_accepted_n": 3_000 * npu_jobs, "f_waited_n": 40, "f_longest_n": 4,
                  "f_snoops_c0p1": 2_000, "f_invalidations_c0p1": 10})
    r.update({"f_bank0_reads": 900, "f_bank1_reads": 800, "f_bank0_writes": 300, "f_bank1_writes": 200,
              "f_bank0_conflicts": 20, "f_snoops_c0p0": 400, "f_invalidations_c0p0": 3, "f_amos": 2,
              "f_resv_ended_h0": 1})
    return r


VALID = {
    "dsp_dot8": base(),
    "cpu_scalar_one_hart": base(family="cpu", method="scalar", harts=1),
    "cold_e2e": base(family="memory", method="scalar", cache_state="cold"),
    "kernel_window": base(window="kernel"),
    "dma": base(family="dma", method="dma", dma_jobs=3),
    "npu": base(family="npu_gemm", method="npu", npu_jobs=2),
    "two_workers": base(family="coherence", method="multicore", workers=2),
    "ecg_pipeline": base(family="ecg", method="pipeline", workers=2, dma_jobs=16, npu_jobs=16),
    "zero_size": dict(base(family="dma", method="cpu_copy"), size=0),
    "ml_npu": base(family="ml", method="npu_direct", npu_jobs=3),
    "npu_4x4_32bit": dict(base(family="npu_gemm", method="npu", npu_jobs=1), npu_dim=4, npu_port_bytes=4,
                          npu_strips=1, npu_macs=10_000),
}


def line(record):
    return bench.emit(record)


def broken(name, change):
    def make():
        r = dict(VALID[name]); change(r); return r
    return make


# Each invariant, broken (the record must fail in both validators).
INVARIANTS = {
    "version": broken("dsp_dot8", lambda r: r.update(version=11)),
    "bad_family": broken("dsp_dot8", lambda r: r.update(family="gpu")),
    "bad_method": broken("dsp_dot8", lambda r: r.update(method="fpga")),
    "bad_window": broken("dsp_dot8", lambda r: r.update(window="both")),
    "bad_status": broken("dsp_dot8", lambda r: r.update(status="OK")),
    "fail_status": broken("dsp_dot8", lambda r: r.update(status="FAIL")),
    "bad_cache_state": broken("dsp_dot8", lambda r: r.update(cache_state="hot")),
    "cold_kernel": broken("dsp_dot8", lambda r: r.update(cache_state="cold", window="kernel")),
    "bad_name": broken("dsp_dot8", lambda r: r.update(name="Conv2D")),
    "long_name": broken("dsp_dot8", lambda r: r.update(name="a" * 49)),
    "zero_iterations": broken("dsp_dot8", lambda r: r.update(iterations=0)),
    "zero_clock": broken("dsp_dot8", lambda r: r.update(clock_hz=0)),
    "three_harts": broken("dsp_dot8", lambda r: r.update(harts=3)),
    "workers_exceed": broken("cpu_scalar_one_hart", lambda r: r.update(workers=2)),
    "zero_workers": broken("dsp_dot8", lambda r: r.update(workers=0)),
    "dcache_two": broken("dsp_dot8", lambda r: r.update(dcache=2)),
    "line_words_odd": broken("dsp_dot8", lambda r: r.update(line_words=3)),
    "line_count_big": broken("dsp_dot8", lambda r: r.update(line_count=2048)),
    "memory_wait_zero": broken("dsp_dot8", lambda r: r.update(memory_wait=0)),
    "memory_wait_big": broken("dsp_dot8", lambda r: r.update(memory_wait=17)),
    "npu_dim_2": broken("dsp_dot8", lambda r: r.update(npu_dim=2)),
    "npu_port_2": broken("dsp_dot8", lambda r: r.update(npu_port_bytes=2)),
    "npu_8x8_32bit": broken("dsp_dot8", lambda r: r.update(npu_port_bytes=4)),
    "npu_strips_3": broken("dsp_dot8", lambda r: r.update(npu_strips=3)),
    "no_window": broken("dsp_dot8", lambda r: r.update(h0_cycles=0, h1_cycles=0)),
    "nothing_retired": broken("dsp_dot8", lambda r: r.update(h0_retired=0)),
    "windows_differ": broken("dsp_dot8", lambda r: r.update(h1_cycles=99_999)),
    "absent_hart_active": broken("cpu_scalar_one_hart", lambda r: r.update(h1_retired=1)),
    "absent_hart_interval": broken("cpu_scalar_one_hart", lambda r: r.update(h1_work_end=5)),
    "retired_exceeds": broken("dsp_dot8", lambda r: r.update(h0_retired=100_001)),
    "h1_txn_exceeds": broken("dsp_dot8", lambda r: r.update(h1_memory_transactions=100_001)),
    "backing_exceeds": broken("dsp_dot8", lambda r: r.update(h0_backing_transactions=200_001)),
    "imisses_exceed": broken("dsp_dot8", lambda r: r.update(h0_icache_misses=60_001)),
    "dmisses_exceed": broken("dsp_dot8", lambda r: r.update(h1_dcache_misses=20_001)),
    "sc_exceed_amos": broken("dsp_dot8", lambda r: r.update(h0_sc_success=4)),
    "dirty_intervention": broken("dsp_dot8", lambda r: r.update(h0_dirty_interventions=1)),
    "writeback_words": broken("dsp_dot8", lambda r: r.update(h1_writeback_words=1)),
    "work_reversed": broken("dsp_dot8", lambda r: r.update(h0_work_start=99_001)),
    "work_past_window": broken("dsp_dot8", lambda r: r.update(h1_work_end=100_001)),
    "dot8_differ": broken("dsp_dot8", lambda r: r.update(h0_dot8_retire=511)),
    "dot8_wait": broken("dsp_dot8", lambda r: r.update(h1_dot8_wait=1)),
    "dma_outcomes": broken("dma", lambda r: r.update(dma_jobs=4)),
    "dma_aborted": broken("dma", lambda r: r.update(dma_completed_jobs=2, dma_aborted_jobs=1)),
    "dma_rejected": broken("dma", lambda r: r.update(dma_rejected=1)),
    "dma_unanswered": broken("dma", lambda r: r.update(dma_reads=191)),
    "dma_counts_without_jobs": broken("dsp_dot8", lambda r: r.update(dma_busy_cycles=5)),
    "dma_busy_exceeds": broken("dma", lambda r: r.update(dma_busy_cycles=100_001)),
    "dma_bytes_exceed": broken("dma", lambda r: r.update(dma_bytes=8 * 192 + 1)),
    "dma_reads_vs_fabric": broken("dma", lambda r: r.update(f_accepted_r=191)),
    "dma_writes_vs_fabric": broken("dma", lambda r: r.update(f_accepted_w=193)),
    "dma_waits_vs_fabric": broken("dma", lambda r: r.update(f_waited_w=3)),
    "dma_invalidations_vs_fabric": broken("dma", lambda r: r.update(f_invalidations_c1p2=1, f_snoops_c1p2=1)),
    "npu_outcomes": broken("npu", lambda r: r.update(npu_jobs=3)),
    "npu_error": broken("npu", lambda r: r.update(npu_completed_jobs=1, npu_error_jobs=1)),
    "npu_active_exceeds": broken("npu", lambda r: r.update(npu_active_cycles=10_001)),
    "npu_job_exceeds": broken("npu", lambda r: r.update(npu_job_cycles=100_001)),
    "npu_macs_exceed": broken("npu", lambda r: r.update(npu_macs=8_000 * 64 + 1)),
    "npu_counts_without_jobs": broken("dsp_dot8", lambda r: r.update(npu_tiles=1)),
    "npu_port_without_jobs": broken("dsp_dot8", lambda r: r.update(f_accepted_n=1)),
    "accepted_exceeds": broken("dsp_dot8", lambda r: r.update(f_accepted_i0=100_001)),
    "waited_exceeds": broken("dsp_dot8", lambda r: r.update(f_waited_d1=100_001)),
    "longest_exceeds_waited": broken("dsp_dot8", lambda r: r.update(f_longest_d0=121)),
    "longest_saturates": broken("dsp_dot8", lambda r: r.update(f_waited_d0=70_000, f_longest_d0=65_536)),
    "invalidations_exceed_snoops": broken("dsp_dot8", lambda r: r.update(f_invalidations_c0p0=401)),
    "bank_writes_exceed": broken("dsp_dot8", lambda r: r.update(f_bank2_writes=100_000)),
    "bank_reads_exceed": broken("dsp_dot8", lambda r: r.update(f_bank3_reads=200_001)),
    "bank_conflicts_exceed": broken("dsp_dot8", lambda r: r.update(f_bank1_conflicts=100_001)),
    "resv_exceeds": broken("dsp_dot8", lambda r: r.update(f_resv_ended_h1=100_001)),
}


def mutations():
    """Every field deleted, duplicated, mis-encoded and set past its width, on one valid record."""
    good = line(VALID["dsp_dot8"])
    tokens = good[len("ASTERBENCH,"):-1].split(",")
    out = {}
    for i, token in enumerate(tokens):
        key, value = token.split("=", 1)
        others = tokens[:i] + tokens[i + 1:]
        out[f"delete_{key}"] = "ASTERBENCH," + ",".join(others) + "\n"
        out[f"duplicate_{key}"] = "ASTERBENCH," + ",".join(tokens + [token]) + "\n"
        out[f"empty_{key}"] = "ASTERBENCH," + ",".join(tokens[:i] + [f"{key}="] + tokens[i + 1:]) + "\n"
        if key in bench.STRING_FIELDS:
            bad = [value.upper() + "X"]
        elif key in bench.HEX32_FIELDS:
            bad = [value.upper().replace("0X", "0x"), value[:-1], value + "0", value[2:]]
            if value[2:].upper() == value[2:]:            # no letters: make one uppercase on purpose
                bad[0] = "0xABCDEF01"
        else:
            width = 32 if key in bench.SMALL_FIELDS else 64
            bad = ["0" + value, "-1", "0x10", "1e3", " " + value, str(1 << width)]
        for j, b in enumerate(bad):
            out[f"encoding_{key}_{j}"] = "ASTERBENCH," + ",".join(tokens[:i] + [f"{key}={b}"] + tokens[i + 1:]) + "\n"
    out["unknown_field"] = good[:-1] + ",extra=1\n"
    out["no_prefix"] = good[len("ASTERBENCH,"):]
    out["no_newline"] = good[:-1]
    out["two_lines"] = good + good
    out["carriage_return"] = good[:-1] + "\r\n"
    out["trailing_comma"] = good[:-1] + ",\n"
    out["too_long"] = good[:-1] + ",x=" + "1" * 6200 + "\n"
    out["empty_body"] = "ASTERBENCH,\n"
    return out


class AsterBenchV12(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cli = Path(cls.tmp.name) / "v12cli"
        subprocess.run(["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-o", str(cls.cli),
                        str(ROOT / "verification/host/asterbench_v12_parser_cli.cpp")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cpp(self, lines, allow_fail=False):
        payload = "".join(f"{len(x.encode())}\n{x}" for x in lines)
        args = [str(self.cli)] + (["--allow-fail"] if allow_fail else [])
        out = subprocess.run(args, input=payload.encode(), capture_output=True, check=True).stdout.decode().split()
        self.assertEqual(len(out), len(lines))
        return [x == "PASS" for x in out]

    def python(self, text, allow_fail=False):
        try:
            bench.validate_line(text, allow_fail=allow_fail)
            return True
        except bench.ValidationError:
            return False

    def test_schema_has_133_fields(self):
        self.assertEqual(len(bench.FIELD_ORDER), 133)

    def test_valid_records_pass_both(self):
        names = list(VALID)
        texts = [line(VALID[n]) for n in names]
        cpp = self.cpp(texts)
        for n, t, c in zip(names, texts, cpp):
            self.assertTrue(self.python(t), f"python rejects valid {n}")
            self.assertTrue(c, f"c++ rejects valid {n}")

    def test_broken_invariants_fail_both(self):
        names = list(INVARIANTS)
        texts = [line(INVARIANTS[n]()) for n in names]
        cpp = self.cpp(texts)
        for n, t, c in zip(names, texts, cpp):
            self.assertFalse(self.python(t), f"python accepts broken invariant {n}")
            self.assertFalse(c, f"c++ accepts broken invariant {n}")

    def test_mutations_fail_both(self):
        cases = mutations()
        names = list(cases)
        cpp = self.cpp([cases[n] for n in names])
        for n, c in zip(names, cpp):
            self.assertFalse(self.python(cases[n]), f"python accepts mutation {n}")
            self.assertFalse(c, f"c++ accepts mutation {n}")
        self.assertGreater(len(names), 1_000)

    def test_allow_fail_accepts_a_failed_run_only_by_request(self):
        failed = line(dict(VALID["dsp_dot8"], status="FAIL"))
        self.assertFalse(self.python(failed))
        self.assertTrue(self.python(failed, allow_fail=True))
        self.assertEqual(self.cpp([failed]), [False])
        self.assertEqual(self.cpp([failed], allow_fail=True), [True])


if __name__ == "__main__":
    unittest.main()
