"""Mutation tests: the Phase 17 retained-baseline audit rejects configuration,
counter, determinism, and source drift even when the bundle is consistently
re-hashed."""

from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_phase17_baseline as audit

BUNDLES = sorted(path for path in (ROOT / "docs/results/phase17").glob("baseline-*")
                 if not path.name.endswith("-FAILED"))


@unittest.skipUnless(BUNDLES, "no retained Phase 17 baseline bundle")
class Phase17BaselineAudit(unittest.TestCase):
    def copy(self):
        temp = tempfile.TemporaryDirectory(prefix="phase17-baseline-")
        self.addCleanup(temp.cleanup)
        bundle = Path(temp.name) / BUNDLES[-1].name
        shutil.copytree(BUNDLES[-1], bundle)
        return bundle

    @staticmethod
    def rehash(bundle):
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"] = audit.inventory(bundle)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    def set_field(self, bundle, model, capture, key, value, repeats=audit.REPEATS):
        for repeat in repeats:
            path = bundle / "records" / model / f"r{repeat}" / f"{capture}.record"
            fields = audit.fields_of(path.read_text())
            self.assertIn(key, fields)
            fields[key] = value
            path.write_text("ASTERBENCH," + ",".join(f"{k}={v}" for k, v in fields.items()) + "\n")
        self.rehash(bundle)

    def assert_rejected(self, bundle, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            audit.audit(bundle)

    def test_retained_bundle_passes(self):
        summary = audit.audit(self.copy())
        self.assertIn("sync1", summary)

    def test_rejects_mismatched_top(self):
        bundle = self.copy()
        self.set_field(bundle, "sync1", "reduce_scalar", "harts", "1")
        self.assert_rejected(bundle, "top/cache configuration")

    def test_rejects_mismatched_clock(self):
        bundle = self.copy()
        self.set_field(bundle, "sync1", "conv2d_npu", "clock_hz", "50000000")
        self.assert_rejected(bundle, "clock_hz")

    def test_rejects_mismatched_memory_mode(self):
        bundle = self.copy()
        self.set_field(bundle, "sync1", "streaming_ecg", "sync_memory", "0")
        self.assert_rejected(bundle, "memory model")

    def test_rejects_misattributed_dma_bytes(self):
        bundle = self.copy()
        self.set_field(bundle, "sync1", "streaming_ecg", "dma_bytes", f"0x{1020:016x}")
        self.assert_rejected(bundle, "DMA totals")

    def test_rejects_last_job_only_npu_total(self):
        bundle = self.copy()
        self.set_field(bundle, "async0", "conv2d_npu", "npu_compute_cycles", f"0x{4900:016x}")
        self.assert_rejected(bundle, "shape oracle")

    def test_rejects_nondeterministic_repeat(self):
        bundle = self.copy()
        path = bundle / "records" / "sync1" / "r2" / "reduce_scalar.record"
        fields = audit.fields_of(path.read_text())
        self.set_field(bundle, "sync1", "reduce_scalar", "cycles",
                       f"0x{int(fields['cycles'], 16) + 1:016x}", repeats=(2,))
        self.assert_rejected(bundle, "determinism")

    def test_rejects_source_hash_drift(self):
        bundle = self.copy()
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["source"]["tree_sha256"] = "0" * 64
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        self.assert_rejected(bundle, "source hash")


if __name__ == "__main__":
    unittest.main()
