"""Harness-owned, single-turn children sharing the same runtime as the parent."""

import copy
from dataclasses import asdict, dataclass, field
import threading

from agent_tools import registry
from tool_support import args_only, integer, tool
from ledger_store import AgentJournal, AGENT_AUTHOR
from privacy import redact
from protocol import SessionStopped
from runtime import AgentRuntime, start_client


@dataclass
class AgentRecord:
    id: str
    author: str
    model: str
    instructions: dict
    bounds: dict
    status: str = 'starting'
    thread_id: str | None = None
    usage: dict | None = None
    results: list = field(default_factory=list)
    error: str | None = None
    worker: object = field(default=None, repr=False)
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self):
        return copy.deepcopy({k: getattr(self, k) for k in (
            'id', 'author', 'model', 'instructions', 'bounds', 'status',
            'thread_id', 'usage', 'results', 'error')})


class Subagents:
    def __init__(self, journal, stop_event, publish, runner=start_client):
        self.journal, self.stop, self.publish, self.runner = journal, stop_event, publish, runner
        self.lock = threading.RLock()
        self.jobs = {}
        self.models = []

    def snapshot(self):
        with self.lock:
            return [record.snapshot() for record in self.jobs.values()]

    def change(self, child_id, **changes):
        with self.lock:
            for key, value in changes.items():
                setattr(self.jobs[child_id], key, value)
        self.publish(self.snapshot())

    def observe(self, event):
        if event.kind == 'usage':
            self.change(event.actor, usage=event.data)

    def start(self, arguments, bounds, access):
        args_only(arguments, ('model', 'instructions', 'bounds'), ('model', 'instructions', 'bounds'))
        model = arguments['model']
        if not isinstance(model, str) or model not in self.models:
            raise ValueError('Choose an available model from the injected catalog.')
        child_bounds = bounds.child(arguments['bounds'])
        instruction = access.resolve(arguments['instructions'])
        if instruction.author != access.author or not any(line.startswith('Bar:') for line in instruction.body.splitlines()):
            raise ValueError('The brief must be parent-authored and contain a Bar: line.')
        child_bounds.require('ledger', 'read', instruction.name)
        with self.lock:
            if self.stop.is_set() or self.journal.failed:
                raise ValueError('The session is stopping.')
            if len(self.jobs) >= 6 or sum(r.status in ('starting', 'running') for r in self.jobs.values()) >= 2:
                raise ValueError('Session limit: two active children and six total.')
            child_id = f'child-{len(self.jobs) + 1:04d}'
            record = AgentRecord(child_id, f'{AGENT_AUTHOR}-{child_id}', model,
                                 arguments['instructions'], child_bounds.describe())
            record.worker = threading.Thread(target=self.run,
                args=(record, child_bounds, asdict(instruction)), name=child_id, daemon=True)
            # Record acceptance before exposing or starting the worker. A failed
            # ledger write must not leave an accepted job without a terminal path.
            self.journal.write('subagent_started', record.snapshot())
            self.jobs[child_id] = record
            record.worker.start()
        self.publish(self.snapshot())
        return {'id': record.id, 'author': record.author, 'status': 'starting', 'bounds': record.bounds}

    def status(self, arguments, access):
        args_only(arguments, ('id', 'wait_seconds'), ('id',))
        wait = integer(arguments.get('wait_seconds', 0), 0, 20, 'wait_seconds')
        child_id = arguments['id']
        if not isinstance(child_id, str) or child_id not in self.jobs:
            raise ValueError('Unknown child id.')
        record = self.jobs[child_id]
        record.done.wait(wait)
        with self.lock:
            result = record.snapshot()
        result['results'] = [r for r in result['results'] if access.bounds.allows('ledger', 'read', r['name'])]
        return result

    def run(self, record, bounds, instruction):
        journal = AgentJournal(self.journal, record.id, record.author, instruction)
        runtime = None
        status, error = 'completed', None
        try:
            tools = registry(self.journal, root=bounds.mounts['nimoi'], bounds=bounds,
                             author=record.author, stop_event=self.stop, actor=record.id)
            runtime = AgentRuntime(journal, self.stop, tools, bounds, record.author,
                                   record.id, record.model, self.runner)
            info = runtime.start()
            self.change(record.id, status='running', thread_id=info['thread_id'], model=info['model'])
            runtime.run_turn(instruction['body'])
        except SessionStopped:
            status = 'cancelled'
        except Exception as problem:
            status, error = 'failed', redact(str(problem))
        finally:
            try:
                if runtime:
                    runtime.close()
            except Exception as problem:
                status, error = 'failed', redact(str(problem))
            if self.journal.failed:
                self.stop.set()
                status, error = 'failed', self.journal.failure
            # Completed envelopes populate this index as they are committed.
            # No ledger scan or protocol parsing is needed to collect a result.
            results = journal.results()
            if not self.journal.failed:
                try:
                    journal.write('subagent_finished', {'id': record.id, 'status': status, 'results': results, 'error': error})
                except Exception as problem:
                    status, error = 'failed', redact(str(problem))
                    self.stop.set()
            self.change(record.id, status=status, results=results, error=error)
            record.done.set()

    def join(self):
        self.stop.set()
        with self.lock:
            workers = [r.worker for r in self.jobs.values()]
        for worker in workers:
            worker.join(timeout=25)
            if worker.is_alive():
                raise RuntimeError('Child did not stop; shared ledger kept open for review.')

    def tools(self, bounds, access):
        strings = {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 16}
        def group(names):
            return {'type': 'object', 'properties': {n: strings for n in names},
                    'required': list(names), 'additionalProperties': False}
        schema = {'type': 'object', 'properties': {'fs': group(('read', 'write', 'execute')),
                                                  'ledger': group(('read', 'write'))},
                  'required': ['fs', 'ledger'], 'additionalProperties': False}
        reference = {'type': 'object', 'properties': {'name': {'type': 'string'}, 'id': {'type': 'string'}},
                     'required': ['name', 'id'], 'additionalProperties': False}
        return {
            'subagent_start': tool('subagent_start', 'Start a harness-owned parallel child with an available model, a parent-authored ledger brief {name,id} containing Bar:, and subset bounds. fs.read must include nimoi:/origins/ and ledger.read must include the brief. [] denies, trailing / selects subtree. Child runs one turn, no grandchildren; at most two active/six total.',
                {'model': {'type': 'string'}, 'instructions': reference, 'bounds': schema},
                ['model', 'instructions', 'bounds'], lambda a: self.start(a, bounds, access)),
            'subagent_status': tool('subagent_status', 'Inspect a child or wait up to 20 seconds for completion. Completed replies are exact ledger references; use ledger_read. Parent can work independently while child runs.',
                {'id': {'type': 'string'}, 'wait_seconds': {'type': 'integer', 'minimum': 0, 'maximum': 20}},
                ['id'], lambda a: self.status(a, access)),
        }
