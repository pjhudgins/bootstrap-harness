"""State, local HTTP and journal checks; no model or SDK connection."""

import http.client
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest

from app import make_server
from conversation import Conversation
from runlog import RunLog

ROOT = Path(__file__).resolve().parent


class ImmediateLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)

    def is_closed(self):
        return False


class AppTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT)
        self.log = RunLog(Path(self.folder.name) / "run.jsonl")
        self.state = Conversation(self.log, "test-model")
        self.state.loop = ImmediateLoop()
        self.state.queue = queue.Queue()
        self.state.update(status="ready")

    def tearDown(self):
        self.log.file.close()
        self.folder.cleanup()

    def test_one_turn_at_a_time_and_refresh_snapshot(self):
        turn = self.state.submit("Use add for 1 + 2")
        with self.assertRaises(RuntimeError):
            self.state.submit("duplicate")
        self.assertEqual(self.state.queue.get_nowait(), (turn, "Use add for 1 + 2"))
        self.state.assistant_text(turn, "3")
        first = self.state.snapshot()
        first["messages"].clear()
        self.assertEqual(len(self.state.snapshot()["messages"]), 2)
        self.state.update(status="ready")
        self.state.submit("Remember that result")
        records = [json.loads(line) for line in self.log.path.read_text().splitlines()]
        self.assertEqual(len(records), 2)

    def test_new_launch_is_empty(self):
        self.state.submit("Previous launch")
        with RunLog(Path(self.folder.name) / "new.jsonl") as new_log:
            new = Conversation(new_log, "test-model")
            self.assertNotEqual(new.data["id"], self.state.data["id"])
            self.assertEqual(new.snapshot()["messages"], [])
            self.assertIsNone(new.snapshot()["usage"])

    def test_validation_and_stop(self):
        for text in (None, "", "  ", "a" * 16001, {}):
            with self.assertRaises(ValueError):
                self.state.submit(text)
        self.state.stop()
        self.state.stop()
        self.assertIsNone(self.state.queue.get_nowait())
        self.assertTrue(self.state.queue.empty())
        with self.assertRaises(RuntimeError):
            self.state.submit("no more")

    def test_concurrent_journal_writes(self):
        threads = [threading.Thread(target=lambda: [self.log.write("event") for _ in range(20)])
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rows = [json.loads(line) for line in self.log.path.read_text().splitlines()]
        self.assertEqual([row["seq"] for row in rows], list(range(1, 81)))

    def test_local_http_boundaries_and_message_acceptance(self):
        server = make_server(self.state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            result = response.status, response.read(), response.getheader("Content-Security-Policy")
            connection.close()
            return result
        try:
            status, page, csp = request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(b"Message Claude", page)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertEqual(request("GET", "/api/state", headers={"Host": "foreign.test"})[0], 403)
            self.assertEqual(request("GET", "/../runlog.py")[0], 404)
            payload = json.dumps({"text": "hello"})
            self.assertEqual(request("POST", "/api/message", payload,
                                     {"Content-Type": "application/json"})[0], 403)
            headers = {"Content-Type": "application/json", "Origin": origin}
            self.assertEqual(request("POST", "/api/message", "[]", headers)[0], 400)
            self.assertEqual(request("POST", "/api/message", payload, headers)[0], 202)
            self.assertEqual(request("POST", "/api/message", payload, headers)[0], 409)
            snapshot = json.loads(request("GET", "/api/state")[1])
            self.assertEqual(snapshot["messages"][0]["text"], "hello")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
