"""Host mutation tests for the Phase 17 same-top Conv2D diagnostic audit."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import phase17_conv_baseline as baseline


# Per-job NPU counters for the 32x32/K=5 Conv2D (M=784, N=1, K=25) on a 4x4
# array, written out independently of the audit's oracle function.
NPU_JOB = {"tiles": 196, "read": 196 * 25 * (4 + 1), "written": 784 * 4,
           "compute": 196 * 25, "busy": 250_000}


def record(name, cycles, *, sync_memory=0, dma_bytes=0, dma_jobs=0, npu_jobs=None, dot8=False,
           npu_scale=None):
    if npu_jobs is None:
        npu_jobs = 4 if name == "conv2d_npu" else 0
    scale = npu_jobs if npu_scale is None else npu_scale
    fields = {
        "version": 11,
        "name": name,
        "category": "dsp",
        "status": "PASS",
        "size": 1024,
        "iterations": 4,
        "param": 5,
        "seed": "0x13570000",
        "checksum": "0x07df8000",
        "clock_hz": 31250000,
        "harts": 2,
        "workers": 1,
        "l1": 1,
        "sync_memory": sync_memory,
        "line_words": 4,
        "line_count": 16,
        "memory_wait": sync_memory,
        "cycles": f"0x{cycles:016x}",
        "retired": f"0x{cycles // 2:016x}",
        "memory_transactions": f"0x{cycles // 3:016x}",
        "backing_transactions": f"0x{cycles // 8:016x}",
        "cache_accesses": f"0x{cycles // 4:016x}",
        "cache_misses": f"0x{cycles // 16:016x}",
        "dma_jobs": dma_jobs,
        "dma_completed_jobs": dma_jobs,
        "dma_aborted_jobs": 0,
        "dma_error_jobs": 0,
        "dma_bytes": f"0x{dma_bytes:016x}",
        "dma_job_cycles": f"0x{200 * dma_jobs:016x}",
        "h0_dot8_accept": f"0x{100 if dot8 else 0:016x}",
        "h0_dot8_wait": f"0x{120 if dot8 else 0:016x}",
        "h0_dot8_complete": f"0x{100 if dot8 else 0:016x}",
        "h0_dot8_retire": f"0x{100 if dot8 else 0:016x}",
        "h1_dot8_accept": "0x0000000000000000",
        "h1_dot8_wait": "0x0000000000000000",
        "h1_dot8_complete": "0x0000000000000000",
        "h1_dot8_retire": "0x0000000000000000",
        "npu_jobs": npu_jobs,
        "npu_completed_jobs": npu_jobs,
        "npu_aborted_jobs": 0,
        "npu_error_jobs": 0,
        "npu_bytes_read": f"0x{NPU_JOB['read'] * scale:016x}",
        "npu_bytes_written": f"0x{NPU_JOB['written'] * scale:016x}",
        "npu_tiles": f"0x{NPU_JOB['tiles'] * scale:016x}",
        "npu_job_cycles": f"0x{NPU_JOB['busy'] * scale:016x}",
        "npu_compute_cycles": f"0x{NPU_JOB['compute'] * scale:016x}",
    }
    return "ASTERBENCH," + ",".join(f"{key}={value}" for key, value in fields.items()) + "\n"


def valid_records():
    return {
        "conv2d_scalar_coh": record("conv2d_scalar_coh", 9_586_170),
        "conv2d_dot8": record("conv2d_dot8", 9_786_108, dot8=True),
        "conv2d_npu": record("conv2d_npu", 4_636_733),
    }


class Phase17ConvBaseline(unittest.TestCase):
    def test_accepts_same_top_independently_checked_matrix(self):
        result = baseline.audit_records(valid_records())
        self.assertEqual(result["checksum"], "0x07df8000")
        self.assertEqual(result["npu_over_coherent_scalar"], 9_586_170 / 4_636_733)
        self.assertEqual(result["npu_cumulative_compute_cycles"], 19_600)
        # 78,400 useful MACs over 19,600 array steps of a 16-PE array: N=1 lights 4 PEs.
        self.assertEqual(result["npu_pe_utilization_when_active"], 0.25)

    def test_rejects_mixed_memory_configuration(self):
        records = valid_records()
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733,
                                        sync_memory=1)
        with self.assertRaises(baseline.bench.ValidationError):
            baseline.audit_records(records)

    def test_rejects_npu_output_misattributed_as_dma(self):
        records = valid_records()
        # A well-formed record (the v11 validator accepts it): the audit's own
        # attribution check must reject DMA activity in a no-DMA Conv2D.
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733,
                                        dma_bytes=12_544, dma_jobs=1)
        baseline.bench.validate_line(records["conv2d_npu"], name="conv2d_npu")
        with self.assertRaisesRegex(baseline.bench.ValidationError, "DMA"):
            baseline.audit_records(records)

    def test_rejects_last_job_only_npu_totals(self):
        records = valid_records()
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733, npu_scale=1)
        with self.assertRaises(baseline.bench.ValidationError):
            baseline.audit_records(records)

    def test_rejects_missing_engine_activity(self):
        records = valid_records()
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733, npu_jobs=0)
        with self.assertRaises(baseline.bench.ValidationError):
            baseline.audit_records(records)


if __name__ == "__main__":
    unittest.main()
