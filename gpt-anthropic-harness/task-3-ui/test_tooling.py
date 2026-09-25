"""Offline checks for the record and capability boundaries; no model calls."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from policy import TOOL_NAME, add_numbers, allowed
from runlog import RunLog

TASK_DIR = Path(__file__).resolve().parent


class ToolingTests(unittest.TestCase):
    def test_add_numbers(self):
        self.assertEqual(add_numbers({"a": 19.25, "b": 22.75}), 42)
        self.assertEqual(add_numbers({"a": -5, "b": 2}), -3)

    def test_invalid_numbers_and_overflow(self):
        for value in (True, "2", None, [], math.nan, math.inf, -math.inf, 10**400):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    add_numbers({"a": value, "b": 1})
        with self.assertRaises(ValueError):
            add_numbers({"a": 1e308, "b": 1e308})
        for args in ({"a": 1}, {"a": 1, "b": 2, "code": "print(42)"}, []):
            with self.assertRaises(ValueError):
                add_numbers(args)

    def test_default_deny(self):
        for name in ("Bash", "Read", "Agent", "mcp__other__add", "add", "unknown"):
            self.assertFalse(allowed(name, {"a": 1, "b": 2}))
        self.assertTrue(allowed(TOOL_NAME, {"a": 1, "b": 2}))
        self.assertFalse(allowed(TOOL_NAME, {"a": True, "b": 2}))

    def test_flushed_exclusive_journal_and_types(self):
        @dataclass
        class Message:
            text: str
        with tempfile.TemporaryDirectory(dir=TASK_DIR) as folder:
            path = Path(folder) / "run.jsonl"
            with RunLog(path) as log:
                log.write("message", message=Message("hello 👋"))
                # Visible before close, with SDK-like type and exact unicode preserved.
                row = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(row["message"], {"_type": "Message", "text": "hello 👋"})
                self.assertEqual(row["seq"], 1)
                with self.assertRaises(FileExistsError):
                    RunLog(path)
                log.write("complete")
            self.assertEqual([json.loads(s)["seq"] for s in path.read_text().splitlines()], [1, 2])

    def test_redaction_preserves_usage(self):
        with tempfile.TemporaryDirectory(dir=TASK_DIR) as folder:
            with patch.dict("os.environ", {"EXAMPLE_API_KEY": "synthetic-secret-value"}):
                with RunLog(Path(folder) / "run.jsonl") as log:
                    clean = log.clean({"access_token": "sensitive", "api_key": "sensitive",
                                       "input_tokens": 123, "outputTokens": 12,
                                       "text": "synthetic-secret-value sk-ant-synthetic123456789"})
                    self.assertEqual(clean["access_token"], "[REDACTED]")
                    self.assertEqual(clean["api_key"], "[REDACTED]")
                    self.assertEqual(clean["input_tokens"], 123)
                    self.assertEqual(clean["outputTokens"], 12)
                    self.assertEqual(clean["text"], "[REDACTED] [REDACTED]")

    def test_live_usage_field_regression(self):
        with tempfile.TemporaryDirectory(dir=TASK_DIR) as folder:
            with RunLog(Path(folder) / "run.jsonl") as log:
                fields = {"estimated_tokens": 7, "estimated_tokens_delta": 2,
                          "maxOutputTokens": 64000,
                          "cache_creation": {"ephemeral_1h_input_tokens": 0,
                                             "ephemeral_5m_input_tokens": 0}}
                log.write("usage", usage=fields, refresh_token="synthetic-credential")
                row = json.loads(log.path.read_text(encoding="utf-8"))
                self.assertEqual(row["usage"], fields)
                self.assertEqual(row["refresh_token"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
