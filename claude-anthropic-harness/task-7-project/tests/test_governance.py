"""The three layers with fake Claude clients (and, for a GPT owner, the real codex binary against the
fake model): dispatch, the owner's self-blocking requests, human approvals, events to the governor,
limits, stops, and the audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from tests.support import HARNESS, make_project, make_tree, scribe
from tests.fake_client import FakeFactory
from tests.test_codex import CODEX

from audit import audit  # noqa: E402
from bounds import Bounds  # noqa: E402
from events import EventBus  # noqa: E402
from governance import BAR_LINE  # noqa: E402
from harness import build_institution  # noqa: E402
from ledgerlog import LedgerLog  # noqa: E402
from session import AgentSession  # noqa: E402

GOV = Bounds.parse("fs.read / !/candidate_repos/\nledger.read *\nledger.write gov/")
CEILING = Bounds.parse("fs.read / !/candidate_repos/\nfs.write /work/\nfs.exec /scripts/\nledger.read *\n"
                       "ledger.write work/")
OWNER = "fs.read /origins /docs /work\nledger.read *\nledger.write work/t1/"
OWNER_MODEL = "claude-fable-5-1"


def dispatch(assignment: str = "gov/assignments/t1", bounds: str = OWNER, model: str = OWNER_MODEL, **kw: Any) -> tuple:
    return ("tool", "mcp__gov__dispatch", {"model": model, "bounds": bounds, "assignment": assignment, **kw})


def assign(*lines: str, name: str = "gov/assignments/t1") -> tuple:
    return ("tool", "mcp__ledger__write", {"name": name, "body": "\n".join(lines)})


class GovCase(unittest.TestCase):
    """A fresh tree, ledger and institution per test; the governor is a fake Claude client."""

    governor_turns: list[list[tuple]] = []
    owner_scripts: dict[str, list[list[tuple]]] = {}

    def make_codex(self) -> Any:
        return None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "nimoi"
        make_tree(self.root)
        self.project = make_project(self.root)
        self.ledger = scribe.Scribe.open(self.project.ledger_dir, "t", session_author=HARNESS, create=True)
        self.bus = EventBus(LedgerLog(scribe, self.ledger, HARNESS))
        self.factory = FakeFactory({"governor": [list(t) for t in self.governor_turns], **self.owner_scripts})
        self.env, self.gov, self.inst = build_institution(
            scribe, self.ledger, self.bus, project=self.project, governor_bounds=GOV, ceiling=CEILING, budget_usd=5.0,
            max_turns=10, client_factory=self.factory, codex=self.make_codex())

    def tearDown(self) -> None:
        if self.ledger.is_open:
            self.ledger.close()
        self.assertEqual(self.view().findings, [], "the test ledger must reload clean")
        self._tmp.cleanup()

    # ---- helpers ----

    def view(self) -> Any:
        return scribe.load(self.project.ledger_dir, "t")

    @property
    def onboarding(self) -> str:
        return str(self.root / "origins" / "onboarding_1.02.md")

    def read_onboarding(self) -> str:
        return f'tool: mcp__fs__read {json.dumps({"path": self.onboarding})}'

    def records(self, kind: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.bus.history if (kind is None or r["kind"] == kind)
                and (agent is None or r.get("agent") == agent)]

    def results(self, agent: str) -> list[dict[str, Any]]:
        return self.factory.clients[agent].results

    def settled(self, s: AgentSession) -> bool:
        owners_busy = any(r.state in ("starting", "working", "waiting") for r in self.inst.owners.values())
        return s.state not in ("busy", "starting") and not s._human and not s._events and not owners_busy

    def run_scenario(self, *messages: str, during: Callable[[AgentSession], Any] | None = None,
                     timeout: float = 60) -> AgentSession:
        async def main() -> AgentSession:
            self.bus.bind_loop(asyncio.get_running_loop())
            s = AgentSession(self.gov, self.inst)
            await s.start()
            for m in messages:
                self.assertIsNone(s.send(m))
            steady = 0
            for _ in range(int(timeout * 50)):
                if during is not None:
                    await during(s)
                steady = steady + 1 if self.settled(s) else 0
                if steady >= 10:
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail(f"not settled: governor {s.state}, owners "
                          f"{[(o, r.state) for o, r in self.inst.owners.items()]}")
            await s.stop()
            return s
        return asyncio.run(main())

    def governor_prompts(self) -> list[dict[str, Any]]:
        return [t for t in self.records("text", agent="governor") if t["direction"] == "to_agent"]

    def start(self, owner_lines: list[str], governor_later: list[list[tuple]], bounds: str = OWNER, **kw: Any) -> None:
        """The governor's first turn writes an assignment (onboarding first, then `owner_lines`, which the fake
        owner runs as its steps) and dispatches it; `governor_later` are its following turns."""
        self.factory.scripts["governor"] = [[assign(self.read_onboarding(), *owner_lines),
                                             dispatch(bounds=bounds, **kw), ("text", "Dispatched.")], *governor_later]


class DispatchTests(GovCase):
    def test_dispatch_runs_an_owner_in_the_background(self):
        self.start(['tool: mcp__calc__add {"a": 19.5, "b": 22.75}',
                    'tool: mcp__ledger__write {"name": "work/t1/sum", "body": 42.25}', "say: 42.25, recorded."],
                   [[("text", "owner.1 reports 42.25.")]])
        s = self.run_scenario("Please start owner t1.")
        self.assertEqual(s.state, "stopped")
        dispatched = json.loads(self.results("governor")[1]["content"][0]["text"])
        self.assertEqual((dispatched["owner"], dispatched["model"]), ("owner.1", OWNER_MODEL))
        self.assertIn("!log", dispatched["bounds"])  # harness records stay private
        owner = self.inst.owners["owner.1"]
        self.assertTrue(owner.core.onboarding_read)
        self.assertEqual(owner.core.last_text, "42.25, recorded.")
        self.assertEqual(self.ledger.current("work/t1/sum").author,
                         f"owner.1@{self.ledger.session}:{OWNER_MODEL}@claude-anthropic-harness")
        self.assertIn("owner", self.ledger.labels("work/t1/sum"))  # labelled by role
        start = self.records("owner_session", agent="owner.1")[0]
        self.assertEqual((start["role"], start["parent"], start["assignment"]),
                         ("owner", "governor", "gov/assignments/t1"))
        prompts = self.governor_prompts()
        self.assertEqual(prompts[0]["author"], "human:session-user")
        self.assertEqual(prompts[1]["author"], self.env.harness_author)  # the event, between turns
        self.assertIn("[harness] Task owner owner.1 (claude-fable-5-1) finished turn 1: success", prompts[1]["text"])
        self.assertIn("42.25, recorded.", prompts[1]["text"])
        states = [r["state"] for r in self.records("owner_state") if r["owner"] == "owner.1"]
        self.assertEqual(states, ["working", "idle", "closed"])
        end = self.records("owner_end")[0]
        self.assertEqual((end["status"], end["reason"]), ("closed", "the launch is ending"))
        self.assertEqual(audit(self.view(), self.ledger.session), [])


class RefusalTests(GovCase):
    def test_dispatch_refusals(self):
        async def main():
            self.bus.bind_loop(asyncio.get_running_loop())
            g = self.gov
            g.guard.write("gov/assignments/ok", "do the thing")
            g.guard.write("gov/assignments/barred", "do the thing\n**Bar:** perfect")
            self.ledger.write("gov/assignments/human", "do it", author="founder")
            cases = [
                (dict(model="claude-sonnet-5"), "not a task-owner model"),
                (dict(model="gpt-6-astra"), "not configured"),
                (dict(bounds_text="fs.read /origins\nfs.write /elsewhere\nledger.read *"), "ceiling"),
                (dict(bounds_text="fs.read /docs\nledger.read *"), "onboarding"),
                (dict(assignment="gov/assignments/barred"), "never the success bar"),
                (dict(assignment="gov/assignments/human"), "written by the governor"),
                (dict(bounds_text="fs.read /origins\nledger.read work/"), "outside the owner's ledger.read"),
                (dict(budget_usd=0.01), "budget"),
                (dict(max_turns=500), "max_turns"),
            ]
            out = []
            for overrides, expected in cases:
                kw = {**dict(model=OWNER_MODEL, bounds_text=OWNER, assignment="gov/assignments/ok"), **overrides}
                try:
                    self.inst.check_dispatch(g, **kw)
                    out.append((overrides, expected, "not refused"))
                except Exception as e:
                    out.append((overrides, expected, str(e)))
            return out
        for overrides, expected, got in asyncio.run(main()):
            self.assertRegex(got, expected, overrides)

    def test_bar_lines(self):
        for text in ("Bar: x", "**Bar:** x", "- bar : x", "intro\n  Bar: y"):
            self.assertRegex(text, BAR_LINE)
        for text in ("Barriers: x", "the bar is high", "Bar chart: x", "crowbar: x"):
            self.assertNotRegex(text, BAR_LINE)

    def test_roles_and_tools(self):
        gov = self.gov.allowed_tools
        self.assertTrue({"mcp__gov__dispatch", "mcp__gov__resolve", "mcp__gov__status", "mcp__gov__message_owner",
                         "mcp__gov__stop_owner", "mcp__fs__read", "mcp__ledger__write"} <= gov)
        self.assertFalse({"mcp__fs__write", "mcp__exec__python", "mcp__agents__spawn", "mcp__gov__request"} & gov)
        self.assertNotRegex(self.gov.system_prompt, r"\{[a-z_]+\}")
        self.assertIn(CEILING.render(), self.gov.system_prompt)
        self.assertIn("never the bar", self.gov.system_prompt)


class RequestTests(GovCase):
    """An owner asks; the governor resolves in its next turn; the owner carries on."""

    def test_clarify_blocks_until_answered(self):
        self.start(['tool: mcp__gov__request {"kind": "clarify", "justification": "Which file should I read?"}',
                    "say: done after the answer"],
                   [[("tool", "mcp__gov__resolve", {"request": "req.1", "decision": "answer",
                                                    "text": "Read docs/a.md."}), ("text", "Answered.")],
                    [("text", "Noted.")]])
        self.run_scenario("go")
        answer = json.loads(self.results("owner.1")[1]["content"][0]["text"])
        self.assertEqual((answer["decision"], answer["text"]), ("answered", "Read docs/a.md."))
        self.assertEqual(self.inst.owners["owner.1"].core.last_text, "done after the answer")
        states = [r["state"] for r in self.records("owner_state") if r["owner"] == "owner.1"]
        self.assertEqual(states[:4], ["working", "waiting", "working", "idle"])
        event = self.governor_prompts()[1]["text"]
        self.assertIn("asks you (req.1, clarify)", event)
        self.assertEqual(audit(self.view(), self.ledger.session), [])

    def test_expand_bounds_applies_at_once(self):
        write = 'tool: mcp__fs__write {"path": "/work/x.txt", "entry_id": "20260101T000000Z:1"}'
        self.start([write,
                    'tool: mcp__gov__request {"kind": "expand_bounds", "justification": "I must write my notes", '
                    '"details": {"bounds": "fs.read /origins /docs /work\\nfs.write /work/\\nledger.read *\\n'
                    'ledger.write work/t1/"}}',
                    write, "say: done"],
                   [[("tool", "mcp__gov__resolve", {"request": "req.1", "decision": "approve",
                                                    "text": "Within the ceiling."}), ("text", "Approved.")],
                    [("text", "Noted.")]])
        self.run_scenario("go")
        results = self.results("owner.1")
        self.assertIn("outside this agent's fs.write", results[1]["content"][0]["text"])  # before
        self.assertNotIn("outside this agent's fs.write", results[3]["content"][0]["text"])  # after: bounds pass
        changed = self.records("bounds_changed")[0]
        self.assertIn("fs.write      /work", changed["new"])
        self.assertIn("mcp__fs__write", self.inst.owners["owner.1"].core.allowed_tools)  # built from the ceiling

    def test_expand_beyond_the_ceiling_is_refused_before_the_governor(self):
        self.start(['tool: mcp__gov__request {"kind": "expand_bounds", "justification": "more", '
                    '"details": {"bounds": "fs.read /\\nfs.write /origins/"}}', "say: refused"], [[("text", "ok")]])
        self.run_scenario("go")
        self.assertIn("the ceiling", self.results("owner.1")[1]["content"][0]["text"])
        self.assertEqual(self.records("request_open"), [])

    def test_more_budget_raises_rounds_during_the_turn(self):
        adds = [f'tool: mcp__calc__add {{"a": {i}, "b": 1}}' for i in range(4)]
        self.start(['tool: mcp__gov__request {"kind": "more_budget", "justification": "four more sums", '
                    '"details": {"turns": 5}}', *adds, "say: all summed"],
                   [[("tool", "mcp__gov__resolve", {"request": "req.1", "decision": "approve", "text": "ok"}),
                     ("text", "Granted.")], [("text", "Noted.")]], max_turns=3)
        self.run_scenario("go")
        owner = self.inst.owners["owner.1"]
        self.assertEqual(owner.core.max_turns, 8)
        self.assertEqual(owner.core.last_text, "all summed")  # 8 rounds needed, 3 + 5 granted
        self.assertEqual(self.records("turn_limit", agent="owner.1"), [])

    def test_max_turns_without_a_grant_interrupts(self):
        adds = [f'tool: mcp__calc__add {{"a": {i}, "b": 1}}' for i in range(4)]
        self.start([*adds, "say: all summed"], [[("text", "Noted.")]], max_turns=3)
        self.run_scenario("go")
        limit = self.records("turn_limit", agent="owner.1")[0]
        self.assertEqual((limit["rounds"], limit["max_turns"]), (4, 3))
        self.assertIn("finished turn 1: max_turns", self.governor_prompts()[1]["text"])

    def test_promote_script_needs_the_human(self):
        draft = self.root / "work" / "probe2.py"
        draft.write_bytes(b"print('reviewed')\n")
        self.start(['tool: mcp__gov__request {"kind": "promote_script", "justification": "a reviewed probe", '
                    '"details": {"source": "/work/probe2.py", "name": "probe2.py"}}', "say: promoted"],
                   [[("tool", "mcp__gov__resolve", {"request": "req.1", "decision": "approve",
                                                    "text": "Prints one line; no side effects."}),
                     ("text", "Forwarded to you for approval.")],
                    [("text", "The human approved it.")], [("text", "Noted.")]])
        decided = []

        async def human(s: AgentSession) -> None:
            if not decided and "approval.1" in self.inst.approvals:
                draft.write_bytes(b"print('changed after review')\n")  # too late: the request snapshotted it
                decided.append(self.inst.human_decide("approval.1", True, "fine"))

        self.run_scenario("go", during=human)
        promoted = (self.root / "scripts" / "probe2.py").read_bytes()
        self.assertEqual(promoted, b"print('reviewed')\n")
        record = self.records("script_promoted")[0]
        self.assertEqual(record["sha256"], hashlib.sha256(promoted).hexdigest())
        answer = json.loads(self.results("owner.1")[1]["content"][0]["text"])
        self.assertEqual(answer["decision"], "approved")
        self.assertTrue(any("The human approved approval.1" in p["text"] for p in self.governor_prompts()))
        self.assertEqual(self.records("human_decision")[0]["decided_by"], "human:session-user")
        self.assertEqual(audit(self.view(), self.ledger.session), [])
        # The audit's governance checks find tampering: a promotion that does not match its request,
        # and an owner with no end.
        self.bus.publish("script_promoted", agent="governor", request="req.1", sha256="0" * 64)
        self.bus.publish("owner_start", agent="governor", owner="owner.9", bounds="fs.read /", ceiling=CEILING.render(),
                         assignment_id=self.records("owner_start")[0]["assignment_id"])
        problems = audit(self.view(), self.ledger.session)
        self.assertEqual(len(problems), 2, problems)
        self.assertRegex(problems[0], "no owner_end")
        self.assertRegex(problems[1], "promoted sha256 does not match")

    def test_an_owner_allowance_covers_its_subagents(self):
        """Live run, 2026-09-29: a subagent's budget came from the whole launch budget, not its owner's allowance."""
        spawn = {"model": "claude-sonnet-5", "bounds": "fs.read /origins\nledger.read work/t1", "instructions":
                 "work/t1/brief", "budget_usd": 2}
        self.start(['tool: mcp__ledger__write {"name": "work/t1/brief", "body": "say: child done"}',
                    f"tool: mcp__agents__spawn {json.dumps(spawn)}", "say: spawned"],
                   [[("text", "Noted.")]], budget_usd=0.5)
        self.run_scenario("go")
        self.assertEqual(self.records("subagent_start")[0]["budget_usd"], 0.5)  # asked 2, capped by the allowance
        self.assertIsNone(self.records("subagent_end")[0]["tokens"])  # a Claude child: dollars, not tokens
        self.assertAlmostEqual(self.inst.subtree_cost("owner.1"), 0.02)  # the owner's query and its child's
        run = self.inst.owners["owner.1"]
        run.allowance_usd = 0.015
        self.assertIn("by it and its subagents", self.inst._turn_refusal(run))

    def test_stop_owner_cancels_its_request(self):
        self.start(['tool: mcp__gov__request {"kind": "clarify", "justification": "Should I?"}', "say: after"],
                   [[("tool", "mcp__gov__stop_owner", {"owner": "owner.1", "reason": "no longer needed"}),
                     ("text", "Stopped.")], [("text", "Noted.")]])
        self.run_scenario("go")
        answer = json.loads(self.results("owner.1")[1]["content"][0]["text"])
        self.assertEqual(answer["decision"], "cancelled")
        end = self.records("owner_end")[0]
        self.assertEqual((end["status"], end["reason"]), ("stopped", "no longer needed"))
        self.assertTrue(any("has ended: stopped" in p["text"] for p in self.governor_prompts()))

    def test_message_owner_starts_a_new_turn(self):
        self.start(["say: first"],
                   [[("tool", "mcp__gov__message_owner", {"owner": "owner.1", "text": "say: second"}),
                     ("text", "Sent.")], [("text", "Noted.")]])
        self.run_scenario("go")
        owner = self.inst.owners["owner.1"]
        self.assertEqual((owner.turns, owner.core.last_text), (2, "second"))
        prompt = self.records("message", agent="owner.1")
        links = [p["message"]["text"] for p in prompt if p["message"].get("_type") == "prompt"]
        self.assertEqual(links[0], "[[gov/assignments/t1]]")
        self.assertTrue(links[1].startswith("[[log/"))  # the governor's text, its own entry
        self.assertEqual(audit(self.view(), self.ledger.session), [])


class SessionTests(GovCase):
    def test_events_wait_for_the_turn_and_humans_go_first(self):
        self.factory.scripts["governor"] = [[("text", "one")], [("text", "two")], [("text", "events")]]

        async def main():
            self.bus.bind_loop(asyncio.get_running_loop())
            s = AgentSession(self.gov, self.inst)
            await s.start()
            s.deliver("[harness] event A")
            s.send("human one")
            s.deliver("[harness] event B")
            s.send("human two")
            for _ in range(500):
                if s.state == "idle" and not s._events and not s._human:
                    break
                await asyncio.sleep(0.01)
            await s.stop()
        asyncio.run(main())
        texts = [p["text"] for p in self.governor_prompts()]
        self.assertEqual(texts, ["human one", "human two", "[harness] event A\n\n[harness] event B"])


@unittest.skipIf(CODEX is None, "codex is not installed")
class GptOwnerTests(GovCase):
    """A GPT task owner: the real codex binary, the fake model, the isolated CODEX_HOME."""

    def make_codex(self) -> Any:
        from backend_codex import offline_settings
        from tests.fake_model import FakeModel
        from tests.test_codex import OFFLINE_HOME, Script
        self.script = Script()
        self.fake = FakeModel(lambda body: self.script(body))
        self.fake.server.handle_error = lambda request, address: None
        return offline_settings(OFFLINE_HOME, self.tmp / "codex-state", self.fake.codex_args())

    def tearDown(self) -> None:
        self.fake.close()
        super().tearDown()

    def test_a_gpt_owner_works_and_asks(self):
        from tests.test_codex import Script
        self.script = Script(("tool", "fs__read", {"path": self.onboarding}),
                             ("tool", "gov__request", {"kind": "clarify", "justification": "Which sum?"}),
                             ("tool", "calc__add", {"a": 1, "b": 2}),
                             ("text", "The sum is 3."))
        self.factory.scripts["governor"] = [
            [assign("Add the numbers the governor names."), dispatch(model="gpt-5.6-sol"), ("text", "Dispatched.")],
            [("tool", "mcp__gov__resolve", {"request": "req.1", "decision": "answer", "text": "1 + 2"}),
             ("text", "Answered.")],
            [("text", "Noted.")]]
        self.run_scenario("go", timeout=120)
        owner = self.inst.owners["owner.1"]
        self.assertEqual((owner.core.backend.kind, owner.core.last_text), ("codex-app-server", "The sum is 3."))
        calls = [(r["tool_name"], r["policy_allow"]) for r in self.records("tool_call", agent="owner.1")
                 if r["phase"] == "pre"]
        self.assertEqual(calls, [("mcp__fs__read", True), ("mcp__gov__request", True), ("mcp__calc__add", True)])
        usage = self.records("usage", agent="owner.1")[-1]
        self.assertEqual((usage["lineage"], usage["subtype"]), ("gpt", "success"))
        self.assertGreater(usage["tool_time_s"], 0)  # the wait for the governor is not model time
        self.assertIn("gpt-5.6-sol", self.governor_prompts()[2]["text"])
        self.assertEqual(audit(self.view(), self.ledger.session), [])


if __name__ == "__main__":
    unittest.main()
