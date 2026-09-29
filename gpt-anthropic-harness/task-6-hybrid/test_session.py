"""Retained UI admission and text-provenance checks with the real ledger."""
import http.client
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest

from app import make_server
from messages import record_sdk_message
from conversation import Conversation
from ledger import AGENT_AUTHOR, HUMAN_AUTHOR, LedgerLog, scribe


class ImmediateLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)
    def is_closed(self):
        return False


class SessionTests(unittest.TestCase):
    def test_http_admission_and_authored_message_references(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            with LedgerLog(Path(tmp) / "ledgers") as log:
                state = Conversation(log, "opus")
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
                    body = json.dumps({"text": "Hello\nworld"})
                    self.assertEqual(request("POST", "/api/message", body, {"Content-Type": "application/json"})[0], 403)
                    headers = {"Content-Type": "application/json", "Origin": origin}
                    self.assertEqual(request("POST", "/api/message", body, headers)[0], 202)
                    self.assertEqual(request("POST", "/api/message", body, headers)[0], 409)
                    turn, text, ref = state.queue.get_nowait()
                    self.assertEqual(text, "Hello\nworld")
                    entry = log.scribe.current(ref["name"])
                    self.assertEqual((entry.body, entry.author), (text, HUMAN_AUTHOR))
                    record_sdk_message(log, turn, "from_agent", {"_type": "AssistantMessage", "error": None,
                        "content": [{"_type": "TextBlock", "text": "Child reply 👋"}]}, agent_author="agent.claude.child-test")
                    name = log.scribe.labelled("message.assistant")[0]
                    self.assertEqual(log.scribe.current(name).author, "agent.claude.child-test")
                    state.assistant_text(turn, "Hello back")
                    self.assertEqual(json.loads(request("GET", "/api/state")[1])["messages"][-1]["text"], "Hello back")
                finally:
                    server.shutdown()
                    thread.join()
                    server.server_close()
            self.assertEqual(scribe.load(log.root, log.name).findings, [])


if __name__ == "__main__":
    unittest.main()
