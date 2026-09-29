"""The bounds notation: parsing, the one rule, the child rule, the invariant, path resolution."""

import unittest

from tests.support import TOP  # noqa: F401  (also sets up sys.path)

from bounds import Bounds, BoundsError, fs_target  # noqa: E402
from scribe_import import NIMOI_ROOT  # noqa: E402


class NotationTests(unittest.TestCase):
    def test_render_is_canonical_and_round_trips(self):
        self.assertIn("fs.read       / !/candidate_repos", TOP.render())
        self.assertEqual(Bounds.parse(TOP.render()), TOP)

    def test_missing_scope_permits_nothing(self):
        b = Bounds.parse("fs.read /origins")
        self.assertFalse(b.permits("fs.write", "/origins/x"))
        self.assertFalse(b.permits("ledger.read", "pilot/x"))
        self.assertTrue(b.permits("fs.read", "/origins/onboarding_1.12.md"))

    def test_segment_boundaries_and_case(self):
        b = Bounds.parse("fs.read /origins\nledger.read pilot")
        self.assertFalse(b.permits("fs.read", "/origins2/x"))
        self.assertTrue(b.permits("fs.read", "/ORIGINS/x"))  # Windows paths ignore case
        self.assertTrue(b.permits("ledger.read", "pilot/a"))
        self.assertFalse(b.permits("ledger.read", "pilot2/a"))
        self.assertFalse(b.permits("ledger.read", "Pilot/a"))  # ledger names do not

    def test_exclusion_wins(self):
        self.assertFalse(TOP.permits("fs.read", "/candidate_repos/x/README.md"))
        self.assertTrue(TOP.permits("fs.read", "/candidate_repos_notes.md"))

    def test_parse_errors(self):
        for bad in ("fs.reed /", "fs.read /\nfs.read /x", "fs.read origins", "fs.read /a/../b",
                    "fs.read /a/*.md", "ledger.read !*", "ledger.read bad name!"):
            with self.assertRaises(BoundsError, msg=bad):
                Bounds.parse(bad)

    def test_invariant(self):
        self.assertEqual(TOP.invariant_problems(), [])
        for text in ("fs.write /t\nfs.exec /t/scripts", "fs.write /t/scripts/x\nfs.exec /t/scripts",
                     "fs.write /\nfs.exec /s"):
            self.assertTrue(Bounds.parse(text).invariant_problems(), text)

    def test_child_subset_and_inherited_exclusions(self):
        eff = Bounds.parse("fs.read /\nledger.read pilot/").within(TOP)
        self.assertEqual(eff.render().splitlines()[0].split(), ["fs.read", "/", "!/candidate_repos"])
        self.assertFalse(eff.permits("fs.read", "/candidate_repos/x"))
        for text in ("fs.write /elsewhere", "fs.exec /work", "ledger.write notpilot/x", "fs.read /candidate_repos/x"):
            with self.assertRaises(BoundsError, msg=text):
                Bounds.parse(text).within(TOP)
        Bounds.parse("ledger.read *\nledger.write pilot").within(TOP)  # equal to the parent's: fine

    def test_with_exclusion(self):
        b = Bounds.parse("ledger.read *")
        self.assertFalse(b.with_exclusion("ledger.read", "log").permits("ledger.read", "log/x/1"))
        narrow = Bounds.parse("ledger.read pilot")
        self.assertIs(narrow.with_exclusion("ledger.read", "log"), narrow)  # nothing to exclude


class PathTests(unittest.TestCase):
    def test_fs_target_forms(self):
        self.assertEqual(fs_target(NIMOI_ROOT, NIMOI_ROOT), "/")
        self.assertEqual(fs_target("origins/x.md", NIMOI_ROOT), "/origins/x.md")
        self.assertEqual(fs_target("/origins/x.md", NIMOI_ROOT), "/origins/x.md")  # notation
        self.assertEqual(fs_target("\\origins\\x.md", NIMOI_ROOT), "/origins/x.md")  # notation, backslashes
        self.assertEqual(fs_target(NIMOI_ROOT / "origins" / "x.md", NIMOI_ROOT), "/origins/x.md")
        self.assertIsNone(fs_target(NIMOI_ROOT.parent / "x", NIMOI_ROOT))
        self.assertIsNone(fs_target("/origins/../../x", NIMOI_ROOT))

    def test_short_names_resolve_to_long_names(self):
        # Peer review 2026-09-25: deny rules checked against a spelling fail open under 8.3 aliases.
        if not (NIMOI_ROOT / "CANDID~1").exists():
            self.skipTest("no 8.3 alias for candidate_repos on this volume")
        self.assertEqual(fs_target("CANDID~1/README.md", NIMOI_ROOT), "/candidate_repos/README.md")
        self.assertFalse(TOP.permits("fs.read", fs_target("CANDID~1", NIMOI_ROOT)))


if __name__ == "__main__":
    unittest.main()
