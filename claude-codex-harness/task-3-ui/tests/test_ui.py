"""Task-3 tests: no login, no model, no tokens. Temporary files stay in ../.runtime/.

ServerSafetyTests use a stub conversation. ConversationTests run the real Codex binary
against an empty in-swimlane Codex home and the scripted fake model (skipped if either
is missing). Run from this folder:
  python -m unittest discover -s tests -t .
"""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import codex_client as cc
import conversation as conv
import ui

TASK_DIR = Path(__file__).resolve().parents[1]
SCRATCH = TASK_DIR / ".runtime" / "test-tmp"  # rules.md: no writes outside the swimlane
OFFLINE_HOME = TASK_DIR.parent / ".runtime" / "offline-codex-home"


def scratch_dir():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


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


class EventLogTests(unittest.TestCase):
    def test_sequence_and_waiting(self):
        log = conv.EventLog()
        self.assertEqual(log.after(0, timeout=0.01), [])
        threading.Timer(0.05, lambda: log.publish("x", n=1)).start()
        events = log.after(0, timeout=2)
        self.assertEqual([(e["seq"], e["type"], e["n"]) for e in events], [(1, "x", 1)])
        log.publish("y")
        self.assertEqual([e["type"] for e in log.after(1, timeout=0)], ["y"])


class ServerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stub = StubConversation()
        self.server = ui.make_server(self.stub)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def token(self):
        status, page = self.call("GET", "/")
        self.assertEqual(status, 200)
        marker = b'name="nimoi-token" content="'
        start = page.index(marker) + len(marker)
        return page[start:page.index(b'"', start)].decode()

    def test_page_carries_a_token(self):
        token = self.token()
        self.assertNotEqual(token, "__NIMOI_TOKEN__")
        self.assertGreater(len(token), 20)

    def test_foreign_host_is_refused(self):
        self.assertEqual(self.call("GET", "/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/", headers={"Host": f"evil.example:{self.port}"})[0], 403)

    def test_post_needs_the_token(self):
        body = json.dumps({"text": "hi"})
        self.assertEqual(self.call("POST", "/api/send", body)[0], 403)
        self.assertEqual(self.call("POST", "/api/send", body, {"X-NIMOI-Token": "wrong"})[0], 403)
        self.assertEqual(self.stub.sent, [])
        status, _ = self.call("POST", "/api/send", body, {"X-NIMOI-Token": self.token()})
        self.assertEqual(status, 202)
        self.assertEqual(self.stub.sent, ["hi"])

    def test_events_need_the_token(self):
        self.assertEqual(self.call("GET", "/api/events?token=wrong")[0], 403)

    def test_static_files_only(self):
        self.assertEqual(self.call("GET", "/static/app.js")[0], 200)
        self.assertEqual(self.call("GET", "/static/../ui.py")[0], 404)
        self.assertEqual(self.call("GET", "/ui.py")[0], 404)


def codex_available():
    try:
        cc.find_codex()
        return OFFLINE_HOME.is_dir()
    except FileNotFoundError:
        return False


@unittest.skipUnless(codex_available(), "needs codex and ../.runtime/offline-codex-home")
class ConversationTests(unittest.TestCase):
    """Real Codex + scripted fake model: the whole conversation path, no tokens."""

    def wait_for(self, conversation, kind, after=0, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in conversation.events.after(after, timeout=1):
                after = event["seq"]
                if event["type"] == kind:
                    return event, after
        self.fail(f"no {kind} event within {timeout} s")

    def test_add_through_a_conversation(self):
        with scratch_dir() as tmp:
            c = conv.Conversation(codex_home=str(OFFLINE_HOME), fake=True, runs_dir=tmp)
            try:
                c.start()
                session, seen = self.wait_for(c, "session")
                self.assertTrue(session["restrictions"]["model_tool_mode_direct"])
                self.assertTrue(c.send("please add 2 and 3"))
                self.assertFalse(c.send("while busy"))  # one turn at a time
                done, _ = self.wait_for(c, "turn_completed", seen)
                self.assertEqual(done["status"], "completed")
                events = c.events.after(0, timeout=0)
                kinds = [e["type"] for e in events]
                tool = next(e for e in events if e["type"] == "tool_call")
                self.assertEqual((tool["arguments"], tool["output"]), ({"a": 2, "b": 3}, '{"sum": 5}'))
                reply = next(e for e in events if e["type"] == "agent_message")
                self.assertIn("5", reply["text"])
                offered = next(e for e in events if e["type"] == "offered_tools")
                self.assertEqual(offered["unreviewed"], [])
                calls = [e for e in events if e["type"] == "model_call"]
                self.assertTrue(calls and all(e["reviewed"] for e in calls))
                self.assertIn("usage", kinds)
                # a second turn gets its own agent message (regression: ids once repeated)
                _, seen = self.wait_for(c, "state", events[-1]["seq"] - 1)
                self.assertTrue(c.send("hello again"))
                self.wait_for(c, "turn_completed", seen)
                replies = [e for e in c.events.after(0, timeout=0) if e["type"] == "agent_message"]
                self.assertEqual(len({e["item_id"] for e in replies}), len(replies))
                self.assertIn("hello again", replies[-1]["text"])
            finally:
                c.close()
            ended = c.events.after(0, timeout=0)[-1]
            self.assertEqual(ended["type"], "ended")
            self.assertEqual(ended["summary"]["tool_calls"], 1)
            records = [json.loads(line) for p in Path(tmp).glob("*.jsonl")
                       for line in p.read_text(encoding="utf-8").splitlines()]
            kinds = {r["kind"] for r in records}
            self.assertTrue({"send", "recv", "restrictions", "tool_call", "model_call",
                             "usage", "codex_home_changes", "summary"} <= kinds)


if __name__ == "__main__":
    unittest.main()
