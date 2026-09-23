"""Offline tests: no model calls, no CLI.

Run from anywhere:
    python -m unittest discover -s <task-2-tooling>/tests -t <task-2-tooling>
"""

import asyncio
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TASK_DIR))

from calc_tool import add, add_numbers  # noqa: E402
from policy import ToolPolicy  # noqa: E402
from runlog import RunLog, to_jsonable  # noqa: E402


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.p = ToolPolicy(TASK_DIR)

    def allowed(self, tool, inp):
        return self.p.check(tool, inp).allow

    def test_execution_and_write_tools_denied(self):
        for tool in ("Bash", "PowerShell", "Write", "Edit", "NotebookEdit", "Agent", "Task", "WebFetch"):
            self.assertFalse(self.allowed(tool, {}), tool)

    def test_add_allowed(self):
        self.assertTrue(self.allowed("mcp__calc__add", {"a": 1, "b": 2}))

    def test_read_inside_root(self):
        self.assertTrue(self.allowed("Read", {"file_path": "fixtures/numbers.txt"}))
        self.assertTrue(self.allowed("Read", {"file_path": str(TASK_DIR / "fixtures" / "numbers.txt")}))

    def test_read_outside_root(self):
        self.assertFalse(self.allowed("Read", {"file_path": "../notebook.md"}))
        self.assertFalse(self.allowed("Read", {"file_path": str(TASK_DIR.parent / ".env")}))
        self.assertFalse(self.allowed("Read", {"file_path": "/etc/passwd"}))
        self.assertFalse(self.allowed("Read", {"file_path": "~/.bashrc"}))
        self.assertFalse(self.allowed("Read", {}))

    def test_secret_names_inside_root(self):
        for name in (".env", ".env.local", "prod.env", "api.key", "credentials.json", "sub/.env"):
            self.assertFalse(self.allowed("Read", {"file_path": name}), name)

    def test_glob_and_grep(self):
        self.assertTrue(self.allowed("Glob", {"pattern": "**/*.py"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "../**/*"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "C:/**/*"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "**/.env"}))
        self.assertFalse(self.allowed("Glob", {"pattern": "*.py", "path": ".."}))
        self.assertTrue(self.allowed("Grep", {"pattern": "def", "path": "."}))
        self.assertFalse(self.allowed("Grep", {"pattern": "KEY", "path": str(TASK_DIR.parent)}))
        self.assertFalse(self.allowed("Grep", {"pattern": "KEY", "glob": "*.env"}))


class AddToolTests(unittest.TestCase):
    def test_add_numbers(self):
        self.assertEqual(add_numbers(2, 3), 5)
        self.assertEqual(add_numbers(1234.5, 678.25), 1912.75)
        for a, b in (("1", 2), (True, 1), (None, 1)):
            with self.assertRaises(TypeError):
                add_numbers(a, b)

    def test_handler(self):
        out = asyncio.run(add.handler({"a": 19.5, "b": 22.75}))
        self.assertEqual(out["content"][0]["text"], "42.25")
        self.assertNotIn("is_error", out)
        bad = asyncio.run(add.handler({"a": "x", "b": 1}))
        self.assertTrue(bad["is_error"])

    def test_tool_name(self):
        self.assertEqual(add.name, "add")


class RunLogTests(unittest.TestCase):
    def test_serialization_and_sequence(self):
        @dataclass
        class Msg:
            text: str
            other: object

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x" / "run.jsonl.log"
            with RunLog(path) as log:
                log.context = {"scenario": "t"}
                log.write("message", message=Msg("hi", object()))
                log.write("usage", n=1)
            lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["seq"] for r in lines], [1, 2])
        self.assertEqual(lines[0]["message"]["_type"], "Msg")
        self.assertEqual(lines[0]["message"]["text"], "hi")
        self.assertTrue(lines[0]["message"]["other"].startswith("<object"))
        self.assertEqual(lines[1]["scenario"], "t")

    def test_to_jsonable_containers(self):
        self.assertEqual(to_jsonable({"a": (1, 2), 3: None}), {"a": [1, 2], "3": None})


if __name__ == "__main__":
    unittest.main()
