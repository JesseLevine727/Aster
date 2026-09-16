"""Host checks for the generic workload checksum oracle."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import workload_reference as reference


class WorkloadReference(unittest.TestCase):
    def test_strided_checksum_matches_firmware(self):
        self.assertEqual(reference.strided_checksum(4096, 4, 16, 0x13570000), 0x5FC0F800)

    def test_sort_checksum_matches_firmware(self):
        self.assertEqual(reference.sort_checksum(1024, 4, 0x13570000), 0xB0E60880)

    def test_fft_checksum_matches_firmware(self):
        self.assertEqual(reference.fft_checksum(1024, 4, 256, 0x13570000), 0x2DFCFF80)

    def test_conv2d_checksum_matches_firmware(self):
        self.assertEqual(reference.conv2d_checksum(1024, 4, 5, 0x13570000), 0x07DF8000)

    def test_reduce_checksum_matches_firmware(self):
        self.assertEqual(reference.reduce_checksum(4096, 4, 2, 0x13570000), 0x5C808000)

    def test_ecg_checksum_matches_firmware(self):
        self.assertEqual(reference.ecg_checksum(64, 16, 16, 0x13570000), 0x19B26EA4)

    def test_ecg_segment_is_committed_physionet_data(self):
        segment = reference.json.loads(reference.ECG_SEGMENT.read_text())
        self.assertEqual(segment["record"], "100")
        self.assertEqual(segment["count"], 1024)
        self.assertEqual(len(segment["samples"]), 1024)

    def test_unknown_workload_rejected(self):
        with self.assertRaises(Exception):
            reference.expected_checksum("nope", 4, 1, 1, 0)


if __name__ == "__main__":
    unittest.main()
