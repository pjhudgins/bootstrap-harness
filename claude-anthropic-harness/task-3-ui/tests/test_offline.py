"""Offline tests: no model calls, no CLI. The web layer runs against a fake session.

Run from anywhere:
    python -m unittest discover -s <task-3-ui>/tests -t <task-3-ui>
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TASK_DIR))

from starlette.testclient import TestClient  # noqa: E402

from app import create_app, sse_stream  # noqa: E402
from calc_tool import add, add_numbers  # noqa: E402
from events import EventBus  # noqa: E402
from policy import ToolPolicy  # noqa: E402
from runlog import RunLog  # noqa: E402

WORKSPACE = TASK_DIR / "workspace"
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"


class TempLogMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = RunLog(Path(self._tmp.name) / "t.jsonl.log")
        self.bus = EventBus(self.log)

    def tearDown(self):
        self.log.close()
        self._tmp.cleanup()

    def log_lines(self):
        return [json.loads(l) for l in self.log.path.read_text(encoding="utf-8").splitlines()]


# ---- carried over from task 2 ------------------------------------------------------------

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = ToolPolicy(WORKSPACE)

    def allowed(self, tool, inp):
        return self.p.check(tool, inp).allow

    def test_execution_and_write_tools_denied(self):
        for tool in ("Bash", "PowerShell", "Write", "Edit", "NotebookEdit", "Agent", "Task", "WebFetch"):
            self.assertFalse(self.allowed(tool, {}), tool)

    def test_workspace_confinement(self):
        self.assertTrue(self.allowed("Read", {"file_path": "numbers.txt"}))
        self.assertFalse(self.allowed("Read", {"file_path": "../app.py"}))  # harness code
        self.assertFalse(self.allowed("Read", {"file_path": str(TASK_DIR / "runs" / "x.jsonl.log")}))
        self.assertFalse(self.allowed("Read", {"file_path": str(TASK_DIR.parent / ".env")}))
        self.assertFalse(self.allowed("Glob", {"pattern": "../**/*"}))
        self.assertFalse(self.allowed("Grep", {"pattern": "x", "path": str(TASK_DIR)}))
        self.assertFalse(self.allowed("Read", {"file_path": ".env"}))

    def test_add(self):
        self.assertTrue(self.allowed("mcp__calc__add", {"a": 1, "b": 2}))
        self.assertEqual(add_numbers(19.5, 22.75), 42.25)
        self.assertTrue(asyncio.run(add.handler({"a": "1", "b": 2}))["is_error"])


# ---- event bus ---------------------------------------------------------------------------

class EventBusTests(TempLogMixin, unittest.TestCase):
    def test_log_first_then_subscribers(self):
        async def go():
            self.bus.bind_loop(asyncio.get_running_loop())
            self.bus.publish("a", x=1)
            backlog, q = self.bus.subscribe(after=0)
            self.bus.publish("b", x=2)
            return backlog, q.get_nowait()
        backlog, live = asyncio.run(go())
        self.assertEqual([r["kind"] for r in backlog], ["a"])
        self.assertEqual(live["kind"], "b")
        self.assertEqual([r["kind"] for r in self.log_lines()], ["a", "b"])  # log holds what UI got

    def test_subscribe_after_seq(self):
        for k in "abc":
            self.bus.publish(k)
        backlog, _ = self.bus.subscribe(after=2)
        self.assertEqual([r["kind"] for r in backlog], ["c"])

    def test_sse_stream_replays_then_stops_on_close(self):
        async def go():
            self.bus.bind_loop(asyncio.get_running_loop())
            self.bus.publish("a")
            out = []
            gen = sse_stream(self.bus, 0, "conv-1")
            hello = await gen.__anext__()
            self.assertTrue(hello.startswith("event: hello\n"))
            self.assertIn('"conv-1"', hello)
            out.append(await gen.__anext__())
            self.bus.publish("b")
            out.append(await gen.__anext__())
            self.bus.close_streams()
            with self.assertRaises(StopAsyncIteration):
                await gen.__anext__()
            return out, len(self.bus._subscribers)
        out, subs = asyncio.run(go())
        self.assertTrue(out[0].startswith("id: 1\ndata: "))
        self.assertIn('"kind": "b"', out[1])
        self.assertEqual(subs, 0)

    def test_publish_from_other_thread_lands_on_loop(self):
        import threading

        async def go():
            self.bus.bind_loop(asyncio.get_running_loop())
            t = threading.Thread(target=lambda: self.bus.publish("from_thread"))
            t.start(); t.join()
            await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertEqual([r["kind"] for r in self.log_lines()], ["from_thread"])


# ---- web layer ---------------------------------------------------------------------------

class FakeSession:
    def __init__(self, bus):
        self.bus, self.state, self.sent, self.started, self.stopped = bus, "starting", [], False, False

    def snapshot(self):
        return {"state": self.state}

    def send(self, text):
        if self.state != "idle":
            return f"agent is {self.state}"
        self.sent.append(text)
        self.state = "busy"
        return None

    async def interrupt(self):
        return self.state == "busy"

    async def start(self):
        self.started, self.state = True, "idle"

    async def stop(self):
        self.stopped = True


class WebTests(TempLogMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.session = FakeSession(self.bus)
        self.client = TestClient(create_app(self.session, self.bus, port=PORT), base_url=ORIGIN)

    def test_lifespan_and_index(self):
        with self.client as c:
            self.assertTrue(self.session.started)
            r = c.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("NIMOI harness", r.text)
            self.assertEqual(c.get("/static/app.js").status_code, 200)
        self.assertTrue(self.session.stopped)

    def test_send_accept_then_busy(self):
        with self.client as c:
            r = c.post("/api/send", json={"text": "hi"}, headers={"Origin": ORIGIN})
            self.assertEqual(r.status_code, 202)
            self.assertEqual(self.session.sent, ["hi"])
            r = c.post("/api/send", json={"text": "again"})
            self.assertEqual(r.status_code, 409)
            self.assertIn("busy", r.json()["error"])

    def test_send_rejections(self):
        with self.client as c:
            self.assertEqual(c.post("/api/send", json={"text": "hi"}, headers={"Origin": "http://evil.example"}).status_code, 403)
            self.assertEqual(c.post("/api/send", content="text=hi", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code, 415)
            self.assertEqual(c.post("/api/send", json={"text": "  "}).status_code, 400)
            self.assertEqual(c.post("/api/send", json=["hi"]).status_code, 400)
            self.assertEqual(self.session.sent, [])

    def test_untrusted_host_rejected(self):
        with self.client as c:
            self.assertEqual(c.get("/", headers={"Host": "attacker.example"}).status_code, 400)

    def test_interrupt_requires_json_post(self):
        with self.client as c:
            self.assertEqual(c.get("/api/interrupt").status_code, 405)
            self.assertEqual(c.post("/api/interrupt", json={}).json()["interrupted"], False)


if __name__ == "__main__":
    unittest.main()
