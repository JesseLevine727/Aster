"""Pure host checks for the Phase 11 model artifact and export."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import phase11_reference as reference

MODEL = ROOT / "docs" / "results" / "phase11" / "model.json"
MANIFEST = ROOT / "docs" / "results" / "phase11" / "export.json"
CODE_BUDGET = 12 * 1024
ROM = 64 * 1024


class Phase11Reference(unittest.TestCase):
    def test_reference_matches_artifact(self):
        result = reference.reference(MODEL)
        self.assertEqual(result["images"], 32)
        self.assertEqual(result["correct"], 30)
        self.assertEqual(result["accuracy"], 30 / 32)

    def test_exported_headers_match_manifest(self):
        manifest = json.loads(MANIFEST.read_text())
        model = json.loads(MODEL.read_text())
        self.assertEqual(manifest["model_hash"], model["hash"])
        for name, digest in manifest["headers"].items():
            data = (ROOT / "software" / "benchmarks" / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)

    def test_rom_budget_fits(self):
        model = json.loads(MODEL.read_text())
        weights = sum(len(layer["weights"]) for layer in model["layers"])
        biases = sum(len(layer["bias_q"]) * 4 for layer in model["layers"])
        test = model["test"]
        data = weights + biases + len(test["images"]) + len(test["labels"]) + len(test["reference_classes"])
        self.assertLessEqual(data + CODE_BUDGET, ROM)

    def test_reference_rejects_tampering(self):
        model = json.loads(MODEL.read_text())
        model["layers"][0]["weights"][0] ^= 1
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(model, handle)
            path = Path(handle.name)
        try:
            with self.assertRaises(ValueError):
                reference.reference(path)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
