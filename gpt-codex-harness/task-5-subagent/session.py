"""One App Server owner thread and one conversation for each application launch."""

import copy
from datetime import datetime, timezone
import hashlib
import queue
import threading
import uuid
from functools import partial

from protocol import SessionStopped, TASK, redact
from ledger_store import AGENT_AUTHOR, HARNESS_AUTHOR, HUMAN_AUTHOR, NIMOI, AgentJournal, LedgerJournal, SCRIBE_PATH, scribe
from agent_tools import registry
from bounds import root_bounds
from subagents import Subagents
from runtime import AgentRuntime, RuntimeOptions, start_client


class Conversation:
    def __init__(self, root=TASK, *, model=None, stream_mode='compact', bounds_path=None, runner=None):
        self.run_id = "chat-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz") + "-" + uuid.uuid4().hex[:8]
        self.root = root
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.commands = queue.Queue()
        self.state = {"run_id": self.run_id, "status": "starting", "thread_id": None,
                      "model": None, "messages": [], "tools": [], "usage": None,
                      "limits": {}, "children": [], "error": None, "notices": [], "revision": 0}
        self.workspace = self.root / "workspace" / self.run_id
        self.workspace.mkdir(parents=True)
        self.bounds = root_bounds(NIMOI, self.workspace, TASK / "scripts", bounds_path)
        self.journal = LedgerJournal(root / "ledgers", self.run_id, self.observe, stream_mode)
        self.log_path = self.journal.path
        self.runner = runner or partial(start_client, options=RuntimeOptions(stream_mode=stream_mode))
        self.manager = Subagents(self.journal, self.stop_event, lambda children: self.update(children=children), self.runner)
        self.tools = registry(self.journal, bounds=self.bounds, author=AGENT_AUTHOR,
                              stop_event=self.stop_event, manager=self.manager)
        self.agent = AgentRuntime(AgentJournal(self.journal, 'main', AGENT_AUTHOR), self.stop_event,
                                  self.tools, self.bounds, AGENT_AUTHOR, model=model, runner=self.runner)
        self.state["bounds"] = self.bounds.describe()
        self.state["workspace"] = str(self.workspace)
        self.state["ledger"] = {"name": self.run_id, "path": str(self.log_path),
                                "agent_author": AGENT_AUTHOR, "harness_author": HARNESS_AUTHOR,
                                "human_author": HUMAN_AUTHOR}
        self.state["tool_names"] = list(self.tools)
        self.worker = threading.Thread(target=self.run, name="codex-conversation", daemon=True)

    def start(self):
        self.worker.start()

    def update(self, **changes):
        with self.lock:
            self.state.update(changes)
            self.state["revision"] += 1

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def submit(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise ValueError("Enter a message between 1 and 16,000 characters.")
        with self.lock:
            if self.state["status"] != "ready":
                raise RuntimeError("The conversation is not ready for another message.")
            self.state["status"] = "busy"
            self.state["revision"] += 1
        # Log before dispatch; this also updates the UI projection. Redact recognizable
        # credential forms before they can become a retained conversation message.
        prompt = redact(prompt.strip())
        message_id = uuid.uuid4().hex
        try:
            self.journal.write("user_message", {"id": message_id, "text": prompt})
        except Exception as error:
            self.update(status="error", error=redact(str(error)))
            self.stop_event.set()
            raise RuntimeError("Message was not dispatched: ledger recording failed.") from error
        self.commands.put((message_id, prompt))

    def observe(self, event):
        kind, data, actor = event.kind, event.data, event.actor
        if actor != 'main':
            self.manager.observe(event)
        with self.lock:
            if kind == 'python_tool':
                self.state['tools'].append({**data, 'actor': actor})
            elif kind == 'restriction_caveat':
                note = f"{actor}: {data['note']}"
                if note not in self.state['notices']:
                    self.state['notices'].append(note)
            elif kind == 'rate_limits_unavailable':
                notice = 'Account limits could not be refreshed; shown values may be stale.'
                if notice not in self.state['notices']:
                    self.state['notices'].append(notice)
            elif kind == 'rate_limits':
                if data.get('rateLimitsByLimitId'):
                    self.state['limits'] = data['rateLimitsByLimitId']
                elif data.get('rateLimits'):
                    bucket = data['rateLimits']
                    self.state['limits'][bucket.get('limitId') or 'codex'] = bucket
            elif actor != 'main':
                return
            elif kind == 'user_message':
                self.state['messages'].append({**data, 'role': 'user', 'complete': True})
            elif kind == 'message_delta':
                self.agent_message(data['id'])['text'] += data['text']
            elif kind == 'message_completed':
                self.agent_message(data['id']).update(data)
            elif kind == 'usage':
                self.state['usage'] = data
            else:
                return
            self.state['revision'] += 1

    def agent_message(self, item_id):
        # Called only while self.lock is held.
        for message in self.state["messages"]:
            if message["id"] == item_id:
                return message
        message = {"id": item_id, "role": "assistant", "text": "", "complete": False}
        self.state["messages"].append(message)
        return message

    def read_limits(self, client):
        # Kept as a small adapter for callers supplying a connection explicitly.
        agent = AgentRuntime(AgentJournal(self.journal, 'main', AGENT_AUTHOR), self.stop_event,
                             self.tools, self.bounds, AGENT_AUTHOR)
        agent.client = client
        agent.read_limits()

    def run(self):
        try:
            self.journal.write("session_start", {"run_id": self.run_id,
                "scribe": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION,
                "scribe_sha256": hashlib.sha256(SCRIBE_PATH.read_bytes()).hexdigest(),
                "agent_author": AGENT_AUTHOR, "harness_author": HARNESS_AUTHOR,
                "human_author": HUMAN_AUTHOR, "message_storage": "authored-text-links-v2", "stream_mode": self.journal.stream_mode,
                "bounds": self.bounds.describe(), "workspace": str(self.workspace)})
            info = self.agent.start()
            self.manager.models = info["models"]
            self.update(**info)
            self.update(status="ready")
            while not self.stop_event.is_set():
                try:
                    message_id, prompt = self.commands.get_nowait()
                except queue.Empty:
                    self.agent.client.receive(idle=True)
                    continue
                self.agent.run_turn(prompt, message_id)
                self.update(status="ready")
        except SessionStopped:
            self.record_if_available("session_stop_requested", {"reason": "Stop requested."})
        except Exception as error:
            message = redact(str(error))
            self.update(status="error", error=message)
            self.record_if_available("session_failed", {"type": type(error).__name__, "error": message})
        finally:
            self.stop_event.set()
            children_stopped = True
            try:
                self.manager.join()
            except Exception as error:
                children_stopped = False
                self.update(status="error", error=str(error))
                self.record_if_available("cleanup_failed", {"error": str(error)})
            try:
                self.agent.close()
            except Exception as error:
                self.update(status="error", error=redact(str(error)))
                self.record_if_available("cleanup_failed", {"error": str(error)})
            if self.snapshot()["status"] != "error":
                self.update(status="stopped")
            self.record_if_available("session_end", {"status": self.snapshot()["status"]})
            try:
                if children_stopped:
                    self.journal.close()
            except Exception as error:
                self.update(status="error", error=redact(str(error)))

    def record_if_available(self, kind, data):
        # A dead scribe cannot record its own failure. Leave its partial file and
        # lease intact, surface the failure in UI, and do not create another log.
        if not self.journal.failed:
            try:
                self.journal.write(kind, data)
            except Exception as error:
                self.update(status="error", error=redact(str(error)))

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.worker.is_alive() and self.state["status"] in ("starting", "ready", "busy"):
                self.state["status"] = "stopping"
                self.state["revision"] += 1

    def wait(self):
        self.worker.join(timeout=60)
