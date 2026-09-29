"""A conversation's tree of agents: first with stand-in agents that run no Codex (caps,
the pinned task link, Stop, waits, shutdown), then the page's port, then end to end with
real Codex (one app-server per agent) and the scripted fake model, skipped if Codex or
the in-swimlane offline Codex home is missing."""

import json
import socket
import threading
import time
import unittest
from pathlib import Path

from tests.helpers import OFFLINE_HOME, make_tree, scratch_dir
import agent as agents
import bounds as bd
import check_ledger
import codex_client as cc
import conversation as conv
import ledger_log
from ledger_log import scribe
import ui


class FakeAgent(agents.Agent):
    """An Agent without Codex. A turn waits until `go` is set or it is stopped, then
    completes with one message; `on_turn(agent, text)`, if set, replaces that. While
    `hold_start` is an unset Event, subagents stay in start(), as if Codex were slow."""
    hold_start = None

    def start(self, instructions):
        if self.parent is not None and FakeAgent.hold_start is not None:
            FakeAgent.hold_start.wait(10)
        self.instructions_text = instructions
        self.go, self.on_turn, self.received = threading.Event(), None, None
        self.record.write("agent_started", agent=self.id,
                          parent=self.parent.id if self.parent else None, depth=self.depth,
                          model=self.model, author=self.author, bounds=self.bounds.as_dict(),
                          tools=self.toolbox.names, instructions=self.instructions_name,
                          instructions_id=self.instructions_id)
        self.state = "idle"
        return {"model": self.model}, {}

    def _run_protocol_turn(self, text, cap, idle):
        self.received = text
        self.turn_cap_deadline = time.monotonic() + cap
        if self.on_turn:
            return {"id": "t", "status": self.on_turn(self, text)}
        while not self.go.wait(0.05):
            if self.stop_requested():
                self.record.write("interrupt", agent=self.id, reason=self._stop_reason)
                return {"id": "t", "status": "interrupted"}
        self._message_done("item-1", f"done: {text}", None)
        return {"id": "t", "status": "completed"}

    def close(self):
        self.server = None
        return 0


class TreeCase(unittest.TestCase):
    def setUp(self):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root_dir = self.tmp / "nimoi"
        make_tree(self.root_dir)
        self.ledger_root = self.tmp / "ledgers"
        self.c = conv.Conversation(ledger_root=self.ledger_root, fs_root=self.root_dir,
                                   runtime_dir=self.tmp / "rt",
                                   workspace=self.root_dir / "workspace",
                                   scripts=self.root_dir / "scripts", agent_factory=FakeAgent)
        self.c.catalog = {"m": {"tool_mode": None, "multi_agent_version": None},
                          "code": {"tool_mode": "code_mode_only", "multi_agent_version": None}}
        self.pilot = FakeAgent(self.c, "pilot", model="m", bounds=self.c.root_bounds, depth=0,
                               role="test-pilot")
        self.pilot.start("root instructions")
        self.c.agents["pilot"] = self.c.root = self.pilot
        self.instructions = self.c.record.scribe.write("agent/tasks/one", "the task",
                                                       author=self.pilot.author)

    def tearDown(self):
        self.c.close()
        view = scribe.load(self.ledger_root, ledger_log.LEDGER_NAME)
        self.assertEqual([str(f) for f in view.findings], [])

    def spawn(self, parent=None, model="m", bounds=None):
        return self.c.spawn(parent or self.pilot, model, bd.Bounds(bounds or {}),
                            "agent/tasks/one", self.instructions)["subagent"]

    def records(self, kind):
        s = self.c.record.scribe
        return [s.current(n).body for n in sorted(s.names())
                if n.startswith(ledger_log.HARNESS_PREFIX) and n.endswith(f".{kind}")]

    def until(self, predicate, seconds=10):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("timed out")
            time.sleep(0.02)


class TreeTests(TreeCase):
    def test_the_task_is_the_pinned_version_linked_with_its_author(self):
        # A collaborator supersedes the instructions after the parent pinned them.
        pinned = self.instructions
        self.c.record.scribe.write("agent/tasks/one", "changed later", author="someone:else",
                                   prev=pinned)
        child_id = self.spawn()
        child = self.c.agents[child_id]
        self.until(lambda: hasattr(child, "go"))
        child.go.set()
        self.until(child.finished.is_set)
        self.assertEqual(child.received, "the task")
        task = [r for r in self.records("message") if r.get("role") == "task"][0]
        self.assertEqual((task["agent"], task["text"], task["text_id"], task["text_author"],
                          task["instructed_by"]),
                         (child_id, "[[agent/tasks/one]]", pinned, self.pilot.author,
                          self.pilot.author))
        names = self.c.record.scribe.names()
        self.assertFalse([n for n in names if n.endswith(f"{child_id}-task")])  # not copied
        report = self.c.wait(self.pilot, child_id, 1)
        self.assertEqual((report["state"], report["report"]), ("done", "done: the task"))
        self.assertIn("agent/tasks/one", child.instructions_text)  # its own instructions

    def test_caps_models_and_depth(self):
        for model in ("code", "unknown"):
            with self.subTest(model=model):
                with self.assertRaises(conv.ToolError) as caught:
                    self.spawn(model=model)
                self.assertIn("direct tools", str(caught.exception))
        child = self.c.agents[self.spawn()]
        grandchild = self.c.agents[self.spawn(parent=child)]
        self.assertEqual((child.id, grandchild.id, grandchild.depth), ("pilot.1", "pilot.1.1", 2))
        with self.assertRaises(conv.ToolError) as caught:
            self.spawn(parent=grandchild)
        self.assertIn("nest at most", str(caught.exception))
        for _ in range(conv.MAX_RUNNING - 2):
            self.spawn()
        with self.assertRaises(conv.ToolError) as caught:
            self.spawn()
        self.assertIn("at once", str(caught.exception))

    def test_stop_reaches_every_running_agent_and_ends_waits(self):
        waited = {}

        def orchestrate(agent, text):  # the pilot's turn: delegate, then wait long
            child_id = self.spawn()
            waited["result"] = agent.wait(child_id, 120)
            return "interrupted" if agent.stop_requested() else "completed"

        self.pilot.on_turn = orchestrate
        self.c.worker = threading.Thread(target=self.c._work, daemon=True)
        self.c.worker.start()
        self.c.state = "idle"
        self.assertTrue(self.c.send("please delegate"))
        self.until(lambda: "pilot.1" in self.c.agents
                   and self.c.agents["pilot.1"].state == "running")
        started = time.monotonic()
        self.assertEqual(sorted(self.c.interrupt()), ["pilot", "pilot.1"])
        self.until(lambda: "result" in waited)
        self.assertLess(time.monotonic() - started, 5)
        self.until(self.c.agents["pilot.1"].finished.is_set)
        self.assertEqual(self.c.agents["pilot.1"].state, "interrupted")
        self.assertEqual(self.records("stop")[0]["agents"], ["pilot", "pilot.1"])
        self.until(lambda: self.c.state == "idle")
        self.assertEqual(self.c.interrupt(), [])  # nothing running: nothing to stop

    def test_stop_reaches_a_subagent_still_starting(self):
        """Live, 2026-09-25: a subagent spawned a moment before Stop was still starting,
        was not stopped, and ran its whole task."""
        FakeAgent.hold_start = threading.Event()
        self.addCleanup(setattr, FakeAgent, "hold_start", None)
        child = self.c.agents[self.spawn()]
        self.assertEqual(child.state, "starting")
        self.assertEqual(self.c.interrupt(), [child.id])
        FakeAgent.hold_start.set()
        self.until(child.finished.is_set)
        self.assertEqual((child.state, child.received), ("interrupted", None))  # never began
        self.assertEqual([r["agent"] for r in self.records("interrupt") if r.get("before_start")],
                         [child.id])

    def test_close_ends_every_agent_and_records_it(self):
        children = [self.spawn(), self.spawn()]
        self.until(lambda: all(self.c.agents[c].state == "running" for c in children))
        self.c.close()
        finished = {r["subagent"]: r["state"] for r in self.records("subagent_finished")}
        self.assertEqual(finished, {c: "interrupted" for c in children})
        self.assertEqual(set(self.records("summary")[0]["agents"]), {"pilot", *children})


class PageTests(unittest.TestCase):
    def test_a_port_in_use_opens_no_ledger_session(self):
        with scratch_dir() as tmp:
            holder = ui.make_server(0)  # another page, or any server, on the port
            try:
                port = holder.server_address[1]
                with self.assertRaises(SystemExit) as caught:
                    ui.main(["--port", str(port), "--no-browser", "--ledger-root", tmp])
                self.assertEqual(caught.exception.code, 1)
                self.assertFalse((Path(tmp) / ledger_log.LEDGER_NAME).exists())
            finally:
                holder.server_close()

    def test_any_http_server_on_the_port_is_detected(self):
        plain = socket.socket()
        plain.bind(("127.0.0.1", 0))
        plain.listen()
        try:
            with self.assertRaises(OSError):
                ui.make_server(plain.getsockname()[1])
        finally:
            plain.close()


def codex_available():
    try:
        cc.find_codex()
        return OFFLINE_HOME.is_dir()
    except FileNotFoundError:
        return False


@unittest.skipUnless(codex_available(), "needs codex and ../.runtime/offline-codex-home")
class EndToEndTests(unittest.TestCase):
    """Real Codex (one app-server per agent) + scripted fake model + temporary ledger."""

    def test_parent_spawns_a_child_that_reads_onboarding_and_runs_a_script(self):
        with scratch_dir() as tmp:
            c = conv.Conversation(codex_home=str(OFFLINE_HOME), fake=True, ledger_root=tmp,
                                  runtime_dir=Path(tmp) / "codex-sqlite")
            try:
                c.start()
                onboarding = json.dumps({"path": c.onboarding})
                script = "bootstrap-harness/claude-codex-harness/task-5-subagent/scripts/hello_safe.py"
                child_task = "\n".join([
                    "Bar: the script ran and its output is in the ledger.",
                    f"tool: fs_read {onboarding}",
                    'tool: python_exec ' + json.dumps({"script": script, "args": ["2", "3"]})])
                child_bounds = {"fs.read": ["origins/"], "fs.exec": [script],
                                "ledger.read": ["agent/tasks/"]}
                lines = [f"tool: fs_read {onboarding}",
                         "tool: ledger_write " + json.dumps({"name": "agent/tasks/child",
                                                             "body": child_task}),
                         "tool: subagent_spawn " + json.dumps(
                             {"model": "gpt-6-astra", "bounds": {}, "instructions": "agent/tasks/child"}),
                         "tool: subagent_spawn " + json.dumps(
                             {"model": "gpt-5.5", "bounds": {"fs.read": ["*"], "fs.write": ["x/"]},
                              "instructions": "agent/tasks/child"}),
                         "tool: subagent_spawn " + json.dumps(
                             {"model": "gpt-5.5", "bounds": child_bounds,
                              "instructions": "agent/tasks/child"}),
                         "tool: subagent_wait " + json.dumps({"subagent": "pilot.1", "seconds": 90})]
                self.assertTrue(c.send("\n".join(lines)))
                deadline = time.monotonic() + 180
                seen, done = 0, None
                while time.monotonic() < deadline and done is None:
                    for event in c.events.after(seen, timeout=1):
                        seen = event["seq"]
                        if event["type"] == "turn_completed" and event["agent"] == "pilot":
                            done = event
                self.assertIsNotNone(done, "the root's turn did not complete")
                calls = [e for e in c.events.after(0, timeout=0)
                         if e["type"] == "tool_call" and e["agent"] == "pilot"]
                results = {i: (e["tool"], e["success"], e["output"]) for i, e in enumerate(calls)}
                self.assertEqual([r[:2] for r in results.values()],
                                 [("fs_read", True), ("ledger_write", True),
                                  ("subagent_spawn", False), ("subagent_spawn", False),
                                  ("subagent_spawn", True), ("subagent_wait", True)], results)
                self.assertIn("direct tools", results[2][2])
                self.assertIn("not inside your", results[3][2])
                report = json.loads(results[5][2])
                self.assertEqual(report["state"], "done", report)
                self.assertEqual(report["unreviewed_calls"], [])
            finally:
                c.close()
            problems, facts = check_ledger.check(tmp, closed=True)
            self.assertEqual(problems, [])
            self.assertGreater(facts["fragments"], 0)  # streamed, and summarised per message
            view = scribe.load(tmp, ledger_log.LEDGER_NAME)
            names = view.names()
            self.assertFalse([n for n in names if n.endswith("pilot.1-task")])  # linked, not copied
            runs = [view.current(n) for n in view.labelled("exec")]
            self.assertEqual(len(runs), 1)
            self.assertIn("sum of numeric arguments: 5.0", runs[0].body)
            requests = [view.current(n).body for n in names if n.endswith(".model_request")]
            self.assertTrue(any(r["new_inputs"] for r in requests))
            self.assertFalse([n for n in names if n.endswith(".recv") and
                              view.current(n).body["message"].get("method") == agents.FRAGMENT])


if __name__ == "__main__":
    unittest.main()
