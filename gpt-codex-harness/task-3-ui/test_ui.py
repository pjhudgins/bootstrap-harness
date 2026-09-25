"""Local state/HTTP checks. No Codex process and no model calls."""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from protocol import Client, RpcError, TASK
from session import Conversation
from serve import make_server


class UITests(unittest.TestCase):
    def setUp(self):
        runtime = TASK / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(runtime.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.conversation = Conversation(self.root)
        self.addCleanup(self.conversation.journal.close)

    def test_only_one_message_is_admitted_at_a_time(self):
        self.conversation.update(status="ready")
        self.conversation.submit("First message")
        with self.assertRaises(RuntimeError):
            self.conversation.submit("Must not be silently queued")
        state = self.conversation.snapshot()
        self.assertEqual(state["status"], "busy")
        self.assertEqual(len(state["messages"]), 1)
        self.assertEqual(self.conversation.commands.qsize(), 1)

    def test_invalid_message_does_not_change_ready_state(self):
        self.conversation.update(status="ready")
        for text in (None, "   ", "a" * 16001, ["wrong type"]):
            with self.subTest(text=str(text)[:20]), self.assertRaises(ValueError):
                self.conversation.submit(text)
        self.assertEqual(self.conversation.snapshot()["status"], "ready")

    def test_completed_stream_replaces_deltas_without_duplicates(self):
        for delta in ("hello", " world"):
            self.conversation.observe("receive", {"method": "item/agentMessage/delta",
                "params": {"itemId": "m1", "delta": delta}})
        self.conversation.observe("receive", {"method": "item/completed", "params": {
            "item": {"type": "agentMessage", "id": "m1", "text": "hello world!", "phase": "final_answer"}}})
        messages = self.conversation.snapshot()["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["text"], "hello world!")
        self.assertTrue(messages[0]["complete"])

    def test_fresh_launch_has_unique_empty_state_and_journal(self):
        other = Conversation(self.root)
        self.addCleanup(other.journal.close)
        self.conversation.update(status="ready")
        self.conversation.submit("Only in first launch")
        self.assertNotEqual(self.conversation.run_id, other.run_id)
        self.assertNotEqual(self.conversation.log_path, other.log_path)
        self.assertEqual(other.snapshot()["messages"], [])

    def test_snapshot_is_detached_and_unknown_usage_stays_unknown(self):
        state = self.conversation.snapshot()
        state["messages"].append({"text": "Do not mutate internal state"})
        self.assertEqual(self.conversation.snapshot()["messages"], [])
        self.assertIsNone(state["usage"])

    def test_cumulative_usage_replaces_instead_of_adding_snapshots(self):
        for tokens in (100, 150):
            self.conversation.observe("receive", {"method": "thread/tokenUsage/updated",
                "params": {"tokenUsage": {"total": {"totalTokens": tokens}}}})
        self.assertEqual(self.conversation.snapshot()["usage"]["total"]["totalTokens"], 150)

    def test_registered_python_tool_result_is_logged_and_returned(self):
        client = Client.__new__(Client)
        client.thread_id = "thread-1"
        client.calls = []
        client.journal = self.conversation.journal
        responses = []
        client.send = responses.append
        request = {"id": "call-1", "method": "item/tool/call", "params": {
            "threadId": "thread-1", "turnId": "turn-1", "callId": "call-1",
            "tool": "add", "arguments": {"a": 19.25, "b": 22.75}}}
        client.handle_request(request)
        self.assertEqual(self.conversation.snapshot()["tools"][0]["output"], {"sum": 42})
        self.assertEqual(json.loads(responses[0]["result"]["contentItems"][0]["text"]), {"sum": 42})
        request["params"]["arguments"]["a"] = True
        client.handle_request(request)
        self.assertFalse(responses[-1]["result"]["success"])
        request["params"]["threadId"] = "wrong-thread"
        with self.assertRaises(RuntimeError):
            client.handle_request(request)
        self.assertIn("error", responses[-1])

    def test_optional_quota_errors_do_not_hide_transport_failures(self):
        class FailedQuota:
            def request(self, *args):
                raise RpcError("Limits unavailable")
        self.conversation.read_limits(FailedQuota())
        self.assertTrue(self.conversation.snapshot()["notices"])
        class BrokenTransport:
            def request(self, *args):
                raise RuntimeError("App Server closed stdout")
        with self.assertRaises(RuntimeError):
            self.conversation.read_limits(BrokenTransport())

    def start_http(self):
        self.server = make_server(self.conversation, 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=2)
        self.addCleanup(cleanup)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_cross_origin_and_wrong_host_cannot_start_turns(self):
        self.start_http()
        self.conversation.update(status="ready")
        headers = {"Origin": "https://unrelated.invalid", "X-Nimoi-UI": "1", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/messages", '{"text":"unwanted"}', headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "unrelated.invalid"})[0], 403)
        self.assertEqual(self.conversation.commands.qsize(), 0)

    def test_http_acceptance_busy_rejection_and_log_download(self):
        self.start_http()
        self.conversation.update(status="ready")
        headers = {"Origin": self.server.origin, "X-Nimoi-UI": "1", "Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/messages", '{"text":"test message"}', headers)[0], 202)
        self.assertEqual(self.request("POST", "/api/messages", '{"text":"duplicate"}', headers)[0], 409)
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["messages"][0]["text"], "test message")
        status, response_headers, log = self.request("GET", "/api/journal")
        self.assertEqual(status, 200)
        self.assertIn("attachment", response_headers["Content-Disposition"])
        self.assertEqual(json.loads(log)["kind"], "user_message")

    def test_static_server_does_not_expose_arbitrary_files(self):
        self.start_http()
        for path in ("/../session.py", "/runs", "/.env"):
            self.assertEqual(self.request("GET", path)[0], 404)
        status, headers, content = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn(b"Message your agent", content)


if __name__ == "__main__":
    unittest.main()
