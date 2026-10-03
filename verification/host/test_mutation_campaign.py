"""The planted-bug campaign (scripts/mutation_campaign.py) stays in step with the
Aster core's RTL: every mutant's anchor occurs exactly once, and every mutant
changes something."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import mutation_campaign


class MutationCampaign(unittest.TestCase):
    def test_every_anchor_occurs_once_in_the_rtl(self):
        sources = mutation_campaign.sources()
        stale = [name for name, (which, old, _) in mutation_campaign.MUTANTS.items()
                 if sources[which].count(old) != 1]
        self.assertEqual(stale, [], "update the mutants whose RTL changed")

    def test_every_mutant_changes_the_rtl(self):
        for name, (_, old, new) in mutation_campaign.MUTANTS.items():
            self.assertNotEqual(old, new, name)


if __name__ == "__main__":
    unittest.main()
