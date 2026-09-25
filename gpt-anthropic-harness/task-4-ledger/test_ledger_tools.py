"""Boundary and integration checks; real scribe, no model or credentials."""
import http.client
import json
import math
import os
from pathlib import Path
import queue
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import make_server
from conversation import Conversation
from filesystem import ReadOnlyFiles
from ledger import AGENT_AUTHOR, HUMAN_AUTHOR, LedgerFailed, LedgerLog, scribe
from redaction import Redactor
from tools import FULL_NAMES, ToolService, allowed

HERE = Path(__file__).resolve().parent


class ImmediateLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)
    def is_closed(self):
        return False


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.base = Path(self.temp.name)
        self.log = LedgerLog(self.base / "ledgers")
        self.fsroot = self.base / "nimoi"
        self.fsroot.mkdir()
        (self.fsroot / "origins").mkdir()
        (self.fsroot / "origins" / "onboarding_1.11.md").write_bytes(b"first\nsecond\nthird\n")
        self.service = ToolService(self.log, self.fsroot)

    def tearDown(self):
        self.log.close()
        self.temp.cleanup()

    def test_harness_events_are_authored_tagged_and_valid_after_close(self):
        event_id = self.log.write("message", direction="from_agent", text="hello 👋")
        entry = self.log.scribe.current("harness/00000001")
        self.assertEqual(entry.id, event_id)
        self.assertEqual(entry.author, "harness")
        self.assertEqual(self.log.scribe.labels(entry.name), {"harness", "log.message"})
        self.log.close()
        view = scribe.load(self.log.root, self.log.name)
        self.assertEqual(view.findings, [])
        self.assertTrue(view.head_closed)
        self.assertFalse((self.log.path.parent / "lease.json").exists())
        self.assertNotIn(b"\r", self.log.path.read_bytes())

    def test_agent_revision_requires_current_id_and_preserves_history(self):
        first = self.log.agent_write("pilot/note", "first", ["pilot.note"])
        with self.assertRaises(ValueError):
            self.log.agent_write("pilot/note", "without prev", ["pilot.note"])
        second = self.log.agent_write("pilot/note", "second", ["pilot.observation"], first["id"])
        with self.assertRaises(ValueError):
            self.log.agent_write("pilot/note", "stale", ["pilot.note"], first["id"])
        self.assertEqual(second["author"], AGENT_AUTHOR)
        self.assertEqual([e.body for e in self.log.scribe.history("pilot/note")], ["first", "second"])
        self.assertEqual(self.log.read(name="pilot/note")["id"], second["id"])
        self.assertEqual(self.log.read(tag="pilot.observation")["entries"][0]["name"], "pilot/note")

    def test_message_text_authorship_links_and_metadata_survive_reload(self):
        prompt = "Hello 👋\n\nKeep my spacing."
        user_ref = self.log.user_message("turn-a", prompt)
        self.log.sdk_message("turn-a", "from_agent", {"_type": "AssistantMessage",
            "model": "test-model", "content": [{"_type": "TextBlock", "text": "First\nparagraph."},
                {"_type": "ToolUseBlock", "name": "add", "input": {"a": 1, "b": 2}},
                {"_type": "TextBlock", "text": "Last paragraph."}], "error": None})
        self.log.sdk_message("turn-a", "runtime", {"_type": "ResultMessage",
            "result": "Last paragraph.", "usage": {"input_tokens": 8}})
        self.log.close()
        view = scribe.load(self.log.root, self.log.name)
        self.assertEqual(view.findings, [])
        texts = [view.current(n) for n in view.labelled("message.text")]
        self.assertEqual(len(texts), 3)  # SDK result reuses its original text entry.
        self.assertEqual(view.current(user_ref["name"]).body, prompt)
        self.assertEqual(view.current(user_ref["name"]).author, HUMAN_AUTHOR)
        for name in view.labelled("message.assistant"):
            self.assertEqual(view.current(name).author, AGENT_AUTHOR)
        events = [view.current(n) for n in view.labelled("log.message")]
        for event in events:
            self.assertEqual(event.author, "harness")
            for ref in event.body["text_entries"]:
                entry = view.current(ref["name"])
                self.assertEqual(entry.id, ref["id"])
                self.assertEqual(ref["link"], "[[" + entry.name + "]]")
                self.assertLess(int(entry.id.split(":")[1]), int(event.id.split(":")[1]))
        content = events[1].body["message"]["content"]
        self.assertEqual(content[1]["input"], {"a": 1, "b": 2})
        self.assertEqual(events[2].body["message"]["result"], content[2]["text"])
        self.assertEqual(events[2].body["message"]["usage"], {"input_tokens": 8})

    def test_message_identity_redaction_and_tool_result_boundaries(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "synthetic-secret-value"}):
            self.log.secrets = Redactor().secrets
        ref = self.log.user_message("turn-b", "human synthetic-secret-value")
        self.assertEqual(self.log.scribe.current(ref["name"]).body, "human [REDACTED]")
        self.log.sdk_message("turn-b", "to_agent", {"_type": "UserMessage", "content": [
            {"_type": "ToolResultBlock", "content": "tool output", "tool_use_id": "tool-1"}]})
        self.log.sdk_message("turn-b", "from_agent", {"_type": "AssistantMessage",
            "error": "authentication_failed", "content": [{"_type": "TextBlock", "text": "Runtime error"}]})
        self.assertEqual(len(self.log.scribe.labelled("message.user")), 1)
        self.assertEqual(self.log.scribe.labelled("message.assistant"), [])
        runtime_name = self.log.scribe.labelled("message.runtime")[0]
        self.assertEqual(self.log.scribe.current(runtime_name).author, "harness")
        for name in (ref["name"], runtime_name):
            with self.assertRaises(ValueError):
                self.log.agent_write(name, "rewrite", ["pilot.note"])

    def test_message_metadata_failure_preserves_text_and_stops(self):
        bad = object.__new__(LedgerLog)
        Redactor.__init__(bad)
        bad.lock, bad.failed, bad.seq = threading.RLock(), False, 0
        bad.text_seq, bad.turn_text = 0, {}
        bad.scribe = Mock(is_open=True)
        bad.scribe.write.side_effect = ["20260925T000000Z:2", OSError("synthetic metadata failure")]
        with self.assertRaises(LedgerFailed):
            bad.user_message("turn-c", "surviving text")
        self.assertTrue(bad.failed)
        self.assertEqual(bad.scribe.write.call_args_list[0].args[1], "surviving text")
        with self.assertRaises(LedgerFailed):
            bad.user_message("retry", "must not be written")
        bad.close()
        self.assertEqual(bad.scribe.write.call_count, 2)
        bad.scribe.close.assert_not_called()

    def test_harness_prefix_author_and_tags_each_protect_entries(self):
        harness_id = self.log.write("test", original=True)
        before = self.log.path.read_bytes()
        for name, tags in (("harness/00000001", ["pilot.note"]), ("pilot/new", ["harness"]),
                           ("pilot/new", ["log.message"]), ("pilot/../harness", ["pilot.note"])):
            with self.assertRaises(ValueError):
                self.log.agent_write(name, "replacement", tags, harness_id)
        self.assertEqual(self.log.path.read_bytes(), before)
        for name, author, tag in (("pilot/author-guard", "harness", "pilot.note"),
                                  ("pilot/tag-guard", AGENT_AUTHOR, "harness"),
                                  ("pilot/untagged", "harness", None)):
            eid = self.log.scribe.write(name, "protected", author=author)
            if tag:
                self.log.scribe.tag(name, tag, author="harness")
            with self.assertRaises(ValueError):
                self.log.agent_write(name, "bad", ["pilot.note"], eid)
            self.assertEqual(self.log.scribe.current(name).body, "protected")

    def test_tools_cannot_choose_author_or_arbitrary_names(self):
        with self.assertRaises(ValueError):
            self.service.invoke("ledger_write", {"name": "pilot/a", "body": "note", "tags": ["pilot.note"], "author": "harness"})
        for name in ("Bash", "Read", "Write", "mcp__calc__add", "mcp__nimoi__execute"):
            self.assertFalse(allowed(name, {}))
        self.assertEqual(len(FULL_NAMES), 5)

    def test_fresh_ledgers_and_concurrent_event_sequences(self):
        with LedgerLog(self.log.root) as other:
            self.assertNotEqual(other.path, self.log.path)
            self.assertEqual(other.scribe.names(), [])
        threads = [threading.Thread(target=lambda: [self.log.write("parallel") for _ in range(6)]) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entries = [self.log.scribe.current(name) for name in self.log.scribe.names()]
        self.assertEqual([e.body["seq"] for e in entries], list(range(1, 19)))

    def test_body_tag_partial_failure_stops_future_writes(self):
        # Fake only the failing storage surface; do not damage or repair a real ledger.
        bad = object.__new__(LedgerLog)
        Redactor.__init__(bad)
        bad.lock, bad.failed, bad.seq = threading.RLock(), False, 0
        bad.scribe = Mock(is_open=True)
        bad.scribe.tag.side_effect = OSError("synthetic disk failure")
        with self.assertRaises(LedgerFailed):
            bad.write("message", text="accepted body, failed tag")
        self.assertTrue(bad.failed)
        with self.assertRaises(LedgerFailed):
            bad.write("retry")
        bad.close()
        self.assertEqual(bad.scribe.write.call_count, 1)
        bad.scribe.close.assert_not_called()

    def test_files_read_paginate_and_redact(self):
        result = self.service.invoke("fs_read", {"path": "origins/onboarding_1.11.md", "max_lines": 2})
        self.assertEqual(result["text"], "first\nsecond\n")
        self.assertEqual(result["next_line"], 3)
        self.assertEqual(self.service.files.read("origins/onboarding_1.11.md", 3)["text"], "third\n")
        with patch.dict(os.environ, {"TEST_API_KEY": "synthetic-secret-value"}):
            redactor = Redactor()
        (self.fsroot / "note.txt").write_text("synthetic-secret-value", encoding="utf-8")
        self.assertEqual(ReadOnlyFiles(self.fsroot, redactor.clean).read("note.txt")["text"], "[REDACTED]")

    def test_files_deny_secret_traversal_and_windows_special_paths(self):
        (self.fsroot / "run").mkdir()
        (self.fsroot / "run" / "opaque.txt").write_text("test-only placeholder")
        with self.assertRaises(ValueError):
            self.service.files.read("run/opaque.txt")
        for name in (".env", "secret.key", "credentials-local.json", "lease.json"):
            (self.fsroot / name).write_text("test-only placeholder")
            with self.assertRaises(ValueError):
                self.service.files.read(name)
        for path in ("../outside.txt", "C:/Windows/win.ini", "//server/share/a", "origins/x:secret", "origins/../.env"):
            with self.assertRaises(ValueError):
                self.service.files.resolve(path)
        names = [e["name"] for e in self.service.files.list()["entries"]]
        self.assertEqual(names, ["origins"])

    def test_hardlinks_and_reparse_points_denied(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside the supplied root")
        os.link(outside, self.fsroot / "hardlink.txt")
        with self.assertRaises(ValueError):
            self.service.files.read("hardlink.txt")
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
        with patch.object(Path, "lstat", return_value=info):
            with self.assertRaises(ValueError):
                self.service.files.resolve("junction")

    def test_file_and_ledger_response_bounds(self):
        (self.fsroot / "large.txt").write_bytes(b"a" * 2_000_001)
        (self.fsroot / "binary.txt").write_bytes(b"a\x00b")
        for name in ("large.txt", "binary.txt"):
            with self.assertRaises(ValueError):
                self.service.files.read(name)
        self.log.write("large_record", text="a" * 30000)
        first = self.log.read(name="harness/00000001")
        rest = self.log.read(name="harness/00000001", body_offset=first["next_body_offset"])
        combined = first["body_json_slice"] + rest["body_json_slice"]
        self.assertEqual(json.loads(combined)["text"], "a" * 30000)

    def test_numeric_validation_and_usage_redaction(self):
        self.assertEqual(self.service.invoke("add", {"a": 19.25, "b": 22.75}), {"sum": 42})
        for value in (True, "2", math.nan, math.inf, 10**400):
            with self.assertRaises(ValueError):
                self.service.invoke("add", {"a": value, "b": 1})
        self.log.write("usage", input_tokens=3, cache_creation_input_tokens=1053, access_token="test-placeholder")
        body = self.log.scribe.current("harness/00000001").body
        self.assertEqual(body["cache_creation_input_tokens"], 1053)
        self.assertEqual(body["access_token"], "[REDACTED]")

    def test_credential_shaped_name_is_rejected_before_scribe(self):
        before = self.log.path.read_bytes()
        with self.assertRaises(ValueError):
            self.log.agent_write("pilot/sk-ant-synthetic123456789", "note", ["pilot.note"])
        self.assertEqual(self.log.path.read_bytes(), before)

    def test_http_turn_serialization_origin_and_snapshot(self):
        state = Conversation(self.log, "test-model")
        state.loop, state.queue = ImmediateLoop(), queue.Queue()
        state.update(status="ready")
        server = make_server(state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            status, data = response.status, response.read()
            conn.close()
            return status, data
        try:
            self.assertEqual(request("GET", "/")[0], 200)
            self.assertEqual(request("GET", "/api/state", headers={"Host": "foreign.test"})[0], 403)
            payload = json.dumps({"text": "Read onboarding"})
            self.assertEqual(request("POST", "/api/message", payload, {"Content-Type": "application/json"})[0], 403)
            headers = {"Content-Type": "application/json", "Origin": origin}
            self.assertEqual(request("POST", "/api/message", payload, headers)[0], 202)
            self.assertEqual(request("POST", "/api/message", payload, headers)[0], 409)
            snapshot = json.loads(request("GET", "/api/state")[1])
            self.assertEqual(snapshot["messages"][0]["text"], "Read onboarding")
            self.assertEqual(len(self.log.scribe.labelled("log.user_message_accepted")), 1)
            accepted = self.log.scribe.current(self.log.scribe.labelled("log.user_message_accepted")[0])
            text_name = self.log.scribe.labelled("message.user")[0]
            self.assertEqual(accepted.body["text"], "[[" + text_name + "]]")
            self.assertEqual(self.log.scribe.current(text_name).author, HUMAN_AUTHOR)
            self.assertEqual(state.queue.get_nowait()[1], "Read onboarding")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
