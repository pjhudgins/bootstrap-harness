"""Offline checks: no login, no model, no tokens. Temporary files stay in ../.runtime/.

Most tests use a scripted stand-in app-server. RealCodexFakeModelTests run the real Codex
binary against an empty in-swimlane Codex home and the scripted fake model (skipped if
either is missing). Run from this folder:
  python -m unittest discover -s tests -t .
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import codex_client as cc
import driver
import policy
import tools

TASK_DIR = Path(__file__).resolve().parents[1]
FAKE = str(Path(__file__).with_name("fake_app_server.py"))
SCRATCH = TASK_DIR / ".runtime" / "test-tmp"  # rules.md: no writes outside the swimlane


def scratch_dir():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


def records(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


class AddToolTests(unittest.TestCase):
    def test_sums(self):
        self.assertEqual(tools.add({"a": 1234.5, "b": 8765.25}), {"sum": 9999.75})
        self.assertEqual(tools.add({"a": 10**30, "b": 1}), {"sum": 10**30 + 1})

    def test_rejects_bad_input(self):
        for arguments in ({"a": 1}, {"a": 1, "b": 2, "c": 3}, {"a": True, "b": 1},
                          {"a": "1", "b": 2}, {"a": float("nan"), "b": 1},
                          {"a": float("inf"), "b": 1}, {"a": 1e308, "b": 1e308},
                          {"a": 10**400, "b": 0.5}, [1, 2]):
            with self.subTest(arguments=arguments):
                with self.assertRaises(tools.ToolInputError):
                    tools.add(arguments)

    def test_call_never_raises(self):
        self.assertEqual(tools.call("add", None, '{"a": 2, "b": 3}'), (True, '{"sum": 5}'))
        self.assertFalse(tools.call("add", None, "not json")[0])
        self.assertFalse(tools.call("add", "ns", {"a": 1, "b": 2})[0])
        self.assertFalse(tools.call("shell", None, {})[0])


class JournalTests(unittest.TestCase):
    def test_append_only_redacted_and_flushed(self):
        with scratch_dir() as tmp:
            journal = cc.Journal(tmp, "t")
            journal.write("recv", message={"account": {"email": "x@example.com", "type": "chatgpt"},
                                           "usage": {"totalTokens": 5, "inputTokens": 4}})
            first = records(journal.path)  # readable before close: flushed per record
            self.assertEqual(first[0]["message"]["account"]["email"], "<redacted>")
            self.assertEqual(first[0]["message"]["usage"], {"totalTokens": 5, "inputTokens": 4})
            with self.assertRaises(FileExistsError):
                open(journal.path, "x").close()
            journal.close()
            self.assertIsNone(journal.write("late", note="after close"))


class CodexHomeDiffTests(unittest.TestCase):
    def test_changes_are_attributed_by_run_window(self):
        with scratch_dir() as tmp:
            root = Path(tmp)
            (root / "kept.txt").write_text("a")
            (root / "edited.txt").write_text("a")
            (root / "gone.txt").write_text("a")
            before = cc.snapshot_tree(root)
            start = time.time()
            (root / "edited.txt").write_text("b")
            os.utime(root / "edited.txt", (start + 1, start + 1))
            (root / "sub").mkdir()
            (root / "sub" / "new.txt").write_text("c")
            os.utime(root / "sub" / "new.txt", (start + 100, start + 100))
            (root / "gone.txt").unlink()
            changes = cc.diff_snapshots(before, cc.snapshot_tree(root), start, start + 10)
            summary = {(c["path"], c["change"], c["while_running"]) for c in changes}
            self.assertEqual(summary, {("edited.txt", "changed", True),
                                       (os.path.join("sub", "new.txt"), "new", False),
                                       ("gone.txt", "removed", None)})


class ClientProtocolTests(unittest.TestCase):
    def test_server_requests_answered_before_response(self):
        with scratch_dir() as tmp:
            journal = cc.Journal(tmp, "protocol")
            server = cc.AppServer([sys.executable, FAKE, "protocol"], journal)
            server.handlers["item/tool/call"] = lambda p: {"contentItems": [], "success": True}
            server.handlers["item/commandExecution/requestApproval"] = lambda p: {"decision": "decline"}
            server.request("initialize", {"clientInfo": {"name": "t", "title": None, "version": "0"},
                                          "capabilities": None})
            seen = []
            turn = cc.run_turn(server, "t-fake", "hi", seen.append, timeout=20)
            self.assertEqual(turn["status"], "completed")
            replies = {m["params"]["to"]: m["params"]["reply"] for m in seen
                       if m.get("method") == "test/reply"}
            # id collision: the server reused the client's turn/start id for its own request
            self.assertEqual(replies["item/tool/call"]["result"]["success"], True)
            self.assertEqual(replies["item/commandExecution/requestApproval"]["result"],
                             {"decision": "decline"})
            self.assertEqual(replies["unknown/thing"]["error"]["code"], -32601)
            self.assertEqual(server.close(), 0)
            kinds = [r["kind"] for r in records(journal.path)]
            self.assertIn("stderr", kinds)
            self.assertEqual(kinds[-1], "exit")
            journal.close()


class DriverSessionTests(unittest.TestCase):
    def run_session(self, scenario):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        journal = cc.Journal(tmp.name, scenario)
        run = driver.Run(journal)
        server = cc.AppServer([sys.executable, FAKE, scenario], journal)
        for method, response in policy.APPROVAL_DECLINES.items():
            server.handlers[method] = run.decliner(method, response)
        server.handlers["item/tool/call"] = run.on_tool_call
        catalog = {"fake-model": {"tool_mode": None, "multi_agent_version": None}}
        driver.session(server, run, preflight=False, catalog=catalog)
        server.close()
        journal.close()
        return run, journal.path

    def test_clean_run_passes_and_keeps_secrets_out(self):
        run, path = self.run_session("clean")
        failed = [c["name"] for c in run.checks if c["hard"] and not c["passed"]]
        self.assertEqual(failed, [])
        text = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("S3CRET-VALUE", text)  # config/read payload omitted at source
        self.assertNotIn("someone@example.com", text)
        kinds = {r["kind"] for r in records(path)}
        self.assertTrue({"send", "recv", "message", "tool_call", "model_call", "usage",
                         "rate_limits", "config_extract", "features", "mcp_servers",
                         "thread_usage", "model_catalog_entry"} <= kinds)
        sent = [r["message"] for r in records(path) if r["kind"] == "send"]
        start = next(m["params"] for m in sent if m.get("method") == "thread/start")
        self.assertEqual(start["environments"], [])
        self.assertIs(start["experimentalRawEvents"], True)
        self.assertEqual(start["model"], policy.MODEL)
        self.assertEqual(start["dynamicTools"], tools.SPECS)
        self.assertEqual(start["config"], {"mcp_servers.demo.enabled": False})

    def test_execution_is_caught(self):
        run, _ = self.run_session("executes")
        failed = {c["name"] for c in run.checks if c["hard"] and not c["passed"]}
        # the approval is declined, the raw exec call and the execution item are both seen
        self.assertEqual(failed, {"no_execution_items", "no_approval_requests",
                                  "model_calls_reviewed"})
        self.assertEqual(run.declined, ["item/commandExecution/requestApproval"])
        self.assertIn("exec", run.model_calls)


class ModelCheckTests(unittest.TestCase):
    def test_code_mode_only_model_fails_restriction(self):
        with scratch_dir() as tmp:
            journal = cc.Journal(tmp, "model")
            run = driver.Run(journal)
            catalog = {"gpt-6-astra": {"tool_mode": "code_mode_only", "multi_agent_version": "v2"},
                       "gpt-5.5": {"tool_mode": None, "multi_agent_version": None}}
            driver.check_model(run, catalog, "gpt-6-astra", restricted=True)
            driver.check_model(run, catalog, "gpt-5.5", restricted=True)
            driver.check_model(run, catalog, "unlisted-model", restricted=True)
            journal.close()
            self.assertEqual([c["passed"] for c in run.checks], [False, True, False])

    def test_call_identity(self):
        self.assertEqual(driver.call_identity({"type": "function_call", "name": "add"}), "add")
        self.assertEqual(driver.call_identity(
            {"type": "function_call", "namespace": "skills", "name": "read"}), "skills.read")
        self.assertEqual(driver.call_identity({"type": "custom_tool_call", "name": "exec"}), "exec")
        self.assertEqual(driver.call_identity({"type": "web_search_call"}), "web_search")
        self.assertIsNone(driver.call_identity({"type": "function_call_output"}))
        self.assertIsNone(driver.call_identity({"type": "message"}))


OFFLINE_HOME = TASK_DIR.parent / ".runtime" / "offline-codex-home"


def codex_available():
    try:
        cc.find_codex()
        return OFFLINE_HOME.is_dir()
    except FileNotFoundError:
        return False


@unittest.skipUnless(codex_available(), "needs codex and ../.runtime/offline-codex-home")
class RealCodexFakeModelTests(unittest.TestCase):
    """The real Codex binary, an empty in-swimlane Codex home, the scripted fake model:
    what Codex actually offers the model under the policy. No login, no tokens."""

    def drive(self, *extra):
        with scratch_dir() as tmp:
            out = subprocess.run(
                [sys.executable, str(TASK_DIR / "driver.py"), "--fake-model",
                 "--codex-home", str(OFFLINE_HOME), "--runs-dir", tmp, *extra],
                capture_output=True, timeout=180, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            summary = json.loads(out.stdout.decode("utf-8"))
            requests = [r for p in Path(tmp).glob("*.jsonl") for r in records(p)
                        if r["kind"] == "offered_tools"]
            return summary, requests[0]["offered"] if requests else None

    def test_policy_model_offers_only_reviewed_tools(self):
        summary, offered = self.drive()
        self.assertEqual(summary["failed"], [], summary)
        self.assertIn("add", offered)
        self.assertFalse([t for t in offered if "exec" in t or t.startswith("collaboration.")])

    def test_code_mode_only_model_is_caught(self):
        summary, offered = self.drive("--model", "gpt-6-astra")
        self.assertIn("offered_tools_reviewed", summary["failed"])
        self.assertIn("model_tool_mode_direct", summary["failed"])
        self.assertIn("functions.exec", offered)


class PolicyTests(unittest.TestCase):
    def test_disable_flags(self):
        flags = policy.disable_flags()
        self.assertEqual(flags[::2], ["--disable"] * len(policy.DISABLED_FEATURES))
        self.assertIn("code_mode_host", policy.DISABLED_FEATURES)
        self.assertLessEqual(policy.EXECUTION_ITEMS, policy.TOOL_ITEMS)
        self.assertNotIn("add", policy.REVIEWED_MODEL_TOOLS)


if __name__ == "__main__":
    unittest.main()
