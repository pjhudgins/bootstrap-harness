"""A conversation's three layers: first with stand-in agents that run no engine (task
owners, models by layer, requests and approvals, news for the governor, Stop, shutdown),
then the page's port, then end to end on both real engines (the Claude CLI and Codex, one
process per agent) with the scripted stand-in models, skipped if Codex, the in-swimlane
offline Codex home or the Claude Agent SDK is missing."""

import hashlib
import json
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
import policy
import ui


class FakeAgent(agents.Agent):
    """An Agent with no engine. A turn completes at once with one message, unless its
    layer is in `blocking`: then it waits for `go` (or a stop). `on_turn(agent, text)`, if
    set, replaces the turn. While `hold_start` is an unset Event, non-governors stay in
    start(), as if the engine were slow."""
    engine = "fake"
    blocking = set()
    hold_start = None

    def start(self, instructions):
        if self.layer != "governor" and FakeAgent.hold_start is not None:
            FakeAgent.hold_start.wait(10)
        self.instructions_text, self.received = instructions, []
        self.go, self.on_turn = threading.Event(), None
        self.record.write("agent_started", agent=self.id, layer=self.layer, engine=self.engine,
                          parent=self.parent.id if self.parent else None,
                          bounds=self.bounds.as_dict(), tools=self.toolbox.names)
        self.state = "idle"
        return self.model, {}

    def _engine_turn(self, text, cap, idle):
        self.received.append(text)
        if self.on_turn:
            return {"id": "t", "status": self.on_turn(self, text)}
        if self.layer in FakeAgent.blocking:
            while not self.go.wait(0.05):
                if self.stop_requested():
                    self.record.write("interrupt", agent=self.id, reason=self._stop_reason)
                    return {"id": "t", "status": "interrupted"}
            self.go.clear()
        self._message_done(f"m{len(self.received)}", f"done: {text[:60]}")
        return {"id": "t", "status": "completed"}

    def close(self):
        return 0


class TreeCase(unittest.TestCase):
    def setUp(self):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root_dir = self.tmp / "nimoi"
        make_tree(self.root_dir)
        self.ledger_root = self.tmp / "ledgers"
        FakeAgent.blocking, FakeAgent.hold_start = set(), None
        self.c = conv.Conversation(ledger_root=self.ledger_root, fs_root=self.root_dir,
                                   runtime_dir=self.tmp / "rt",
                                   workspace=self.root_dir / "workspace",
                                   scripts=self.root_dir / "scripts",
                                   engines={"claude": FakeAgent, "codex": FakeAgent})
        self.c.codex = "codex (stand-in)"
        self.gov = FakeAgent(self.c, "gov", model=policy.GOVERNOR_MODEL,
                             bounds=self.c.governor_bounds, layer="governor")
        self.gov.start("governor instructions")
        self.c.agents["gov"] = self.c.governor = self.gov
        self.c.state = "idle"
        self.c.worker = threading.Thread(target=self.c._work, daemon=True)
        self.c.worker.start()
        self.ticket = self.c.record.scribe.write("tickets/1", "Bar: done is done. Do it.",
                                                 author=self.gov.author)

    def tearDown(self):
        FakeAgent.blocking, FakeAgent.hold_start = set(), None
        self.c.close()
        view = scribe.load(self.ledger_root, ledger_log.LEDGER_NAME)
        self.assertEqual([str(f) for f in view.findings], [])

    def owner(self, model="claude-opus-5-5", bounds=None):
        result = self.c.start_owner(self.gov, model, bd.Bounds(bounds or {"fs.read": ["origins/"]}),
                                    "tickets/1", self.ticket)
        owner = self.c.agents[result["owner"]]
        self.until(lambda: owner.stats["turns"] >= 1 and owner.state != "running")
        return owner

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


class OwnerTests(TreeCase):
    def test_an_owner_works_its_ticket_then_messages_until_closed(self):
        owner = self.owner()
        self.assertEqual(owner.received, ["Bar: done is done. Do it."])   # the pinned ticket
        ticket = [r for r in self.records("message") if r.get("role") == "ticket"][0]
        self.assertEqual((ticket["text"], ticket["text_id"], ticket["text_author"]),
                         ("[[tickets/1]]", self.ticket, self.gov.author))
        # Its report reached the governor, as a harness message in a turn of its own.
        self.until(lambda: any("Task owner gov.1 ended turn 1" in t for t in self.gov.received))
        self.c.message_owner(self.gov, owner.id, "Also check the notes.")
        self.until(lambda: owner.stats["turns"] == 2 and owner.state == "idle")
        follow_up = [r for r in self.records("message") if r.get("role") == "governor"][0]
        self.assertEqual(follow_up["text_author"], self.gov.author)
        status = self.c.owner_status(self.gov, owner.id, 0)
        self.assertEqual((status["state"], status["turns"]), ("idle", 2))
        self.c.close_owner(self.gov, owner.id, "done")
        self.until(owner.finished.is_set)
        self.assertEqual(self.records("owner_closed")[0]["owner"], owner.id)
        with self.assertRaises(conv.ToolError):
            self.c.message_owner(self.gov, owner.id, "more")

    def test_models_by_layer(self):
        for model in ("claude-sonnet-5", "gpt-5.6-terra", "gpt-5.5"):  # not task owners
            with self.subTest(model=model):
                with self.assertRaises(conv.ToolError):
                    self.c.start_owner(self.gov, model, bd.Bounds({}), "tickets/1", self.ticket)
        owner = self.owner("gpt-6-sol")
        self.assertEqual(owner.layer, "task-owner")
        task = self.c.record.scribe.write("agent/gov.1/task", "sub task", author=owner.author)
        for model in ("claude-haiku-4-5-20251001", "gpt-5.5", "gpt-6-luna"):
            with self.subTest(model=model):
                with self.assertRaises(conv.ToolError):
                    self.c.spawn(owner, model, bd.Bounds({}), "agent/gov.1/task", task)
        child = self.c.agents[self.c.spawn(owner, "claude-fable-5-1", bd.Bounds({}),
                                           "agent/gov.1/task", task)["subagent"]]
        self.until(child.finished.is_set)
        self.assertEqual((child.layer, child.depth, child.state), ("subagent", 2, "done"))
        with self.assertRaises(conv.ToolError):  # the leaf layer spawns nothing
            self.c.spawn(child, "claude-sonnet-5", bd.Bounds({}), "agent/gov.1/task", task)


class RequestTests(TreeCase):
    def ask(self, owner, kind, details, seconds=5):
        result = {}
        thread = threading.Thread(target=lambda: result.update(
            self.c.make_request(owner, kind, "because the ticket needs it", details, seconds)))
        before = len(self.c.requests)
        thread.start()
        self.until(lambda: len(self.c.requests) > before)
        return thread, result, list(self.c.requests)[-1]

    def test_a_grant_within_the_governors_bounds(self):
        owner = self.owner()
        thread, result, rid = self.ask(owner, "bounds", {"bounds": {"ledger.write": ["agent/gov.1/"]}})
        self.until(lambda: any(f"Request {rid}" in t for t in self.gov.received))  # news
        with self.assertRaises(conv.ToolError):  # beyond its own bounds: only the human
            self.c.answer_request(self.gov, rid, "grant", "ok", {"ledger.write": ["notes/"]})
        self.c.answer_request(self.gov, rid, "grant", "that folder only",
                              {"ledger.write": ["agent/gov.1/"]})
        thread.join(5)
        self.assertEqual((result["state"], result["decision"]["decision"]), ("decided", "grant"))
        self.assertTrue(owner.bounds.allows("ledger.write", "agent/gov.1/note"))
        grant = self.records("bounds_granted")[0]
        self.assertEqual((grant["by"], grant["after"]["ledger.write"]),
                         (self.gov.author, ["agent/gov.1/"]))
        with self.assertRaises(conv.ToolError):  # decided once
            self.c.answer_request(self.gov, rid, "refuse", "no", None)

    def test_the_human_decides_what_is_beyond_the_governor(self):
        owner = self.owner()
        thread, result, rid = self.ask(owner, "bounds", {"bounds": {"fs.read": ["*"],
                                                                     "ledger.write": ["notes/"]}},
                                       seconds=10)
        answer = self.c.answer_request(self.gov, rid, "ask_human", "I recommend yes", None)
        self.assertEqual(answer["state"], "asked_human")
        with self.assertRaises(conv.ToolError):  # now the human's
            self.c.answer_request(self.gov, rid, "refuse", "no", None)
        self.c.decide(rid, True, "fine")
        thread.join(5)
        self.assertEqual(result["decision"]["by"], ledger_log.HUMAN_AUTHOR)
        self.assertTrue(owner.bounds.allows("ledger.write", "notes/x"))
        self.until(lambda: any(f"The human approved request {rid}" in t for t in self.gov.received))
        with self.assertRaises(ValueError):
            self.c.decide(rid, True, "again")

    def test_a_script_promotion_is_the_humans_and_the_harness_copies_it(self):
        draft = self.root_dir / "workspace" / "draft.py"
        draft.write_bytes(b"print('vetted')\r\n")
        owner = self.owner()
        details = {"from_path": "workspace/draft.py", "to_path": "scripts/draft.py"}
        thread, result, rid = self.ask(owner, "promote_script", details, seconds=10)
        with self.assertRaises(conv.ToolError):
            self.c.answer_request(self.gov, rid, "grant", "yes", {})
        self.c.answer_request(self.gov, rid, "ask_human", "looks harmless", None)
        self.c.decide(rid, True, "")
        thread.join(5)
        promoted = self.root_dir / "scripts" / "draft.py"
        self.assertEqual(promoted.read_bytes(), draft.read_bytes())
        record = self.records("script_promoted")[0]
        self.assertEqual((record["by"], record["sha256"]),
                         (ledger_log.HUMAN_AUTHOR, hashlib.sha256(draft.read_bytes()).hexdigest()))
        # A second promotion onto the same name is refused, even when approved.
        thread, result, rid = self.ask(owner, "promote_script", details, seconds=10)
        self.c.answer_request(self.gov, rid, "ask_human", "again", None)
        self.c.decide(rid, True, "")
        thread.join(5)
        self.assertEqual(result["decision"]["decision"], "failed")

    def test_a_denial_and_a_pending_wait(self):
        owner = self.owner()
        thread, result, rid = self.ask(owner, "question", {}, seconds=0.2)
        thread.join(5)
        self.assertIn("pending", result["note"])
        self.c.answer_request(self.gov, rid, "answer", "Use the notes folder.", None)
        decided = self.c.wait_request(owner, rid, 1)
        self.assertEqual((decided["decision"]["decision"], decided["decision"]["message"]),
                         ("answer", "Use the notes folder."))


class StopTests(TreeCase):
    def test_stop_reaches_running_turns_and_waiting_subagents(self):
        FakeAgent.blocking = {"task-owner", "subagent"}
        result = self.c.start_owner(self.gov, "claude-opus-5-5", bd.Bounds({"fs.read": ["origins/"]}),
                                    "tickets/1", self.ticket)
        owner = self.c.agents[result["owner"]]
        self.until(lambda: owner.state == "running")
        FakeAgent.hold_start = threading.Event()
        task = self.c.record.scribe.write("agent/gov.1/task", "sub", author=owner.author)
        child = self.c.agents[self.c.spawn(owner, "claude-sonnet-5", bd.Bounds({}),
                                           "agent/gov.1/task", task)["subagent"]]
        self.assertEqual(child.state, "starting")
        self.assertEqual(sorted(self.c.interrupt()), sorted([owner.id, child.id]))
        FakeAgent.hold_start.set()
        self.until(child.finished.is_set)
        self.assertEqual((child.state, child.received), ("interrupted", []))  # never began
        self.until(lambda: owner.state == "idle")        # stopped, not closed
        self.assertFalse(owner.finished.is_set())

    def test_close_cancels_requests_and_closes_owners(self):
        owner = self.owner()
        result = {}
        thread = threading.Thread(target=lambda: result.update(
            self.c.make_request(owner, "question", "why?", {}, 30)))
        thread.start()
        self.until(lambda: self.c.requests)
        self.c.close()
        thread.join(5)
        self.assertEqual(result["decision"]["decision"], "cancelled")
        self.assertTrue(owner.finished.is_set())
        self.assertEqual({a for a in self.records("summary")[0]["agents"]}, {"gov", "gov.1"})


class PageTests(unittest.TestCase):
    def test_a_port_in_use_opens_no_ledger_session(self):
        with scratch_dir() as tmp:
            holder = ui.make_server(0)
            try:
                port = holder.server_address[1]
                with self.assertRaises(SystemExit) as caught:
                    ui.main(["--port", str(port), "--no-browser", "--ledger-root", tmp])
                self.assertEqual(caught.exception.code, 1)
                self.assertFalse((Path(tmp) / ledger_log.LEDGER_NAME).exists())
            finally:
                holder.server_close()


def engines_available():
    try:
        import claude_agent_sdk  # noqa: F401
        cc.find_codex()
        return OFFLINE_HOME.is_dir()
    except (ImportError, FileNotFoundError):
        return False


@unittest.skipUnless(engines_available(), "needs codex, ../.runtime/offline-codex-home and "
                                          "the Claude Agent SDK")
class EndToEndTests(unittest.TestCase):
    """Real engines (the Claude CLI for the governor and a subagent, Codex for a task
    owner) with the scripted stand-in models; offline homes; a temporary ledger."""

    def test_three_layers_on_two_engines(self):
        with scratch_dir() as tmp:
            c = conv.Conversation(fake=True, ledger_root=tmp, runtime_dir=Path(tmp) / "rt")
            seen = [0]

            def wait_for(predicate, seconds=180):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    for event in c.events.after(seen[0], timeout=0.5):
                        seen[0] = event["seq"]
                        if predicate(event):
                            return event
                self.fail("timed out")
            try:
                c.start()
                onboarding = json.dumps({"path": c.onboarding, "max_lines": 5000})
                script = "bootstrap-harness/claude-codex-harness/task-6-hybrid/scripts/hello_safe.py"
                child_task = "\n".join([
                    "Bar: the script ran.", f"tool: fs_read {onboarding}",
                    "tool: python_exec " + json.dumps({"script": script, "args": ["2", "3"]})])
                ticket = "\n".join([
                    "Bar: a note, a request granted, and a subagent's run.",
                    f"tool: fs_read {onboarding}",
                    "tool: governor_request " + json.dumps(
                        {"kind": "bounds", "justification": "to keep a note",
                         "bounds": {"ledger.write": ["agent/gov.1/"]}, "seconds": 120}),
                    "tool: ledger_write " + json.dumps({"name": "agent/gov.1/task", "body": child_task}),
                    "tool: subagent_spawn " + json.dumps(
                        {"model": "claude-sonnet-5", "instructions": "agent/gov.1/task",
                         "bounds": {"fs.read": ["origins/"], "fs.exec": [script]}}),
                    "tool: subagent_wait " + json.dumps({"subagent": "gov.1.1", "seconds": 120})])
                owner_bounds = {"fs.read": ["*"], "fs.exec": [
                    "bootstrap-harness/claude-codex-harness/task-6-hybrid/scripts/"],
                    "ledger.read": ["*"]}
                self.assertTrue(c.send("\n".join([
                    f"tool: fs_read {onboarding}",
                    "tool: ledger_write " + json.dumps({"name": "tickets/1-e2e", "body": ticket}),
                    "tool: owner_start " + json.dumps({"model": "gpt-6-astra", "ticket": "tickets/1-e2e",
                                                       "bounds": owner_bounds})])))
                wait_for(lambda e: e["type"] == "request")
                wait_for(lambda e: e["type"] == "state" and e["state"] == "idle")
                self.assertTrue(c.send("tool: request_answer " + json.dumps(
                    {"request": "r1", "decision": "grant", "message": "ok",
                     "bounds": {"ledger.write": ["agent/gov.1/"]}})))
                done = wait_for(lambda e: e["type"] == "turn_completed" and e["agent"] == "gov.1")
                self.assertEqual(done["status"], "completed")
                child = c.agents["gov.1.1"]
                self.assertEqual((child.engine, child.state), ("claude", "done"))
            finally:
                c.close()
            problems, facts = check_ledger.check(tmp, closed=True)
            self.assertEqual(problems, [])
            self.assertEqual((facts["owners"], facts["subagents"], facts["requests"],
                              facts["grants"], facts["scripts_run"]), (1, 1, 1, 1, 1))
            self.assertGreater(facts["fragments"], 0)
            view = scribe.load(tmp, ledger_log.LEDGER_NAME)
            names = view.names()
            requests = [view.current(n).body for n in names if n.endswith(".model_request")]
            self.assertEqual({r["engine"] for r in requests}, {"claude", "codex"})
            inits = [view.current(n).body for n in names if n.endswith(".agent_init")]
            self.assertTrue(all(set(i["tools"]) <= {policy.claude_tool_name(t) for t in
                                                    c.agents[i["agent"]].toolbox.names}
                                for i in inits))
            runs = [view.current(n).body for n in names if n.endswith(".run")]
            self.assertTrue(all(not n.upper().startswith(policy.HOST_AGENT_PREFIXES)
                                for n in __import__("os").environ))
            self.assertIn("host_agent_variables_removed", runs[0])


if __name__ == "__main__":
    unittest.main()
