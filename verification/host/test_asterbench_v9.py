"""Pure host checks for the frozen AsterBench v9 contract."""

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v9 as bench

MODEL = ROOT / "docs" / "results" / "phase11" / "model.json"


def make_fields(model, image=0, method="npu"):
    in_features = model["layers"][0]["in"]
    logits = bench.infer(model, model["test"]["images"][image * in_features:(image + 1) * in_features])
    best = max(range(len(logits)), key=logits.__getitem__)
    fields = {
        "name": "mnist_mlp", "method": method, "status": "PASS", "model": model["hash"],
        "logits": bytes([value & 0xFF for value in logits]).hex(),
        "version": 9, "image": image, "label": model["test"]["labels"][image],
        "class": best, "expected": model["test"]["reference_classes"][image], "logit_match": 1,
        "h0_cycles": f"0x{400000:016x}", "h0_retired": f"0x{60000:016x}",
        "clock_hz": 31_250_000, "l1": 1, "sync_memory": 0,
    }
    if method == "npu":
        fields.update(npu_job_cycles=f"0x{380000:016x}", npu_compute_cycles=f"0x{6000:016x}",
                      npu_tiles=11, npu_bytes_read=31776, npu_bytes_written=168)
    else:
        fields.update(npu_job_cycles="0x" + "0" * 16, npu_compute_cycles="0x" + "0" * 16,
                      npu_tiles=0, npu_bytes_read=0, npu_bytes_written=0)
    return fields


def render(fields):
    return "ASTERBENCH," + ",".join(f"{key}={fields[key]}" for key in fields) + "\n"


class AsterBenchV9(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = bench.load_model(MODEL)

    def test_valid_npu_record(self):
        result = bench.validate_line(render(make_fields(self.model)), self.model, method="npu")
        self.assertEqual(result["method"], "npu")

    def test_valid_non_npu_record(self):
        result = bench.validate_line(render(make_fields(self.model, method="scalar")), self.model)
        self.assertEqual(result["method"], "scalar")

    def test_complete_stream(self):
        count = len(self.model["test"]["labels"])
        lines = [render(make_fields(self.model, image=i)) for i in range(count)]
        results = bench.validate_stream(lines, self.model, method="npu", complete=True)
        self.assertEqual(len(results), count)

    def test_complete_rejects_missing_image(self):
        lines = [render(make_fields(self.model, image=i)) for i in range(5)]
        with self.assertRaises(bench.ValidationError):
            bench.validate_stream(lines, self.model, complete=True)

    def test_parser_rejects_truncation_and_unknown_fields(self):
        for line in ("ASTERBENCH,\n", "ASTERBENCH,unknown=0\n", "ASTERBENCH,version=9"):
            with self.subTest(line=line), self.assertRaises(bench.ValidationError):
                bench._parse_fields(line)

    def test_validator_rejects_mutations(self):
        base = make_fields(self.model)
        mutations = {
            "version": 8,
            "status": "FAIL",
            "method": "bogus",
            "model": "0" * 64,
            "image": 999,
            "label": (base["label"] + 1) % 10,
            "expected": (base["expected"] + 1) % 10,
            "logit_match": 0,
            "class": (base["class"] + 1) % 10,
            "logits": "00" * 10,
            "h0_cycles": "0x" + "0" * 16,
            "clock_hz": 1,
            "l1": 7,
        }
        for key, value in mutations.items():
            fields = make_fields(self.model)
            fields[key] = value
            with self.subTest(key=key), self.assertRaises(bench.ValidationError):
                bench.validate_line(render(fields), self.model, method="npu")

    def test_non_npu_with_npu_activity_is_rejected(self):
        fields = make_fields(self.model, method="scalar")
        fields["npu_tiles"] = 1
        with self.assertRaises(bench.ValidationError):
            bench.validate_line(render(fields), self.model)

    def test_npu_without_activity_is_rejected(self):
        fields = make_fields(self.model, method="npu")
        fields["npu_job_cycles"] = "0x" + "0" * 16
        with self.assertRaises(bench.ValidationError):
            bench.validate_line(render(fields), self.model, method="npu")

    def test_duplicate_and_missing_fields(self):
        fields = make_fields(self.model)
        body = render(fields)[len("ASTERBENCH,"):-1]
        first = body.split(",", 1)[0]
        with self.assertRaises(bench.ValidationError):
            bench.validate_line("ASTERBENCH," + first + "," + body + "\n", self.model)
        trimmed = render({key: value for key, value in fields.items() if key != "class"})
        with self.assertRaises(bench.ValidationError):
            bench.validate_line(trimmed, self.model)


if __name__ == "__main__":
    unittest.main()
