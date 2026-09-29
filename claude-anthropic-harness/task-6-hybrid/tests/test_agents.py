"""Agents end to end with the fake client: turns, spawns, the onboarding gate, fail-closed, audit."""

import asyncio
import json
import re
import unittest

from tests.support import TOP, HarnessCase

from agent import AgentCore, BudgetPool, planned_tools  # noqa: E402
from audit import audit  # noqa: E402
from bounds import Bounds  # noqa: E402
from ledger_tools import Denied  # noqa: E402
from session import AgentSession  # noqa: E402

CHILD_MODEL = "claude-sonnet-5"  # task 6: the subagent layer is sonnet or higher
CHILD_BOUNDS = "fs.read /origins\nledger.read pilot/tasks\nledger.write pilot/tasks/out"


class TopLevelTests(HarnessCase):
    scripts = {"pilot": [[("tool", "mcp__calc__add", {"a": 1, "b": 2}), ("text", "The sum is 3.")]]}

    def test_a_turn_is_recorded_and_audits_clean(self):
        async def scenario():
            s = AgentSession(self.top)
            await s.start()
            self.assertIsNone(s.send("please add 1 and 2"))
            for _ in range(500):
                if s.state != "busy":
                    break
                await asyncio.sleep(0.01)
            await s.stop()
            return s
        s = self.run_async(scenario())
        self.assertEqual(s.state, "stopped")
        texts = self.records("text")
        self.assertEqual([(t["author"], t["text"]) for t in texts],
                         [("human:session-user", "please add 1 and 2"), (self.top.author, "The sum is 3.")])
        pre, post = self.records("tool_call")[:2]
        self.assertEqual((pre["phase"], pre["policy_allow"], post["phase"]), ("pre", True, "post"))
        self.assertEqual(self.records("session_start")[0]["account"], {"subscriptionType": "fake", "apiProvider": "fake"})
        self.assertNotIn("never-logged@example.com", json.dumps(self.bus.history))
        self.assertEqual(self.records("tool_inventory_mismatch"), [])
        self.assertNotIn("gridRows", json.dumps(self.records("context_usage")))
        self.assertEqual(audit(self.view(), self.ledger.session), [])

    def test_planned_tools_are_exactly_the_built_tools(self):
        for text, role in ((TOP.render(), "owner"), ("fs.read /origins", "subagent"), ("ledger.read x", "owner"),
                           ("", "subagent")):
            bounds = Bounds.parse(text)
            core = AgentCore(self.env, agent_id="pilot.9", model="claude-sonnet-5", bounds=bounds, system_prompt="p",
                             role=role)
            self.assertEqual(core.can_spawn, role == "owner")  # task 6: spawning follows role, not depth
            self.assertEqual(set(core.allowed_tools), set(planned_tools(bounds, core.can_spawn)), text)
        self.assertEqual(self.top.backend.options().tools, [])  # no built-in tools at all

    def test_prompts_list_exactly_the_agents_tools(self):
        prompt = self.top.system_prompt
        self.assertNotRegex(prompt, r"\{[a-z_]+\}")
        self.assertIn(TOP.render(), prompt)
        mentioned = set(re.findall(r"\*\*`(mcp__[a-z_]+)`", prompt))
        self.assertEqual(mentioned, set(self.top.allowed_tools))


class SpawnTests(HarnessCase):
    def setUp(self):
        super().setUp()
        self.top.announce_start()  # AgentSession does this in a live run; the audit needs the parent's bounds

    def onboarding(self) -> str:
        return str(self.root / "origins" / "onboarding_1.02.md")

    def brief(self, *lines, name="pilot/tasks/t1") -> str:
        return self.top.guard.write(name, "\n".join(lines))["written"]

    def spawn(self, bounds=CHILD_BOUNDS, instructions="pilot/tasks/t1", **kw):
        return self.run_async(self.env.spawner.spawn(self.top, model=CHILD_MODEL, bounds_text=bounds,
                                                     instructions=instructions, **kw))

    def test_spawn_end_to_end(self):
        v1 = self.brief(f'tool: mcp__fs__read {json.dumps({"path": self.onboarding()})}',
                        'tool: mcp__calc__add {"a": 19.5, "b": 22.75}',
                        'tool: mcp__ledger__write {"name": "pilot/tasks/out/sum", "body": 42.25}',
                        "say: 42.25, written.")
        out = self.spawn()
        self.assertEqual((out["status"], out["onboarding_read"], out["agent"]), ("success", True, "pilot.1"))
        self.assertEqual(out["final_reply"], "42.25, written.")
        child = self.factory.clients["pilot.1"]
        self.assertEqual(child.queries, [self.ledger.line(v1)["body"]])  # the pinned brief, word for word
        prompt = self.records("message", agent="pilot.1")[0]
        self.assertEqual((prompt["message"]["text"], prompt["text_id"]), ("[[pilot/tasks/t1]]", v1))
        self.assertEqual([t for t in self.records("text", agent="pilot.1") if t["direction"] == "to_agent"], [])
        self.assertEqual(self.ledger.current("pilot/tasks/out/sum").author,
                         f"pilot.1@{self.ledger.session}:{CHILD_MODEL}@claude-anthropic-harness")  # session-qualified
        self.assertIn("ledger.read   pilot/tasks\n", out["bounds"])  # nothing covers log/, so no !log is added
        kinds = [r["kind"] for r in self.bus.history]
        self.assertLess(kinds.index("subagent_start"), kinds.index("subagent_session"))
        self.assertLess(kinds.index("onboarding_read"), kinds.index("subagent_end"))
        self.assertEqual(audit(self.view(), self.ledger.session), [])

    def test_gate_needs_the_whole_file(self):
        self.brief(f'tool: mcp__fs__read {json.dumps({"path": self.onboarding(), "limit": 10})}',
                   'tool: mcp__calc__add {"a": 1, "b": 1}',
                   f'tool: mcp__fs__read {json.dumps({"path": self.onboarding(), "offset": 11})}',
                   'tool: mcp__calc__add {"a": 2, "b": 2}')
        out = self.spawn()
        results = self.factory.clients["pilot.1"].results
        self.assertTrue(results[1]["is_error"])  # gate still shut after 10 of 30 lines
        self.assertIn("in full", results[1]["content"][0]["text"])
        self.assertFalse(results[3].get("is_error"))  # open after lines 11-30
        self.assertTrue(out["onboarding_read"])

    def test_gate_blocks_everything_but_onboarding(self):
        self.brief('tool: mcp__ledger__read {"name": "pilot/tasks/t1"}', "say: skipped onboarding")
        out = self.spawn()
        self.assertFalse(out["onboarding_read"])
        self.assertTrue(self.factory.clients["pilot.1"].results[0]["is_error"])

    def test_pinned_revision_is_delivered(self):
        v1 = self.brief("say: version one")
        self.top.guard.write("pilot/tasks/t1", "say: version two", prev=v1)
        out = self.spawn(instructions_id=v1)
        self.assertEqual(self.factory.clients["pilot.1"].queries, ["say: version one"])
        self.assertEqual(out["instructions_id"], v1)

    def test_refusals(self):
        self.brief("say: hi")
        self.ledger.write("pilot/tasks/by-founder", "say: hi", author="founder")
        self.top.guard.write("pilot/other", "say: hi")
        cases = [
            (dict(model="gpt-5"), "not available"),
            (dict(bounds_text="fs.read /origins\nfs.write /elsewhere\nledger.read pilot/tasks"), "not within"),
            (dict(bounds_text="fs.read /origins\nfs.write /work\nfs.exec /work\nledger.read pilot/tasks"),
             "not within|overlaps"),
            (dict(bounds_text="fs.read /docs\nledger.read pilot/tasks"), "onboarding"),
            (dict(instructions="pilot/tasks/by-founder"), "written by the parent"),
            (dict(instructions="pilot/other"), "outside the child's ledger.read"),
            (dict(instructions="pilot/tasks/missing"), "never been written"),
            (dict(budget_usd=0.01), "budget"),
            (dict(bounds_text=42), "notation"),
        ]
        for kw, why in cases:
            args = dict(model=CHILD_MODEL, bounds_text=CHILD_BOUNDS, instructions="pilot/tasks/t1")
            args.update(kw)
            with self.assertRaisesRegex(Denied, why, msg=str(kw)):
                self.env.spawner.check(self.top, **args)
        self.assertEqual(self.records("subagent_start"), [])

    def test_harness_records_stay_private_unless_granted(self):
        self.brief("say: hi")
        wide, _, _, _ = self.env.spawner.check(self.top, model=CHILD_MODEL, bounds_text="fs.read /origins\nledger.read *",
                                               instructions="pilot/tasks/t1")
        self.assertFalse(wide.permits("ledger.read", f"log/{self.ledger.session}/000001"))
        self.assertTrue(wide.permits("ledger.read", "pilot/tasks/t1"))
        granted, _, _, _ = self.env.spawner.check(self.top, model=CHILD_MODEL, instructions="pilot/tasks/t1",
                                                  bounds_text="fs.read /origins\nledger.read pilot/tasks log/")
        self.assertTrue(granted.permits("ledger.read", f"log/{self.ledger.session}/000001"))

    def test_subagents_cannot_spawn(self):
        self.brief("say: hi")
        bounds, onboarding, _, _ = self.env.spawner.check(self.top, model=CHILD_MODEL, bounds_text=CHILD_BOUNDS,
                                                          instructions="pilot/tasks/t1")
        child = AgentCore(self.env, agent_id="pilot.1", model="claude-sonnet-5", bounds=bounds, system_prompt="p",
                          role="subagent", depth=1, parent_id="pilot", onboarding_required=onboarding)
        self.assertNotIn("mcp__agents__spawn", child.allowed_tools)  # task 6: the subagent layer is the last
        with self.assertRaisesRegex(Denied, "only task owners"):
            self.env.spawner.check(child, model=CHILD_MODEL, bounds_text="fs.read /origins",
                                   instructions="pilot/tasks/t1")

    def test_gpt_subagents_need_codex(self):
        self.brief("say: hi")
        with self.assertRaisesRegex(Denied, "Codex, which is not configured"):
            self.env.spawner.check(self.top, model="gpt-5.6-terra", bounds_text=CHILD_BOUNDS,
                                   instructions="pilot/tasks/t1")
        with self.assertRaisesRegex(Denied, "not available"):
            self.env.spawner.check(self.top, model="claude-haiku-4-5-20251001", bounds_text=CHILD_BOUNDS,
                                   instructions="pilot/tasks/t1")  # below the subagent layer's floor


class InventoryTests(HarnessCase):
    init_tools = ["mcp__calc__add", "Bash"]
    scripts = {"pilot": [[("tool", "Bash", {"command": "echo hi"}), ("text", "tried")]]}

    def test_unexpected_tools_are_recorded_and_refused(self):
        async def scenario():
            s = AgentSession(self.top)
            await s.start()
            s.send("go")
            for _ in range(500):
                if s.state != "busy":
                    break
                await asyncio.sleep(0.01)
            await s.stop()
        self.run_async(scenario())
        mismatch = self.records("tool_inventory_mismatch")[0]
        self.assertEqual(mismatch["extra"], ["Bash"])
        self.assertEqual(self.records("unexpected_tool_use")[0]["tool_name"], "Bash")
        self.assertFalse(self.records("tool_call")[0]["policy_allow"])


class FailClosedTests(HarnessCase):
    def test_no_tool_and_no_message_after_a_ledger_failure(self):
        self.ledger.close()
        self.bus.log.write("status", state="x", agent="pilot")  # the failed write latches
        self.assertIn("fail closed", self.top.check("mcp__calc__add", {}).reason)
        s = AgentSession(self.top)
        s.state = "idle"
        self.assertIn("fail closed", s.send("hello"))
        self.assertEqual(s.state, "ledger_failed")
        from tests.test_files import scribe_reopen
        self.ledger = scribe_reopen(self)


class AuditTests(HarnessCase):
    def setUp(self):
        super().setUp()
        self.top.announce_start()

    def test_audit_catches_broken_promises(self):
        v1 = self.top.guard.write("pilot/draft", "x")["written"]
        self.top.file_tools.write(v1, "/work/d.txt")
        self.assertEqual(audit(self.view(), self.ledger.session), [])
        log = self.bus.log
        log.write("file_write", agent="pilot", entry_id=v1, sha256="0" * 64, started="none")
        log.write("exec_started", agent="pilot", script="/scripts/x.py")
        log.write("subagent_start", agent="pilot", child="pilot.7", bounds="fs.write /elsewhere")
        log.write("tool_call", agent="pilot", phase="pre", tool_use_id="t9", policy_allow=True)
        self.ledger.write("elsewhere/x", "y", author=self.top.author)
        problems = "\n".join(audit(self.view(), self.ledger.session))
        for expected in ("sha256 does not match", "exec_started has no matching end", "no subagent_end",
                         "not within pilot's", "t9 was allowed but has no post", "outside its ledger.write"):
            self.assertIn(expected, problems)

    def test_budget_pool(self):
        pool = BudgetPool(1.0)
        pool.record("pilot", 0.2)
        pool.record("pilot.1", 0.3)
        pool.record("pilot", 0.1)  # running totals never go down
        self.assertAlmostEqual(pool.spent, 0.5)
        self.assertAlmostEqual(pool.remaining, 0.5)


if __name__ == "__main__":
    unittest.main()
