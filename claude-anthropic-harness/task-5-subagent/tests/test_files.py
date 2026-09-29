"""Harness-owned file tools: one resolver, fixed denials, listing, paged reads, search, pinned writes."""

import hashlib
import unittest

from tests.support import TOP, HarnessCase  # noqa: F401

from bounds import Bounds  # noqa: E402
from files import FsAccess  # noqa: E402
from ledger_tools import Denied  # noqa: E402
from scribe_import import NIMOI_ROOT  # noqa: E402


class AccessTests(HarnessCase):
    def fs(self, bounds=TOP) -> FsAccess:
        return FsAccess(self.root, bounds, ["/scripts"])

    def test_fixed_denials_whatever_the_bounds(self):
        fs = self.fs(Bounds.parse("fs.read /\nfs.write /"))
        for path, why in (("/docs/.env", "secret"), ("/ledgers/old/20260101T000000Z.ledger", "raw ledger"),
                          ("/ledgers/old/lease.json", "raw ledger")):
            self.assertIn(why, fs.check("fs.read", path).reason, path)
        self.assertIn("scripts directory", fs.check("fs.write", "/scripts/new.py").reason)

    def test_bounds_and_forms(self):
        fs = self.fs()
        self.assertTrue(fs.check("fs.read", "/docs/a.md").allow)
        self.assertTrue(fs.check("fs.read", str(self.root / "docs" / "a.md")).allow)
        self.assertTrue(fs.check("fs.read", "docs\\a.md").allow)
        self.assertFalse(fs.check("fs.read", "/candidate_repos/x/README.md").allow)
        self.assertFalse(fs.check("fs.read", "/docs/../candidate_repos/x").allow)
        self.assertFalse(fs.check("fs.read", str(self.tmp / "outside.txt")).allow)
        self.assertFalse(fs.check("fs.write", "/docs/new.md").allow)
        self.assertFalse(fs.check("fs.read", "").allow)

    def test_real_short_names_cannot_dodge_exclusions(self):
        if not (NIMOI_ROOT / "CANDID~1").exists():
            self.skipTest("no 8.3 alias for candidate_repos on this volume")
        fs = FsAccess(NIMOI_ROOT, TOP)
        for path in ("CANDID~1", "CANDID~1/README.md", "/CANDID~1/x"):
            self.assertFalse(fs.check("fs.read", path).allow, path)
        with self.assertRaises(Denied):
            fs.search("x", "CANDID~1")
        names = [e["name"] for e in fs.list_dir("/", limit=200)["entries"]]
        self.assertNotIn("candidate_repos", names)

    def test_list_hides_what_it_refuses(self):
        out = self.fs().list_dir("/docs")
        self.assertEqual([e["name"] for e in out["entries"]], ["a.md", "big.bin", "sub"])
        self.assertEqual(out["hidden"], 1)  # .env
        root = self.fs().list_dir("/")
        self.assertNotIn("candidate_repos", [e["name"] for e in root["entries"]])

    def test_read_pages(self):
        fs = self.fs()
        first = fs.read_file("/origins/onboarding_1.02.md", 1, 10)
        self.assertEqual((first["start_line"], first["end_line"], first["next_offset"]), (1, 10, 11))
        last = fs.read_file("/origins/onboarding_1.02.md", 21, 100)
        self.assertEqual((last["end_line"], last["next_offset"], last["total_lines"]), (30, None, 30))
        self.assertEqual(last["sha256"], hashlib.sha256((self.root / "origins" / "onboarding_1.02.md").read_bytes()).hexdigest())
        with self.assertRaisesRegex(Denied, "binary"):
            fs.read_file("/docs/big.bin")
        with self.assertRaisesRegex(Denied, "not a file"):
            fs.read_file("/docs")

    def test_search_prunes_and_hides(self):
        out = self.fs().search("needle")
        paths = sorted(m["path"] for m in out["matches"])
        self.assertEqual(paths, ["/docs/a.md", "/docs/sub/b.md"])  # not .env, .ledger, binary or excluded
        self.assertIn("/candidate_repos", out["pruned"])
        only_b = self.fs().search("needle", "/docs", glob="b.*")
        self.assertEqual([m["path"] for m in only_b["matches"]], ["/docs/sub/b.md"])
        self.assertEqual(only_b["files_searched"], 1)  # live 2026-09-25: the count had included glob misses
        self.assertGreater(only_b["files_walked"], 1)
        with self.assertRaisesRegex(Denied, "regular expression"):
            self.fs().search("(")


class WriteTests(HarnessCase):
    def write(self, entry_id, path, expected=None):
        return self.top.file_tools.write(entry_id, path, expected)

    def test_pinned_revision_exact_bytes_and_record(self):
        body = "print('hi')\r\nline 2\n"
        v1 = self.top.guard.write("pilot/draft", body)["written"]
        self.top.guard.write("pilot/draft", "changed later", prev=v1)
        out = self.write(v1, "/work/drafts/draft.py")
        data = (self.root / "work" / "drafts" / "draft.py").read_bytes()
        self.assertEqual(data, body.encode("utf-8"))  # the pinned revision, not the current one
        self.assertEqual(out["sha256"], hashlib.sha256(data).hexdigest())
        started, done = self.records("file_write_started")[-1], self.records("file_write")[-1]
        self.assertLess(started["seq"], done["seq"])  # recorded before the write
        self.assertEqual((done["entry_id"], done["started"]), (v1, started["id"]))

    def test_create_only_and_guarded_replace(self):
        v1 = self.top.guard.write("pilot/data", {"a": 1})["written"]
        self.write(v1, "/work/data.json")
        with self.assertRaisesRegex(Denied, "exists"):
            self.write(v1, "/work/data.json")
        with self.assertRaisesRegex(Denied, "has changed"):
            self.write(v1, "/work/data.json", "0" * 64)
        current = hashlib.sha256((self.root / "work" / "data.json").read_bytes()).hexdigest()
        out = self.write(v1, "/work/data.json", current)
        self.assertEqual(out["replaced_sha256"], current)
        with self.assertRaisesRegex(Denied, "does not exist"):
            self.write(v1, "/work/missing.json", current)

    def test_refusals_write_nothing(self):
        v1 = self.top.guard.write("pilot/draft", "x")["written"]
        self.ledger.write("other/secret", "x", author="founder")
        for entry_id, path, why in ((v1, "/scripts/promoted.py", "scripts directory"),
                                    (v1, "/docs/x.py", "outside"), (v1, "/work/.env", "secret"),
                                    (v1, "/work/x.ledger", "raw ledger"), ("pilot/draft", "/work/x", "not a ledger id"),
                                    ("20990101T000000Z:1", "/work/x", "not a body revision")):
            with self.assertRaisesRegex(Denied, why, msg=path):
                self.write(entry_id, path)
        self.assertFalse((self.root / "scripts" / "promoted.py").exists())
        self.assertEqual(self.records("file_write_started"), [])

    def test_no_record_no_write(self):
        v1 = self.top.guard.write("pilot/draft", "x")["written"]
        self.ledger.close()  # every later record fails: the harness must not act
        with self.assertRaisesRegex(Denied, "could not record"):
            self.write(v1, "/work/after-failure.txt")
        self.assertFalse((self.root / "work" / "after-failure.txt").exists())
        self.ledger = scribe_reopen(self)


def scribe_reopen(case):
    from tests.support import scribe
    return scribe.Scribe.open(case.tmp, "t", session_author="harness:test")


if __name__ == "__main__":
    unittest.main()
