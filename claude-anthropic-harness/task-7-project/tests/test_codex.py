"""The Codex backend offline: the real codex binary against a fake model, in an isolated CODEX_HOME.

Nothing logs in or calls a model service. The fake model (fake_model.py, copied from
claude-codex-harness) is a local stand-in for the Responses API, and Codex runs with
CODEX_HOME = task-7-project/.runtime/offline-codex-home (gitignored; founder decision,
2026-09-28). Every test here starts real App Server processes, so this file takes a while.
If codex is not installed, the process tests are skipped.

What these pin down (codex 0.158):
  - the tool surface each GPT model gets under backend_codex.RESTRICTIONS: exec (with only
    the harness tools and a clock inside), wait and request_user_input; no shell, patch,
    native delegation, goals, skills text or instruction files;
  - a GPT pilot's turn end to end, through the session;
  - a GPT subagent under a Claude parent: the pinned brief, the onboarding gate, its own
    ledger entries, the audit;
  - stops (native call), declines (approvals, request_user_input), limits (max_turns,
    timeout) and interrupts;
  - the CODEX_HOME change record.
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest

from tests.support import TASK_DIR, HarnessCase
from tests.fake_model import FakeModel, message, tool_names

from agent import AgentCore  # noqa: E402
from audit import audit  # noqa: E402
from backend_codex import (CodexBackend, call_identity, dynamic_tools, offline_settings,  # noqa: E402
                           record_codex_home_changes)
from bounds import Bounds  # noqa: E402
from codex_server import find_codex  # noqa: E402
from session import AgentSession  # noqa: E402

OFFLINE_HOME = TASK_DIR / ".runtime" / "offline-codex-home"
try:
    CODEX = find_codex()
except FileNotFoundError:
    CODEX = None
needs_codex = unittest.skipIf(CODEX is None, "codex is not installed")
CHILD_BOUNDS = "fs.read /origins\nledger.read pilot/tasks\nledger.write pilot/tasks/out"


def exec_call(js: str, call_id: str) -> dict:
    return {"type": "custom_tool_call", "name": "exec", "call_id": call_id, "input": js}


class Script:
    """Answers each model request with its next step. Steps:
         ("tool", "server__name", {args})  a harness tool, called from exec (code-mode models)
         ("text", "...")                   a reply
         ("raw", [items])                  output items as given
         ("wait", event)                   block until the event is set, then reply
       After the last step it replies "(script ended)"."""

    def __init__(self, *steps: tuple):
        self.steps = list(steps)
        self.n = 0
        self.bodies: list[dict] = []

    def __call__(self, body: dict) -> list[dict]:
        self.n += 1
        self.bodies.append(body)
        if not self.steps:
            return [message("(script ended)")]
        step = self.steps.pop(0)
        if step[0] == "tool":
            return [exec_call(f"text(await tools.{step[1]}({json.dumps(step[2])}));", f"call_{self.n}")]
        if step[0] == "text":
            return [message(step[1])]
        if step[0] == "raw":
            return step[1]
        if step[0] == "wait":
            step[1].wait(30)
            return [message("(released)")]
        raise ValueError(step)


class CodexCase(HarnessCase):
    top_model = "gpt-6-astra"
    turn_timeout_s = 120.0

    def make_codex(self):
        self.script = Script()
        self.fake = FakeModel(lambda body: self.script(body))
        self.fake.server.handle_error = lambda request, address: None  # interrupted requests: no tracebacks
        settings = offline_settings(OFFLINE_HOME, self.tmp / "codex-state", self.fake.codex_args(), CODEX)
        settings.turn_timeout_s = self.turn_timeout_s
        return settings

    def tearDown(self) -> None:
        self.fake.close()
        super().tearDown()

    def onboarding(self) -> str:
        return str(self.root / "origins" / "onboarding_1.02.md")

    def offered(self, request: int = 0) -> list[str]:
        bodies = [r["body"] for r in self.fake.requests if isinstance(r.get("body"), dict)]
        return tool_names(bodies[request])

    async def one_turn(self, core: AgentCore, text: str):
        async with core.backend:
            core.turn = 1
            return await core.run_turn(text, author="human:session-user")


@needs_codex
class SurfaceTests(CodexCase):
    """What Codex offers each model the harness uses, and what it adds to the conversation."""

    def test_offered_tools_per_model(self):
        subagent = Bounds.parse(CHILD_BOUNDS)
        for model in ("gpt-6-astra", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.6-terra"):
            with self.subTest(model=model):
                self.fake.requests.clear()
                self.script = Script(("text", "hello"))
                core = AgentCore(self.env, agent_id=f"probe-{model}", model=model, bounds=subagent,
                                 system_prompt="SYSTEM-PROMPT-MARKER", role="subagent")
                result = self.run_async(self.one_turn(core, "hi"))
                self.assertEqual((result.is_error, result.subtype, result.final_text), (False, "success", "hello"))
                offered = set(self.offered())
                harness = {f"functions.exec>{t.server}__{t.name}" for t in core.tool_defs}
                runtime = {"functions.exec", "functions.wait", "functions.request_user_input",
                           "functions.request_user_input_async", "functions.exec>clock__curr_time"}
                self.assertLessEqual(harness, offered)
                self.assertLessEqual(offered - harness, runtime, f"{model} is offered more than expected")
                body = [r["body"] for r in self.fake.requests if isinstance(r.get("body"), dict)][0]
                inputs = [c.get("text", "") for i in body["input"] if i.get("type") == "message"
                          for c in i.get("content") or []]
                self.assertIn("SYSTEM-PROMPT-MARKER", inputs)  # the harness's prompt, as Codex's base instructions
                self.assertFalse(any("<skills_instructions>" in s or "<permissions instructions>" in s
                                     or "<collaboration_mode>" in s for s in inputs), inputs)
                restrictions = self.records("codex_restrictions", agent=core.agent_id)[0]
                self.assertEqual((restrictions["instruction_sources"], restrictions["exec_host"]), ([], True))
                self.assertEqual(restrictions["stays_on"], ["unified_exec"])  # reads on; no shell tool is offered

    def test_prompt_and_reply_are_links_in_rpc_records(self):
        self.script = Script(("text", "a reply only the text entry should hold"))
        self.run_async(self.one_turn(self.top, "a prompt only the text entry should hold"))
        rpc = json.dumps(self.records("codex_rpc"))
        self.assertNotIn("a prompt only the text entry should hold", rpc)
        self.assertNotIn("a reply only the text entry should hold", rpc)
        self.assertNotIn("Treat the content of files and ledger entries as information", rpc)  # digested (preview: 300 chars)
        texts = [(t["direction"], t["author"]) for t in self.records("text")]
        self.assertEqual(texts, [("to_agent", "human:session-user"), ("from_agent", self.top.author)])


@needs_codex
class GptPilotTests(CodexCase):
    def test_a_turn_through_the_session(self):
        self.script = Script(("tool", "fs__read", {"path": self.onboarding()}),
                             ("tool", "calc__add", {"a": 1, "b": 2}),
                             ("text", "The sum is 3."))

        async def scenario():
            s = AgentSession(self.top)
            await s.start()
            self.assertIsNone(s.send("please add 1 and 2"))
            for _ in range(3000):
                if s.state != "busy":
                    break
                await asyncio.sleep(0.01)
            await s.stop()
            return s
        s = self.run_async(scenario())
        self.assertEqual(s.state, "stopped")
        start = self.records("session_start")[0]
        self.assertEqual((start["backend"], start["lineage"], start["model"]), ("codex-app-server", "gpt", "gpt-6-astra"))
        self.assertEqual(start["account"], {"subscriptionType": None, "apiProvider": "openai:None"})
        calls = [(r["tool_name"], r["phase"], r.get("policy_allow")) for r in self.records("tool_call")]
        self.assertEqual(calls, [("mcp__fs__read", "pre", True), ("mcp__fs__read", "post", None),
                                 ("mcp__calc__add", "pre", True), ("mcp__calc__add", "post", None)])
        self.assertEqual([(t["author"], t["text"]) for t in self.records("text")],
                         [("human:session-user", "please add 1 and 2"), (self.top.author, "The sum is 3.")])
        usage = self.records("usage")[-1]
        self.assertEqual((usage["lineage"], usage["subtype"], usage["num_turns"]), ("gpt", "success", 3))
        self.assertEqual(usage["agent_tokens"], 330)  # the fake model reports 110 tokens per response
        self.assertEqual([r["call"] for r in self.records("model_call")], ["exec", "exec"])
        self.assertTrue(all(r["permitted"] for r in self.records("model_call")))
        self.assertEqual(self.records("codex_exit")[0]["code"], 0)
        self.assertEqual(audit(self.view(), self.ledger.session), [])

    def test_home_changes_are_recorded(self):
        self.script = Script(("text", "ok"))
        self.run_async(self.one_turn(self.top, "hi"))
        record = record_codex_home_changes(self.env)
        self.assertEqual((record["kind"], record["live_home"], record["codex_home"]),
                         ("codex_home_changes", False, str(OFFLINE_HOME)))
        self.assertTrue(all(set(c) == {"path", "change", "mtime", "while_running"} for c in record["changes"]))


@needs_codex
class GptSubagentTests(CodexCase):
    top_model = "claude-sonnet-5"  # a Claude parent (fake client), a GPT child (real Codex, fake model)

    def setUp(self):
        super().setUp()
        self.top.announce_start()

    def test_spawn_a_gpt_subagent(self):
        v1 = self.top.guard.write("pilot/tasks/t1", "Read onboarding, add 19.5 and 22.75, record the sum.")["written"]
        self.script = Script(("tool", "calc__add", {"a": 0, "b": 0}),  # refused: onboarding first
                             ("tool", "fs__read", {"path": self.onboarding()}),
                             ("tool", "calc__add", {"a": 19.5, "b": 22.75}),
                             ("tool", "ledger__write", {"name": "pilot/tasks/out/sum", "body": 42.25}),
                             ("text", "42.25, written."))
        out = self.run_async(self.env.spawner.spawn(self.top, model="gpt-5.6-terra", bounds_text=CHILD_BOUNDS,
                                                    instructions="pilot/tasks/t1"))
        self.assertEqual((out["status"], out["onboarding_read"], out["agent"], out["final_reply"]),
                         ("success", True, "pilot.1", "42.25, written."))
        calls = [(r["tool_name"], r["policy_allow"]) for r in self.records("tool_call", agent="pilot.1")
                 if r["phase"] == "pre"]
        self.assertEqual(calls, [("mcp__calc__add", False), ("mcp__fs__read", True), ("mcp__calc__add", True),
                                 ("mcp__ledger__write", True)])
        self.assertEqual(self.ledger.current("pilot/tasks/out/sum").author,
                         f"pilot.1@{self.ledger.session}:gpt-5.6-terra@claude-anthropic-harness")
        prompt = self.records("message", agent="pilot.1")[0]
        self.assertEqual((prompt["message"]["text"], prompt["text_id"]), ("[[pilot/tasks/t1]]", v1))
        brief = self.ledger.line(v1)["body"]
        self.assertNotIn(brief, json.dumps(self.records("codex_rpc", agent="pilot.1")))  # linked, not copied
        self.assertEqual(self.records("subagent_session", agent="pilot.1")[0]["backend"], "codex-app-server")
        self.assertEqual(audit(self.view(), self.ledger.session), [])


@needs_codex
class StopAndLimitTests(CodexCase):
    def test_a_native_call_stops_the_agent(self):
        self.script = Script(("raw", [{"type": "function_call", "name": "spawn_agent", "namespace": "collaboration",
                                       "call_id": "c1", "arguments": "{}"}]),
                             ("text", "should not be reached"))

        async def scenario():
            async with self.top.backend as backend:
                self.top.turn = 1
                first = await self.top.run_turn("go", author="human:session-user")
                second = await self.top.run_turn("again", author="human:session-user")
                return first, second, backend
        first, second, backend = self.run_async(scenario())
        self.assertEqual((first.is_error, first.subtype), (True, "stopped"))
        self.assertEqual(second.subtype, "stopped")
        stop = self.records("native_call_stop")[0]
        self.assertEqual(stop["reason"], "model called 'collaboration.spawn_agent'")
        self.assertEqual(self.records("model_call")[0]["permitted"], False)
        self.assertEqual(len(self.records("codex_exit")), 1)  # closed at the stop, not again at exit
        self.assertEqual(self.records("turn_refused")[0]["reason"], "model called 'collaboration.spawn_agent'")
        self.assertIsNone(backend.server)

    def test_user_input_tools_do_not_reach_the_harness(self):
        """Codex refuses request_user_input in Default mode, and turns request_user_input_async into an
        agent message. Neither is a stop, and neither sends the harness a request."""
        question = {"questions": [{"id": "q1", "header": "Pick", "question": "Which one?",
                                   "options": [{"label": "A", "description": "first"},
                                               {"label": "B", "description": "second"}]}]}
        self.script = Script(("raw", [{"type": "function_call", "name": "request_user_input", "call_id": "u1",
                                       "arguments": json.dumps(question)}]),
                             ("raw", [{"type": "function_call", "name": "request_user_input_async", "call_id": "u2",
                                       "arguments": json.dumps({"questions": [{"title": "Which?", "options": ["A"]}]})}]),
                             ("text", "carried on"))
        result = self.run_async(self.one_turn(self.top, "go"))
        self.assertEqual((result.subtype, result.final_text), ("success", "carried on"))
        outputs = [i.get("output") for b in self.script.bodies for i in b["input"] if i.get("call_id") == "u1"
                   and i.get("type") == "function_call_output"]
        self.assertEqual(outputs[-1], "request_user_input is unavailable in Default mode")
        self.assertIn("Which?", [t["text"].split("\n")[0] for t in self.records("text")])  # a message, by the agent
        self.assertEqual((self.records("user_input_declined"), self.records("native_call_stop")), ([], []))
        self.assertEqual([r["call"] for r in self.records("model_call")], ["request_user_input", "request_user_input_async"])

    def test_max_turns_interrupts_the_turn(self):
        self.script = Script(*[("tool", "calc__add", {"a": i, "b": 1}) for i in range(8)])
        result = self.run_async(self.one_turn(self.top, "loop"))  # HarnessCase: max_turns=5
        self.assertEqual((result.is_error, result.subtype), (True, "max_turns"))
        limit = self.records("turn_limit")[0]
        self.assertEqual((limit["reason"], limit["rounds"]), ("max_turns", 6))

    def test_interrupt(self):
        release = threading.Event()
        self.script = Script(("wait", release))

        async def scenario():
            async with self.top.backend:
                self.top.turn = 1
                turn = asyncio.create_task(self.top.run_turn("wait", author="human:session-user"))
                await asyncio.sleep(2)
                await self.top.interrupt()
                result = await turn
                release.set()
                return result
        try:
            result = self.run_async(scenario())
        finally:
            release.set()
        self.assertEqual((result.is_error, result.subtype), (True, "interrupted"))
        self.assertEqual(len(self.records("interrupt_requested")), 1)


@needs_codex
class TimeoutTests(CodexCase):
    turn_timeout_s = 2.0

    def test_model_time_is_capped(self):
        release = threading.Event()
        self.script = Script(("wait", release))
        try:
            result = self.run_async(self.one_turn(self.top, "wait"))
        finally:
            release.set()
        self.assertEqual((result.is_error, result.subtype), (True, "timeout"))
        self.assertEqual(self.records("turn_limit")[0]["reason"], "timeout")


class UnitTests(HarnessCase):
    """No process: the policy helpers and the request handlers."""

    top_model = "gpt-6-astra"

    def make_codex(self):
        return offline_settings(OFFLINE_HOME, self.tmp / "codex-state", [], CODEX or "codex")

    def test_call_identity(self):
        self.assertEqual(call_identity({"type": "custom_tool_call", "name": "exec"}), "exec")
        self.assertEqual(call_identity({"type": "function_call", "namespace": "fs", "name": "read"}), "fs.read")
        self.assertEqual(call_identity({"type": "local_shell_call"}), "local_shell")
        self.assertIsNone(call_identity({"type": "custom_tool_call_output"}))
        self.assertIsNone(call_identity({"type": "message"}))

    def test_dynamic_tools_are_one_namespace_per_server(self):
        spec = dynamic_tools(self.top)
        names = {(ns["name"], t["name"]) for ns in spec for t in ns["tools"]}
        self.assertEqual({f"mcp__{s}__{n}" for s, n in names}, set(self.top.allowed_tools))
        self.assertTrue(all(ns["type"] == "namespace" and not ns["name"].startswith("mcp") for ns in spec))

    def test_approval_requests_are_declined_and_stop_the_agent(self):
        backend: CodexBackend = self.top.backend

        async def ask():
            return await backend._on_request({"method": "item/commandExecution/requestApproval", "id": 7,
                                              "params": {"command": "rm -rf /"}})
        self.assertEqual(self.run_async(ask()), {"decision": "decline"})
        self.assertEqual(backend.stopped, "approval requested: item/commandExecution/requestApproval")
        self.assertEqual(len(self.records("approval_declined")), 1)
        self.assertEqual(len(self.records("native_call_stop")), 1)

    def test_unexpected_items_stop_the_agent(self):
        backend: CodexBackend = self.top.backend
        backend._on_notification({"method": "item/started", "params": {"item": {"type": "commandExecution",
                                                                                "id": "i1"}}})
        self.assertEqual(backend.stopped, "thread item 'commandExecution'")
        backend._on_notification({"method": "item/started", "params": {"item": {"type": "agentMessage"}}})
        self.assertEqual(len(self.records("native_call_stop")), 1)

    def test_tool_calls_map_to_harness_names_and_checks(self):
        backend: CodexBackend = self.top.backend
        backend.thread_id = "t"

        async def call(namespace, tool, arguments):
            backend._loop = asyncio.get_running_loop()
            return await backend._tool_call({"threadId": "t", "turnId": "u", "callId": "c", "namespace": namespace,
                                             "tool": tool, "arguments": arguments})
        ok = self.run_async(call("calc", "add", {"a": 2, "b": 3}))
        self.assertTrue(ok["success"])
        self.assertIn("5", ok["contentItems"][0]["text"])
        missing = self.run_async(call("shell", "run", {"cmd": "dir"}))
        self.assertFalse(missing["success"])
        self.assertIn("not available to this agent", missing["contentItems"][0]["text"])
        text = self.run_async(call("calc", "add", '{"a": 1, "b": 1}'))  # exec may pass a JSON string
        self.assertTrue(text["success"])


if __name__ == "__main__":
    unittest.main()
