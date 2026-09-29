"""Ledger tools: bounds, protection, authorship, pinned revisions, logging and refusal records."""

import unittest

from tests.support import HARNESS, HarnessCase, scribe

from bounds import Bounds  # noqa: E402
from ledger_tools import Denied, LedgerGuard, denied_reply, label_reserved  # noqa: E402

AGENT = "pilot:claude-sonnet-5@claude-anthropic-harness"


class GuardTests(HarnessCase):
    def test_write_labels_bounds_and_reserved_labels(self):
        out = self.top.guard.write("pilot/notes", "hi", labels=["harness-notes"])  # exact reservation only
        self.assertEqual(set(out["labels"]), {"pilot", "agent.pilot", "harness-notes"})
        with self.assertRaisesRegex(Denied, "ledger.write"):
            self.top.guard.write("elsewhere/x", "hi")
        for label in ("agent.pilot.1", "harness", "log.x"):
            self.assertTrue(label_reserved(label))
            with self.assertRaises(Denied, msg=label):
                self.top.guard.write("pilot/l", "x", labels=[label])

    def test_harness_protection_and_ownership(self):
        rec = self.bus.log.write("status", state="idle", agent="pilot")
        with self.assertRaisesRegex(Denied, "belong to the harness"):
            self.top.guard.write(rec["name"], "x", prev=rec["id"])
        self.ledger.write("pilot/founder-note", "x", author="founder")
        with self.assertRaisesRegex(Denied, "only update its own"):
            self.top.guard.write("pilot/founder-note", "y", prev=self.ledger.current("pilot/founder-note").id)

    def test_read_bounds_and_pinned_revisions(self):
        v1 = self.top.guard.write("pilot/a", 1)["written"]
        v2 = self.top.guard.write("pilot/a", 2, prev=v1)["written"]
        self.ledger.write("other/b", 2, author="founder")
        narrow = LedgerGuard(scribe, self.ledger, AGENT, Bounds.parse("ledger.read pilot"), "pilot.1")
        self.assertEqual(narrow.read("pilot/a")["current"]["body"], 2)
        old = narrow.read(entry_id=v1)
        self.assertEqual((old["revision"]["body"], old["is_current"]), (1, False))
        self.assertTrue(narrow.read(entry_id=v2)["is_current"])
        with self.assertRaisesRegex(Denied, "ledger.read"):
            narrow.read("other/b")
        with self.assertRaisesRegex(Denied, "ledger.read"):
            narrow.read_line(self.ledger.current("other/b").id)
        self.assertEqual([r["name"] for r in narrow.list()["names"]], ["pilot/a"])

    def test_denials_are_recorded(self):
        denied_reply(self.bus, "pilot.1", "mcp__fs__write", {"path": "/x"}, "outside fs.write")
        rec = self.records("tool_denied")[-1]
        self.assertEqual(rec["agent"], "pilot.1")
        self.assertIn("agent.pilot.1", self.ledger.labels(rec["name"]))


class LoggingTests(HarnessCase):
    def test_records_and_text_entries(self):
        self.top.turn = 3
        rec = self.top.pub("status", state="idle")
        self.assertEqual((rec["agent"], rec["turn"]), ("pilot", 3))
        self.assertEqual(self.ledger.labels(rec["name"]), {"harness", "log.status", "agent.pilot"})
        self.assertEqual(self.ledger.current(rec["name"]).author, HARNESS)
        text = self.bus.publish_text("hi", author="human:session-user", direction="to_agent", agent="pilot", turn=3)
        entry = self.ledger.current(text["name"])
        self.assertEqual((entry.body, entry.author), ("hi", "human:session-user"))
        self.assertEqual(self.ledger.labels(text["name"]), {"harness", "log.text", "agent.pilot"})

    def test_unencodable_body_falls_back(self):
        rec = self.bus.log.write("usage", cost=float("nan"), agent="pilot")
        self.assertIn("ledger_note", rec)
        self.assertIn("nan", self.ledger.current(rec["name"]).body["unrecordable"])


if __name__ == "__main__":
    unittest.main()
