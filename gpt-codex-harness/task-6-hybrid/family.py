"""Harness-owned hierarchy and durable, self-blocking governance requests."""
import copy
from dataclasses import asdict, dataclass, field
import threading

from access import LedgerAccess
from agent_tools import registry
from ledger_store import AgentJournal, HUMAN_AUTHOR
from privacy import redact
from protocol import SessionStopped
from roles import allowed_models, validate_model
from tool_support import args_only, integer, tool

TERMINAL = {'completed', 'failed', 'cancelled'}
STRING = {'type': 'string'}
REFERENCE = {'type': 'object', 'properties': {'name': STRING, 'id': STRING},
             'required': ['name', 'id'], 'additionalProperties': False}


def bounds_schema():
    def group(keys):
        return {'type': 'object', 'properties': {k: {'type': 'array', 'items': STRING, 'maxItems': 16} for k in keys},
                'required': list(keys), 'additionalProperties': False}
    return {'type': 'object', 'properties': {'fs': group(('read', 'write', 'execute')),
            'ledger': group(('read', 'write'))}, 'required': ['fs', 'ledger'], 'additionalProperties': False}


class StopSignal:
    def __init__(self, parent):
        self.parent, self.local = parent, threading.Event()
    def is_set(self):
        return self.local.is_set() or self.parent.is_set()
    def set(self):
        self.local.set()
    def wait(self, seconds):
        return self.local.wait(seconds) or self.parent.is_set()


@dataclass
class AgentRecord:
    id: str
    role: str
    parent: str | None
    author: str
    model: str
    bounds: object
    instructions: dict | None = None
    status: str = 'starting'
    usage: dict | None = None
    info: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    error: str | None = None
    worker: object = None
    stop: object = None
    tool_lock: object = field(default_factory=threading.RLock)
    done: threading.Event = field(default_factory=threading.Event)

    def snapshot(self):
        return copy.deepcopy({**{k: getattr(self, k) for k in
            ('id', 'role', 'parent', 'author', 'model', 'instructions', 'status', 'usage', 'info', 'results', 'error')},
            'bounds': self.bounds.describe()})


class Family:
    def __init__(self, journal, stop, governor, ceiling, notify, publish, runtime_factory):
        self.journal, self.stop, self.ceiling = journal, stop, ceiling
        self.notify, self.publish, self.runtime_factory = notify, publish, runtime_factory
        self.lock = threading.RLock()
        self.jobs = {'main': governor}
        self.requests = {}
        self.answers = {}
        self.cleanup_failed = False

    def snapshot(self):
        with self.lock:
            return [r.snapshot() for r in self.jobs.values()]

    def request_snapshot(self):
        with self.lock:
            return copy.deepcopy(list(self.requests.values()))

    def change(self, actor, **changes):
        with self.lock:
            for key, value in changes.items():
                setattr(self.jobs[actor], key, value)
        self.publish()

    def make_tools(self, record):
        access = LedgerAccess(self.journal, record.bounds, record.author)
        result = registry(self.journal, root=record.bounds.mounts['nimoi'], bounds=record.bounds,
                          author=record.author, actor=record.id, stop_event=record.stop)
        if record.role == 'governor':
            for name in ('add', 'fs_write', 'python_execute'):
                result.pop(name, None)
        for name, (spec, handler) in self.role_tools(record, access).items():
            result[name] = spec, result.wrap(name, handler)
        # SDKs can request concurrent tools. Once blocked, reject new calls rather
        # than letting another branch of the same owner continue its task.
        for name, (spec, handler) in list(result.items()):
            def guarded(arguments, handler=handler):
                with self.lock:
                    if record.stop.is_set() or self.journal.failed:
                        raise SessionStopped()
                    if record.status == 'blocked':
                        raise ValueError('This owner is blocked on its governor request.')
                with record.tool_lock:
                    with self.lock:
                        if record.stop.is_set() or record.status == 'blocked':
                            raise ValueError('Agent is blocked or stopping.')
                    return handler(arguments)
            result[name] = spec, guarded
        return result

    def role_tools(self, record, access):
        if record.role == 'subagent':
            return {}
        start_name = 'task_start' if record.role == 'governor' else 'subagent_start'
        status_name = 'agent_status' if record.role == 'governor' else 'subagent_status'
        child_role = 'owner' if record.role == 'governor' else 'subagent'
        tools = {
            start_name: tool(start_name, 'Start a parallel harness-owned ' + child_role +
                ' from your exact ledger brief (must contain Bar:) and subset bounds. Returns promptly.',
                {'model': {'type': 'string', 'enum': list(allowed_models(child_role))},
                 'instructions': REFERENCE, 'bounds': bounds_schema()}, ['model', 'instructions', 'bounds'],
                lambda a: self.start(a, record, access)),
            status_name: tool(status_name, 'Inspect your workers. Optional id; wait_seconds 0..20 for one worker. '
                'Results include exact text references and bodies explicitly reported to the supervisor.',
                {'id': STRING, 'wait_seconds': {'type': 'integer', 'minimum': 0, 'maximum': 20}}, [],
                lambda a: self.status(a, record)),
        }
        if record.role == 'owner':
            tools['request_governor'] = tool('request_governor',
                'Block yourself until governor resolution. First write a justified request in your own ledger '
                'and pass its exact reference. needs_human:true requires human approval for an affirmative answer. '
                'Approval never changes bounds. Do not dispatch parallel work alongside this call.',
                {'request': REFERENCE, 'needs_human': {'type': 'boolean'}}, ['request', 'needs_human'],
                lambda a: self.request(a, record, access))
        else:
            tools['request_resolve'] = tool('request_resolve',
                'Resolve a pending owner request from your own pinned decision entry. approved/denied releases '
                'the owner; needs_human displays this exact decision for human approval and keeps it blocked. '
                'A request marked needs_human cannot be approved by the governor alone.',
                {'id': STRING, 'decision': REFERENCE,
                 'outcome': {'type': 'string', 'enum': ['approved', 'denied', 'needs_human']}},
                ['id', 'decision', 'outcome'], lambda a: self.resolve(a, access))
        return tools

    def start(self, args, parent, access):
        args_only(args, ('model', 'instructions', 'bounds'), ('model', 'instructions', 'bounds'))
        role = 'owner' if parent.role == 'governor' else 'subagent'
        if parent.role not in ('governor', 'owner'):
            raise ValueError('Subagents cannot delegate.')
        validate_model(role, args['model'])
        bounds = (self.ceiling if role == 'owner' else parent.bounds).child(args['bounds'])
        brief = access.resolve(args['instructions'])
        if brief.author != parent.author or not any(line.startswith('Bar:') for line in brief.body.splitlines()):
            raise ValueError('Brief must be authored by the dispatcher and contain a Bar: line.')
        bounds.require('ledger', 'read', brief.name)
        with self.lock:
            same_role = [r for r in self.jobs.values() if r.role == role]
            peers = [r for r in same_role if r.parent == parent.id]
            if len(same_role) >= (6 if role == 'owner' else 12) or sum(r.status not in TERMINAL for r in peers) >= 2:
                raise ValueError('Worker limit reached: two active per supervisor, six owners/twelve subagents total.')
            if parent.stop.is_set() or parent.status in TERMINAL or parent.status == 'blocked':
                raise ValueError('Supervisor is not able to dispatch.')
            actor = f'{role}-{len(same_role) + 1:04d}'
            record = AgentRecord(actor, role, parent.id, f'{self.journal.directory.name}/{actor}',
                                 args['model'], bounds, {'name': brief.name, 'id': brief.id}, stop=StopSignal(parent.stop))
            record.worker = threading.Thread(target=self.run, args=(record, asdict(brief)), name=actor, daemon=True)
            self.journal.write('agent_spawned', record.snapshot(), actor=parent.id)
            self.jobs[actor] = record
            record.worker.start()
        self.publish()
        return {'id': actor, 'author': record.author, 'status': 'starting', 'bounds': bounds.describe()}

    def status(self, args, parent):
        args_only(args, ('id', 'wait_seconds'))
        wait = integer(args.get('wait_seconds', 0), 0, 20, 'wait_seconds')
        with self.lock:
            visible = [r for r in self.jobs.values() if r.parent == parent.id or
                       (parent.role == 'governor' and r.id != 'main')]
            if 'id' in args:
                visible = [r for r in visible if r.id == args['id']]
                if not visible:
                    raise ValueError('Unknown or unrelated worker id.')
        if wait and len(visible) == 1:
            visible[0].done.wait(wait)
        with self.lock:
            records = [r.snapshot() for r in visible]
        return {'agents': records}

    def run(self, record, instruction):
        journal = AgentJournal(self.journal, record.id, record.author, instruction)
        runtime, status, error = None, 'completed', None
        try:
            tools = self.make_tools(record)
            runtime = self.runtime_factory(record, journal, tools, record.bounds)
            info = runtime.start()
            self.change(record.id, status='running', info=info)
            runtime.run_turn(instruction['body'])
            if not tools.gate.complete:
                raise RuntimeError('Worker finished without completing onboarding.')
            with self.lock:
                live_children = [r for r in self.jobs.values() if r.parent == record.id and not r.done.is_set()]
            if live_children:
                raise RuntimeError('Owner ended before its subagents completed; cancelling its remaining children.')
        except SessionStopped:
            status = 'cancelled'
        except Exception as problem:
            status, error = 'failed', redact(str(problem))
        finally:
            record.stop.set()
            with self.lock:
                children = [r for r in self.jobs.values() if r.parent == record.id]
            for child in children:
                child.worker.join(25)
                if child.worker.is_alive():
                    self.stop.set()
                    status, error = 'failed', 'Child did not stop; family cleanup required.'
            try:
                if runtime:
                    runtime.close()
            except Exception as problem:
                status, error = 'failed', redact(str(problem))
                self.cleanup_failed = True
                self.stop.set()
            results = journal.results()
            # Reporting text to a supervisor is an explicit communication channel,
            # not arbitrary access to the worker's ledger grants.
            for ref in results:
                with self.journal.lock:
                    entry = next((e for e in self.journal.scribe.history(ref['name']) if e.id == ref['id']), None)
                    ref['body'] = entry.body if entry else None
            if self.journal.failed:
                self.stop.set()
                status, error = 'failed', self.journal.failure
            else:
                try:
                    journal.write('agent_finished', {'id': record.id, 'role': record.role,
                        'status': status, 'results': [{k: v for k, v in r.items() if k != 'body'} for r in results], 'error': error})
                except Exception as problem:
                    self.stop.set()
                    status, error = 'failed', redact(str(problem))
            self.change(record.id, status=status, error=error, results=results)
            record.done.set()
            if record.role == 'owner' and not self.stop.is_set():
                self.notify({'kind': 'owner_finished', 'id': record.id, 'status': status,
                    'error': error, 'results': [{k: v for k, v in r.items() if k != 'body'} for r in results]})

    def request(self, args, owner, access):
        args_only(args, ('request', 'needs_human'), ('request', 'needs_human'))
        if owner.role != 'owner' or type(args['needs_human']) is not bool:
            raise ValueError('Only task owners request governance; needs_human must be boolean.')
        entry = access.resolve(args['request'])
        if entry.author != owner.author or 'justification:' not in entry.body.lower():
            raise ValueError('Request must be owner-authored and contain Justification:.')
        with self.lock:
            if owner.status != 'running':
                raise ValueError('Owner already blocked or not running.')
            rid = f'request-{len(self.requests) + 1:04d}'
            request = {'id': rid, 'owner': owner.id, 'request': asdict(entry),
                       'needs_human': args['needs_human'], 'status': 'pending_governor', 'decision': None, 'answer': None}
            self.journal.write('governance_requested', {**request,
                'request': {'name': entry.name, 'id': entry.id, 'author': entry.author}}, actor=owner.id)
            self.requests[rid] = request
            event = self.answers[rid] = threading.Event()
            owner.status = 'blocked'
        self.publish()
        self.notify({'kind': 'governance_requested', 'id': rid, 'owner': owner.id,
                     'needs_human': args['needs_human'], 'request': {'name': entry.name, 'id': entry.id},
                     'body': entry.body})
        while not event.wait(.2):
            if owner.stop.is_set() or self.journal.failed:
                with self.lock:
                    if request['answer'] is None:
                        if not self.journal.failed:
                            self.journal.write('governance_cancelled', {'id': rid}, actor=owner.id)
                        request['status'] = 'cancelled'
                self.publish()
                raise SessionStopped()
        self.change(owner.id, status='running')
        return copy.deepcopy(request['answer'])

    def resolve(self, args, access):
        args_only(args, ('id', 'decision', 'outcome'), ('id', 'decision', 'outcome'))
        outcome = args['outcome']
        if outcome not in ('approved', 'denied', 'needs_human'):
            raise ValueError('Invalid resolution outcome.')
        decision = access.resolve(args['decision'])
        if decision.author != self.jobs['main'].author:
            raise ValueError('Decision must be governor-authored.')
        with self.lock:
            request = self.requests.get(args['id'])
            if not request or request['status'] != 'pending_governor':
                raise ValueError('Request is not pending governor resolution.')
            if request['needs_human'] and outcome == 'approved':
                raise ValueError('This affirmative decision requires human approval; use needs_human.')
            self.journal.write('governance_decided', {'id': args['id'], 'outcome': outcome,
                'decision': {'name': decision.name, 'id': decision.id, 'author': decision.author}})
            request['decision'] = asdict(decision)
            if outcome == 'needs_human':
                request['status'] = 'pending_human'
            else:
                self._answer(request, outcome, decision.body, decision.author)
        self.publish()
        return {'id': args['id'], 'status': request['status'], 'bounds_changed': False}

    def _answer(self, request, outcome, text, author):
        request['status'] = outcome
        request['answer'] = {'id': request['id'], 'outcome': outcome, 'text': text,
                             'author': author, 'governor_decision': copy.deepcopy(request['decision']),
                             'bounds_changed': False}
        self.answers[request['id']].set()

    def human_decision(self, rid, approved, comment=''):
        if type(approved) is not bool or not isinstance(comment, str) or len(comment) > 4000:
            raise ValueError('Expected approved:boolean and a comment up to 4,000 characters.')
        with self.lock:
            request = self.requests.get(rid)
            if (not request or request['status'] != 'pending_human' or self.stop.is_set()
                    or self.jobs[request['owner']].stop.is_set()):
                raise ValueError('Request is not waiting for human approval.')
            outcome = 'approved' if approved else 'denied'
            text = f'{outcome.capitalize()} {rid}.\n' + redact(comment)
            ref = self.journal.text_message(text, HUMAN_AUTHOR, 'main', role='human', source='approval-ui')
            self.journal.write('human_decision', {'id': rid, 'outcome': outcome,
                'decision': {k: ref[k] for k in ('name', 'id', 'author')}})
            self._answer(request, outcome, text, HUMAN_AUTHOR)
        self.publish()
        self.notify({'kind': 'human_decision', 'id': rid, 'outcome': outcome})

    def join(self):
        self.stop.set()
        with self.lock:
            workers = [r for r in self.jobs.values() if r.worker]
        for record in workers:
            record.worker.join(30)
            if record.worker.is_alive():
                raise RuntimeError(f'{record.id} did not stop; keep the ledger open for review.')
        if self.cleanup_failed:
            raise RuntimeError('A runtime cleanup failed; keep the ledger open for review.')
