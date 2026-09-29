"""Regression checks for the peer-review changes, with real files and scribe."""

from dataclasses import FrozenInstanceError
import json
import queue
import unittest
from unittest.mock import patch

from access import LedgerAccess
from agent_tools import ReadFiles, registry
from bounds import Bounds, root_bounds
from capabilities import FileCapabilities
from inspect_ledger import inspect
from ledger_store import AGENT_AUTHOR, AgentJournal, LedgerJournal
from policy import call_identity, needs_exec, permitted_call
from protocol import Client
import test_task5 as fixtures


class Improvements(unittest.TestCase):
    setUp = fixtures.Task5Tests.setUp
    note = fixtures.Task5Tests.note
    child_spec = fixtures.Task5Tests.child_spec

    def test_directory_contents_are_filtered_before_paging_and_counts(self):
        for name in ('a-hidden', 'b-visible', 'c-hidden', 'd-visible'):
            (self.root / 'workspace' / name).write_text(name, encoding='utf-8')
        spec = self.child_spec()
        spec['fs']['read'] = ['nimoi:/origins/', 'workspace:/b-visible', 'workspace:/d-visible', 'nimoi:/workspace']
        bounds = self.bounds.child(spec)
        files = FileCapabilities(ReadFiles(self.root), bounds, self.access, self.stop, 'child')
        first = files.list({'path': 'workspace:/', 'limit': 1})
        self.assertEqual([e['name'] for e in first['entries']], ['b-visible'])
        self.assertEqual(first['next_offset'], 1)
        self.assertEqual(first['excluded_count'], 0)
        second = files.list({'path': 'workspace:/', 'offset': 1, 'limit': 1})
        self.assertEqual([e['name'] for e in second['entries']], ['d-visible'])
        self.assertIsNone(second['next_offset'])
        spec['fs']['read'] = ['nimoi:/origins/', 'nimoi:/workspace']
        files.bounds = self.bounds.child(spec)
        self.assertEqual(files.list({'path': 'workspace:/', 'limit': 1})['entries'], [])

    def test_bounds_are_immutable_and_config_can_only_narrow(self):
        with self.assertRaises(FrozenInstanceError):
            self.bounds.selectors = ()
        with self.assertRaises(TypeError):
            self.bounds.grants['fs', 'read'] = ()
        snapshot = self.bounds.describe()
        snapshot['fs']['read'].clear()
        self.assertTrue(self.bounds.allows('fs', 'read', 'nimoi:/origins/'))
        path = self.root / 'bounds.json'
        spec = self.bounds.describe()
        spec['fs']['execute'] = []
        path.write_text(json.dumps(spec), encoding='utf-8')
        changed = root_bounds(self.root, self.root / 'workspace', self.root / 'scripts', path)
        self.assertFalse(changed.allows('fs', 'execute', 'scripts:/smoke.py'))
        spec['fs']['write'] = ['nimoi:/']
        path.write_text(json.dumps(spec), encoding='utf-8')
        with self.assertRaises(ValueError):
            root_bounds(self.root, self.root / 'workspace', self.root / 'scripts', path)

    def test_inventory_follows_bounds_and_gate_requires_contiguous_complete_read(self):
        spec = self.child_spec()
        spec['fs']['write'] = spec['fs']['execute'] = spec['ledger']['write'] = []
        tools = registry(self.journal, root=self.root, bounds=self.bounds.child(spec), author='child')
        self.assertTrue({'fs_write', 'python_execute', 'ledger_write', 'subagent_start'}.isdisjoint(tools))
        with self.assertRaisesRegex(ValueError, 'every page'):
            tools['add'][1]({'a': 1, 'b': 2})
        path = 'nimoi:/origins/onboarding_1.00.md'
        tools['fs_read'][1]({'path': path, 'offset': 5})
        self.assertFalse(tools.gate.complete)
        first = tools['fs_read'][1]({'path': path, 'limit': 5})
        self.assertFalse(tools.gate.complete)
        tools['fs_read'][1]({'path': path, 'offset': first['next_offset']})
        self.assertTrue(tools.gate.complete)
        self.assertEqual(tools['add'][1]({'a': 1, 'b': 2}), {'sum': 3})
        self.assertEqual(len(self.journal.scribe.labelled('log-onboarding_complete')), 1)

    def test_onboarding_edit_between_pages_invalidates_prior_progress(self):
        tools = registry(self.journal, root=self.root, bounds=self.bounds, author=AGENT_AUTHOR)
        path = 'nimoi:/origins/onboarding_1.00.md'
        tools['fs_read'][1]({'path': path, 'limit': 5})
        (self.root / 'origins/onboarding_1.00.md').write_text('replacement onboarding', encoding='utf-8')
        tools['fs_read'][1]({'path': path, 'offset': 5})
        self.assertFalse(tools.gate.complete)
        tools['fs_read'][1]({'path': path})
        self.assertTrue(tools.gate.complete)

    def test_compact_stream_checkpoints_preserve_authors_and_do_not_claim_completion(self):
        events = []
        j = LedgerJournal(self.root / 'compact', 'chat', events.append, stream_mode='compact')
        self.addCleanup(j.close)
        child = AgentJournal(j, 'child', 'child-author')
        for part in ('unfinished ', 'child reply'):
            child.write('receive', {'method': 'item/agentMessage/delta', 'params': {
                'turnId': 'turn', 'itemId': 'a', 'delta': part}})
        self.assertEqual(j.scribe.labelled('message-fragment'), [])
        self.assertEqual([e.kind for e in events], ['message_delta', 'message_delta'])
        child.write('session_failed', {'error': 'simulated interruption'})
        fragments = [j.scribe.current(n) for n in j.scribe.labelled('message-fragment')]
        self.assertEqual([(e.body, e.author) for e in fragments], [('unfinished child reply', 'child-author')])
        self.assertEqual(j.results_for('child'), [])
        self.assertEqual(j.scribe.labelled('log-message'), [])
        j.close()
        self.assertEqual(inspect(j.root, 'chat', closed=True)['problems'], [])

    def test_compact_size_and_idle_checkpoints_and_completed_result_index(self):
        j = LedgerJournal(self.root / 'compact', 'chat', stream_mode='compact')
        self.addCleanup(j.close)
        delta = lambda text: {'method': 'item/agentMessage/delta', 'params': {'itemId': 'a', 'turnId': 't', 'delta': text}}
        with patch('ledger_store.time.monotonic', return_value=0):
            j.write('receive', delta('x' * 2048))
            j.write('receive', delta('ending'))
        self.assertEqual(len(j.scribe.labelled('message-fragment')), 1)
        with patch('ledger_store.time.monotonic', return_value=2):
            j.flush_pending()
        self.assertEqual(len(j.scribe.labelled('message-fragment')), 2)
        j.write('receive', {'method': 'item/completed', 'params': {'turnId': 't', 'item': {
            'type': 'agentMessage', 'id': 'a', 'text': 'x' * 2048 + 'ending'}}})
        with patch.object(j.scribe, 'labelled', side_effect=AssertionError('Do not rescan ledger')):
            self.assertEqual(len(j.results_for('main')), 1)

    def test_raw_call_monitor_stops_unpermitted_native_delegation(self):
        client = Client.__new__(Client)
        client.journal, client.human_message_id = self.journal, None
        client.methods, client.tools, client.queue = {}, {'add': None}, queue.Queue()
        client.stop_event, client.items, client.turns, client.usage = self.stop, [], {}, []
        client.queue.put(('stdout', json.dumps({'method': 'rawResponseItem/completed', 'params': {
            'item': {'type': 'function_call', 'namespace': 'collaboration', 'name': 'spawn_agent', 'arguments': '{}'}}})))
        with self.assertRaisesRegex(RuntimeError, 'Detection is not prevention'):
            client.receive()
        self.assertTrue(self.stop.is_set())
        event = self.journal.scribe.current(self.journal.scribe.labelled('log-model_call')[0]).body
        self.assertFalse(event['data']['permitted'])

    def test_raw_model_text_and_completed_message_share_authored_body(self):
        self.journal.write('receive', {'method': 'rawResponseItem/completed', 'params': {
            'turnId': 't', 'item': {'type': 'message', 'role': 'assistant', 'id': 'reply',
                                  'content': [{'type': 'output_text', 'text': 'an authored reply'}]}}})
        self.journal.write('receive', {'method': 'item/completed', 'params': {
            'turnId': 't', 'item': {'type': 'agentMessage', 'id': 'reply', 'text': 'an authored reply'}}})
        self.assertEqual(len(self.journal.scribe.labelled('message-text')), 1)
        for name in self.journal.scribe.labelled('log-receive'):
            self.assertNotIn('an authored reply', json.dumps(self.journal.scribe.current(name).body))
        self.assertEqual(len(self.journal.results_for('main')), 1)

    def test_exec_policy_and_approval_refusal(self):
        self.assertTrue(needs_exec({'tool_mode': 'code_mode_only'}))
        self.assertFalse(needs_exec({'tool_mode': None}))
        self.assertTrue(permitted_call('functions.exec', {}))
        self.assertFalse(permitted_call('functions.exec_command', {}))
        self.assertFalse(permitted_call('collaboration.spawn_agent', {}))
        self.assertEqual(call_identity({'type': 'custom_tool_call', 'namespace': 'functions', 'name': 'exec'}), 'functions.exec')
        replies = []
        client = Client.__new__(Client)
        client.journal, client.send = self.journal, replies.append
        client.handle_request({'id': 'approval', 'method': 'item/commandExecution/requestApproval'})
        self.assertEqual(replies, [{'id': 'approval', 'result': {'decision': 'decline'}}])

    def test_inspector_reports_bad_links_and_missing_child_terminal_records(self):
        self.journal.write('user_message', {'id': 'human', 'text': 'hello'})
        text = self.journal.scribe.current(self.journal.scribe.labelled('message-text')[0])
        self.journal._event('message', {'text': '[[wrong/name]]', 'text_id': text.id,
            'author': text.author, 'direction': 'from-user'})
        self.journal.write('subagent_started', {'id': 'unfinished', 'instructions': {'name': 'agent/x', 'id': text.id}})
        self.journal.close()
        result = inspect(self.journal.root, 'test', closed=True)
        self.assertTrue(any('Invalid text reference' in p for p in result['problems']))
        self.assertTrue(any('no terminal record' in p for p in result['problems']))


if __name__ == '__main__':
    unittest.main()
