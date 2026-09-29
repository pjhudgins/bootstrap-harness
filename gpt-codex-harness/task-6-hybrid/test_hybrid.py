"""Offline checks of hierarchy, blocking, provenance and inherited boundaries."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from access import LedgerAccess
from bounds import Bounds
from family import AgentRecord, Family, StopSignal
from ledger_store import AgentJournal, LedgerJournal, HUMAN_AUTHOR, HARNESS_AUTHOR
from protocol import SessionStopped, TASK
from roles import GOVERNOR_MODEL, validate_model


def eventually(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for expected state')


class HybridTests(unittest.TestCase):
    def setUp(self):
        runtime = TASK / '.runtime'
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to(runtime.resolve())
        for name in ('origins', 'workspace', 'scripts'):
            (self.root / name).mkdir()
        (self.root / 'origins/onboarding_1.01.md').write_text('Bar: fixture.\nRead both pages.\n', encoding='utf-8')
        self.spec = {'fs': {'read': ['nimoi:/'], 'write': ['workspace:/'], 'execute': ['scripts:/']},
                     'ledger': {'read': ['/'], 'write': ['agent/']}}
        self.bounds = Bounds(self.spec, {'nimoi': self.root, 'workspace': self.root / 'workspace', 'scripts': self.root / 'scripts'})
        self.log = LedgerJournal(self.root / 'ledgers', 'fixture')
        self.stop = threading.Event()
        govspec = copy.deepcopy(self.spec)
        govspec['fs']['write'], govspec['fs']['execute'] = [], []
        self.gov = AgentRecord('main', 'governor', None, 'fixture/governor', GOVERNOR_MODEL,
                               Bounds(govspec, self.bounds.mounts), status='running', stop=self.stop)
        self.notifications = []
        self.request_workers = []
        self.family = Family(self.log, self.stop, self.gov, self.bounds, self.notifications.append,
                             lambda: None, self.fake_runtime)
        self.behaviors = {}

    def tearDown(self):
        self.stop.set()
        for worker in self.request_workers:
            worker.join(2)
        self.family.join()
        self.log.close()
        self.temp.cleanup()

    def fake_runtime(self, record, journal, tools, delegation_bounds):
        test = self
        class Runtime:
            def start(self):
                journal.write('agent_start', {'author': record.author, 'bounds': record.bounds.describe(),
                                             'requested_model': record.model})
                return {'model': record.model, 'backend': 'fake'}
            def run_turn(self, text):
                test.onboard(tools)
                test.behaviors.get(record.role, lambda *a: None)(record, tools)
                journal.write('agent_text', {'id': record.id + '-reply', 'text': record.id + ' done', 'complete': True})
            def close(self):
                pass
        return Runtime()

    def onboard(self, tools):
        tools['fs_read'][1]({'path': 'nimoi:/' + tools.gate.path, 'limit': 24000})

    def access(self, record):
        return LedgerAccess(self.log, record.bounds, record.author)

    def note(self, record, name, body):
        return self.access(record).agent_write(name, body, None, [])

    def ref(self, receipt):
        return {k: receipt[k] for k in ('name', 'id')}

    def owner(self):
        record = AgentRecord('owner-0001', 'owner', 'main', 'fixture/owner-0001', 'gpt-6-astra',
                             self.bounds, status='running', stop=StopSignal(self.stop))
        self.family.jobs[record.id] = record
        return record

    def start_request(self, needs_human=False):
        owner = self.owner()
        note = self.note(owner, 'agent/request', 'Requested action: use an approved label.\nJustification: human must authorize it.')
        tools = self.family.make_tools(owner)
        self.onboard(tools)
        replies, errors = [], []
        def wait():
            try:
                replies.append(tools['request_governor'][1]({'request': self.ref(note), 'needs_human': needs_human}))
            except SessionStopped:
                errors.append('cancelled')
        worker = threading.Thread(target=wait)
        worker.start()
        self.request_workers.append(worker)
        eventually(lambda: bool(self.family.requests))
        return owner, tools, worker, replies, errors

    def test_roles_and_model_floor(self):
        self.assertEqual(allowed := set(self.family.make_tools(self.gov)),
            {'fs_read', 'fs_list', 'ledger_read', 'ledger_list', 'ledger_write', 'task_start', 'agent_status', 'request_resolve'})
        self.assertNotIn('python_execute', allowed)
        owner = self.owner()
        self.assertIn('request_governor', self.family.make_tools(owner))
        child = AgentRecord('s', 'subagent', owner.id, 'child', 'claude-sonnet-5', self.bounds, stop=owner.stop)
        self.assertNotIn('subagent_start', self.family.make_tools(child))
        for role, model in [('governor', 'gpt-6-astra'), ('owner', 'claude-sonnet-5'), ('subagent', 'claude-haiku-4-5')]:
            with self.assertRaises(ValueError):
                validate_model(role, model)

    def test_onboarding_before_substantive_tools(self):
        tools = self.family.make_tools(self.gov)
        with self.assertRaisesRegex(ValueError, 'Read every page'):
            tools['ledger_write'][1]({'name': 'agent/note', 'body': 'x', 'prev': None, 'tags': []})
        self.onboard(tools)
        self.assertTrue(tools.gate.complete)

    def test_narrow_inventory_and_directory_listing(self):
        owner = self.owner()
        (self.root / 'private').mkdir()
        (self.root / 'private/hidden.txt').write_text('hidden', encoding='utf-8')
        spec = copy.deepcopy(self.spec)
        spec['fs'] = {'read': ['nimoi:/origins/', 'nimoi:/private'], 'write': [], 'execute': []}
        owner.bounds = self.bounds.child(spec)
        tools = self.family.make_tools(owner)
        self.assertNotIn('fs_write', tools)
        self.assertNotIn('python_execute', tools)
        result = tools['fs_list'][1]({'path': 'nimoi:/private'})
        self.assertEqual(result['entries'], [])
        self.assertEqual(result['excluded_count'], 0)

    def test_human_approval_blocks_until_recorded_decision(self):
        owner, tools, worker, replies, _ = self.start_request(True)
        self.assertTrue(worker.is_alive())
        self.assertEqual(owner.status, 'blocked')
        with self.assertRaisesRegex(ValueError, 'blocked'):
            tools['add'][1]({'a': 1, 'b': 2})
        decision = self.note(self.gov, 'agent/decision', 'Authorize the requested label only. No permission changes.')
        args = {'id': 'request-0001', 'decision': self.ref(decision), 'outcome': 'approved'}
        with self.assertRaisesRegex(ValueError, 'human approval'):
            self.family.resolve(args, self.access(self.gov))
        args['outcome'] = 'needs_human'
        self.family.resolve(args, self.access(self.gov))
        self.assertEqual(self.family.requests[args['id']]['status'], 'pending_human')
        self.assertEqual(replies, [])
        self.family.human_decision(args['id'], True, 'Approved scope only.')
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(replies[0]['outcome'], 'approved')
        self.assertFalse(replies[0]['bounds_changed'])
        self.assertEqual(owner.bounds.describe(), self.spec)
        self.assertEqual(owner.status, 'running')
        with self.assertRaises(ValueError):
            self.family.human_decision(args['id'], True)
        entries = [self.log.scribe.current(n) for n in self.log.scribe.names()]
        self.assertTrue(any(e.author == HUMAN_AUTHOR and isinstance(e.body, str) and 'Approved request' in e.body for e in entries if e))

    def test_governor_denial_releases_without_human(self):
        _, _, worker, replies, _ = self.start_request(True)
        note = self.note(self.gov, 'agent/decision', 'Denied; outside this assignment.')
        self.family.resolve({'id': 'request-0001', 'decision': self.ref(note), 'outcome': 'denied'}, self.access(self.gov))
        worker.join(2)
        self.assertEqual(replies[0]['outcome'], 'denied')

    def test_shutdown_cancels_waiting_owner(self):
        _, _, worker, replies, errors = self.start_request()
        self.stop.set()
        worker.join(2)
        self.assertEqual(errors, ['cancelled'])
        self.assertEqual(replies, [])
        self.assertEqual(self.family.requests['request-0001']['status'], 'cancelled')

    def test_human_endpoint_cannot_approve_unreviewed_request(self):
        self.start_request()
        with self.assertRaises(ValueError):
            self.family.human_decision('request-0001', True)

    def test_dispatch_checks_author_model_and_bounds(self):
        owner = self.owner()
        brief = self.note(owner, 'agent/wrong', 'Bar: informative.\nTest.')
        args = {'model': 'gpt-6-astra', 'instructions': self.ref(brief), 'bounds': self.spec}
        with self.assertRaisesRegex(ValueError, 'authored'):
            self.family.start(args, self.gov, self.access(self.gov))
        brief = self.note(self.gov, 'agent/right', 'Bar: informative.\nTest.')
        args['instructions'] = self.ref(brief)
        args['model'] = 'claude-sonnet-5'
        with self.assertRaisesRegex(ValueError, 'not permitted'):
            self.family.start(args, self.gov, self.access(self.gov))
        args['model'] = 'gpt-6-astra'
        args['bounds'] = copy.deepcopy(self.spec)
        args['bounds']['fs']['write'] = ['nimoi:/']
        with self.assertRaises(ValueError):
            self.family.start(args, self.gov, self.access(self.gov))

    def test_three_layers_and_pinned_results(self):
        def owner_work(record, tools):
            note = tools['ledger_write'][1]({'name': 'agent/sub-brief', 'body': 'Bar: fixture.\nAdd.', 'prev': None, 'tags': []})
            child = tools['subagent_start'][1]({'model': 'claude-sonnet-5', 'instructions': self.ref(note), 'bounds': self.spec})
            reply = tools['subagent_status'][1]({'id': child['id'], 'wait_seconds': 2})
            self.assertEqual(reply['agents'][0]['status'], 'completed')
            self.assertEqual(reply['agents'][0]['results'][0]['body'], child['id'] + ' done')
        self.behaviors['owner'] = owner_work
        brief = self.note(self.gov, 'agent/owner-brief', 'Bar: fixture.\nDelegate one child.')
        child = self.family.start({'model': 'gpt-6-astra', 'instructions': self.ref(brief), 'bounds': self.spec}, self.gov, self.access(self.gov))
        record = self.family.jobs[child['id']]
        self.assertTrue(record.done.wait(5))
        self.assertEqual(record.status, 'completed', record.error)
        self.assertEqual({r.role for r in self.family.jobs.values()}, {'governor', 'owner', 'subagent'})
        self.assertEqual(len(self.notifications), 1)
        self.assertEqual(record.results[0]['author'], record.author)

    def test_unrelated_owner_cannot_inspect_sibling(self):
        owner = self.owner()
        sibling = AgentRecord('owner-0002', 'owner', 'main', 'sibling', 'gpt-6-astra', self.bounds, stop=owner.stop)
        self.family.jobs[sibling.id] = sibling
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            self.family.status({'id': sibling.id}, owner)

    def test_bounds_are_immutable_and_write_execute_disjoint(self):
        with self.assertRaises(TypeError):
            self.bounds.grants['fs', 'write'] = ()
        spec = copy.deepcopy(self.spec)
        spec['fs']['execute'] = ['workspace:/']
        with self.assertRaisesRegex(ValueError, 'overlap'):
            Bounds(spec, self.bounds.mounts)

    def test_message_authorship_and_checkpoint(self):
        agent = AgentJournal(self.log, 'owner-a', 'actor-a')
        agent.write('agent_delta', {'id': 'text-1', 'text': 'unfinished'})
        agent.flush_pending(True)
        agent.write('agent_text', {'id': 'text-2', 'text': 'complete', 'complete': True})
        ref = agent.results()[0]
        self.assertEqual(self.log.scribe.current(ref['name']).body, 'complete')
        self.assertEqual(ref['author'], 'actor-a')


if __name__ == '__main__':
    unittest.main()
