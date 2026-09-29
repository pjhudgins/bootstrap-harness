"""The web layer: local-only access, security headers, SSE, shutdown."""

import asyncio
import json
import unittest

from starlette.testclient import TestClient

from tests.support import HarnessCase

from web import create_app, sse_stream  # noqa: E402

PORT = 8767
ORIGIN = f"http://127.0.0.1:{PORT}"


class FakeSession:
    def __init__(self):
        self.state, self.sent, self.decisions = "starting", [], []

    def snapshot(self):
        return {"state": self.state}

    def send(self, text):
        if self.state != "idle":
            return f"agent is {self.state}"
        self.sent.append(text)
        self.state = "busy"
        return None

    def decide(self, approval, approve, note):
        if approval != "approval.1":
            return f"no pending approval {approval!r}"
        self.decisions.append((approval, approve, note))
        return None

    async def interrupt(self):
        return self.state == "busy"

    async def start(self):
        self.state = "idle"

    async def stop(self):
        pass


class WebTests(HarnessCase):
    def client(self, exits=None):
        exits = [] if exits is None else exits
        session = FakeSession()
        app = create_app(session, self.bus, port=PORT, on_shutdown=lambda: exits.append(1))
        return session, TestClient(app, base_url=ORIGIN)

    def test_page_headers(self):
        _, client = self.client()
        with client as c:
            for path in ("/", "/static/app.js", "/static/style.css"):
                r = c.get(path)
                self.assertEqual(r.status_code, 200, path)
                self.assertEqual(r.headers.get("cache-control"), "no-cache", path)
                self.assertIn("default-src 'self'", r.headers.get("content-security-policy", ""), path)
                self.assertEqual(r.headers.get("x-content-type-options"), "nosniff", path)
            self.assertIn('"/static/app.js?v=test"', c.get("/").text)

    def test_local_only(self):
        session, client = self.client()
        with client as c:
            self.assertEqual(c.get("/", headers={"Host": "attacker.example"}).status_code, 400)
            r = c.post("/api/send", json={"text": "hi"}, headers={"Origin": "http://evil.example"})
            self.assertEqual(r.status_code, 403)  # refused after the body was read
            self.assertEqual(c.post("/api/send", content="text=hi",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code, 415)
            self.assertEqual(c.post("/api/send", json=["hi"]).status_code, 400)
            self.assertEqual(session.sent, [])
            self.assertEqual(c.post("/api/send", json={"text": "hi"}, headers={"Origin": ORIGIN}).status_code, 202)
            self.assertEqual(c.post("/api/send", json={"text": "again"}).status_code, 409)

    def test_approve(self):
        session, client = self.client()
        with client as c:
            self.assertEqual(c.post("/api/approve", json={"approval": "approval.1", "decision": "approve"},
                                    headers={"Origin": "http://evil.example"}).status_code, 403)
            self.assertEqual(c.post("/api/approve", json={"approval": "approval.1", "decision": "maybe"}).status_code, 400)
            self.assertEqual(c.post("/api/approve", json={"approval": "approval.9", "decision": "deny"}).status_code, 409)
            r = c.post("/api/approve", json={"approval": "approval.1", "decision": "deny", "note": "not now"})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(session.decisions, [("approval.1", False, "not now")])

    def test_shutdown(self):
        exits = []
        _, client = self.client(exits)
        with client as c:
            self.assertEqual(c.post("/api/shutdown", json={}, headers={"Origin": "http://evil.example"}).status_code, 403)
            self.assertEqual(exits, [])
            self.assertEqual(c.post("/api/shutdown", json={}).status_code, 202)
        self.assertEqual(exits, [1])
        self.assertEqual(len(self.records("shutdown_requested")), 1)

    def test_sse_replays_then_stops(self):
        async def go():
            self.bus.publish("a", agent="pilot")
            gen = sse_stream(self.bus, 0, "conv-1")
            hello, first = await gen.__anext__(), await gen.__anext__()
            self.bus.close_streams()
            with self.assertRaises(StopAsyncIteration):
                await gen.__anext__()
            return hello, json.loads(first.split("data: ", 1)[1])
        hello, rec = self.run_async(go())
        self.assertIn('"conv-1"', hello)
        self.assertEqual(self.ledger.line(rec["id"])["body"]["kind"], "a")


if __name__ == "__main__":
    unittest.main()
