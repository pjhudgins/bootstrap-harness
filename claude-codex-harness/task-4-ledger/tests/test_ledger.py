"""Task-4 tests: no login, no model, no tokens. Temporary ledgers and files stay in
../.runtime/test-tmp (rules.md: no writes outside the swimlane).

ConversationTests run the real Codex binary against an empty in-swimlane Codex home and
the scripted fake model (skipped if either is missing). Run from this folder:
  python -m unittest discover -s tests -t .
"""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import ledger_log  # first: switches off bytecode caching before scribe is imported
from ledger_log import scribe
import codex_client as cc
import conversation as conv
import tools
import ui

TASK_DIR = Path(__file__).resolve().parents[1]
SCRATCH = TASK_DIR / ".runtime" / "test-tmp"
OFFLINE_HOME = TASK_DIR.parent / ".runtime" / "offline-codex-home"
AGENT = "test-pilot:fake-model@claude-codex-harness"


def scratch_dir():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


def call(box, tool, **arguments):
    ok, text = box.call(tool, None, arguments)
    return ok, (json.loads(text) if ok else text)


class LedgerJournalTests(unittest.TestCase):
    def test_records_are_harness_entries_with_tags_and_a_trailer(self):
        with scratch_dir() as tmp:
            journal = ledger_log.LedgerJournal(tmp)
            first = journal.write("recv", message={"account": {"email": "x@example.com"}})
            journal.write("usage", token_usage={"total": {"totalTokens": 5}})
            journal.write("odd", value=float("nan"))  # not JSON: recorded as unrecordable
            with self.assertRaises(scribe.OpenRefused) as refused:  # one scribe per ledger
                ledger_log.LedgerJournal(tmp)
            self.assertEqual(refused.exception.code, "lease_held")
            journal.close()
            journal.close()  # twice is harmless
            self.assertIsNone(journal.write("late", note="after close"))
            view = scribe.load(tmp, ledger_log.LEDGER_NAME)
            self.assertTrue(view.head_closed)
            names = view.names()
            self.assertEqual(len(names), 3)
            self.assertTrue(all(n.startswith(f"harness/{journal.session}/") for n in names))
            entry = view.current(names[0])
            self.assertEqual(entry.id, first)
            self.assertEqual(entry.author, ledger_log.HARNESS_AUTHOR)
            self.assertEqual(entry.body["message"]["account"]["email"], "<redacted>")
            self.assertEqual(view.labels(names[0]), {"harness", "log.recv"})
            self.assertEqual(view.current(names[2]).body["unrecordable"], "bad_body")

    def test_each_chat_is_a_new_session_file_in_one_ledger(self):
        with scratch_dir() as tmp:
            for _ in range(2):  # the scribe waits for a new second itself (W§5.5)
                journal = ledger_log.LedgerJournal(tmp)
                journal.write("run", note="chat")
                journal.close()
            files = sorted((Path(tmp) / ledger_log.LEDGER_NAME).glob("*.ledger"))
            self.assertEqual(len(files), 2)
            self.assertEqual(len(scribe.load(tmp, ledger_log.LEDGER_NAME).sessions), 2)


class LedgerToolTests(unittest.TestCase):
    def setUp(self):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        self.journal = ledger_log.LedgerJournal(tmp.name)
        self.addCleanup(self.journal.close)
        self.journal.write("run", note="harness record")
        # a name outside harness/ that the harness protects by tag
        self.journal.scribe.write("shared/rules", "harness text", author=ledger_log.HARNESS_AUTHOR)
        self.journal.scribe.tag("shared/rules", "harness", author=ledger_log.HARNESS_AUTHOR)
        self.box = tools.Toolbox(self.journal.scribe, AGENT, fs_root=tmp.name)

    def test_agent_writes_are_attested_and_tagged(self):
        ok, result = call(self.box, "ledger_write", name="pilot/obs", body="first",
                          labels=["notes"])
        self.assertTrue(ok, result)
        entry = self.journal.scribe.current("pilot/obs")
        self.assertEqual(entry.author, AGENT)
        self.assertEqual(set(result["labels"]), {"pilot", "notes"})
        # an identity in the arguments is not an identity: the tool has no such field
        ok, message = call(self.box, "ledger_write", name="pilot/other", body="x",
                           author="founder")
        self.assertFalse(ok)
        self.assertNotIn("pilot/other", self.journal.scribe.names())

    def test_updates_cite_prev(self):
        _, first = call(self.box, "ledger_write", name="pilot/obs", body="one")
        ok, message = call(self.box, "ledger_write", name="pilot/obs", body="two")
        self.assertFalse(ok)
        self.assertIn("prev", message)
        ok, _ = call(self.box, "ledger_write", name="pilot/obs", body="two", prev=first["id"])
        self.assertTrue(ok)
        self.assertEqual(self.journal.scribe.current("pilot/obs").body, "two")

    def test_harness_entries_cannot_be_overwritten_or_retagged(self):
        harness_name = self.journal.scribe.names()[0]
        cases = [
            ("ledger_write", {"name": harness_name, "body": "x",
                              "prev": self.journal.scribe.current(harness_name).id}),
            ("ledger_write", {"name": "harness/new", "body": "x"}),
            ("ledger_write", {"name": "shared/rules", "body": "x",
                              "prev": self.journal.scribe.current("shared/rules").id}),
            ("ledger_write", {"name": "pilot/a", "body": "x", "labels": ["harness"]}),
            ("ledger_write", {"name": "pilot/b", "body": "x", "labels": ["log.fake"]}),
            ("ledger_write", {"name": "pilot/c", "body": "x", "labels": ["_reserved"]}),
            ("ledger_tag", {"name": harness_name, "label": "anything"}),
            ("ledger_tag", {"name": "shared/rules", "label": "harness", "remove": True}),
            ("ledger_tag", {"name": "pilot/d", "label": "harness"}),
            ("ledger_tag", {"name": "pilot/e", "label": "log.recv"}),
        ]
        before = len(self.journal.scribe.names(deleted=True))
        for tool, arguments in cases:
            with self.subTest(tool=tool, arguments=arguments):
                ok, message = call(self.box, tool, **arguments)
                self.assertFalse(ok)
        self.assertEqual(len(self.journal.scribe.names(deleted=True)), before)
        self.assertEqual(self.journal.scribe.current("shared/rules").body, "harness text")
        self.assertIn("harness", self.journal.scribe.labels("shared/rules"))

    def test_reading_and_listing(self):
        call(self.box, "ledger_write", name="pilot/obs", body="seen")
        _, listed = call(self.box, "ledger_list")
        self.assertEqual(listed["names"], ["pilot/obs", "shared/rules"])
        _, everything = call(self.box, "ledger_list", include_harness=True)
        self.assertEqual(everything["total"], 3)
        _, harness = call(self.box, "ledger_list", label="harness", include_harness=True)
        self.assertEqual(harness["total"], 2)
        _, read = call(self.box, "ledger_read", name=everything["names"][0], history=True)
        self.assertEqual(read["current"]["author"], ledger_log.HARNESS_AUTHOR)
        self.assertIn("harness record", read["current"]["body"])
        _, missing = call(self.box, "ledger_read", name="pilot/none")
        self.assertFalse(missing["exists"])


class FsToolTests(unittest.TestCase):
    def setUp(self):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.root = base / "nimoi"
        (self.root / "sub").mkdir(parents=True)
        (self.root / ".git").mkdir()
        (self.root / "a.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
        (self.root / "sub" / "b.md").write_text("# B\n", encoding="utf-8")
        (self.root / ".env").write_text("OPENAI_API_KEY=sk-should-never-be-read\n")
        (self.root / ".env.example").write_text("OPENAI_API_KEY=\n")
        (self.root / "secrets.json").write_text("{}")
        (self.root / "k.pem").write_text("x")
        (self.root / ".git" / "config").write_text("[core]\n")
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02")
        (self.root / "notes.txt").write_text(
            "key: sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 and fine text\n", encoding="utf-8")
        (base / "outside.txt").write_text("outside")
        journal = ledger_log.LedgerJournal(base / "ledgers")
        self.addCleanup(journal.close)
        self.box = tools.Toolbox(journal.scribe, AGENT, fs_root=self.root)
        self.outside = base / "outside.txt"

    def test_reads_inside_the_root(self):
        ok, result = call(self.box, "fs_read", path="a.txt", start_line=2, max_lines=1)
        self.assertTrue(ok, result)
        self.assertEqual(result["text"], "line2")
        self.assertEqual(result["lines"], "2-2 of 3")
        ok, listing = call(self.box, "fs_list", path=".")
        names = {e["name"]: e for e in listing["entries"]}
        self.assertFalse(names[".env"]["readable"])
        self.assertNotIn("readable", names[".env.example"])
        self.assertTrue(call(self.box, "fs_read", path="sub/b.md")[0])
        self.assertTrue(call(self.box, "fs_read", path=".env.example")[0])

    def test_refusals(self):
        for path in ["../outside.txt", str(self.outside), ".env", "secrets.json", "k.pem",
                     ".git/config", "blob.bin", "missing.txt", "sub", ""]:
            with self.subTest(path=path):
                ok, message = call(self.box, "fs_read", path=path)
                self.assertFalse(ok)
                self.assertNotIn("sk-should-never-be-read", message)
        self.assertFalse(call(self.box, "fs_list", path="..")[0])
        self.assertFalse(call(self.box, "fs_list", path=".git")[0])

    def test_key_like_strings_are_redacted(self):
        ok, result = call(self.box, "fs_read", path="notes.txt")
        self.assertTrue(ok)
        self.assertEqual(result["redactions"], 1)
        self.assertNotIn("sk-proj-ABCDEF", result["text"])
        self.assertIn("fine text", result["text"])

    def test_the_real_nimoi_root_has_onboarding(self):
        box = tools.Toolbox(self.box.ledger, AGENT)  # default root: nimoi
        ok, listing = call(box, "fs_list", path="origins")
        self.assertTrue(ok, listing)
        self.assertTrue(any(e["name"].startswith("onboarding_") for e in listing["entries"]))


class StubConversation:
    def __init__(self):
        self.events = conv.EventLog()
        self.state = "idle"
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True

    def interrupt(self):
        pass

    def close(self):
        self.events.publish("ended", journal="stub", summary={})


class ServerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stub = StubConversation()
        self.server = ui.make_server(self.stub)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_host_and_token(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403)
        body = json.dumps({"text": "hi"})
        self.assertEqual(self.request("POST", "/api/send", body)[0], 403)
        self.assertEqual(self.request("GET", "/api/events?token=wrong")[0], 403)
        status, page = self.request("GET", "/")
        marker = b'name="nimoi-token" content="'
        start = page.index(marker) + len(marker)
        token = page[start:page.index(b'"', start)].decode()
        self.assertEqual(self.request("POST", "/api/send", body, {"X-NIMOI-Token": token})[0], 202)
        self.assertEqual(self.stub.sent, ["hi"])


def codex_available():
    try:
        cc.find_codex()
        return OFFLINE_HOME.is_dir()
    except FileNotFoundError:
        return False


@unittest.skipUnless(codex_available(), "needs codex and ../.runtime/offline-codex-home")
class ConversationTests(unittest.TestCase):
    """Real Codex + scripted fake model + a temporary ledger: the whole path, no tokens."""

    def turn(self, c, text, timeout=60):
        seen = c.events.after(0, timeout=0)[-1]["seq"]
        self.assertTrue(c.send(text))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in c.events.after(seen, timeout=1):
                seen = event["seq"]
                if event["type"] == "turn_completed":
                    calls = [e for e in c.events.after(0, timeout=0) if e["type"] == "tool_call"]
                    return event, calls[-1] if calls else None
        self.fail(f"no turn_completed within {timeout} s")

    def test_tools_and_ledger_through_a_conversation(self):
        with scratch_dir() as tmp:
            c = conv.Conversation(codex_home=str(OFFLINE_HOME), fake=True, ledger_root=tmp)
            try:
                c.start()
                session = next(e for e in c.events.after(0, timeout=5) if e["type"] == "session")
                self.assertTrue(session["restrictions"]["model_tool_mode_direct"])
                done, tool = self.turn(c, 'tool: ledger_write {"name": "pilot/observations", '
                                          '"body": "the ledger tool works"}')
                self.assertEqual(done["status"], "completed")
                self.assertTrue(tool["success"], tool)
                _, tool = self.turn(c, 'tool: ledger_write {"name": "harness/forged", "body": "x"}')
                self.assertFalse(tool["success"])
                _, tool = self.turn(c, 'tool: fs_list {"path": "origins"}')
                self.assertTrue(tool["success"])
                _, tool = self.turn(c, 'tool: add {"a": 2, "b": 3}')
                self.assertEqual(tool["output"], '{"sum": 5}')
                offered = next(e for e in c.events.after(0, timeout=0)
                               if e["type"] == "offered_tools")
                self.assertEqual(offered["unreviewed"], [])
                self.assertTrue(set(c.toolbox.names) <= set(offered["names"]))
            finally:
                c.close()
            view = scribe.load(tmp, ledger_log.LEDGER_NAME)
            self.assertTrue(view.head_closed)
            note = view.current("pilot/observations")
            self.assertEqual(note.author, c.agent_author)
            self.assertIn("pilot", view.labels("pilot/observations"))
            self.assertIsNone(view.current("harness/forged"))
            harness = [n for n in view.names() if n.startswith("harness/")]
            self.assertTrue(harness)
            self.assertTrue(all(view.labels(n) >= {"harness"} for n in harness))
            self.assertTrue(all(view.current(n).author == ledger_log.HARNESS_AUTHOR
                                for n in harness))
            kinds = {view.current(n).body["kind"] for n in harness}
            self.assertTrue({"run", "send", "recv", "restrictions", "tool_call", "model_call",
                             "usage", "codex_home_changes", "summary"} <= kinds)
            start = next(view.current(n).body["message"]["params"] for n in harness
                         if view.current(n).body["kind"] == "send"
                         and view.current(n).body["message"].get("method") == "thread/start")
            self.assertIn("test pilot", start["developerInstructions"])
            self.assertIn("onboarding", start["developerInstructions"])


if __name__ == "__main__":
    unittest.main()
