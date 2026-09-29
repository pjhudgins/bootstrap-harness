"""The Python execute tool: isolation, record-before-run, output caps, timeouts, refusals."""

import unittest
from unittest import mock

from tests.support import HarnessCase

import exec_tool  # noqa: E402
from ledger_tools import Denied  # noqa: E402


class ExecTests(HarnessCase):
    def run_script(self, script, args=None):
        return self.run_async(self.top.exec_tool.run(script, args))

    def test_safe_probe_runs_isolated_and_is_recorded(self):
        out = self.run_script("/scripts/safe_probe.py", ["alpha", "β"])
        self.assertEqual(out["exit_code"], 0, out["stderr"])
        self.assertFalse(out["timed_out"] or out["overflowed"])
        self.assertIn("isolated    True", out["stdout"])
        self.assertIn("['alpha', 'β']", out["stdout"])
        self.assertIn(f"cwd         {self.root}", out["stdout"])  # task 7: cwd is the project folder
        env_line = next(line for line in out["stdout"].splitlines() if line.startswith("env keys"))
        self.assertNotIn("'PATH'", env_line)
        self.assertNotIn("ANTHROPIC", env_line)
        started, done = self.records("exec_started")[-1], self.records("exec")[-1]
        self.assertLess(started["seq"], done["seq"])
        self.assertEqual(done["started"], started["id"])
        self.assertEqual(len(started["script_sha256"]), 64)
        self.assertEqual(self.view().current(out["output_entry"][2:-2]).body["stdout"], out["stdout"])
        self.assertFalse((self.root / "scripts" / "__pycache__").exists())  # -B

    def test_output_cap_kills_the_script(self):
        out = self.run_script("/scripts/loud.py")
        self.assertTrue(out["overflowed"])
        self.assertLessEqual(len(self.records("exec")[-1]["stdout"]), exec_tool.OUTPUT_CAP_BYTES)
        self.assertTrue(out["reply_truncated"])

    def test_timeout_kills_the_script(self):
        with mock.patch.object(exec_tool, "EXEC_TIMEOUT_S", 1):
            out = self.run_script("/scripts/slow.py")
        self.assertTrue(out["timed_out"])
        self.assertLess(out["duration_ms"], 20000)

    def test_refusals(self):
        v = self.top.guard.write("pilot/evil", "print('x')")["written"]
        self.top.file_tools.write(v, "/work/evil.py")
        for script, why in (("/work/evil.py", "fs.exec"), ("/scripts/notes.txt", "not a .py"),
                            ("/scripts/missing.py", "not a .py"), ("/docs/a.md", "fs.exec")):
            with self.assertRaisesRegex(Denied, why, msg=script):
                self.run_script(script)
        with self.assertRaisesRegex(Denied, "args"):
            self.run_script("/scripts/safe_probe.py", [1])
        self.assertEqual(self.records("exec_started"), [])  # nothing started

    def test_no_record_no_run(self):
        self.ledger.close()
        with self.assertRaisesRegex(Denied, "could not record"):
            self.run_script("/scripts/safe_probe.py")
        from tests.test_files import scribe_reopen
        self.ledger = scribe_reopen(self)


if __name__ == "__main__":
    unittest.main()
