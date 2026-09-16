"""Host checks for the tiny CIFAR-10 CNN artifact and record checksum."""

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import cifar_reference as reference

MODEL = ROOT / "docs" / "results" / "workloads" / "cifar_model.json"


class CifarReference(unittest.TestCase):
    def test_reference_matches_artifact(self):
        result = reference.reference(MODEL)
        self.assertEqual(result["images"], 20)
        self.assertEqual(result["correct"], 14)

    def test_record_checksum_matches_firmware(self):
        model = reference.load_model(MODEL)
        self.assertEqual(reference.record_checksum(model), 0xFEB1B300)

    def test_geometry_fits_npu(self):
        model = json.loads(MODEL.read_text())
        self.assertLessEqual(14 * 14, 1024)
        self.assertLessEqual(5 * 5, 1024)
        self.assertLessEqual(model["fc"]["in_features"], 1024)


if __name__ == "__main__":
    unittest.main()
