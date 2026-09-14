"""Pure host checks for the frozen AsterBench v7 contract."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import asterbench_v7 as bench


class AsterBenchV7Plan(unittest.TestCase):
    def test_primary_plan_is_frozen(self):
        plan = bench.study_plan()
        self.assertEqual(len(plan), 96)
        self.assertEqual([item["capture"] for item in plan], list(range(1, 97)))
        self.assertEqual({item["placement"] for item in plan}, set(bench.PLACEMENTS))
        self.assertEqual({item["l1"] for item in plan}, {0, 1})
        self.assertEqual({(item["m"], item["n"], item["k"]) for item in plan}, set(bench.STUDY_SHAPES))
        self.assertEqual(len({item["seed"] for item in plan}), 96)

    def test_parser_rejects_truncation_and_unknown_fields(self):
        for line in ("ASTERBENCH,\n", "ASTERBENCH,unknown=0\n", "ASTERBENCH,version=7"):
            with self.subTest(line=line), self.assertRaises(bench.ValidationError):
                bench.parse_record(line)


if __name__ == "__main__":
    unittest.main()
