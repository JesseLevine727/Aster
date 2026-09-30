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

    def set_line_field(self, bundle, model, capture, version, key, value, repeats=audit.REPEATS):
        """Edit `key` on every ASTERBENCH line of one version in a multi-line record."""
        for repeat in repeats:
            path = bundle / "records" / model / f"r{repeat}" / f"{capture}.record"
            lines = path.read_text().splitlines(keepends=True)
            edited = 0
            for index, line in enumerate(lines):
                if line.startswith(f"ASTERBENCH,version={version},"):
                    fields = audit.fields_of(line)
                    self.assertIn(key, fields)
                    fields[key] = value(fields[key]) if callable(value) else value
                    lines[index] = "ASTERBENCH," + ",".join(f"{k}={v}" for k, v in fields.items()) + "\n"
                    edited += 1
            self.assertGreater(edited, 0)
            path.write_text("".join(lines))
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

    def test_rejects_last_layer_only_mnist_npu_total(self):
        bundle = self.copy()
        for repeat in audit.REPEATS:
            path = bundle / "records" / "sync1" / f"r{repeat}" / "mnist_npu.record"
            lines = path.read_text().splitlines(keepends=True)
            index = next(i for i, line in enumerate(lines) if line.startswith("ASTERBENCH,version=11,"))
            fields = audit.fields_of(lines[index])
            fields["npu_compute_cycles"] = f"0x{96 * 32:016x}"
            lines[index] = "ASTERBENCH," + ",".join(f"{k}={v}" for k, v in fields.items()) + "\n"
            path.write_text("".join(lines))
        self.rehash(bundle)
        self.assert_rejected(bundle, "oracle")

    def test_rejects_mnist_summary_on_another_top(self):
        bundle = self.copy()
        self.set_line_field(bundle, "sync1", "mnist_scalar", 11, "harts", "1")
        self.assert_rejected(bundle, "top/cache configuration")

    def test_rejects_uniformly_wrong_clock(self):
        # Every capture agrees, but not with the declared SoC clock.
        bundle = self.copy()
        for capture in audit.CAPTURES:
            for version in ((9, 11) if capture["format"] == "v9" else (int(capture["format"][1:]),)):
                self.set_line_field(bundle, "async0", capture["id"], version, "clock_hz", "50000000")
        self.assert_rejected(bundle, "clock_hz")  # the v9 validator also pins the fabric clock

    def test_rejects_mnist_summary_cycles_not_summed(self):
        bundle = self.copy()
        self.set_line_field(bundle, "sync1", "mnist_dot8", 11, "cycles", lambda v: f"0x{int(v, 16) - 1:016x}")
        self.assert_rejected(bundle, "sum of the v9 image windows")

    def test_rejects_mnist_summary_transactions_not_summed(self):
        bundle = self.copy()
        self.set_line_field(bundle, "async0", "mnist_multicore", 11, "memory_transactions",
                            lambda v: f"0x{int(v, 16) + 4:016x}")
        self.assert_rejected(bundle, "memory_transactions")

    def test_rejects_mnist_summary_checksum(self):
        bundle = self.copy()
        self.set_line_field(bundle, "sync1", "mnist_npu", 11, "checksum", lambda v: f"0x{int(v, 16) ^ 1:08x}")
        self.assert_rejected(bundle, "checksum")

    def test_rejects_malformed_firmware_hash(self):
        bundle = self.copy()
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["captures"][0]["firmware_sha256"] = "g" * 64
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        self.assert_rejected(bundle, "firmware hash")

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
