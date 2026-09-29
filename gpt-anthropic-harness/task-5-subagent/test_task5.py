"""Real-scribe capability and parallel-child tests; no model credentials required."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from uuid import uuid4

from bounds import Bounds, WORKSPACE, SCRIPTS, parent_bounds, covers
from ledger import LedgerLog, scribe, AGENT_AUTHOR
from tools import ToolService
from subagents import Subagents


class Task5Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name).resolve()
        (self.root / WORKSPACE).mkdir(parents=True)
        (self.root / SCRIPTS).mkdir(parents=True)
        (self.root / "origins").mkdir()
        (self.root / "origins/onboarding_1.12.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
        self.log = LedgerLog(self.root / "ledgers")
        self.state = SimpleNamespace(stopping=False, activity=Mock(), child_update=Mock())
        self.manager = Subagents(self.log, self.root, self.state, runner=self.fake_child)
        self.service = ToolService(self.log, parent_bounds(), AGENT_AUTHOR, root=self.root, manager=self.manager)
        await self.service.invoke("fs_read", {"path": "origins/onboarding_1.12.md"})

    async def fake_child(self, log, task_dir, state, context, entry):
        await asyncio.sleep(0.01)
        return "Completed " + entry["body"]

    async def asyncTearDown(self):
        await self.manager.close()
        self.log.close()
        view = scribe.load(self.log.root, self.log.name)
        self.assertEqual(view.findings, [])
        self.tmp.cleanup()

    def child_bounds(self):
        return {"fs.read": ["origins/**"], "fs.write": [], "fs.execute": [],
                "ledger.read": ["pilot/**"], "ledger.write": ["pilot/child/**"], "ledger.tags": ["pilot.note"]}

    async def note(self, name="pilot/instructions", body="Bar: test only. Return a finding."):
        return await self.service.invoke("ledger_write", {"name": name, "body": body, "tags": ["pilot.note"]})

    def test_grant_language_and_subset_are_structural(self):
        self.assertTrue(covers("origins/**", "origins/x.md"))
        self.assertFalse(covers("origins/**", "origins-extra/x.md"))
        self.assertFalse(covers("origins/x.md", "origins/**"))
        self.assertTrue(Bounds.parse(self.child_bounds()).subset_of(parent_bounds()))
        for pattern in ("../origins/**", "/origins/**", "origins//x", "origins/*", "C:/data", "origins\\x"):
            data = self.child_bounds()
            data["fs.read"] = [pattern]
            with self.assertRaises(ValueError):
                Bounds.parse(data)
        data = self.child_bounds()
        data["fs.write"] = [SCRIPTS + "/**"]
        with self.assertRaises(ValueError):
            Bounds.parse(data)
        data = self.child_bounds()
        data["fs.execute"] = [WORKSPACE + "/draft.py"]
        with self.assertRaises(ValueError):
            Bounds.parse(data)

    async def test_onboarding_gate_requires_full_ordered_read(self):
        service = ToolService(self.log, parent_bounds(), "agent.claude.child", root=self.root)
        with self.assertRaises(ValueError):
            await service.invoke("add", {"a": 1, "b": 2})
        await service.invoke("fs_read", {"path": "origins/onboarding_1.12.md", "start_line": 3})
        self.assertFalse(service.onboarded)
        await service.invoke("fs_read", {"path": "origins/onboarding_1.12.md", "max_lines": 2})
        self.assertFalse(service.onboarded)
        await service.invoke("fs_read", {"path": "origins/onboarding_1.12.md", "start_line": 3})
        self.assertEqual(await service.invoke("add", {"a": 1, "b": 2}), {"sum": 3})

    async def test_export_uses_pinned_body_and_cannot_promote_execute_or_overwrite(self):
        first = await self.note(body="print('draft')\n")
        await self.service.invoke("ledger_write", {"name": "pilot/instructions", "body": "revised", "tags": ["pilot.note"], "prev": first["id"]})
        path = WORKSPACE + "/draft.py"
        result = await self.service.invoke("fs_write", {"entry_id": first["id"], "path": path})
        self.assertEqual(result["source_id"], first["id"])
        self.assertEqual((self.root / path).read_text(), "print('draft')\n")
        for operation, args in (("fs_write", {"entry_id": first["id"], "path": SCRIPTS + "/promoted.py"}),
                ("python_execute", {"path": path}), ("fs_write", {"entry_id": first["id"], "path": path}),
                ("fs_write", {"entry_id": first["id"], "path": WORKSPACE + "/.env"})):
            with self.assertRaises(ValueError):
                await self.service.invoke(operation, args)
        self.assertFalse((self.root / SCRIPTS / "promoted.py").exists())

    async def test_execute_output_is_text_body_with_status(self):
        script = self.root / SCRIPTS / "safe_test.py"
        script.write_text("import sys\nprint('sum=42')\nprint('diagnostic', file=sys.stderr)\n", encoding="utf-8")
        result = await self.service.invoke("python_execute", {"path": SCRIPTS + "/safe_test.py", "output_name": "pilot/output-" + uuid4().hex})
        entry = self.log.scribe.current(result["output"]["name"])
        self.assertIsInstance(entry.body, str)
        self.assertIn("sum=42", entry.body)
        self.assertIn("diagnostic", entry.body)
        self.assertEqual(entry.author, "harness")
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["truncated"])

    async def test_execute_bounds_output_volume_and_no_draft_import(self):
        (self.root / WORKSPACE / "injected.py").write_text("raise RuntimeError('draft loaded')")
        script = self.root / SCRIPTS / "safe_test.py"
        script.write_text("try:\n import injected\nexcept ModuleNotFoundError:\n print('isolated')\n", encoding="utf-8")
        isolated = await self.service.invoke("python_execute", {"path": SCRIPTS + "/safe_test.py", "output_name": "pilot/output-" + uuid4().hex})
        self.assertEqual(self.log.scribe.current(isolated["output"]["name"]).body.strip(), "isolated")
        script.write_text("import sys\nprint('x' * 100000)\n", encoding="utf-8")
        result = await self.service.invoke("python_execute", {"path": SCRIPTS + "/safe_test.py", "output_name": "pilot/output-" + uuid4().hex})
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(self.log.scribe.current(result["output"]["name"]).body.encode()), 65536)

    async def test_child_ledger_filter_and_author_restriction(self):
        parent = await self.note()
        await self.note(name="pilot/hidden", body="private")
        data = self.child_bounds()
        data["ledger.read"] = ["pilot/child/**", "pilot/instructions"]
        child = ToolService(self.log, Bounds.parse(data), "agent.claude.child", root=self.root)
        await child.invoke("fs_read", {"path": "origins/onboarding_1.12.md"})
        listing = await child.invoke("ledger_read", {})
        self.assertEqual([e["name"] for e in listing["entries"]], ["pilot/instructions"])
        for args in ({"name": "pilot/hidden"}, {"name": "harness/00000001"}):
            with self.assertRaises(ValueError):
                await child.invoke("ledger_read", args)
        note = await child.invoke("ledger_write", {"name": "pilot/child/report", "body": "mine", "tags": ["pilot.note"]})
        self.assertEqual(note["author"], "agent.claude.child")
        with self.assertRaises(ValueError):
            await self.service.invoke("ledger_write", {"name": note["name"], "body": "take over", "tags": ["pilot.note"], "prev": note["id"]})
        with self.assertRaises(ValueError):
            await child.invoke("fs_read", {"path": WORKSPACE + "/draft.py"})

    async def test_subagents_parallel_pinned_instructions_and_identity(self):
        instructions = await self.note(body="Bar: test.\noriginal instructions")
        await self.service.invoke("ledger_write", {"name": instructions["name"], "body": "new instructions", "tags": ["pilot.note"], "prev": instructions["id"]})
        a = self.manager.start(self.service, "haiku", instructions["id"], self.child_bounds(), "pilot/result-" + uuid4().hex)
        b = self.manager.start(self.service, "sonnet", instructions["id"], self.child_bounds(), "pilot/result-" + uuid4().hex)
        self.assertNotEqual(a["author"], b["author"])
        self.assertEqual(a["status"], "running")
        with self.assertRaises(ValueError):
            self.manager.start(self.service, "opus", instructions["id"], self.child_bounds(), "pilot/result-" + uuid4().hex)
        with self.assertRaises(ValueError):
            await self.manager.status("some-other-parent", a["agent_id"])
        done = await self.manager.status(AGENT_AUTHOR, a["agent_id"])
        self.assertEqual(done["status"], "completed")
        result = self.log.scribe.current(done["result"]["name"])
        self.assertEqual(result.body, "Completed Bar: test.\noriginal instructions")
        self.assertEqual(result.author, a["author"])
        child = ToolService(self.log, Bounds.parse(self.child_bounds()), a["author"], root=self.root)
        self.assertNotIn("subagent_start", child.operations)

    async def test_raw_ledger_file_cannot_bypass_ledger_read_grants(self):
        data = self.child_bounds()
        data["fs.read"] = ["**"]
        data["ledger.read"] = ["pilot/child/**"]
        child = ToolService(self.log, Bounds.parse(data), "agent.claude.child", root=self.root)
        raw_path = self.log.path.relative_to(self.root).as_posix()
        with self.assertRaises(ValueError):
            await child.invoke("fs_read", {"path": raw_path})
        listing = await child.invoke("fs_list", {"path": self.log.path.parent.relative_to(self.root).as_posix()})
        self.assertEqual(listing["entries"], [])

    async def test_escalation_and_missing_onboarding_or_instructions_refused_before_launch(self):
        note = await self.note()
        for key, value in (("ledger.write", ["**"]), ("fs.execute", [SCRIPTS + "/other.py"]),
                           ("fs.read", []), ("ledger.read", ["pilot/child/**"])):
            data = self.child_bounds()
            data[key] = value
            with self.assertRaises(ValueError):
                self.manager.start(self.service, "haiku", note["id"], data, "pilot/result-" + uuid4().hex)
        self.assertEqual(self.manager.jobs, {})

    async def test_cancelled_and_failed_children_leave_terminal_records(self):
        note = await self.note()
        async def fail(*args):
            raise ValueError("synthetic child failure")
        self.manager.runner = fail
        a = self.manager.start(self.service, "haiku", note["id"], self.child_bounds(), "pilot/result-" + uuid4().hex)
        self.assertEqual((await self.manager.status(AGENT_AUTHOR, a["agent_id"]))["status"], "failed")
        async def wait(*args):
            await asyncio.Event().wait()
        self.manager.runner = wait
        b = self.manager.start(self.service, "haiku", note["id"], self.child_bounds(), "pilot/result-" + uuid4().hex)
        await asyncio.sleep(0)
        await self.manager.close()
        self.assertEqual(self.manager.jobs[b["agent_id"]].status, "cancelled")

    async def test_immediate_shutdown_records_child_that_never_started(self):
        note = await self.note()
        job = self.manager.start(self.service, "haiku", note["id"], self.child_bounds(), "pilot/result-" + uuid4().hex)
        await self.manager.close()
        self.assertEqual(self.manager.jobs[job["agent_id"]].status, "cancelled")

    async def test_script_timeout_preserves_output(self):
        (self.root / SCRIPTS / "safe_test.py").write_text("import time\nprint('started', flush=True)\ntime.sleep(30)\n")
        result = await self.service.invoke("python_execute", {"path": SCRIPTS + "/safe_test.py", "output_name": "pilot/output-" + uuid4().hex})
        self.assertTrue(result["timed_out"])
        self.assertIn("started", self.log.scribe.current(result["output"]["name"]).body)


if __name__ == "__main__":
    unittest.main()
