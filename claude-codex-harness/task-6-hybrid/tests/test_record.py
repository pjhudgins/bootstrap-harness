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
    GOVERNOR = {"fs.read": ["*"], "fs.write": ["w/"], "fs.exec": ["s/"],
                "ledger.read": ["*"], "ledger.write": ["agent/", "tickets/"]}

    def run_record(self):
        self.record.write("run", record_format=ledger_log.RECORD_FORMAT,
                          governor_bounds=self.GOVERNOR)
        self.record.write("agent_started", agent="gov", parent=None, bounds=self.GOVERNOR)

    def test_a_clean_session(self):
        self.run_record()
        self.record.message("gov", "user", "hi", ledger_log.HUMAN_AUTHOR)
        owner = {"fs.read": ["origins/"], "fs.write": [], "fs.exec": [], "ledger.read": [],
                 "ledger.write": []}
        self.record.write("owner_start", agent="gov", owner="gov.1", bounds=owner)
        self.record.write("agent_started", agent="gov.1", parent="gov", bounds=owner)
        self.record.request("gov.1", "may I write?", "task-owner:m#gov.1", request="r1")
        widened = {**owner, "ledger.write": ["agent/gov.1/"]}
        self.record.write("bounds_granted", agent="gov.1", request="r1", by="governor:m#gov",
                          granted={"ledger.write": ["agent/gov.1/"]}, after=widened)
        self.record.write("request_decided", agent="gov.1", request="r1", decision="grant")
        self.record.write("owner_closed", agent="gov.1", owner="gov.1")
        self.record.write("summary", agents={"gov": {"unreviewed_calls": []}})
        self.record.close()
        problems, facts = check_ledger.check(self.ledger_root, closed=True)
        self.assertEqual(problems, [])
        self.assertEqual((facts["owners"], facts["requests"], facts["grants"]), (1, 1, 1))

    def test_what_it_catches(self):
        self.run_record()
        s = self.record.scribe
        # a harness record with a second label; a text nothing links to; an intent with no
        # result; a task owner never closed; a request never decided; a subagent whose
        # bounds exceed its task owner's; a governor grant beyond its own bounds; a record
        # only a repr survived of; no summary
        record_id = self.record.write("usage", tokens=1)
        s.tag(s.line(record_id)["name"], "extra", author=self.record.author)
        s.write(f"transcript/{self.record.session}/0099-gov-agent", "orphan",
                author="governor:m#gov")
        s.tag(f"transcript/{self.record.session}/0099-gov-agent", "transcript.agent",
              author=self.record.author)
        self.record.write("fs_write_started", agent="gov.1", path="w/a.txt")
        self.record.write("owner_start", agent="gov", owner="gov.1")
        self.record.write("agent_started", agent="gov.1", parent="gov",
                          bounds={"fs.read": ["origins/"]})
        self.record.request("gov.1", "please", "task-owner:m#gov.1", request="r1")
        self.record.write("agent_started", agent="gov.1.1", parent="gov.1",
                          bounds={"fs.read": ["*"]})
        self.record.write("bounds_granted", agent="gov.1", request="r1", by="governor:m#gov",
                          granted={"fs.write": ["elsewhere/"]},
                          after={"fs.read": ["origins/"], "fs.write": ["elsewhere/"]})
        self.record.write("odd", value=(1, 2))  # a tuple fails the JSON round trip
        self.record.close()
        problems, _ = check_ledger.check(self.ledger_root, closed=True)
        text = chr(10).join(problems)
        for expected in ["labels ['extra', 'log.usage']", "linked from 0", "has no fs_write",
                         "has no owner_closed", "has no request_decided", "gov.1.1's bounds",
                         "beyond the governor's bounds", "recorded only as text", "no summary"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)


class PromptTests(unittest.TestCase):
    BOUNDS = bd.Bounds({"fs.read": ["origins/"], "ledger.write": ["agent/gov.1/"]})
    ONBOARDING = "origins/onboarding_1.12.md"

    def governor(self):
        return prompt.governor_instructions(bounds=self.BOUNDS, author="governor:m#gov",
                                            onboarding=self.ONBOARDING)

    def owner(self):
        return prompt.owner_instructions(bounds=self.BOUNDS, author="task-owner:m#gov.1",
                                         onboarding=self.ONBOARDING, agent_id="gov.1",
                                         governor="governor:m#gov", ticket="tickets/1",
                                         ticket_id="20260928T000000Z:7")

    def sub(self):
        return prompt.subagent_instructions(bounds=self.BOUNDS, author="subagent:m#gov.1.1",
                                            onboarding=self.ONBOARDING, agent_id="gov.1.1",
                                            parent="task-owner:m#gov.1",
                                            instructions="agent/tasks/one",
                                            instructions_id="20260928T000000Z:9")

    @staticmethod
    def words(text):
        return " ".join(text.split())  # the templates are wrapped; compare the words

    def test_filled_completely(self):
        for text in (self.governor(), self.owner(), self.sub()):
            self.assertNotIn("{{", text)
            self.assertIn(self.BOUNDS.render(), text)

    def test_every_layer_sees_the_same_notation_and_rules(self):
        def rules(text):  # all but the always-readable line, which names each agent's own
            text = self.words(text)
            always = text.index("- Always readable")
            return text[text.index("Five keys"):always] + text[text.index("- Never read"):
                                                               text.index("Everything you do")]
        self.assertEqual(rules(self.governor()), rules(self.owner()))
        self.assertEqual(rules(self.owner()), rules(self.sub()))

    def test_each_layer_is_told_its_part(self):
        governor, owner, sub = (self.words(t) for t in (self.governor(), self.owner(), self.sub()))
        self.assertIn("Do not do a task owner's work yourself", governor)
        self.assertIn("gpt-6-astra", governor)                    # the task-owner models
        self.assertIn("ask_human", governor)
        self.assertIn("your ticket (tickets/1)", owner)           # always readable to it
        self.assertIn("version 20260928T000000Z:7", owner)
        self.assertIn("governor_request", owner)
        self.assertIn("claude-sonnet-5", owner)                   # the subagent models
        self.assertIn("strictly subordinate", sub)
        self.assertIn("your instructions entry (agent/tasks/one)", sub)
        self.assertNotIn("subagent_spawn", sub)
        self.assertIn("agent/gov/notebook", governor)

    def test_fill_never_fills_values_twice(self):
        self.assertEqual(prompt.fill("{{a}}", a="{{b}}"), "{{b}}")
        with self.assertRaises(KeyError):
            prompt.fill("{{missing}}")


if __name__ == "__main__":
    unittest.main()
