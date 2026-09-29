"""Scripted SDK-boundary tests: real SDK types/options, no provider or model calls."""
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from bounds import Bounds, Mounts, SCRIPTS, WORKSPACE, parent_bounds
from context import AgentContext
from conversation import Conversation, serve_conversation
from ledger import AGENT_AUTHOR, LedgerLog, LedgerFailed, scribe
from runtime import AgentSession
from tools import ToolService
from subagents import Subagents


class ScriptedClient:
    def __init__(self, options, script):
        self.options, self.script = options, script
        self.closed = False
        self.turns = 0

    async def __aenter__(self):
        self.owner = asyncio.current_task()
        return self

    async def __aexit__(self, *args):
        assert asyncio.current_task() is self.owner, "SDK context changed tasks"
        self.closed = True

    async def get_server_info(self):
        return {"account": {"apiProvider": "scripted"}}

    async def get_mcp_status(self):
        return {"mcpServers": [{"name": "nimoi", "status": "connected", "scope": "local"}]}

    async def query(self, text):
        self.turns += 1

    def receive_response(self):
        return self.script(self)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # SDK import is intentional: these tests exercise installed SDK objects,
        # but the injected client never opens a connection.
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage, SystemMessage
        self.Assistant, self.Text, self.Result, self.System = AssistantMessage, TextBlock, ResultMessage, SystemMessage
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name).resolve()
        (self.root / WORKSPACE).mkdir(parents=True)
        (self.root / SCRIPTS).mkdir(parents=True)
        (self.root / "origins").mkdir()
        (self.root / "origins/onboarding_1.12.md").write_text("one\ntwo\n", encoding="utf-8")
        self.log = LedgerLog(self.root / "ledgers")
        self.state = Conversation(self.log, "sonnet")
        self.parent = AgentContext("parent", AGENT_AUTHOR, "sonnet", parent_bounds(), "parent", True)

    async def asyncTearDown(self):
        self.log.close()
        if not self.log.failed:
            self.assertEqual(scribe.load(self.log.root, self.log.name).findings, [])
        self.tmp.cleanup()

    def agent(self, context, script, manager=None):
        return AgentSession(self.log, self.root, self.state, context, manager,
                            root=self.root, client_factory=lambda options: ScriptedClient(options, script))

    def final(self, context, text, cost=0.1, error=False):
        return self.Result(subtype="error_during_execution" if error else "success", duration_ms=1,
            duration_api_ms=1, is_error=error, num_turns=1, session_id=context.agent_id,
            total_cost_usd=cost, usage={"input_tokens": 1, "output_tokens": 2}, result=text)

    async def onboard(self, agent):
        args = {"path": "origins/onboarding_1.12.md"}
        name = "mcp__nimoi__fs_read"
        decision = await agent.pre_tool({"tool_name": name, "tool_input": args}, "read", None)
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "allow")
        await agent.permission(name, args, None)
        result = await agent.service.call("fs_read", args, agent.turn)
        self.assertFalse(result.get("isError", False))
        await agent.post_tool({"hook_event_name": "PostToolUse", "tool_name": name, "tool_response": result}, "read", None)

    async def test_interleaved_agents_authorship_result_reuse_and_cumulative_usage(self):
        ready = [asyncio.Event(), asyncio.Event()]
        agents = []
        contexts = [self.parent, AgentContext("child-1", "agent.claude.child-1", "haiku", parent_bounds())]
        def scripted(index):
            async def stream(client):
                agent, context = agents[index], contexts[index]
                yield self.System(subtype="init", data={"tools": sorted(agent.service.full_names), "model": context.model})
                await self.onboard(agent)
                text = context.agent_id + " reply " + str(client.turns)
                yield self.Assistant(content=[self.Text(text=text)], model=context.model)
                ready[index].set()
                await ready[1-index].wait()
                yield self.final(context, text, 0.05 if index else (0.1 if client.turns == 1 else 0.15))
            return stream
        agents.extend(self.agent(context, scripted(i)) for i, context in enumerate(contexts))

        async def run(agent):
            async with agent:
                await agent.query("directed test", text_ref={})
                if agent.context.is_parent:
                    await agent.query("second turn", text_ref={})
            self.assertTrue(agent.client.closed)
        await asyncio.gather(*(run(a) for a in agents))
        self.assertAlmostEqual(self.state.snapshot()["family_cost_usd"], 0.20)
        self.assertEqual({m["actor"] for m in self.state.snapshot()["messages"]}, {"parent", "child-1"})
        refs = [self.log.scribe.current(n) for n in self.log.scribe.labelled("message.assistant")]
        self.assertEqual(len(refs), 3)  # Result repeats reuse text IDs.
        self.assertEqual({e.author for e in refs}, {c.author for c in contexts})
        self.assertEqual(agents[0].client.options.tools, [])
        events = [self.log.scribe.current(n).body for n in self.log.scribe.labelled("log.message")]
        for event in events:
            for ref in event["text_entries"]:
                entry = self.log.scribe.line(ref["id"])
                self.assertEqual(entry["author"], ref["author"])

    async def test_sdk_error_text_is_runtime_authored_and_client_closes(self):
        async def stream(client):
            yield self.Assistant(content=[self.Text(text="Synthetic authentication failure")], model="haiku", error="authentication_failed")
            yield self.final(self.parent, "Synthetic authentication failure", error=True)
        agent = self.agent(self.parent, stream)
        with self.assertRaises(RuntimeError):
            async with agent:
                await agent.query("test", text_ref={})
        self.assertTrue(agent.client.closed)
        entries = [self.log.scribe.current(n) for n in self.log.scribe.labelled("message.runtime")]
        self.assertEqual([e.author for e in entries], ["harness"])
        self.assertEqual(self.state.snapshot()["messages"], [])

    async def test_native_inventory_rejected_before_reply(self):
        async def stream(client):
            yield self.System(subtype="init", data={"tools": ["Bash", "Task"]})
        agent = self.agent(self.parent, stream)
        with self.assertRaisesRegex(RuntimeError, "Unexpected initialized tool"):
            async with agent:
                await agent.query("test", text_ref={})
        self.assertTrue(agent.client.closed)

    async def test_cancellation_waits_for_script_and_sdk_cleanup(self):
        (self.root / SCRIPTS / "safe_test.py").write_text("import time\nprint('started', flush=True)\ntime.sleep(30)\n")
        running = asyncio.Event()
        async def stream(client):
            await self.onboard(agent)
            running.set()
            await agent.service.invoke("python_execute", {"path": "scripts:/safe_test.py", "output_name": "pilot/cancel-output"})
            yield self.final(self.parent, "unreachable")
        agent = self.agent(self.parent, stream)
        async def run():
            async with agent:
                await agent.query("test", text_ref={})
        task = asyncio.create_task(run())
        await running.wait()
        # Wait for the execution event, not an arbitrary sleep.
        for _ in range(200):
            if self.log.scribe.labelled("log.execution_start"):
                break
            await asyncio.sleep(0.01)
        before = time.monotonic()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(time.monotonic() - before, 3)
        self.assertTrue(agent.client.closed)
        events = [self.log.scribe.current(n).body for n in self.log.scribe.labelled("log.execution_complete")]
        self.assertEqual(events[-1]["status"], "cancelled")
        self.assertIn("output.finished", self.log.scribe.labels("pilot/cancel-output"))

    async def test_unreadable_output_refused_before_process_or_reservation(self):
        data = parent_bounds().data()
        data["ledger.read"] = ["pilot/readable/**"]
        context = AgentContext("child", "agent.claude.child", "haiku", Bounds.parse(data))
        service = ToolService(self.log, context.bounds, context.author, root=self.root, context=context)
        await service.invoke("fs_read", {"path": "origins/onboarding_1.12.md"})
        (self.root / SCRIPTS / "safe_test.py").write_text("print('test')")
        with patch("capabilities.subprocess.Popen") as launch:
            response = await service.call("python_execute", {"path": "scripts:/safe_test.py", "output_name": "pilot/unreadable"}, "t")
            self.assertTrue(response["isError"])
            launch.assert_not_called()
        self.assertIsNone(self.log.scribe.current("pilot/unreadable"))
        self.assertTrue(self.log.scribe.labelled("log.tool_denied"))
        result = await service.invoke("python_execute", {"path": "scripts:/safe_test.py", "output_name": "pilot/readable/output"})
        self.assertEqual(service.access.resolve(result["output"]["id"])["body"].strip(), "test")
        with self.assertRaises(ValueError):
            service.access.write("pilot/readable/output", "overwrite", ["pilot.note"], result["output"]["id"])

    async def test_mounts_subsets_and_capability_tool_availability(self):
        mounts = Mounts(WORKSPACE + "/launch-a", SCRIPTS)
        parent = parent_bounds(mounts)
        self.assertTrue(parent.permits("fs.write", "workspace:/draft.py"))
        self.assertFalse(parent.permits("fs.write", WORKSPACE + "/old.py"))
        data = parent.data()
        data.update({"fs.write": [], "fs.execute": [], "ledger.write": [], "ledger.tags": []})
        service = ToolService(self.log, Bounds.parse(data, mounts), "child", root=self.root)
        self.assertFalse({"fs_write", "python_execute", "ledger_write", "subagent_start"} & service.operations)
        narrowed = parent.data()
        narrowed["fs.read"] = ["origins/**"]
        narrowed["ledger.read"] = ["pilot/brief"]
        narrow = Bounds.parse(narrowed, mounts)
        problems = parent.problems_as_child_of(narrow)
        self.assertTrue(any("fs.read" in p for p in problems))
        self.assertTrue(any("ledger.read" in p for p in problems))
        for path in ("workspace:/../scripts/test.py", "workspace:/a/../../x", "scripts:/C:/x"):
            with self.assertRaises(ValueError):
                parent.permits("fs.read", path)

    async def test_child_result_access_checked_and_status_follows_cleanup(self):
        manager = Subagents(self.log, self.root, self.state)
        service = ToolService(self.log, self.parent.bounds, self.parent.author, root=self.root, manager=manager, context=self.parent)
        brief = service.access.write("pilot/brief", "Bar: test.\nSay done.", ["pilot.note"])
        data = parent_bounds().data()
        data["ledger.read"] = ["pilot/brief"]
        with self.assertRaises(ValueError):
            manager.start(service, "haiku", brief["id"], data, "pilot/result")
        self.assertEqual(manager.jobs, {})
        data["ledger.read"].append("pilot/result")
        cleaned = asyncio.Event()
        async def runner(log, task_dir, state, context, entry):
            async def stream(client):
                await self.onboard(agent)
                yield self.Assistant(content=[self.Text(text="done")], model="haiku")
                yield self.final(context, "done", 0.01)
            agent = self.agent(context, stream)
            async with agent:
                result = await agent.query(entry["body"], text_ref={})
            self.assertTrue(agent.client.closed)
            cleaned.set()
            return result
        manager.runner = runner
        job = manager.start(service, "haiku", brief["id"], data, "pilot/result")
        done = await manager.status(self.parent.author, job["agent_id"])
        self.assertTrue(cleaned.is_set())
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.log.scribe.current("pilot/result").author, done["author"])
        await manager.close()

    async def test_ledger_failure_stops_family_and_rejects_late_callback(self):
        entered = asyncio.Event()
        async def stream(client):
            entered.set()
            while True:
                await asyncio.sleep(1)
                yield self.final(self.parent, "waiting")
        agent = self.agent(self.parent, stream)
        # Exercise the actual conversation stop watcher with an injected SDK boundary.
        with patch("conversation.AgentSession", return_value=agent), patch("conversation.NIMOI", self.root):
            worker = asyncio.create_task(serve_conversation(self.state, self.root / Path(WORKSPACE).parent))
            for _ in range(200):
                if self.state.snapshot()["status"] == "ready":
                    break
                await asyncio.sleep(0.01)
            self.state.submit("test")
            await entered.wait()
            with patch.object(scribe, "_write_all", side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(LedgerFailed):
                    self.log.write("synthetic_failure")
            with self.assertRaises(LedgerFailed):
                await asyncio.wait_for(worker, timeout=3)
        self.assertTrue(self.log.stop_event.is_set())
        self.assertTrue(agent.client.closed)
        with self.assertRaises(LedgerFailed):
            await agent.post_tool({"hook_event_name": "PostToolUse", "tool_name": "mcp__nimoi__add"}, "late", None)

    async def test_idle_stop_does_not_cancel_sdk_teardown(self):
        async def stream(client):
            yield self.final(self.parent, "unused")
        class SlowClose(ScriptedClient):
            async def __aexit__(client, *args):
                await asyncio.sleep(0.12)  # Longer than the launch stop watch interval.
                return await super().__aexit__(*args)
        agent = self.agent(self.parent, stream)
        agent.client_factory = lambda options: SlowClose(options, stream)
        with patch("conversation.AgentSession", return_value=agent), patch("conversation.NIMOI", self.root):
            task = asyncio.create_task(serve_conversation(self.state, self.root / Path(WORKSPACE).parent))
            for _ in range(200):
                if self.state.snapshot()["status"] == "ready":
                    break
                await asyncio.sleep(0.01)
            self.state.stop()
            await asyncio.wait_for(task, timeout=3)
        self.assertTrue(agent.client.closed)


if __name__ == "__main__":
    unittest.main()
