"""The rules before any tool: the bounds notation (bounds.py) and the fixed filesystem
rules (paths.py)."""

import json
import os
import unittest
from pathlib import Path

from tests.helpers import ROOT, make_tree, scratch_dir, short_name  # helpers first: see its imports
import bounds as bd
import paths


class BoundsTests(unittest.TestCase):
    def test_entry_semantics(self):
        b = bd.Bounds({"fs.read": ["origins/", "notes/a.md"], "ledger.read": ["*"]})
        self.assertTrue(b.allows("fs.read", "origins"))           # the folder itself
        self.assertTrue(b.allows("fs.read", "origins/x/y.md"))
        self.assertFalse(b.allows("fs.read", "originsX/y.md"))    # segments, not strings
        self.assertTrue(b.allows("fs.read", "notes/a.md"))
        self.assertFalse(b.allows("fs.read", "notes/a.md.bak"))   # exact means exact
        self.assertTrue(b.allows("ledger.read", "anything/at/all"))
        self.assertFalse(b.allows("fs.write", "origins/x"))       # missing key: nothing
        self.assertFalse(b.allows("ledger.write", "x"))
        if bd.FS_CASE_INSENSITIVE:
            self.assertTrue(b.allows("fs.read", "ORIGINS/NIMOI.md"))
            self.assertFalse(bd.Bounds({"ledger.read": ["A/"]}).allows("ledger.read", "a/x"))

    def test_ledger_write_grants_read(self):
        b = bd.Bounds({"ledger.write": ["agent/pilot.1/"]})
        self.assertTrue(b.allows("ledger.read", "agent/pilot.1/notes"))
        self.assertFalse(b.allows("ledger.read", "agent/other"))
        self.assertEqual(b["ledger.read"], ())  # the notation itself is unchanged
        bd.child_bounds(b, {"ledger.read": ["agent/pilot.1/notes"]})  # inside the write grant

    def test_malformed(self):
        for bad in [{"fs.read": ["/etc/"]}, {"fs.read": ["C:/x"]}, {"fs.read": ["a/../b"]},
                    {"fs.read": [""]}, {"fs.read": "a//b"}, {"fs.reed": ["a"]},
                    {"ledger.write": [3]}, {"fs.exec": {"a": 1}}]:
            with self.subTest(bad=bad):
                with self.assertRaises(bd.BoundsError):
                    bd.Bounds(bad)

    def test_subset(self):
        parent = bd.Bounds(ROOT)
        ok = [{"fs.read": ["origins/"]}, {"fs.write": ["workspace/drafts/"]},
              {"fs.exec": ["scripts/hello_safe.py"]}, {"ledger.write": ["agent/notes/"]},
              {"fs.read": ["*"], "ledger.read": ["*"]}, {}]
        for requested in ok:
            with self.subTest(ok=requested):
                bd.child_bounds(parent, requested)
        narrow = bd.Bounds({"fs.read": ["origins/"], "fs.write": ["workspace/a.md"],
                            "ledger.write": ["notes/x"]})
        bad = [{"fs.read": ["*"]}, {"fs.read": ["bootstrap-ledger/"]},
               {"fs.write": ["workspace/"]},   # a subtree does not fit inside one file
               {"ledger.write": ["notes/"]}, {"fs.exec": ["scripts/"]}]
        for requested in bad:
            with self.subTest(bad=requested):
                with self.assertRaises(bd.BoundsError) as refused:
                    bd.child_bounds(narrow, requested)
                self.assertIn("is not inside your", str(refused.exception))

    def test_write_stays_out_of_script_folders(self):
        refused = [({"fs.write": ["a/"], "fs.exec": ["a/b.py"]}),
                   ({"fs.write": ["s/new.py"], "fs.exec": ["s/run.py"]}),   # one folder
                   ({"fs.write": ["s/sub/"], "fs.exec": ["s/run.py"]}),     # below it
                   ({"fs.write": ["w/"], "fs.exec": ["top.py"]}),           # nimoi's own
                   ({"fs.write": ["*"], "fs.exec": ["s/"]})]
        for mapping in refused:
            with self.subTest(refused=mapping):
                problems = bd.Bounds(mapping).invariant_problems()
                self.assertTrue(problems)
                self.assertIn("where scripts run", problems[0])
        allowed = [{"fs.write": ["workspace/"], "fs.exec": ["scripts/"]},
                   {"fs.write": ["workspace/a.md"], "fs.exec": ["scripts/x.py"]}]
        for mapping in allowed:
            with self.subTest(allowed=mapping):
                self.assertEqual(bd.Bounds(mapping).invariant_problems(), [])
        with self.assertRaises(bd.BoundsError):  # a child is held to it too
            bd.child_bounds(bd.Bounds({"fs.write": ["s/"], "fs.exec": ["s/"]}),
                            {"fs.write": ["s/a.py"], "fs.exec": ["s/b.py"]})

    def test_one_rendering(self):
        b = bd.Bounds({"ledger.write": ["notes/"], "fs.read": ["origins/", "origins/"]})
        self.assertEqual(json.loads(b.render()), {"fs.read": ["origins/"], "fs.write": [],
                                                  "fs.exec": [], "ledger.read": [],
                                                  "ledger.write": ["notes/"]})
        self.assertEqual(list(json.loads(b.render())), list(bd.KEYS))


class PathTests(unittest.TestCase):
    def setUp(self):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "nimoi"
        make_tree(self.root)
        self.policy = paths.PathPolicy(self.root)

    def refused(self, path, must_exist=True):
        with self.assertRaises(paths.PathRefused) as caught:
            self.policy.resolve(path, must_exist=must_exist)
        return str(caught.exception)

    def test_inside_nimoi(self):
        self.assertEqual(self.policy.resolve("notes/a.txt")[1], "notes/a.txt")
        self.assertEqual(self.policy.resolve(".")[1], "")
        self.assertIn("outside", self.refused("../outside.txt", must_exist=False))
        self.assertIn("outside", self.refused(str(self.root.parent / "x.txt"), must_exist=False))
        # A UNC path is refused before anything touches the disk or the network.
        self.assertIn("outside", self.refused(r"\\nimoi-test-no-such-host\share\x.txt"))

    def test_harness_state_and_git_are_never_touched(self):
        for path in ["notes/x.ledger", "notes/lease.json", ".runtime/state.txt", ".runtime",
                     "notes/X.LEDGER", ".Runtime/state.txt"]:
            with self.subTest(path=path):
                self.assertIn("ledger tools", self.refused(path))
        for path in [".git/config", ".GIT/config", "notes/.git/x"]:
            with self.subTest(path=path):
                self.assertIn(".git", self.refused(path, must_exist=False))

    def test_windows_short_names_resolve_to_the_real_name(self):
        alias = short_name(self.root / "notes" / "x.ledger")
        if alias is None:
            self.skipTest("this volume makes no 8.3 short names")
        self.assertIn("ledger tools", self.refused(f"notes/{alias}"))  # e.g. X~1.LED

    def test_names_windows_would_rewrite(self):
        for path in ["workspace/a.txt.", "workspace/a.txt ", "workspace/a.txt:stream",
                     "workspace/NUL", "workspace/con.txt", "workspace/x.ledger./y"]:
            with self.subTest(path=path):
                self.assertIn("path part", self.refused(path, must_exist=False))

    def test_secret_names(self):
        for name in [".env", ".env.local", "prod.env", "id_rsa", "auth.json", "x.PEM"]:
            with self.subTest(name=name):
                self.assertTrue(paths.secret_name(name))
        self.assertFalse(paths.secret_name(".env.example"))
        with self.assertRaises(paths.PathRefused):
            paths.check_not_secret(self.root / ".env")


if __name__ == "__main__":
    unittest.main()
