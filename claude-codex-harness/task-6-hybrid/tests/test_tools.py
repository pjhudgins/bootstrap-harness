"""The tools, through a Toolbox bound to a stand-in agent: no engine, no model."""

import json
import os
import time
import unittest
from unittest import mock

from tests.helpers import LedgerCase, ONBOARDING, ROOT, StubAgent, process_running, short_name
import bounds as bd
import fs_tools
import ledger_log
from ledger_log import scribe
import toolkit
import tools


class ToolboxCase(LedgerCase):
    def setUp(self):
        super().setUp()
        self.instructions_id = self.record.scribe.write("tasks/t1", "do the thing",
                                                        author="test-pilot:x#pilot")

    def box(self, bounds=ROOT, **kw):
        self.agent = StubAgent(self.record, bd.Bounds(bounds), **kw)
        return tools.Toolbox(self.agent, fs_root=self.root, workspace=self.root / "workspace",
                             scripts=self.root / "scripts")

    def call(self, box, tool, **arguments):
        ok, text = box.call(tool, None, arguments)
        return ok, (json.loads(text) if ok else text)

    def ledger_lines(self, name):
        """Every line of this session's file for one name (tags included)."""
        lines = self.record.path.read_bytes().split(b"\n")
        return [json.loads(line) for line in lines
                if line.strip() and json.loads(line).get("name") == name]


class OfferAndGateTests(ToolboxCase):
    def test_tools_by_layer(self):
        reading = ["add", "ledger_list", "ledger_read", "ledger_write", "ledger_tag", "fs_list",
                   "fs_read"]
        governor = self.box(layer="governor").names
        self.assertEqual(governor, reading + ["owner_start", "owner_message", "owner_status",
                                              "owner_close", "request_list", "request_answer"])
        # A task owner gets its whole layer, whatever its bounds: a grant may come later.
        owner = self.box({"fs.read": ["origins/"]}, layer="task-owner").names
        self.assertEqual(owner, reading + ["fs_write", "python_exec", "subagent_spawn",
                                           "subagent_wait", "governor_request", "governor_wait"])
        # A subagent's tools follow its fixed bounds (task 5).
        narrow = self.box({"fs.read": ["origins/"], "ledger.read": ["tasks/"]}, layer="subagent")
        self.assertEqual(narrow.names, ["add", "ledger_list", "ledger_read", "fs_list", "fs_read"])
        self.assertIn("python_exec", self.box(ROOT, layer="subagent").names)

    def test_onboarding_gate(self):
        box = self.box({"ledger.read": ["*"], "ledger.write": ["*"]}, read_onboarding=False)
        ok, message = self.call(box, "ledger_write", name="n/a", body="x")
        self.assertFalse(ok)
        self.assertIn(ONBOARDING, message)
        self.assertTrue(self.call(box, "fs_read", path=ONBOARDING, max_lines=2)[0])  # always readable
        self.assertFalse(self.call(box, "add", a=1, b=2)[0])  # two of three lines: still shut
        self.assertTrue(self.call(box, "fs_read", path=ONBOARDING)[0])
        self.assertEqual(self.call(box, "add", a=1, b=2), (True, {"sum": 3}))
        self.assertFalse(self.call(box, "fs_read", path="notes/a.txt")[0])  # fs.read is []

    def test_bad_calls_are_results_not_crashes(self):
        box = self.box()
        self.assertIn("strings", self.call(box, "ledger_list", prefix=5)[1])
        self.assertIn("missing", self.call(box, "ledger_read")[1])
        self.assertIn("unexpected", self.call(box, "add", a=1, b=2, c=3)[1])
        with mock.patch.object(toolkit.TOOLS["add"], "handler",
                               side_effect=RuntimeError("a bug")):
            ok, message = self.call(box, "add", a=1, b=2)
        self.assertFalse(ok)
        self.assertIn("internal error in add", message)


class LedgerToolTests(ToolboxCase):
    def test_bounds_own_instructions_and_write_implies_read(self):
        box = self.box({"ledger.write": ["notes/"]}, instructions="tasks/t1")
        self.assertTrue(self.call(box, "ledger_read", name="tasks/t1")[0])  # own instructions
        self.assertFalse(self.call(box, "ledger_read", name="tasks/other")[0])
        self.assertTrue(self.call(box, "ledger_write", name="notes/x", body="hi")[0])
        self.assertTrue(self.call(box, "ledger_read", name="notes/x")[0])  # write implies read
        self.assertFalse(self.call(box, "ledger_write", name="tasks/x", body="hi")[0])
        self.assertFalse(self.call(box, "ledger_write", name="exec/x", body="hi")[0])
        self.assertFalse(self.call(box, "ledger_write", name="requests/x", body="hi")[0])
        _, listed = self.call(box, "ledger_list")
        self.assertEqual(listed["names"], ["notes/x", "tasks/t1"])

    def test_anyone_within_bounds_supersedes_a_name(self):
        """Founder, 2026-09-25: the ledger is append-only; supersession by name is not
        restricted by author, and the ledger keeps every version with its author."""
        first = self.record.scribe.write("agent/shared", "by someone else", author="someone:else")
        box = self.box()
        ok, refused = self.call(box, "ledger_write", name="agent/shared", body="mine")
        self.assertFalse(ok)
        self.assertIn("current id as prev", refused)
        ok, written = self.call(box, "ledger_write", name="agent/shared", body="mine", prev=first)
        self.assertTrue(ok, written)
        _, read = self.call(box, "ledger_read", name="agent/shared", history=True)
        self.assertEqual([v["author"] for v in read["history"]],
                         ["someone:else", self.agent.author])

    def test_labels_protected_and_keys_refused(self):
        box = self.box()
        self.assertTrue(self.call(box, "ledger_write", name="agent/n", body="x",
                                  labels=["harness"])[0])  # no longer protected
        for label in ["log.x", "transcript", "transcript.user", "exec", "exec.y", "request",
                      "-bad"]:
            with self.subTest(label=label):
                self.assertFalse(self.call(box, "ledger_write", name="agent/m", body="x",
                                           labels=[label])[0])
        key = "sk-proj-" + "a1B2" * 8
        for args in [{"name": "agent/k", "body": f"my key is {key}"},
                     {"name": f"agent/{key}", "body": "x"}]:
            with self.subTest(args=args["name"][:20]):
                ok, message = self.call(box, "ledger_write", **args)
                self.assertFalse(ok)
                self.assertIn("key-like", message)
        self.assertNotIn(key, self.record.path.read_text(encoding="utf-8"))

    def test_labels_are_written_once(self):
        box = self.box()
        _, first = self.call(box, "ledger_write", name="agent/once", body="1", labels=["topic"])
        self.call(box, "ledger_write", name="agent/once", body="2", prev=first["id"],
                  labels=["topic"])
        tags = [line["tag"] for line in self.ledger_lines("agent/once") if "tag" in line]
        self.assertEqual(sorted(tags), ["agent", "topic"])


class FileToolTests(ToolboxCase):
    def test_raw_ledgers_and_harness_state_are_not_files_to_agents(self):
        """The raw-ledger bypass (review, 2026-09-25): a child with narrow ledger.read and
        fs.read over a ledger folder could read every record through fs_read."""
        child = self.box({"fs.read": ["notes/", ".runtime/"], "ledger.read": ["tasks/"]})
        for path in ["notes/x.ledger", "notes/lease.json", ".runtime/state.txt"]:
            with self.subTest(path=path):
                ok, message = self.call(child, "fs_read", path=path)
                self.assertFalse(ok)
                self.assertIn("ledger tools", message)
        alias = short_name(self.root / "notes" / "x.ledger")
        if alias:
            self.assertFalse(self.call(child, "fs_read", path=f"notes/{alias}")[0])
        _, listed = self.call(child, "fs_list", path="notes")
        unreadable = {e["name"] for e in listed["entries"] if e.get("readable") is False}
        self.assertEqual(unreadable, {"x.ledger", "lease.json"})
        self.assertFalse(self.call(child, "fs_list", path=".runtime")[0])
        self.assertFalse(self.call(self.box(), "fs_read", path=".env")[0])

    def test_fs_write_writes_the_version_it_names(self):
        box = self.box()
        v1 = self.record.scribe.write("drafts/d", "line 1\r\nline 2\nno newline", author="x")
        self.record.scribe.write("drafts/d", "a later version", author="y", prev=v1)
        ok, result = self.call(box, "fs_write", id=v1, path="workspace/sub/d.txt")
        self.assertTrue(ok, result)
        self.assertEqual((self.root / "workspace" / "sub" / "d.txt").read_bytes(),
                         b"line 1\r\nline 2\nno newline")
        self.assertEqual((result["entry"], result["entry_id"]), ("drafts/d", v1))
        kinds = self.harness_kinds()
        self.assertEqual(kinds[-2:], ["fs_write_started", "fs_write"])  # intent, then result

    def test_fs_write_creates_or_replaces_by_hash(self):
        box = self.box()
        v1 = self.record.scribe.write("agent/a", "first", author="x")
        v2 = self.record.scribe.write("agent/a", "second", author="x", prev=v1)
        self.assertTrue(self.call(box, "fs_write", id=v1, path="workspace/a.txt")[0])
        ok, refused = self.call(box, "fs_write", id=v2, path="workspace/a.txt")
        self.assertFalse(ok)
        current = refused.split("sha256 ")[1].split(")")[0]
        self.assertFalse(self.call(box, "fs_write", id=v2, path="workspace/a.txt",
                                   replace_sha256="0" * 64)[0])
        self.assertFalse(self.call(box, "fs_write", id=v2, path="workspace/new.txt",
                                   replace_sha256=current)[0])  # nothing there to replace
        ok, result = self.call(box, "fs_write", id=v2, path="workspace/a.txt",
                               replace_sha256=current)
        self.assertTrue(ok, result)
        self.assertEqual(result["replaced_sha256"], current)
        self.assertEqual((self.root / "workspace" / "a.txt").read_text(), "second")

    def test_fs_write_refusals(self):
        box = self.box()
        v = self.record.scribe.write("drafts/d", "text", author="x")
        tag = self.record.scribe.tag("drafts/d", "t", author="x")
        data = self.record.scribe.write("drafts/json", {"not": "text"}, author="x")
        for entry_id, path in [(v, "notes/d.txt"), (v, "scripts/d.py"), (v, "workspace/.env"),
                               (v, "../outside.txt"), (v, "workspace"), (v, "workspace/x.ledger"),
                               (v, "workspace/a.txt."), ("nope:1", "workspace/n.txt"),
                               (tag, "workspace/t.txt"), (data, "workspace/j.txt"),
                               (self.instructions_id, "workspace/i.txt")]:
            with self.subTest(entry_id=entry_id, path=path):
                narrow = self.box({"fs.write": ["workspace/"], "ledger.read": ["drafts/"]})
                self.assertFalse(self.call(narrow, "fs_write", id=entry_id, path=path)[0])
        rogue = self.box({"fs.write": ["scripts/"], "ledger.read": ["*"]})
        self.assertFalse(self.call(rogue, "fs_write", id=v, path="scripts/x.py")[0])
        self.assertEqual(list((self.root / "workspace").iterdir()), [])

    def test_no_write_happens_unless_it_is_recorded_first(self):
        self.ledger_broken = True  # the failed write ends the session; the lease stays
        box = self.box()
        v = self.record.scribe.write("drafts/d", "text", author="x")
        with mock.patch.object(scribe, "_write_all", side_effect=OSError("disk full")):
            with self.assertRaises(ledger_log.LedgerFailure):
                box.call("fs_write", None, {"id": v, "path": "workspace/d.txt"})
        self.assertFalse((self.root / "workspace" / "d.txt").exists())
        with self.assertRaises(ledger_log.LedgerFailure):  # and nothing after it either
            box.call("python_exec", None, {"script": "scripts/hello_safe.py"})


class ExecToolTests(ToolboxCase):
    def test_python_exec(self):
        box = self.box()
        ok, result = self.call(box, "python_exec", script="scripts/hello_safe.py", args=["2", "3"])
        self.assertTrue(ok, result)
        self.assertEqual((result["exit_code"], result["left_running"]), (0, []))
        out = self.record.scribe.current(result["output_entry"])
        self.assertIn("hello from the NIMOI task-6 scripts folder", out.body)
        self.assertIn("sum of numeric arguments: 5.0", out.body)
        self.assertNotIn("\r", out.body)  # line ends normalised
        self.assertEqual(out.author, ledger_log.HARNESS_AUTHOR)
        self.assertEqual(self.record.scribe.labels(result["output_entry"]), {"exec"})
        self.assertEqual(self.harness_kinds()[-2:], ["python_exec_started", "python_exec"])
        _, failed = self.call(box, "python_exec", script="scripts/hello_safe.py", args=["--fail"])
        self.assertEqual(failed["exit_code"], 2)
        self.assertIn("--- stderr ---", self.record.scribe.current(failed["output_entry"]).body)
        os.environ["NIMOI_TEST_SECRET"] = "sk-proj-should-not-leak-0000000000"
        self.addCleanup(os.environ.pop, "NIMOI_TEST_SECRET")
        _, env = self.call(box, "python_exec", script="scripts/env_probe.py")
        self.assertNotIn("NIMOI_TEST_SECRET", env["stdout"])
        for script in ["scripts/notes.txt", "notes/a.txt", "scripts/missing.py"]:
            with self.subTest(script=script):
                self.assertFalse(self.call(box, "python_exec", script=script)[0])

    def test_own_output_is_readable_with_narrow_ledger_bounds(self):
        box = self.box({"fs.exec": ["scripts/hello_safe.py"], "ledger.read": ["tasks/"]})
        _, result = self.call(box, "python_exec", script="scripts/hello_safe.py")
        ok, read = self.call(box, "ledger_read", name=result["output_entry"])
        self.assertTrue(ok, read)
        self.assertIn("squares", read["current"]["body"])

    def test_never_execute_where_writable(self):
        rogue = self.box({"fs.write": ["scripts/"], "fs.exec": ["scripts/"]})
        ok, message = self.call(rogue, "python_exec", script="scripts/hello_safe.py")
        self.assertFalse(ok)
        self.assertIn("never execute where you can write", message)
        near = self.box({"fs.write": ["scripts/new.py"], "fs.exec": ["scripts/hello_safe.py"]})
        self.assertFalse(self.call(near, "python_exec", script="scripts/hello_safe.py")[0])

    def test_what_a_script_starts_ends_with_it(self):
        box = self.box()
        started = time.monotonic()
        _, result = self.call(box, "python_exec", script="scripts/leaver.py")
        self.assertLess(time.monotonic() - started, 10)  # not waiting on a held pipe
        self.assertEqual((result["exit_code"], result["pipes_held"]), (0, False))
        self.assertEqual(result["left_running"], ["python.exe"])
        pid = int(result["stdout"].split()[1])
        self.assertFalse(process_running(pid))

    def test_timeout_ends_the_script_and_what_it_started(self):
        box = self.box()
        with mock.patch.object(fs_tools, "EXEC_TIMEOUT", 2):
            started = time.monotonic()
            _, result = self.call(box, "python_exec", script="scripts/leaver.py", args=["--hang"])
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((result["timed_out"], result["exit_code"]), (True, None))
        self.assertFalse(process_running(int(result["stdout"].split()[1])))


class SpawnToolTests(ToolboxCase):
    def test_spawn_checks_bounds_and_pins_the_current_version(self):
        box = self.box({"fs.read": ["origins/"], "ledger.read": ["tasks/"],
                        "ledger.write": ["tasks/"]})
        ok, _ = self.call(box, "subagent_spawn", model="m", instructions="tasks/t1",
                          bounds={"fs.read": ["origins/"], "ledger.read": ["tasks/t1"]})
        self.assertTrue(ok)
        name, args = self.agent.conv.calls[0]
        self.assertEqual((name, args[3:]), ("spawn", ("tasks/t1", self.instructions_id)))
        for bounds, instructions in [({"fs.read": ["*"]}, "tasks/t1"),
                                     ({"ledger.write": ["notes/"]}, "tasks/t1"),
                                     ({}, "tasks/missing"), ({}, "notes/not-readable")]:
            with self.subTest(bounds=bounds, instructions=instructions):
                self.assertFalse(self.call(box, "subagent_spawn", model="m", bounds=bounds,
                                           instructions=instructions)[0])
        self.assertEqual(len(self.agent.conv.calls), 1)


class GovernanceToolTests(ToolboxCase):
    GOVERNOR = {"fs.read": ["*"], "fs.write": ["workspace/"], "fs.exec": ["scripts/"],
                "ledger.read": ["*"], "ledger.write": ["agent/", "tickets/"]}

    def test_owner_start_checks_ticket_and_bounds(self):
        box = self.box(self.GOVERNOR, layer="governor", agent_id="gov")
        self.call(box, "ledger_write", name="tickets/1", body="Bar: x. Do it.")
        ok, _ = self.call(box, "owner_start", model="m", ticket="tickets/1",
                          bounds={"fs.read": ["origins/"], "ledger.write": ["agent/gov.1/"]})
        self.assertTrue(ok)
        name, args = self.agent.conv.calls[0]
        self.assertEqual((name, args[1], args[3]), ("start_owner", "m", "tickets/1"))
        for bounds, ticket in [({"fs.read": ["*"], "ledger.write": ["notes/"]}, "tickets/1"),
                               ({}, "tickets/missing"), ({"fs.write": ["scripts/"]}, "tickets/1")]:
            with self.subTest(bounds=bounds, ticket=ticket):
                self.assertFalse(self.call(box, "owner_start", model="m", ticket=ticket,
                                           bounds=bounds)[0])
        self.assertEqual(len(self.agent.conv.calls), 1)

    def test_request_answer_and_status_arguments(self):
        box = self.box(self.GOVERNOR, layer="governor", agent_id="gov")
        self.assertTrue(self.call(box, "request_answer", request="r1", decision="refuse",
                                  message="no")[0])
        self.assertFalse(self.call(box, "request_answer", request="r1", decision="maybe",
                                   message="no")[0])
        self.assertFalse(self.call(box, "owner_status", owner="gov.1", seconds=301)[0])
        self.assertTrue(self.call(box, "owner_status")[0])
        self.assertFalse(self.call(box, "request_list", state="odd")[0])

    def test_governor_request_arguments(self):
        box = self.box({"fs.read": ["origins/"]}, layer="task-owner")
        ok, _ = self.call(box, "governor_request", kind="bounds", justification="to write",
                          bounds={"ledger.write": ["agent/gov.1/"]}, seconds=0)
        self.assertTrue(ok)
        name, args = self.agent.conv.calls[0]
        self.assertEqual((name, args[1], args[3]["bounds"]["ledger.write"]),
                         ("make_request", "bounds", ["agent/gov.1/"]))
        for args in [{"kind": "bounds", "justification": "x"},              # no bounds
                     {"kind": "promote_script", "justification": "x", "from_path": "a.py"},
                     {"kind": "wish", "justification": "x"},
                     {"kind": "question", "justification": " "}]:
            with self.subTest(args=args):
                self.assertFalse(self.call(box, "governor_request", **args)[0])
        self.assertTrue(self.call(box, "governor_request", kind="promote_script",
                                  justification="vetted", from_path="workspace/a.py",
                                  to_path="scripts/a.py")[0])


if __name__ == "__main__":
    unittest.main()
