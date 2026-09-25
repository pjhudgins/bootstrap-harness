"""Offline integration checks using the actual scribe; no model calls."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_tools import ReadFiles, registry
from ledger_store import AGENT_AUTHOR, HARNESS_AUTHOR, LedgerJournal, LedgerUnavailable, scribe
from protocol import Client, RpcError, TASK
from serve import make_server
from session import Conversation


class Task4Tests(unittest.TestCase):
    def setUp(self):
        runtime = TASK / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(runtime.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.c = Conversation(self.root)
        self.j = self.c.journal
        self.addCleanup(self.j.close)

    def test_harness_logs_have_attested_author_tags_and_valid_trailer(self):
        self.j.write("user_message", {"id": "u1", "text": "hello"})
        path = self.j.path
        self.j.close()
        view = scribe.load(self.j.root, self.c.run_id)
        self.assertEqual(view.findings, [])
        entry = view.current("harness/events/00000001")
        self.assertEqual(entry.author, HARNESS_AUTHOR)
        self.assertEqual(view.labels(entry.name), {"harness", "log-user_message", "message"})
        self.assertEqual(entry.body["data"]["text"], "hello")
        lines = path.read_bytes().splitlines(keepends=True)
        self.assertEqual(json.loads(lines[-1])["hash"], "sha256:" + hashlib.sha256(b"".join(lines[:-1])).hexdigest())
        self.assertFalse((path.parent / "lease.json").exists())
        self.assertEqual(list(self.root.rglob("*.jsonl")), [])

    def test_agent_revisions_preserve_history_and_require_prev(self):
        a = self.j.agent_write("agent/note", "first", None, ["agent", "agent-observation"])
        before = self.j.bytes()
        with self.assertRaisesRegex(ValueError, "stale_prev"):
            self.j.agent_write("agent/note", "stale", None, ["agent-new-tag"])
        self.assertEqual(self.j.bytes(), before)
        b = self.j.agent_write("agent/note", "second", a["id"], [])
        self.assertEqual(b["author"], AGENT_AUTHOR)
        history = self.j.read_entry("agent/note", True)["history"]
        self.assertEqual([e["body"] for e in history], ["first", "second"])
        self.assertEqual(history[1]["prev"], a["id"])

    def test_names_tags_authors_and_identity_spoofing_are_blocked(self):
        self.j.write("probe", {"note": "immutable harness record"})
        for name, tags in (("harness/events/00000001", []), ("agent/new", ["harness"]),
                           ("agent/new", ["log-send"]), ("agent/../harness", [])):
            before = self.j.bytes()
            with self.subTest(name=name, tags=tags), self.assertRaises(ValueError):
                self.j.agent_write(name, "attempt", None, tags)
            self.assertEqual(before, self.j.bytes())
        self.j.scribe.tag("agent/protected", "harness", author=HARNESS_AUTHOR)
        self.j.scribe.write("agent/protected", "protected even under agent/", author=HARNESS_AUTHOR)
        before = self.j.bytes()
        with self.assertRaises(ValueError):
            self.j.agent_write("agent/protected", "attempt", self.j.scribe.current("agent/protected").id, [])
        with self.assertRaises(ValueError):
            self.c.tools["ledger_write"][1]({"name": "agent/spoof", "body": "x", "prev": None, "tags": [], "author": HARNESS_AUTHOR})
        self.assertEqual(before, self.j.bytes())

    def test_agent_tools_read_and_filter_actual_ledger(self):
        self.j.write("probe", {"test": True})
        result = self.c.tools["ledger_write"][1]({"name": "agent/note", "body": "hello", "prev": None, "tags": ["agent-note"]})
        listed = self.c.tools["ledger_list"][1]({"tag": "agent-note"})
        self.assertEqual([e["name"] for e in listed["entries"]], ["agent/note"])
        read = self.c.tools["ledger_read"][1]({"name": "agent/note"})
        self.assertEqual(json.loads(read["text"])["entry"]["id"], result["id"])

    def test_real_scribe_io_failure_stops_writes_and_keeps_lease(self):
        self.j.write("before_failure", {})
        with patch.object(scribe, "_write_all", side_effect=OSError("injected disk failure")):
            with self.assertRaises(LedgerUnavailable):
                self.j.write("fails", {})
        frozen = self.j.bytes()
        with self.assertRaises(LedgerUnavailable):
            self.j.write("must_not_continue", {})
        self.j.close()
        self.assertEqual(frozen, self.j.bytes())
        self.assertTrue((self.j.directory / "lease.json").exists())
        self.assertFalse(self.j.scribe.is_open)
        # Fixture cleanup is confined to this disposable test directory. Real
        # sessions never clear a failed lease automatically.

    def test_failed_user_record_is_not_dispatched(self):
        self.c.update(status="ready")
        with patch.object(scribe, "_write_all", side_effect=OSError("injected disk failure")):
            with self.assertRaises(RuntimeError):
                self.c.submit("Must not reach the model")
        self.assertEqual(self.c.commands.qsize(), 0)
        self.assertEqual(self.c.snapshot()["status"], "error")
        self.assertTrue(self.c.stop_event.is_set())

    def test_read_files_pagination_and_redaction(self):
        files = self.root / "files"
        files.mkdir()
        (files / "note.md").write_text("abcdef", encoding="utf-8")
        (files / "redaction.md").write_text("Bearer FAKE_TEST_VALUE_NOT_A_CREDENTIAL", encoding="utf-8")
        (files / ".env").write_text("Synthetic fixture; not a credential", encoding="utf-8")
        fs = ReadFiles(files)
        self.assertEqual(fs.read({"path": "note.md", "offset": 2, "limit": 2})["text"], "cd")
        self.assertEqual(fs.read({"path": "note.md", "offset": 2, "limit": 2})["next_offset"], 4)
        listing = fs.list({"path": "."})
        self.assertEqual([e["name"] for e in listing["entries"]], ["note.md", "redaction.md"])
        self.assertEqual(listing["excluded_count"], 1)
        redacted = fs.read({"path": "redaction.md", "offset": 0, "limit": 12})
        self.assertEqual(redacted["text"], "Bearer [REDA")
        self.assertTrue(redacted["redacted"])

    def test_read_boundary_blocks_outside_traversal_ads_and_credentials(self):
        files = self.root / "files"
        files.mkdir()
        (files / "note.md").write_text("inside", encoding="utf-8")
        outside = self.root / "outside.txt"
        outside.write_text("outside fixture", encoding="utf-8")
        fs = ReadFiles(files)
        for path in (str(outside), "../outside.txt", "note.md:stream", ".env", ".git/config",
                     "run/tokens.json", "lease.json", "NUL", "\\\\server\\share\\file"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                fs.read({"path": path})
        os.link(outside, files / "hardlink.txt")
        with self.assertRaises(ValueError):
            fs.read({"path": "hardlink.txt"})

    def test_reparse_points_are_refused_before_read(self):
        files = self.root / "files"
        files.mkdir()
        (files / "note.md").write_text("inside", encoding="utf-8")
        fs = ReadFiles(files)
        class Reparse:
            st_mode = 0o100644
            st_file_attributes = 0x400
        with patch.object(Path, "lstat", return_value=Reparse()):
            with self.assertRaisesRegex(ValueError, "reparse"):
                fs.path("note.md")

    def test_fresh_conversation_and_streaming_usage(self):
        other = Conversation(self.root)
        self.addCleanup(other.journal.close)
        self.assertNotEqual(self.c.run_id, other.run_id)
        self.assertNotEqual(self.c.log_path, other.log_path)
        self.c.update(status="ready")
        self.c.submit("first")
        with self.assertRaises(RuntimeError):
            self.c.submit("duplicate")
        self.assertEqual(other.snapshot()["messages"], [])
        self.assertIsNone(other.snapshot()["usage"])
        for delta in ("hello", " world"):
            self.c.observe("receive", {"method": "item/agentMessage/delta", "params": {"itemId": "a", "delta": delta}})
        self.c.observe("receive", {"method": "item/completed", "params": {"item": {"id": "a", "type": "agentMessage", "text": "hello world!"}}})
        self.assertEqual(len(self.c.snapshot()["messages"]), 2)
        self.assertEqual(self.c.snapshot()["messages"][-1]["text"], "hello world!")
        for tokens in (100, 150):
            self.c.observe("receive", {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": {"totalTokens": tokens}}}})
        self.assertEqual(self.c.snapshot()["usage"]["total"]["totalTokens"], 150)

    def test_dynamic_tool_dispatch_logs_success_and_denial(self):
        client = Client.__new__(Client)
        client.tools = self.c.tools
        client.thread_id, client.calls, client.journal = "t", [], self.j
        responses = []
        client.send = responses.append
        request = {"id": 1, "method": "item/tool/call", "params": {"threadId": "t", "turnId": "turn", "callId": "call", "tool": "add", "arguments": {"a": 19.25, "b": 22.75}}}
        client.handle_request(request)
        self.assertTrue(responses[-1]["result"]["success"])
        self.assertEqual(self.c.snapshot()["tools"][0]["output"], {"sum": 42})
        request["params"].update(tool="ledger_write", arguments={"name": "harness/x", "body": "x", "prev": None, "tags": []})
        client.handle_request(request)
        self.assertFalse(responses[-1]["result"]["success"])
        request["params"]["tool"] = "shell"
        with self.assertRaises(RuntimeError):
            client.handle_request(request)

    def test_quota_error_does_not_hide_broken_transport(self):
        class QuotaError:
            def request(self, *args):
                raise RpcError("unavailable")
        self.c.read_limits(QuotaError())
        self.assertTrue(self.c.snapshot()["notices"])
        class TransportError:
            def request(self, *args):
                raise RuntimeError("closed stdout")
        with self.assertRaises(RuntimeError):
            self.c.read_limits(TransportError())

    def test_http_acceptance_origin_and_ledger_download(self):
        server = make_server(self.c, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(cleanup)
        def request(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                c.request(method, path, body, headers or {})
                r = c.getresponse()
                return r.status, dict(r.getheaders()), r.read()
            finally:
                c.close()
        self.c.update(status="ready")
        headers = {"Origin": server.origin, "X-Nimoi-UI": "1", "Content-Type": "application/json"}
        self.assertEqual(request("POST", "/api/messages", '{"text":"hello"}', {**headers, "Origin": "https://invalid.example"})[0], 403)
        self.assertEqual(request("POST", "/api/messages", '{"text":"hello"}', headers)[0], 202)
        self.assertEqual(request("POST", "/api/messages", '{"text":"duplicate"}', headers)[0], 409)
        status, response_headers, body = request("GET", "/api/ledger")
        self.assertEqual(status, 200)
        self.assertIn(".ledger", response_headers["Content-Disposition"])
        self.assertEqual(body, self.j.bytes())
        for path in ("/../session.py", "/ledgers", "/.env", "/api/journal"):
            self.assertEqual(request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
