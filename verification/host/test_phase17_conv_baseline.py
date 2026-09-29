"""Host mutation tests for the Phase 17 same-top Conv2D diagnostic audit."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import phase17_conv_baseline as baseline


def record(name, cycles, *, sync_memory=0, dma_bytes=0, accelerator_cycles=0):
    fields = {
        "version": 10,
        "name": name,
        "category": "dsp",
        "status": "PASS",
        "size": 1024,
        "iterations": 4,
        "param": 5,
        "seed": "0x13570000",
        "checksum": "0x07df8000",
        "clock_hz": 31250000,
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
        "dma_bytes": f"0x{dma_bytes:016x}",
        "accelerator_cycles": f"0x{accelerator_cycles:016x}",
    }
    return "ASTERBENCH," + ",".join(f"{key}={value}" for key, value in fields.items()) + "\n"


def valid_records():
    return {
        "conv2d_scalar_coh": record("conv2d_scalar_coh", 9_586_170),
        "conv2d_dot8": record("conv2d_dot8", 9_786_108),
        "conv2d_npu": record("conv2d_npu", 4_636_733, accelerator_cycles=4_900),
    }


class Phase17ConvBaseline(unittest.TestCase):
    def test_accepts_same_top_independently_checked_matrix(self):
        result = baseline.audit_records(valid_records())
        self.assertEqual(result["checksum"], "0x07df8000")
        self.assertEqual(result["npu_over_coherent_scalar"], 9_586_170 / 4_636_733)

    def test_rejects_mixed_memory_configuration(self):
        records = valid_records()
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733,
                                        sync_memory=1, accelerator_cycles=4_900)
        with self.assertRaises(baseline.bench.ValidationError):
            baseline.audit_records(records)

    def test_rejects_npu_output_misattributed_as_dma(self):
        records = valid_records()
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733,
                                        dma_bytes=12_544, accelerator_cycles=4_900)
        with self.assertRaises(baseline.bench.ValidationError):
            baseline.audit_records(records)

    def test_rejects_missing_engine_activity(self):
        records = valid_records()
        records["conv2d_npu"] = record("conv2d_npu", 4_636_733)
        with self.assertRaises(baseline.bench.ValidationError):
            baseline.audit_records(records)


if __name__ == "__main__":
    unittest.main()
