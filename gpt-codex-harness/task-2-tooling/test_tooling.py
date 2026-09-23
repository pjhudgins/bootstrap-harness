"""Offline checks for tool and recording boundaries; no model calls."""

import json
from pathlib import Path
import queue
import tempfile
import time
import unittest

from tooling import Client, Journal, TASK, add


class ToolingTests(unittest.TestCase):
    def setUp(self):
        runtime = TASK / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.path = Path(self.temp.name) / "events.jsonl"
        self.journal = Journal(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.journal.close)

    def client(self):
        client = Client.__new__(Client)
        client.journal = self.journal
        client.queue = queue.Queue()
        client.next_id = 0
        client.methods = {}
        client.thread_id = "test-thread"
        client.turns, client.items, client.calls, client.usage = {}, [], [], []
        client.deadline = time.monotonic() + 1
        client.sent = []
        client.send = client.sent.append
        return client

    def test_add_rejects_non_numeric_or_executable_inputs(self):
        self.assertEqual(add({"a": -1.5, "b": 2}), 0.5)
        for arguments in ({"a": True, "b": 2}, {"a": "1+2", "b": 3},
                          {"a": float("inf"), "b": 0}, {"a": float("nan"), "b": 1},
                          {"a": 1, "b": 2, "code": "print('unexpected')"},
                          {"a": 1e308, "b": 1e308}, [1, 2]):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                add(arguments)

    def test_journal_flushes_and_refuses_overwrite(self):
        self.journal.write("message", {"text": "Hello — world"})
        self.journal.write("failure", {"error": "synthetic offline failure"})
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["sequence"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["data"]["text"], "Hello — world")
        with self.assertRaises(FileExistsError):
            Journal(self.path)

    def test_redaction_preserves_token_usage_numbers(self):
        # Synthetic placeholders only; no real credentials are used by these tests.
        self.journal.write("test", {"api_key": "test-secret", "accessToken": "test-token",
                                   "nested": {"email": "test@example.invalid"},
                                   "usage": {"inputTokens": 7, "outputTokens": 3}})
        row = json.loads(self.path.read_text(encoding="utf-8"))["data"]
        self.assertEqual(row["api_key"], "[REDACTED]")
        self.assertEqual(row["accessToken"], "[REDACTED]")
        self.assertEqual(row["nested"]["email"], "[REDACTED]")
        self.assertEqual(row["usage"]["inputTokens"], 7)

    def test_config_and_account_payloads_omitted_even_at_shutdown(self):
        client = self.client()
        client.methods = {1: "config/read", 2: "account/read"}
        client.log_message({"id": 1, "result": {"arbitrary": "private-config-value"}}, "shutdown_receive")
        client.log_message({"id": 2, "result": {"account": {
            "type": "chatgpt", "planType": "test-plan", "email": "private-person",
        }}})
        logged = self.path.read_text(encoding="utf-8")
        self.assertNotIn("private-config-value", logged)
        self.assertNotIn("private-person", logged)
        self.assertIn("chatgpt", logged)

    def test_early_events_survive_rpc_response_wait(self):
        client = self.client()
        events = [
            {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": 8}}},
            {"method": "turn/completed", "params": {"turn": {"id": "t", "status": "completed"}}},
            {"id": 1, "result": {"turn": {"id": "t"}}},
        ]
        for event in events:
            client.queue.put(("stdout", json.dumps(event)))
        client.request("turn/start", {})
        self.assertEqual(len(client.usage), 1)
        self.assertEqual(client.turns["t"]["status"], "completed")

    def test_dynamic_request_is_handled_before_turn_start_response(self):
        client = self.client()
        client.queue.put(("stdout", json.dumps({"id": 1, "method": "item/tool/call", "params": {
            "threadId": "test-thread", "turnId": "t", "callId": "c", "tool": "add",
            "arguments": {"a": 19.25, "b": 22.75},
        }})))
        client.queue.put(("stdout", json.dumps({"id": 1, "result": {"turn": {"id": "t"}}})))
        result = client.request("turn/start", {})
        self.assertEqual(result["turn"]["id"], "t")
        self.assertTrue(client.sent[-1]["result"]["success"])
        self.assertEqual(client.calls[0]["output"], {"sum": 42.0})

    def test_unknown_tool_is_rejected_without_dispatch(self):
        client = self.client()
        with self.assertRaises(RuntimeError):
            client.handle_request({"id": 7, "method": "item/tool/call", "params": {
                "tool": "exec", "threadId": "test-thread", "arguments": {},
            }})
        self.assertEqual(client.sent[-1]["error"]["code"], -32601)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
