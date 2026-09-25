"""Offline tests: no model calls, no CLI. Scribe ledgers live in temporary directories.

Run from anywhere:
    python -m unittest discover -s <task-4-ledger>/tests -t <task-4-ledger>
"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TASK_DIR))

from starlette.testclient import TestClient  # noqa: E402

from scribe_import import NIMOI_ROOT, SCRIBE_DIR, import_scribe  # noqa: E402

scribe = import_scribe()

from app import agent_author, create_app, fill_system_prompt, sse_stream  # noqa: E402
from calc_tool import add_numbers  # noqa: E402
from events import EventBus  # noqa: E402
from ledger_tools import AGENT_LABEL, Denied, LedgerGuard  # noqa: E402
from ledgerlog import LedgerLog  # noqa: E402
from policy import ToolPolicy  # noqa: E402

HARNESS = "harness:test"
AGENT = agent_author("claude-test")
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"


class LedgerMixin:
    """A fresh ledger per test, closed and checked afterwards."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ledger = scribe.Scribe.open(self.root, "t", session_author=HARNESS, create=True)
        self.log = LedgerLog(scribe, self.ledger, HARNESS)
        self.bus = EventBus(self.log)
        self.guard = LedgerGuard(scribe, self.ledger, AGENT)

    def tearDown(self):
        self.ledger.close()
        view = scribe.load(self.root, "t")
        self.assertEqual([f for f in view.findings], [], "ledger should load with no findings")
        self._tmp.cleanup()


# ---- import and prompt -------------------------------------------------------------------

class ImportTests(unittest.TestCase):
    def test_scribe_from_bootstrap_ledger(self):
        self.assertEqual(Path(scribe.__file__).resolve().parent, SCRIBE_DIR.resolve())
        self.assertTrue(scribe.SCRIBE_ID.startswith("python-scribe/"))
        self.assertNotIn(str(SCRIBE_DIR), sys.path)  # path entry removed after import

    def test_nimoi_root(self):
        self.assertTrue((NIMOI_ROOT / "origins").is_dir())
        self.assertTrue((NIMOI_ROOT / "bootstrap-harness" / "rules.md").is_file())

    def test_system_prompt_placeholders_all_filled(self):
        text = (TASK_DIR / "system_prompt.md").read_text(encoding="utf-8")
        filled = fill_system_prompt(text, nimoi_root="R", ledger="L", session="S", agent_author="A", model="M")
        for key in ("nimoi_root", "ledger", "session", "agent_author", "model"):
            self.assertNotIn("{" + key + "}", filled)
        self.assertIn("test pilot", filled)
        self.assertIn("onboarding", filled)


# ---- policy ------------------------------------------------------------------------------

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = ToolPolicy(NIMOI_ROOT)

    def allowed(self, tool, inp):
        return self.p.check(tool, inp).allow

    def test_execution_and_write_tools_denied(self):
        for tool in ("Bash", "PowerShell", "Write", "Edit", "NotebookEdit", "Agent", "Task", "WebFetch"):
            self.assertFalse(self.allowed(tool, {}), tool)

    def test_mcp_tools_allowed(self):
        for tool in ("mcp__calc__add", "mcp__ledger__read", "mcp__ledger__list", "mcp__ledger__write"):
            self.assertTrue(self.allowed(tool, {}), tool)
        self.assertFalse(self.allowed("mcp__ledger__delete", {}))

    def test_read_within_nimoi(self):
        self.assertTrue(self.allowed("Read", {"file_path": str(NIMOI_ROOT / "origins" / "onboarding_1.11.md")}))
        self.assertTrue(self.allowed("Read", {"file_path": "bootstrap-harness/rules.md"}))
        self.assertFalse(self.allowed("Read", {"file_path": str(NIMOI_ROOT.parent / "x.txt")}))
        self.assertFalse(self.allowed("Read", {"file_path": "../outside.txt"}))
        self.assertFalse(self.allowed("Read", {"file_path": "bootstrap-harness/claude-anthropic-harness/.env"}))

    def test_candidate_repos_excluded(self):
        self.assertFalse(self.allowed("Read", {"file_path": "candidate_repos/x/README.md"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "*", "path": "candidate_repos"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "**/*.md"}))          # from root, descends
        self.assertFalse(self.allowed("Glob", {"pattern": "*/README.md"}))       # wildcard first segment
        self.assertFalse(self.allowed("Glob", {"pattern": "candidate_repos/**"}))
        self.assertFalse(self.allowed("Grep", {"pattern": "x"}))                 # root recursion
        self.assertFalse(self.allowed("Grep", {"pattern": "x", "path": str(NIMOI_ROOT)}))

    def test_searches_clear_of_exclusion(self):
        self.assertTrue(self.allowed("Glob", {"pattern": "origins/onboarding_*.md"}))
        self.assertTrue(self.allowed("Glob", {"pattern": "*.md"}))               # top level only
        self.assertTrue(self.allowed("Glob", {"pattern": "**/*.md", "path": "origins"}))
        self.assertTrue(self.allowed("Grep", {"pattern": "x", "path": "origins"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "**/.env", "path": "bootstrap-harness"}))


# ---- ledger logging ----------------------------------------------------------------------

class LedgerLogTests(LedgerMixin, unittest.TestCase):
    def test_record_becomes_tagged_ledger_entry(self):
        self.log.context = {"turn": 2}
        rec = self.log.write("tool_call", tool_name="mcp__calc__add", tool_input={"a": 1, "b": 2})
        self.assertEqual(rec["name"], f"log/{self.ledger.session}/000001")
        entry = self.ledger.current(rec["name"])
        self.assertEqual(entry.id, rec["id"])
        self.assertEqual(entry.author, HARNESS)
        self.assertEqual(entry.body["kind"], "tool_call")
        self.assertEqual(entry.body["turn"], 2)
        self.assertEqual(self.ledger.labels(rec["name"]), {"harness", "log.tool_call"})

    def test_unencodable_body_falls_back(self):
        rec = self.log.write("usage", cost=float("nan"))
        self.assertIn("ledger_note", rec)
        body = self.ledger.current(rec["name"]).body
        self.assertIn("nan", body["unrecordable"])

    def test_write_failure_marks_later_records(self):
        self.ledger.close()  # any later write is refused: session_ended
        rec = self.log.write("status", state="idle")
        self.assertIn("ledger_error", rec)
        self.assertNotIn("id", rec)
        self.assertIn("ledger_error", self.log.write("status", state="busy"))
        self.ledger = scribe.Scribe.open(self.root, "t", session_author=HARNESS)  # for tearDown


# ---- agent ledger tools ------------------------------------------------------------------

class LedgerGuardTests(LedgerMixin, unittest.TestCase):
    def test_create_read_update_own_entry(self):
        out = self.guard.write("pilot/notes", {"seen": 1}, labels=["obs"])
        self.assertEqual(out["author"], AGENT)
        self.assertEqual(set(out["labels"]), {AGENT_LABEL, "obs"})
        current = self.guard.read("pilot/notes")["current"]
        out2 = self.guard.write("pilot/notes", {"seen": 2}, prev=current["id"])
        read = self.guard.read("pilot/notes", history=True)
        self.assertEqual(read["current"]["body"], {"seen": 2})
        self.assertEqual(len(read["history"]), 2)
        self.assertEqual(read["current"]["id"], out2["written"])

    def test_scribe_refusals_pass_through(self):
        self.guard.write("pilot/x", "a")
        with self.assertRaisesRegex(Denied, "bound"):
            self.guard.write("pilot/x", "b")  # no prev
        with self.assertRaisesRegex(Denied, "stale_prev"):
            self.guard.write("pilot/x", "b", prev="20000101T000000Z:1")
        with self.assertRaisesRegex(Denied, "bad_name"):
            self.guard.write("bad name!", "b")

    def test_harness_entries_protected(self):
        rec = self.log.write("status", state="idle")
        with self.assertRaisesRegex(Denied, "harness"):
            self.guard.write(rec["name"], "overwrite", prev=rec["id"])
        with self.assertRaisesRegex(Denied, "belong to the harness"):
            self.guard.write("log/new", "x")
        # A harness-labelled name outside log/ is protected by its label alone.
        self.ledger.write("notes/founder", "x", author=HARNESS)
        self.ledger.tag("notes/founder", "harness", author=HARNESS)
        with self.assertRaisesRegex(Denied, "protected"):
            self.guard.write("notes/founder", "y", prev=self.ledger.current("notes/founder").id)
        self.assertEqual(self.ledger.current(rec["name"]).author, HARNESS)

    def test_others_entries_not_writable(self):
        self.ledger.write("shared/doc", "x", author="founder")
        with self.assertRaisesRegex(Denied, "only update its own"):
            self.guard.write("shared/doc", "y", prev=self.ledger.current("shared/doc").id)

    def test_protected_labels_and_deletes_refused(self):
        for label in ("harness", "harness.x", "log.tool_call", "_x", "bad label"):
            with self.assertRaises(Denied, msg=label):
                self.guard.write("pilot/l", "x", labels=[label])
        with self.assertRaisesRegex(Denied, "null"):
            self.guard.write("pilot/l", None)
        self.assertIsNone(self.ledger.current("pilot/l"))  # nothing written by any refusal

    def test_list_filters(self):
        self.log.write("status", state="idle")
        self.guard.write("pilot/a", 1)
        self.assertEqual([r["name"] for r in self.guard.list(prefix="pilot/")["names"]], ["pilot/a"])
        self.assertEqual(self.guard.list(label="log.status")["total"], 1)
        self.assertEqual(self.guard.list(label="harness")["names"][0]["author"], HARNESS)


# ---- bus and web -------------------------------------------------------------------------

class BusTests(LedgerMixin, unittest.TestCase):
    def test_ui_records_carry_ledger_ids(self):
        async def go():
            self.bus.bind_loop(asyncio.get_running_loop())
            self.bus.publish("a")
            gen = sse_stream(self.bus, 0, "conv")
            hello = await gen.__anext__()
            first = await gen.__anext__()
            self.bus.close_streams()
            with self.assertRaises(StopAsyncIteration):
                await gen.__anext__()
            return hello, json.loads(first.split("data: ", 1)[1])
        hello, rec = asyncio.run(go())
        self.assertIn('"conv"', hello)
        self.assertEqual(self.ledger.line(rec["id"])["body"]["kind"], "a")


class FakeSession:
    def __init__(self):
        self.state, self.sent, self.started, self.stopped = "starting", [], False, False

    def snapshot(self):
        return {"state": self.state}

    def send(self, text):
        if self.state != "idle":
            return f"agent is {self.state}"
        self.sent.append(text)
        self.state = "busy"
        return None

    async def interrupt(self):
        return self.state == "busy"

    async def start(self):
        self.started, self.state = True, "idle"

    async def stop(self):
        self.stopped = True


class WebTests(LedgerMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.session = FakeSession()
        self.exits = []
        self.client = TestClient(create_app(self.session, self.bus, port=PORT,
                                            on_shutdown=lambda: self.exits.append(1)), base_url=ORIGIN)

    def test_index_send_and_lifespan(self):
        with self.client as c:
            self.assertIn("test pilot", c.get("/").text)
            self.assertEqual(c.post("/api/send", json={"text": "hi"}, headers={"Origin": ORIGIN}).status_code, 202)
            self.assertEqual(c.post("/api/send", json={"text": "again"}).status_code, 409)
        self.assertTrue(self.session.started and self.session.stopped)

    def test_page_and_static_not_cached(self):
        with self.client as c:
            for path in ("/", "/static/app.js", "/static/style.css"):
                r = c.get(path)
                self.assertEqual(r.status_code, 200, path)
                self.assertEqual(r.headers.get("cache-control"), "no-cache", path)
            page = c.get("/").text
            self.assertIn('"/static/app.js?v=test"', page)  # conversation_id defaults to "test"
            self.assertIn('"/static/style.css?v=test"', page)

    def test_shutdown_endpoint(self):
        with self.client as c:
            self.assertEqual(c.post("/api/shutdown", json={}, headers={"Origin": "http://evil.example"}).status_code, 403)
            self.assertEqual(self.exits, [])
            self.assertEqual(c.post("/api/shutdown", json={}).status_code, 202)
            self.assertEqual(self.exits, [1])
        self.assertEqual(self.guard.list(label="log.shutdown_requested")["total"], 1)

    def test_untrusted_host_and_form_post_rejected(self):
        with self.client as c:
            self.assertEqual(c.get("/", headers={"Host": "attacker.example"}).status_code, 400)
            self.assertEqual(c.post("/api/send", content="text=hi",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code, 415)


class MessageTextTests(LedgerMixin, unittest.TestCase):
    """Message text is its own entry, written first; the message entry links to it."""

    def session(self):
        from session import AgentSession
        return AgentSession(self.bus, self.guard, model="m", root=NIMOI_ROOT, system_prompt="p",
                            budget_usd=1, max_turns=1, ledger_facts={})

    def test_write_text_entry(self):
        from ledgerlog import HUMAN_AUTHOR
        rec = self.log.write_text("hello there", author=HUMAN_AUTHOR, direction="to_agent")
        entry = self.ledger.current(rec["name"])
        self.assertEqual(entry.body, "hello there")
        self.assertEqual(entry.author, HUMAN_AUTHOR)
        self.assertEqual(self.ledger.labels(rec["name"]), {"harness", "log.text"})
        self.assertEqual((rec["text"], rec["direction"]), ("hello there", "to_agent"))

    def test_user_text_then_linked_message(self):
        from ledgerlog import HUMAN_AUTHOR
        s = self.session()
        s._turn_links = {}
        link = s._log_text("please add", author=HUMAN_AUTHOR, direction="to_agent")
        self.bus.publish("message", direction="to_agent", message={"_type": "prompt", "text": link})
        text_name, msg_name = self.guard.list(prefix="log/")["names"][-2:]
        text_name, msg_name = text_name["name"], msg_name["name"]
        self.assertEqual(link, f"[[{text_name}]]")
        self.assertLess(text_name, msg_name)  # text first
        self.assertEqual(self.ledger.current(msg_name).body["message"]["text"], link)
        self.assertEqual(self.ledger.current(msg_name).author, HARNESS)

    def test_agent_text_blocks_linked_and_authored_by_agent(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
        s = self.session()
        s._turn_links = {}
        msg = AssistantMessage(content=[TextBlock("The sum is 6.5."), ToolUseBlock("t1", "mcp__calc__add", {"a": 1})],
                               model="m")
        body = s._message_body(msg)
        link = body["content"][0]["text"]
        name = link[2:-2]
        self.assertEqual(self.ledger.current(name).body, "The sum is 6.5.")
        self.assertEqual(self.ledger.current(name).author, AGENT)
        self.assertEqual(body["content"][1]["input"], {"a": 1})  # tool input stays inline
        result = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                               num_turns=1, session_id="x", result="The sum is 6.5.")
        self.assertEqual(s._message_body(result)["result"], link)
        # Agent-authored text under log/ is still not writable by the agent.
        with self.assertRaisesRegex(Denied, "belong to the harness"):
            self.guard.write(name, "edited", prev=self.ledger.current(name).id)

    def test_text_inline_when_ledger_failed(self):
        s = self.session()
        s._turn_links = {}
        self.ledger.close()
        self.assertEqual(s._log_text("kept", author=AGENT, direction="from_agent"), "kept")
        self.ledger = scribe.Scribe.open(self.root, "t", session_author=HARNESS)  # for tearDown


class IsolationOptionsTests(LedgerMixin, unittest.TestCase):
    """The options that keep host inputs out of the session (tasks 2 and 4 findings)."""

    def test_options(self):
        from session import AgentSession
        s = AgentSession(self.bus, self.guard, model="m", root=NIMOI_ROOT, system_prompt="p",
                         budget_usd=1, max_turns=1, ledger_facts={})
        opts = s._options()
        self.assertEqual(opts.env.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"), "1")
        self.assertTrue(opts.strict_mcp_config)
        self.assertEqual(opts.setting_sources, [])
        self.assertEqual(opts.tools, ["Read", "Glob", "Grep"])
        self.assertIn("no-session-persistence", opts.extra_args)


class CalcTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add_numbers(19.5, 22.75), 42.25)


if __name__ == "__main__":
    unittest.main()
