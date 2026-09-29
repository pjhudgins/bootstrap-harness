"""The harness's record (ledger_log.py), the post-run checker (check_ledger.py) and the
prompts built from prompts/*.md."""

import json
import unittest
from unittest import mock

from tests.helpers import LedgerCase
import bounds as bd
import check_ledger
import ledger_log
from ledger_log import scribe
import prompt


class RecordTests(LedgerCase):
    def labels(self, name):
        return self.record.scribe.labels(name)

    def test_one_label_per_entry(self):
        record_id = self.record.write("usage", agent="pilot", tokens=3)
        name = self.record.scribe.line(record_id)["name"]
        self.assertTrue(name.endswith(".usage"))
        self.assertEqual(self.labels(name), {"log.usage"})
        text_name, _, _ = self.record.message("pilot", "user", "hello", ledger_log.HUMAN_AUTHOR)
        self.assertEqual(self.labels(text_name), {"transcript.user"})
        exec_name, _, _ = self.record.exec_output("pilot", "scripts/x.py", "out", exit_code=0)
        self.assertEqual(self.labels(exec_name), {"exec"})

    def test_texts_are_their_writers_and_records_link_them(self):
        name, text_id, record_id = self.record.message("pilot.1", "agent", "the report",
                                                       "subagent:m@claude-codex-harness#pilot.1")
        text = self.record.scribe.line(text_id)
        self.assertEqual((text["name"], text["author"], text["body"]),
                         (name, "subagent:m@claude-codex-harness#pilot.1", "the report"))
        link = self.record.scribe.line(record_id)["body"]
        self.assertEqual((link["kind"], link["text"], link["text_id"]),
                         ("message", f"[[{name}]]", text_id))

    def test_a_subagent_task_is_linked_not_copied(self):
        task_id = self.record.scribe.write("agent/tasks/one", "do it", author="someone:else")
        before = set(self.record.scribe.names())
        record_id = self.record.link_message("pilot.1", "task", "agent/tasks/one", task_id,
                                             "someone:else", instructed_by="test-pilot:m#pilot")
        body = self.record.scribe.line(record_id)["body"]
        self.assertEqual((body["text"], body["text_id"], body["text_author"], body["instructed_by"]),
                         ("[[agent/tasks/one]]", task_id, "someone:else", "test-pilot:m#pilot"))
        added = set(self.record.scribe.names()) - before
        self.assertEqual([n for n in added if n.startswith(ledger_log.TRANSCRIPT_PREFIX)], [])

    def test_keys_never_reach_the_ledger(self):
        key = "ghp_" + "x" * 36
        self.record.write("note", text=f"a key {key}")
        self.record.message("pilot", "user", f"here: {key}", ledger_log.HUMAN_AUTHOR)
        self.assertNotIn(key, self.record.path.read_text(encoding="utf-8"))

    def test_key_like_means_a_key_not_a_word_ending_in_sk(self):
        """Live, 2026-09-25: the pilot's entry name task-5-hello-safe-subagent-instructions
        was refused as a key, and its wikilinks would have been redacted."""
        key = "sk-proj-" + "Ab1" * 10
        for text in [key, f"OPENAI_API_KEY={key}", f'"{key}"', f"token: {key}"]:
            with self.subTest(text=text[:20]):
                self.assertTrue(ledger_log.has_key_like(text))
        for text in ["agent/pilot/task-5-hello-safe-subagent-instructions",
                     "risk-assessment-framework-document-v2", "[[agent/desk-notes-for-the-harness-run]]"]:
            with self.subTest(text=text):
                self.assertFalse(ledger_log.has_key_like(text))
        name = "agent/pilot/task-5-hello-safe-subagent-instructions"
        self.assertEqual(ledger_log.scrub({"text": f"[[{name}]]"}), {"text": f"[[{name}]]"})

    def test_scribe_provenance(self):
        found = ledger_log.scribe_provenance()
        self.assertEqual((found["id"], found["source"]),
                         (scribe.SCRIBE_ID, "bootstrap-ledger/python-scribe/scribe.py"))
        self.assertEqual(len(found["source_sha256"]), 64)

    def test_a_failed_write_ends_the_record(self):
        self.ledger_broken = True
        with mock.patch.object(scribe, "_write_all", side_effect=OSError("disk full")):
            with self.assertRaises(ledger_log.LedgerFailure):
                self.record.write("usage", tokens=1)
        with self.assertRaises(ledger_log.LedgerFailure):
            self.record.message("pilot", "user", "later", ledger_log.HUMAN_AUTHOR)
        self.assertTrue((self.ledger_root / ledger_log.LEDGER_NAME / "lease.json").exists())


class CheckerTests(LedgerCase):
    def run_record(self):
        self.record.write("run", record_format=ledger_log.RECORD_FORMAT,
                          root_bounds={"fs.read": ["*"], "fs.write": ["w/"], "fs.exec": ["s/"],
                                       "ledger.read": ["*"], "ledger.write": ["agent/"]})

    def test_a_clean_session(self):
        self.run_record()
        self.record.message("pilot", "user", "hi", ledger_log.HUMAN_AUTHOR)
        self.record.write("summary", agents={"pilot": {"unreviewed_calls": []}})
        self.record.close()
        problems, facts = check_ledger.check(self.ledger_root, closed=True)
        self.assertEqual(problems, [])
        self.assertEqual(facts["record_format"], ledger_log.RECORD_FORMAT)

    def test_what_it_catches(self):
        self.run_record()
        s = self.record.scribe
        # a harness record with a second label; a text nothing links to; an intent with
        # no result; a child whose bounds exceed its parent's; no summary
        record_id = self.record.write("usage", tokens=1)
        s.tag(s.line(record_id)["name"], "extra", author=self.record.author)
        s.write(f"transcript/{self.record.session}/0099-pilot-agent", "orphan",
                author="test-pilot:m#pilot")
        s.tag(f"transcript/{self.record.session}/0099-pilot-agent", "transcript.agent",
              author=self.record.author)
        self.record.write("fs_write_started", agent="pilot", path="w/a.txt")
        self.record.write("agent_started", agent="pilot", parent=None,
                          bounds={"fs.read": ["*"], "fs.write": ["w/"], "fs.exec": ["s/"],
                                  "ledger.read": ["*"], "ledger.write": ["agent/"]})
        self.record.write("agent_started", agent="pilot.1", parent="pilot",
                          bounds={"fs.write": ["x/"]})
        self.record.close()
        problems, _ = check_ledger.check(self.ledger_root, closed=True)
        text = "\n".join(problems)
        for expected in ["labels ['extra', 'log.usage']", "linked from 0", "has no result",
                         "pilot.1's bounds", "no summary"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


class PromptTests(unittest.TestCase):
    BOUNDS = bd.Bounds({"fs.read": ["origins/"], "ledger.write": ["agent/pilot.1/"]})

    def root(self, **kw):
        return prompt.root_instructions(bounds=self.BOUNDS, author="test-pilot:m#pilot",
                                        onboarding="origins/onboarding_1.12.md",
                                        agent_id="pilot", can_delegate=True, **kw)

    def sub(self, can_delegate=False):
        return prompt.subagent_instructions(
            bounds=self.BOUNDS, author="subagent:m#pilot.1", onboarding="origins/onboarding_1.12.md",
            agent_id="pilot.1", parent="test-pilot:m#pilot", instructions="agent/tasks/one",
            instructions_id="20260925T000000Z:7", can_delegate=can_delegate)

    @staticmethod
    def words(text):
        return " ".join(text.split())  # the templates are wrapped; compare the words

    def test_filled_completely(self):
        for text in (self.root(), self.sub(), self.sub(True)):
            self.assertNotIn("{{", text)
            self.assertIn(self.BOUNDS.render(), text)

    def test_parent_and_child_see_the_same_notation_and_rules(self):
        def rules(text):  # all of it but the always-readable line, which names each agent's own
            text = self.words(text)
            always = text.index("- Always readable")
            return text[text.index("Five keys"):always] + text[text.index("- Never read"):
                                                               text.index("Everything you do")]
        self.assertEqual(rules(self.root()), rules(self.sub()))

    def test_what_differs(self):
        root, sub = self.words(self.root()), self.words(self.sub())
        self.assertIn("your instructions entry (agent/tasks/one)", sub)  # always readable to it
        self.assertIn("version 20260925T000000Z:7", sub)
        self.assertIn("Delegate only when the user asks you to", root)
        self.assertIn(prompt.NO_DELEGATION, sub)
        self.assertIn("Delegate only when your instructions ask you to", self.words(self.sub(True)))
        self.assertIn("agent/pilot/notebook", root)

    def test_fill_never_fills_values_twice(self):
        self.assertEqual(prompt.fill("{{a}}", a="{{b}}"), "{{b}}")
        with self.assertRaises(KeyError):
            prompt.fill("{{missing}}")


if __name__ == "__main__":
    unittest.main()
