"""Task-5 boundary/lifecycle tests; actual scribe, files and approved Python."""

import copy
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from access import LedgerAccess
from agent_tools import ReadFiles, registry
from bounds import Bounds, root_bounds
from capabilities import FileCapabilities
from ledger_store import AGENT_AUTHOR, HARNESS_AUTHOR, AgentJournal, LedgerJournal, scribe
from protocol import TASK, SessionStopped
from subagents import Subagents


class Task5Tests(unittest.TestCase):
    def setUp(self):
        (TASK / '.runtime').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TASK / '.runtime')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ('origins', 'workspace', 'scripts', 'outside'):
            (self.root / name).mkdir()
        (self.root / 'origins/onboarding_1.00.md').write_text('Bar: fixture.\nRead all of this.', encoding='utf-8')
        self.bounds = root_bounds(self.root, self.root / 'workspace', self.root / 'scripts')
        self.journal = LedgerJournal(self.root / 'ledgers', 'test')
        self.addCleanup(self.journal.close)
        self.stop = threading.Event()
        self.access = LedgerAccess(self.journal, self.bounds, AGENT_AUTHOR)
        self.files = FileCapabilities(ReadFiles(self.root), self.bounds, self.access, self.stop, 'main')

    def note(self, name='agent/brief', body='Bar: one bounded reply.\nReply hello.'):
        written = self.access.agent_write(name, body, None, [])
        return {key: written[key] for key in ('name', 'id')}

    def child_spec(self):
        return {'fs': {'read': ['nimoi:/origins/', 'workspace:/child/'],
                       'write': ['workspace:/child/'], 'execute': ['scripts:/smoke.py']},
                'ledger': {'read': ['agent/brief', 'agent/child/'], 'write': ['agent/child/']}}

    def test_subset_and_empty_permissions_use_same_notation(self):
        child = self.bounds.child(self.child_spec())
        self.assertTrue(child.allows('fs', 'write', 'workspace:/child/a.txt'))
        self.assertFalse(child.allows('fs', 'write', 'workspace:/children/a.txt'))
        self.assertFalse(child.allows('ledger', 'read', 'agent/brief-other'))
        empty = self.child_spec()
        empty['fs']['write'] = empty['fs']['execute'] = []
        empty['ledger']['write'] = []
        self.assertFalse(self.bounds.child(empty).allows('fs', 'execute', 'scripts:/smoke.py'))
        self.assertEqual(child.describe(), self.child_spec())

    def test_escalation_overlap_and_malformed_selectors_are_refused(self):
        for domain, action, value in [('fs', 'write', ['nimoi:/']), ('fs', 'execute', ['workspace:/']),
                                      ('ledger', 'write', ['/']), ('fs', 'read', ['nimoi:/../']),
                                      ('ledger', 'read', ['agent/*'])]:
            spec = self.child_spec()
            spec[domain][action] = value
            with self.subTest(domain=domain, action=action, value=value), self.assertRaises(ValueError):
                self.bounds.child(spec)
        spec = self.bounds.describe()
        spec['fs']['execute'] = ['nimoi:/workspace/']
        with self.assertRaisesRegex(ValueError, 'overlap'):
            Bounds(spec, self.bounds.mounts)
        spec = self.child_spec()
        spec['fs']['read'] = ['workspace:/child/']
        with self.assertRaisesRegex(ValueError, 'onboarding'):
            self.bounds.child(spec)

    def test_ledger_read_filter_covers_list_history_and_pinned_references(self):
        ref = self.note()
        self.note('agent/private', 'Do not expose this body')
        child = LedgerAccess(self.journal, self.bounds.child(self.child_spec()), 'child-author')
        self.assertEqual([e['name'] for e in child.list_entries()['entries']], ['agent/brief'])
        for operation in (lambda: child.read_entry('agent/private'), lambda: child.read_entry('agent/private', True),
                          lambda: child.resolve({'name': 'agent/private', 'id': ref['id']})):
            with self.assertRaises(ValueError):
                operation()
        revised = self.access.agent_write('agent/brief', 'later', ref['id'], [])
        self.assertNotEqual(child.resolve(ref).id, revised['id'])
        self.assertIn('Bar:', child.resolve(ref).body)

    def test_child_authorship_and_other_author_protection(self):
        child = LedgerAccess(self.journal, self.bounds.child(self.child_spec()), 'child-author')
        note = child.agent_write('agent/child/note', 'child text', None, [])
        self.assertEqual(self.journal.scribe.current(note['name']).author, 'child-author')
        with self.assertRaises(ValueError):
            self.access.agent_write(note['name'], 'parent rewrite', note['id'], [])
        with self.assertRaises(ValueError):
            child.agent_write('agent/parent', 'outside', None, [])

    def test_materialization_pins_body_and_checks_replacement_hash(self):
        ref = self.note(body='first body\nλ')
        self.access.agent_write(ref['name'], 'second body', ref['id'], [])
        args = {'source': ref, 'path': 'workspace:/drafts/a.py', 'expected_sha256': None}
        result = self.files.write(args)
        file = self.root / 'workspace/drafts/a.py'
        self.assertEqual(file.read_text(encoding='utf-8'), 'first body\nλ')
        with self.assertRaises(ValueError):
            self.files.write(args)
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.files.write({**args, 'expected_sha256': '0' * 64})
        self.assertEqual(file.read_text(encoding='utf-8'), 'first body\nλ')
        other = self.note('agent/new', 'replacement')
        self.files.write({**args, 'source': other, 'expected_sha256': result['sha256']})
        self.assertEqual(file.read_text(), 'replacement')

    def test_file_write_cannot_promote_escape_or_overwrite_hardlink(self):
        ref = self.note(body='print("draft")')
        for value in ('scripts:/smoke.py', 'workspace:/../scripts/smoke.py', 'nimoi:/outside/x', 'workspace:/x:stream'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.files.write({'source': ref, 'path': value, 'expected_sha256': None})
        source = self.root / 'outside/original.txt'
        source.write_text('outside')
        os.link(source, self.root / 'workspace/link.txt')
        with self.assertRaises(ValueError):
            self.files.write({'source': ref, 'path': 'workspace:/link.txt',
                              'expected_sha256': hashlib.sha256(b'outside').hexdigest()})
        self.assertEqual(source.read_text(), 'outside')

    def test_raw_ledger_cannot_bypass_ledger_bounds(self):
        self.note()
        with self.assertRaises(ValueError):
            self.files.read({'path': str(self.journal.path)})
        (self.root / 'outside/copy.ledger').write_text('fixture')
        with self.assertRaises(ValueError):
            self.files.read({'path': 'nimoi:/outside/copy.ledger'})

    def test_script_output_is_plain_protected_text_and_env_is_stripped(self):
        (self.root / 'scripts/smoke.py').write_text('import os, sys\nprint("42")\nprint(os.environ.get("NIMOI_TEST_SECRET", "absent"))\nprint(chr(955))\nprint("diagnostic", file=sys.stderr)\n')
        with patch.dict(os.environ, {'NIMOI_TEST_SECRET': 'synthetic-test-only'}):
            result = self.files.execute({'path': 'scripts:/smoke.py', 'output_name': 'agent/output'})
        self.assertEqual(result['status'], 'completed')
        entry = self.access.resolve({k: result['output'][k] for k in ('name', 'id')})
        self.assertEqual(entry.author, HARNESS_AUTHOR)
        self.assertEqual(entry.body.splitlines()[:2], ['42', 'absent'])
        self.assertEqual(entry.body.splitlines()[2], chr(955))
        self.assertIn('[stderr]\ndiagnostic', entry.body)
        with self.assertRaises(ValueError):
            self.access.agent_write(entry.name, 'replace evidence', entry.id, [])
        with self.assertRaises(ValueError):
            self.files.execute({'path': 'scripts:/smoke.py', 'output_name': 'agent/output'})

    def test_drafts_and_arbitrary_execute_options_are_refused(self):
        (self.root / 'workspace/draft.py').write_text('raise RuntimeError("should never run")')
        for args in ({'path': 'workspace:/draft.py', 'output_name': 'agent/no'},
                     {'path': 'scripts:/smoke.py', 'output_name': 'agent/no', 'code': 'print(1)'},
                     {'path': 'scripts:/smoke.py', 'output_name': 'agent/no', 'args': []}):
            with self.assertRaises(ValueError):
                self.files.execute(args)

    def test_script_nonzero_timeout_and_output_limit_keep_evidence(self):
        path = self.root / 'scripts/smoke.py'
        for index, (body, expected) in enumerate((('print("partial"); raise SystemExit(3)', 'failed'),
                                                ('import time; print("partial", flush=True); time.sleep(10)', 'timeout'),
                                                ('print("x" * 10000)', 'output_limit'))):
            path.write_text(body)
            self.files.timeout, self.files.output_limit = 0.3, 1000
            result = self.files.execute({'path': 'scripts:/smoke.py', 'output_name': f'agent/output-{index}'})
            self.assertEqual(result['status'], expected)
            entry = self.journal.scribe.current(result['output']['name'])
            self.assertIsInstance(entry.body, str)
            self.assertIn('protected', self.journal.scribe.labels(entry.name))

    def test_failed_record_prevents_file_and_script_side_effects(self):
        ref = self.note()
        with patch.object(scribe, '_write_all', side_effect=OSError('injected ledger failure')):
            with self.assertRaises(Exception):
                self.files.write({'source': ref, 'path': 'workspace:/not-created', 'expected_sha256': None})
        self.assertFalse((self.root / 'workspace/not-created').exists())
        self.assertTrue(self.journal.failed)
        self.assertTrue((self.journal.directory / 'lease.json').exists())
        (self.root / 'scripts/smoke.py').write_text('print(42)')
        with patch('capabilities.subprocess.Popen') as launch:
            with self.assertRaises(Exception):
                self.files.execute({'path': 'scripts:/smoke.py', 'output_name': 'agent/unrecorded'})
            launch.assert_not_called()

    def test_missing_file_is_a_tool_refusal_without_poisoning_the_session(self):
        for method in (self.files.read, self.files.list):
            with self.assertRaisesRegex(ValueError, 'Filesystem access failed'):
                method({'path': 'workspace:/missing'})
        self.assertFalse(self.journal.failed)
        with self.assertRaises(ValueError):
            self.files.read({'path': 'C:relative.txt'})

    def test_parallel_children_use_distinct_authors_and_parent_instruction_refs(self):
        ref = self.note()
        entered, release = threading.Event(), threading.Event()
        received = []
        def runner(journal, stop, tools, bounds, author, actor, model):
            received.append((actor, author, model, bounds.describe(), list(tools)))
            class FakeClient:
                def request(self, *args):
                    return {}
                def run_turn(self, text):
                    entered.set()
                    release.wait(2)
                    journal.write('send', {'id': 1, 'method': 'turn/start', 'params': {'input': [{'type': 'text', 'text': text}]}})
                    journal.write('receive', {'method': 'item/completed', 'params': {'turnId': 'same-turn',
                        'item': {'id': 'same-item', 'type': 'agentMessage', 'text': 'child response'}}})
                def close(self):
                    pass
            return FakeClient(), {'model': model, 'thread_id': actor}
        manager = Subagents(self.journal, self.stop, lambda rows: None, runner)
        manager.models = ['test-model']
        args = {'model': 'test-model', 'instructions': ref, 'bounds': self.child_spec()}
        one = manager.start(args, self.bounds, self.access)
        self.assertTrue(entered.wait(1))
        self.assertEqual(manager.status({'id': one['id']}, self.access)['status'], 'running')
        two = manager.start(args, self.bounds, self.access)
        with self.assertRaisesRegex(ValueError, 'two active'):
            manager.start(args, self.bounds, self.access)
        self.access.agent_write('agent/parent-progress', 'parent can work while children wait', None, [])
        release.set()
        for child in (one, two):
            status = manager.status({'id': child['id'], 'wait_seconds': 2}, self.access)
            self.assertEqual(status['status'], 'completed')
            self.assertEqual(self.access.resolve({k: status['results'][0][k] for k in ('name', 'id')}).author, child['author'])
        manager.join()
        self.assertEqual(len({row[1] for row in received}), 2)
        self.assertTrue(all('subagent_start' not in row[4] for row in received))
        self.assertEqual(self.journal.scribe.labelled('from-human'), [])
        sends = [self.journal.scribe.current(n).body for n in self.journal.scribe.labelled('log-send')]
        self.assertTrue(all(e['data']['params']['input'][0]['text'] == '[[agent/brief]]' for e in sends))
        self.journal.close()
        self.assertEqual(scribe.load(self.journal.root, 'test').findings, [])

    def test_subagent_start_refuses_unreadable_or_unowned_brief_and_unknown_model(self):
        manager = Subagents(self.journal, self.stop, lambda rows: None)
        manager.models = ['test-model']
        ref = self.note()
        for delta in ({'model': 'missing'}, {'instructions': self.note('agent/no-bar', 'no bar')},
                      {'bounds': {**self.child_spec(), 'ledger': {'read': [], 'write': []}}}):
            args = {'model': 'test-model', 'instructions': ref, 'bounds': self.child_spec(), **delta}
            with self.assertRaises(ValueError):
                manager.start(args, self.bounds, self.access)
        self.assertEqual(manager.snapshot(), [])

    def test_stop_cancels_child_and_closes_connection_before_return(self):
        entered, closed = threading.Event(), threading.Event()
        def runner(journal, stop, tools, bounds, author, actor, model):
            class FakeClient:
                def request(self, *args):
                    return {}
                def run_turn(self, text):
                    entered.set()
                    stop.wait(3)
                    raise SessionStopped()
                def close(self):
                    closed.set()
            return FakeClient(), {'model': model, 'thread_id': actor}
        manager = Subagents(self.journal, self.stop, lambda rows: None, runner)
        manager.models = ['test-model']
        child = manager.start({'model': 'test-model', 'instructions': self.note(), 'bounds': self.child_spec()}, self.bounds, self.access)
        self.assertTrue(entered.wait(1))
        manager.join()
        self.assertTrue(closed.is_set())
        self.assertEqual(manager.status({'id': child['id']}, self.access)['status'], 'cancelled')

    def test_child_record_failure_reports_failed_even_with_bodyless_message_tags(self):
        def runner(journal, stop, tools, bounds, author, actor, model):
            class FakeClient:
                def request(self, *args):
                    return {}
                def run_turn(self, text):
                    real_write = self.journal.scribe.write
                    def fail_message(name, body, **kwargs):
                        if isinstance(body, dict) and body.get('kind') == 'message':
                            with patch.object(scribe, '_write_all', side_effect=OSError('injected child envelope failure')):
                                return real_write(name, body, **kwargs)
                        return real_write(name, body, **kwargs)
                    with patch.object(self.journal.scribe, 'write', side_effect=fail_message):
                        journal.write('receive', {'method': 'item/completed', 'params': {'turnId': 'turn',
                            'item': {'id': 'reply', 'type': 'agentMessage', 'text': 'surviving child text'}}})
                def close(self):
                    pass
            client = FakeClient()
            client.journal = self.journal
            return client, {'model': model, 'thread_id': actor}
        manager = Subagents(self.journal, self.stop, lambda rows: None, runner)
        manager.models = ['test-model']
        child = manager.start({'model': 'test-model', 'instructions': self.note(), 'bounds': self.child_spec()}, self.bounds, self.access)
        result = manager.status({'id': child['id'], 'wait_seconds': 2}, self.access)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(self.stop.is_set())
        self.assertTrue(manager.jobs[child['id']].done.is_set())
        self.assertTrue(self.journal.failed)
        self.assertEqual(result['results'], [])
        self.assertTrue(any(self.journal.scribe.current(n) is None for n in self.journal.scribe.labelled('log-message')))
        self.assertTrue(any(self.journal.scribe.current(n).body == 'surviving child text' for n in self.journal.scribe.labelled('message-text')))
        manager.join()


if __name__ == '__main__':
    unittest.main()
