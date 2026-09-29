"""One human/governor conversation and its harness-owned worker family."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import queue
import threading
import uuid

from agent_tools import registry
from bounds import Bounds, root_bounds
from family import AgentRecord, Family
from ledger_store import AgentJournal, LedgerJournal, HARNESS_AUTHOR, HUMAN_AUTHOR, NIMOI, SCRIBE_PATH, scribe
from privacy import redact
from protocol import SessionStopped, TASK
from roles import GOVERNOR_MODEL
from runtime import runtime_for


class Conversation:
    def __init__(self, root=TASK, *, stream_mode='compact', bounds_path=None, runtime_factory=runtime_for):
        self.run_id = 'hybrid-' + datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz') + '-' + uuid.uuid4().hex[:8]
        self.root, self.lock = root, threading.RLock()
        self.stop_event = threading.Event()
        self.commands = queue.Queue()
        self.state = {'run_id': self.run_id, 'status': 'starting', 'model': GOVERNOR_MODEL,
                      'messages': [], 'tools': [], 'limits': {}, 'notices': [], 'error': None, 'revision': 0}
        self.workspace = root / 'workspace' / self.run_id
        self.workspace.mkdir(parents=True)
        self.ceiling = root_bounds(NIMOI, self.workspace, TASK / 'scripts', bounds_path)
        # Governance retains reads and ledger briefs, but workers alone get file
        # writes/execution. Delegation authority is explicitly the session ceiling.
        spec = self.ceiling.describe()
        spec['fs']['write'], spec['fs']['execute'] = [], []
        self.bounds = Bounds(spec, self.ceiling.mounts)
        self.journal = LedgerJournal(root / 'ledgers', self.run_id, self.observe, stream_mode)
        self.log_path = self.journal.path
        governor = AgentRecord('main', 'governor', None, f'{self.run_id}/governor', GOVERNOR_MODEL,
                               self.bounds, stop=self.stop_event)
        self.family = Family(self.journal, self.stop_event, governor, self.ceiling,
                             self.notify, self.publish, runtime_factory)
        self.tools = self.family.make_tools(governor)
        self.agent_journal = AgentJournal(self.journal, 'main', governor.author)
        self.agent = runtime_factory(governor, self.agent_journal, self.tools, self.ceiling)
        self.state.update(bounds=self.bounds.describe(), delegation_bounds=self.ceiling.describe(),
            workspace=str(self.workspace), ledger={'name': self.run_id, 'path': str(self.log_path),
                'harness_author': HARNESS_AUTHOR, 'human_author': HUMAN_AUTHOR}, tool_names=list(self.tools))
        self.worker = threading.Thread(target=self.run, name='hybrid-governor', daemon=True)

    def start(self):
        self.worker.start()

    def update(self, **changes):
        with self.lock:
            self.state.update(changes)
            self.state['revision'] += 1

    def publish(self):
        self.update()

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
        state['agents'] = self.family.snapshot()
        state['requests'] = self.family.request_snapshot()
        return state

    def submit(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError('Enter a message between 1 and 16,000 characters.')
        with self.lock:
            if self.state['status'] != 'ready' or self.stop_event.is_set():
                raise RuntimeError('The governor is not ready for another message.')
            self.state['status'] = 'busy'
            self.state['revision'] += 1
        text, mid = redact(text.strip()), uuid.uuid4().hex
        try:
            self.journal.write('user_message', {'id': mid, 'text': text})
        except Exception:
            self.stop_event.set()
            self.update(status='error', error='Message was not dispatched: ledger recording failed.')
            raise
        self.commands.put((text, mid, None))

    def notify(self, data):
        if self.stop_event.is_set():
            return
        text = 'Harness notification (not a new human instruction):\n' + json.dumps(data, ensure_ascii=False, indent=2)
        try:
            ref = self.journal.text_message(text, HARNESS_AUTHOR, 'main', source='family-notification')
            self.commands.put((ref['body'], None, ref))
        except Exception:
            self.stop_event.set()
            raise
        self.publish()

    def observe(self, event):
        actor, data, kind = event.actor, event.data, event.kind
        if kind == 'usage' and hasattr(self, 'family'):
            self.family.change(actor, usage=data)
        with self.lock:
            if kind == 'python_tool':
                self.state['tools'].append({**data, 'actor': actor})
            elif kind == 'python_tool_started':
                self.state['active_tool'] = {'actor': actor, 'tool': data['tool']}
            elif kind == 'restriction_caveat':
                self.state['notices'].append(f"{actor}: {data['note']}")
            elif kind in ('rate_limits', 'sdk_limits'):
                self.state['limits'][actor] = data
            elif kind == 'rate_limits_unavailable':
                self.state['notices'].append(f'{actor}: account limits unavailable.')
            elif kind == 'user_message':
                self.state['messages'].append({**data, 'role': 'user', 'actor': 'main', 'complete': True})
            elif kind in ('message_delta', 'message_completed'):
                message = next((m for m in self.state['messages'] if m['id'] == data['id'] and m['actor'] == actor), None)
                if message is None:
                    message = {'id': data['id'], 'actor': actor, 'role': 'assistant', 'text': '', 'complete': False}
                    self.state['messages'].append(message)
                if kind == 'message_delta':
                    message['text'] += data['text']
                else:
                    message.update(data)
            self.state['revision'] += 1

    def run(self):
        try:
            self.journal.write('session_start', {'run_id': self.run_id, 'task': 'task-6-hybrid',
                'bar': 'Useful prototype behavior, informative failures, surviving records.',
                'scribe': scribe.SCRIBE_ID, 'scribe_sha256': hashlib.sha256(SCRIBE_PATH.read_bytes()).hexdigest(),
                'governor': self.family.jobs['main'].snapshot(), 'ceiling': self.ceiling.describe(),
                'stream_mode': self.journal.stream_mode})
            info = self.agent.start()
            self.family.change('main', status='ready', info=info)
            self.update(status='ready')
            while not self.stop_event.is_set():
                try:
                    text, mid, instruction = self.commands.get_nowait()
                except queue.Empty:
                    self.agent.idle()
                    continue
                self.update(status='busy')
                self.family.change('main', status='running')
                self.agent_journal.instruction = instruction
                self.agent.run_turn(text, mid)
                if not self.tools.gate.complete:
                    raise RuntimeError('Governor finished without onboarding.')
                self.family.change('main', status='ready', results=self.agent_journal.results())
                self.update(status='ready')
        except SessionStopped:
            self.record_if_available('session_stop_requested', {})
        except Exception as error:
            self.update(status='error', error=redact(str(error)))
            self.record_if_available('session_failed', {'error': redact(str(error))})
        finally:
            self.stop_event.set()
            clean = True
            for close in (self.family.join, self.agent.close):
                try:
                    close()
                except Exception as error:
                    clean = False
                    self.update(status='error', error=redact(str(error)))
                    self.record_if_available('cleanup_failed', {'error': redact(str(error))})
            if self.state['status'] != 'error':
                self.update(status='stopped')
            self.family.change('main', status=self.state['status'])
            self.record_if_available('session_end', {'status': self.state['status'], 'cleanup_complete': clean})
            if clean and not self.journal.failed:
                self.journal.close()

    def record_if_available(self, kind, data):
        if not self.journal.failed:
            try:
                self.journal.write(kind, data)
            except Exception as error:
                self.stop_event.set()
                self.update(status='error', error=redact(str(error)))

    def stop(self):
        self.stop_event.set()
        self.update(status='stopping' if self.worker.is_alive() else self.state['status'])

    def wait(self):
        self.worker.join(60)
        if self.worker.is_alive():
            raise RuntimeError('Session did not stop; preserve its open ledger for review.')
