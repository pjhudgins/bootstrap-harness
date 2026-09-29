"""Deterministic task-6 role, request, bounds, cleanup and provenance checks."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounds import Bounds, Denied, Mounts, WORKSPACE, SCRIPTS, parent_bounds
from context import AgentContext
from conversation import Conversation
from hierarchy import Hierarchy
from ledger import AGENT_AUTHOR, LedgerLog, scribe
from models import select_model
from tools import ToolService
from codex_runtime import Journal


class HybridTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        (self.root / WORKSPACE).mkdir(parents=True)
        (self.root / SCRIPTS).mkdir(parents=True)
        (self.root / "origins").mkdir()
        (self.root / "origins/onboarding_1.12.md").write_text("one\ntwo\n")
        self.log = LedgerLog(self.root / "ledgers")
        self.state = Conversation(self.log)
        self.state.loop, self.state.queue = asyncio.get_running_loop(), asyncio.Queue()
        self.manager = Hierarchy(self.log, self.root, self.state, runner=self.waiting_runner)
        self.state.manager = self.manager
        self.governor = self.service(AgentContext("governor", AGENT_AUTHOR, select_model("governor", "opus"),
                                                parent_bounds(), "governor", True))
        await self.onboard(self.governor)

    async def asyncTearDown(self):
        await self.manager.close()
        self.log.close()
        self.assertEqual(scribe.load(self.log.root, self.log.name).findings, [])
        self.tmp.cleanup()

    async def waiting_runner(self, *args):
        await asyncio.Event().wait()

    def service(self, context):
        return ToolService(self.log, context.bounds, context.author, context=context, root=self.root,
                           manager=self.manager, state=self.state)

    async def onboard(self, service):
        await service.invoke("fs_read", {"path": "origins/onboarding_1.12.md"})

    def note(self, service, name, text="Bar: prototype. Return evidence."):
        return service.access.write(name, text, ["pilot.note"])

    async def owner(self, model="gpt-6-sol"):
        note = self.note(self.governor, "pilot/owner-brief")
        job = self.manager.start(self.governor, model, note["id"], parent_bounds().data(), "pilot/owner-result")
        owner = self.service(self.manager.jobs[job["agent_id"]].context)
        await self.onboard(owner)
        return owner

    def test_model_roles_and_no_silent_fallback(self):
        self.assertEqual(select_model("governor", "opus"), "claude-opus-5-5")
        for role, model in (("governor", "sonnet"), ("governor", "gpt-6-astra"),
                            ("owner", "sonnet"), ("owner", "gpt-5.6-terra"),
                            ("worker", "haiku"), ("worker", "gpt-6-luna"), ("worker", "unknown")):
            with self.assertRaises(Denied):
                select_model(role, model)
        for model in ("opus", "fable", "gpt-6-astra", "gpt-6-sol"):
            select_model("owner", model)
        for model in ("sonnet", "gpt-5.6-terra", "gpt-6-astra"):
            select_model("worker", model)

    async def test_governor_cannot_execute_owner_work(self):
        for tool in ("add", "fs_write", "python_execute", "governor_request"):
            self.assertNotIn(tool, self.governor.operations)
            with self.assertRaises(Denied):
                await self.governor.invoke(tool, {})
        self.assertIn("agent_start", self.governor.operations)
        self.assertIn("governor_resolve", self.governor.operations)

    async def test_three_layers_and_monotonic_bounds(self):
        owner = await self.owner()
        note = self.note(owner, "pilot/worker-brief")
        data = parent_bounds().data()
        data.update({"fs.write": [], "fs.execute": [], "ledger.write": [], "ledger.tags": []})
        worker = self.manager.start(owner, "sonnet", note["id"], data, "pilot/worker-result")
        context = self.manager.jobs[worker["agent_id"]].context
        self.assertEqual((context.role, context.parent_id, context.provider), ("worker", owner.context.agent_id, "claude"))
        tools = self.service(context)
        self.assertFalse({"agent_start","agent_status","governor_request","governor_resolve","fs_write","python_execute"} & tools.operations)
        with self.assertRaises(Denied):
            self.manager.start(tools, "opus", note["id"], data, "pilot/fourth-layer")
        narrowed = parent_bounds().data()
        narrowed["fs.read"] = ["origins/**"]
        limited = self.service(AgentContext("owner-limited","limited","gpt-6-sol",Bounds.parse(narrowed),"owner",True,"governor"))
        brief = self.note(limited, "pilot/limited-brief")
        with self.assertRaises(Denied):
            self.manager.start(limited, "sonnet", brief["id"], parent_bounds().data(), "pilot/escalated")

    async def test_request_blocks_owner_until_governor_answer(self):
        owner = await self.owner()
        note = self.note(owner, "pilot/request", "Need governor guidance: explain the authorized next step.")
        task = asyncio.create_task(owner.invoke("governor_request", {"request_id": note["id"], "response_name": "pilot/response"}))
        await asyncio.sleep(0)
        self.assertEqual(self.manager.jobs[owner.context.agent_id].status, "blocked")
        self.assertFalse(task.done())
        with self.assertRaises(Denied):
            await owner.invoke("add", {"a":1,"b":2})
        reply = self.note(self.governor, "pilot/decision", "Proceed within existing bounds.")
        answer = await self.governor.invoke("governor_resolve", {"request_id":"request-1","response_id":reply["id"],"decision":"approve"})
        self.assertEqual(await task, answer)
        self.assertFalse(answer["bounds_changed"])
        self.assertEqual(self.manager.jobs[owner.context.agent_id].status, "running")
        self.assertEqual((await owner.invoke("add", {"a":1,"b":2}))["sum"], 3)
        self.assertEqual(self.log.scribe.current("pilot/response").author, AGENT_AUTHOR)
        notification = self.state.queue.get_nowait()
        self.assertIn("governor_request", notification[1])

    async def test_human_gate_cannot_be_forged_or_overridden(self):
        owner = await self.owner()
        note = self.note(owner, "pilot/request", "Human decision needed for this proposed action.")
        task = asyncio.create_task(owner.invoke("governor_request", {"request_id":note["id"],"response_name":"pilot/response"}))
        await asyncio.sleep(0)
        reply = self.note(self.governor, "pilot/proposal", "Please decide whether to authorize the proposed action.")
        args = {"request_id":"request-1","response_id":reply["id"],"decision":"needs_human"}
        await self.governor.invoke("governor_resolve", args)
        with self.assertRaises(Denied):
            await self.governor.invoke("governor_resolve", {**args,"decision":"approve"})
        self.assertFalse(task.done())
        await self.manager.human_decision("request-1","deny","Do not perform it.")
        self.assertTrue(self.manager.is_blocked(owner.context.agent_id))
        with self.assertRaises(Denied):
            await self.governor.invoke("governor_resolve", {**args,"decision":"approve"})
        denial = self.note(self.governor, "pilot/denial", "Human denied the proposed action; do not perform it.")
        await self.governor.invoke("governor_resolve", {**args,"decision":"deny","response_id":denial["id"]})
        self.assertEqual((await task)["decision"],"deny")
        self.assertEqual(len(self.log.scribe.labelled("log.human_decision")),1)

    async def test_request_pauses_active_deadline(self):
        owner = await self.owner()
        note = self.note(owner, "pilot/request", "Need guidance.")
        async with asyncio.timeout(0.15) as deadline:
            owner.deadline = deadline
            task = asyncio.create_task(owner.invoke("governor_request", {"request_id":note["id"],"response_name":"pilot/response"}))
            await asyncio.sleep(0.2)
            self.assertIsNone(deadline.when())
            response = self.note(self.governor, "pilot/decision", "Proceed.")
            self.manager.resolve(self.governor, "request-1", response["id"], "approve")
            await task
        self.assertFalse(self.manager.is_blocked(owner.context.agent_id))

    async def test_request_ownership_and_readability(self):
        owner = await self.owner()
        other = self.note(self.governor,"pilot/not-owner", "Untrusted request claim.")
        with self.assertRaises(Denied):
            await owner.invoke("governor_request", {"request_id":other["id"],"response_name":"pilot/response"})
        with self.assertRaises(Denied):
            await owner.invoke("governor_resolve", {})
        self.assertEqual(self.manager.requests, {})

    async def test_owner_completion_waits_for_worker_cleanup(self):
        worker_started, worker_release = asyncio.Event(), asyncio.Event()
        async def runner(log, task_dir, state, context, entry, manager):
            service = self.service(context)
            if context.role == "owner":
                note = self.note(service,"pilot/worker")
                manager.start(service,"sonnet",note["id"],parent_bounds().data(),"pilot/worker-result")
                return "Owner submitted report."
            worker_started.set()
            await worker_release.wait()
            return "Worker result."
        self.manager.runner = runner
        owner = await self.owner()
        await worker_started.wait()
        self.assertEqual(self.manager.jobs[owner.context.agent_id].status,"waiting_workers")
        worker_release.set()
        await self.manager.tasks[owner.context.agent_id]
        self.assertEqual(self.manager.jobs[owner.context.agent_id].status,"completed")
        events = [self.log.scribe.current(n).body for n in self.log.scribe.labelled("log.agent_completed")]
        self.assertEqual([e["role"] for e in events], ["worker","owner"])

    async def test_shutdown_cancels_pending_request_and_before_start(self):
        owner = await self.owner()
        note = self.note(owner,"pilot/request","Need guidance.")
        task = asyncio.create_task(owner.invoke("governor_request", {"request_id":note["id"],"response_name":"pilot/response"}))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.manager.requests["request-1"]["status"],"cancelled")
        await self.manager.close()
        self.assertEqual(self.manager.jobs[owner.context.agent_id].status,"cancelled")

    async def test_pinned_export_script_output_and_write_execute_separation(self):
        owner = await self.owner()
        note = self.note(owner,"pilot/code","print('draft')")
        self.note_revision = owner.access.write("pilot/code","new text",["pilot.note"],note["id"])
        await owner.invoke("fs_write", {"entry_id":note["id"],"path":"workspace:/draft.py"})
        self.assertEqual((self.root/WORKSPACE/"draft.py").read_text(),"print('draft')")
        with self.assertRaises(Denied):
            await owner.invoke("python_execute", {"path":"workspace:/draft.py","output_name":"pilot/out"})
        (self.root/SCRIPTS/"safe_test.py").write_text("print(42)")
        result = await owner.invoke("python_execute", {"path":"scripts:/safe_test.py","output_name":"pilot/output"})
        self.assertEqual(owner.access.resolve(result["output"]["id"])["body"].strip(),"42")
        with self.assertRaises(ValueError):
            await owner.invoke("fs_write", {"entry_id":note["id"],"path":"workspace:/draft.py"})

    async def test_codex_cancellation_joins_tool_cleanup(self):
        from codex_runtime import CodexSession
        owner = await self.owner()
        session = CodexSession(self.log, self.root, self.state, owner.context, self.manager, root=self.root)
        session.loop = asyncio.get_running_loop()
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def blocking_call(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.05)
                cleaned.set()
        session.service.call = blocking_call
        handler = session.registry()["add"][1]
        task = asyncio.create_task(session._blocking(handler, {"a":1,"b":2}))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await session.__aexit__()
        self.assertTrue(cleaned.is_set())
        self.assertFalse(session.tool_tasks)

    async def test_owner_failure_cancels_unstarted_worker(self):
        async def runner(log, task_dir, state, context, entry, manager):
            service = self.service(context)
            note = self.note(service, "pilot/worker")
            manager.start(service, "sonnet", note["id"], parent_bounds().data(), "pilot/worker-result")
            raise RuntimeError("Owner failed before worker started")
        self.manager.runner = runner
        owner = await self.owner()
        await self.manager.tasks[owner.context.agent_id]
        self.assertEqual(self.manager.jobs["owner-1.worker-1"].status, "cancelled")
        self.assertEqual(self.manager.jobs["owner-1"].status, "failed")

    async def test_codex_prose_authorship_and_ui_separation(self):
        owner = await self.owner()
        from types import SimpleNamespace
        session = SimpleNamespace(log=self.log, state=self.state, context=owner.context, turn="test",
                                  prompt_ref={"text":"[[pilot/brief]]","instructions_id":"pinned"})
        journal = Journal(session)
        payload = {"method":"item/completed","params":{"item":{"type":"agentMessage","text":"Owner result"}}}
        journal.write("receive",payload)
        self.assertEqual(self.state.snapshot()["messages"],[])
        self.assertEqual(self.state.snapshot()["agent_messages"][owner.context.agent_id][0]["text"],"Owner result")
        text = self.log.scribe.current(self.log.scribe.labelled("message.assistant")[0])
        self.assertEqual((text.body,text.author),("Owner result",owner.context.author))
        journal.write("send",{"method":"turn/start","params":{"input":[{"type":"text","text":"original instruction"}]}})
        record = self.log.scribe.current(self.log.scribe.labelled("log.codex_send")[0]).body
        self.assertEqual(record["payload"]["params"]["input"][0]["text"],"[[pilot/brief]]")
        journal.write("receive", {"method":"item/completed", "params":{"item":{"type":"userMessage",
                      "content":[{"type":"text","text":"original instruction"}]}}})
        record = self.log.scribe.current(self.log.scribe.labelled("log.codex_receive")[-1]).body
        self.assertEqual(record["payload"]["params"]["item"]["content"][0]["text"], "[[pilot/brief]]")


if __name__ == "__main__":
    unittest.main()
