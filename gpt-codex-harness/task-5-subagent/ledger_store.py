"""One scribe, attested authors, protected tags, and no secondary log files."""

from dataclasses import asdict
import copy
import importlib.util
from pathlib import Path
import re
import sys
import threading
import time

from events import MessageRecorder, normalize

from privacy import redact


NIMOI = Path(__file__).resolve().parents[3]
SCRIBE_PATH = NIMOI / "bootstrap-ledger" / "python-scribe" / "scribe.py"
# Load this exact sibling module without copying it or searching site-packages.
spec = importlib.util.spec_from_file_location("nimoi_bootstrap_scribe", SCRIBE_PATH)
scribe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scribe
previous_bytecode_setting = sys.dont_write_bytecode
try:
    sys.dont_write_bytecode = True  # Do not create/update caches in the sibling project.
    spec.loader.exec_module(scribe)
finally:
    sys.dont_write_bytecode = previous_bytecode_setting

HARNESS_AUTHOR = "gpt-codex-harness"
AGENT_AUTHOR = "gpt-codex-test-pilot"
HUMAN_AUTHOR = "human-user-of-session"
AGENT_NAME = re.compile(r"agent/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
AGENT_TAG = re.compile(r"agent-[A-Za-z0-9][A-Za-z0-9._-]{0,56}")


def log_tags(kind, data):
    tags = {"harness", "log-" + kind}
    if kind in ("send", "receive", "shutdown_receive"):
        tags.add("protocol")
    method = data.get("method", "") if isinstance(data, dict) else ""
    item = data.get("params", {}).get("item", {}) if isinstance(data, dict) else {}
    if kind in ("message", "user_message") or method == "turn/start" or "agentMessage" in method or item.get("type") in ("agentMessage", "userMessage"):
        tags.add("message")
    if kind == "python_tool" or method == "item/tool/call" or item.get("type") == "dynamicToolCall":
        tags.add("tool")
    if "tokenUsage" in method:
        tags.add("usage")
    if kind.startswith("rate_limits") or "rateLimits" in method:
        tags.add("account-limits")
    if "failed" in kind or "error" in kind or kind in ("stderr", "shutdown_stderr"):
        tags.add("error")
    if kind.startswith("session_") or kind in ("preflight", "server_exit"):
        tags.add("lifecycle")
    if kind == "restriction_caveat":
        tags.add("restriction")
    return sorted(tags)


class LedgerUnavailable(RuntimeError):
    pass


class LedgerJournal:
    def __init__(self, root, run_id, observer=None, stream_mode="detailed"):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Reserve the directory first. Even a UUID collision cannot resume a ledger.
        self.directory = self.root / run_id
        self.directory.mkdir()
        self.lock = threading.RLock()
        self.sequence = 0
        self.text_sequence = 0
        self.text_refs = {}
        self.message_refs = set()
        if stream_mode not in ('compact', 'detailed'):
            raise ValueError('stream_mode must be compact or detailed.')
        self.stream_mode = stream_mode
        self.pending = {}
        self.result_refs = {}
        self.messages = MessageRecorder(self)
        self.observer = observer
        self.failed = False
        self.failure = None
        self.scribe = scribe.Scribe.open(self.root, run_id,
                                        session_author=HARNESS_AUTHOR)
        self.path = self.directory / (self.scribe.session + ".ledger")

    def _call(self, function, *args, **kwargs):
        if self.failed:
            raise LedgerUnavailable(self.failure)
        try:
            return function(*args, **kwargs)
        except scribe.Refused as error:
            raise ValueError(str(error)) from None
        except scribe.ScribeError as error:
            self.failed = True
            self.failure = f"Ledger unavailable ({error.code}); session stopped. Preserve the ledger and lease for review."
            raise LedgerUnavailable(self.failure) from error

    def _event(self, kind, data, actor="main"):
        """Caller holds lock. Text dependencies must already be durable."""
        self.sequence += 1
        name = f"harness/events/{self.sequence:08d}"
        for tag in log_tags(kind, data):
            self._call(self.scribe.tag, name, tag, author=HARNESS_AUTHOR)
        return self._call(self.scribe.write, name,
            {"sequence": self.sequence, "kind": kind, "actor": actor, "data": data}, author=HARNESS_AUTHOR)

    def _text(self, text, role, key=None, fragment=False, author=None):
        """Immutable text entry; cache by provenance and text, never text alone."""
        author = author or (HUMAN_AUTHOR if role == "human" else AGENT_AUTHOR)
        cache_key = (author, role, key, text) if key is not None else None
        if cache_key in self.text_refs:
            return self.text_refs[cache_key]
        self.text_sequence += 1
        group = "fragments" if fragment else role
        name = f"messages/{group}/{self.text_sequence:08d}"
        # Tags are assigned by the harness; body author is the attested speaker.
        for tag in ("transcript", "protected", "message-fragment" if fragment else "message-text", "from-" + role):
            self._call(self.scribe.tag, name, tag, author=HARNESS_AUTHOR)
        entry_id = self._call(self.scribe.write, name, text, author=author)
        ref = {"text": f"[[{name}]]", "text_id": entry_id, "author": author}
        if cache_key is not None:
            self.text_refs[cache_key] = ref
        return ref

    def _message(self, ref, role, actor="main", **metadata):
        if (actor, ref["text_id"]) not in self.message_refs:
            self._event("message", {**metadata, **ref,
                "direction": "from-user" if role == "human" else ("to-agent" if role == "instruction" else "to-user" if actor == "main" else "to-parent"),
                "complete": True}, actor)
            self.message_refs.add((actor, ref["text_id"]))
            if role == 'agent':
                self.result_refs.setdefault(actor, []).append({
                    'name': ref['text'][2:-2], 'id': ref['text_id'], 'author': ref['author']})

    def write(self, kind, data, *, human_id=None, actor="main", agent_author=AGENT_AUTHOR, instruction=None):
        data = redact(data)
        with self.lock:
            stored = copy.deepcopy(data)
            self.messages.transform(kind, stored, human_id, actor, agent_author, instruction)
            delta = isinstance(data, dict) and data.get('method') == 'item/agentMessage/delta'
            force = kind not in ('send', 'receive') or (isinstance(data, dict) and data.get('method') in ('item/completed', 'turn/completed'))
            self.flush_pending(force=force)
            entry_id = None if delta and self.stream_mode == 'compact' else self._event(kind, stored, actor)
        event = normalize(kind, data, actor)
        if self.observer and event:
            # Compact-mode deltas are provisional until their checkpoint is written.
            self.observer(event)
        return entry_id

    def results_for(self, actor):
        with self.lock:
            return copy.deepcopy(self.result_refs.get(actor, []))

    def fragment(self, params, actor, author):
        if self.stream_mode == 'detailed':
            return self._text(params['delta'], 'agent', fragment=True, author=author)['text']
        key = (actor, params.get('turnId'), params['itemId'])
        pending = self.pending.setdefault(key, {'text': '', 'since': time.monotonic(), 'author': author})
        pending['text'] += params['delta']
        return '[stream checkpoint pending]'

    def flush_pending(self, force=False):
        with self.lock:
            if self.failed:
                return
            now = time.monotonic()
            for key, pending in list(self.pending.items()):
                if not force and len(pending['text']) < 2048 and now - pending['since'] < 1:
                    continue
                actor, turn, item = key
                ref = self._text(redact(pending['text']), 'agent', fragment=True, author=pending['author'])
                self._event('message_checkpoint', {**ref, 'turn_id': turn, 'item_id': item, 'complete': False}, actor)
                del self.pending[key]

    def bytes(self):
        with self.lock:
            return self.path.read_bytes()

    def _entry(self, name):
        entry = self.scribe.current(name)
        return None if entry is None else {**asdict(entry), "tags": sorted(self.scribe.labels(name))}

    def list_entries(self, tag=None, prefix="", offset=0, limit=50):
        with self.lock:
            names = self.scribe.labelled(tag) if tag else self.scribe.names(deleted=True)
            names = [name for name in names if name.startswith(prefix)]
            selected = names[offset:offset + limit]
            entries = []
            for name in selected:
                entry = self._entry(name)
                entries.append({key: entry[key] for key in ("id", "name", "author", "tags")}
                               if entry else {"name": name, "id": None, "tags": sorted(self.scribe.labels(name))})
            return {"ledger": self.scribe.ledger, "entries": entries,
                    "next_offset": offset + limit if len(names) > offset + limit else None}

    def read_entry(self, name, history=False):
        with self.lock:
            if history:
                return {"ledger": self.scribe.ledger, "history": [asdict(e) for e in self.scribe.history(name)],
                        "tags": sorted(self.scribe.labels(name))}
            return {"ledger": self.scribe.ledger, "entry": self._entry(name)}

    def agent_write(self, name, body, prev, tags, *, author=AGENT_AUTHOR):
        if (not isinstance(name, str) or len(name) > 256 or not AGENT_NAME.fullmatch(name)
                or any(p in (".", "..") for p in name.split("/"))):
            raise ValueError("Agent entries must use names under agent/ with simple path segments.")
        if (not isinstance(tags, list) or len(tags) > 12
                or any(not isinstance(t, str) or (t != "agent" and not AGENT_TAG.fullmatch(t)) for t in tags)):
            raise ValueError("Tags may be agent or agent-*; at most 12 tags. The harness adds agent automatically.")
        if not isinstance(body, str) or not 1 <= len(body) <= 24000:
            raise ValueError("Agent body must be text, 1 to 24,000 characters; no tombstones.")
        if prev is not None and not isinstance(prev, str):
            raise ValueError("prev must be the id read from the ledger, or null for a new name.")
        # Check everything before changing tags; stale updates have no side effects.
        body = redact(body)
        body.encode("utf-8")
        with self.lock:
            current = self.scribe.current(name)
            existing_tags = self.scribe.labels(name)
            if any(t != "agent" and not AGENT_TAG.fullmatch(t) for t in existing_tags):
                raise ValueError("Protected tags: agent cannot change this entry.")
            if current and (current.author != author or "agent" not in existing_tags):
                raise ValueError("Protected author or missing agent tag: write refused.")
            if (current and prev != current.id) or (current is None and prev is not None):
                raise ValueError("stale_prev: read the current entry and cite its id; use null for a create.")
            for tag in sorted({"agent", *tags}):
                if tag not in existing_tags:
                    self._call(self.scribe.tag, name, tag, author=author)
            entry_id = self._call(self.scribe.write, name, body, author=author, prev=prev)
            return {"ledger": self.scribe.ledger, "id": entry_id, "name": name,
                    "author": author, "tags": sorted(self.scribe.labels(name))}

    def close(self):
        with self.lock:
            if not self.failed:
                self.flush_pending(force=True)
                self._call(self.scribe.close)


class AgentJournal:
    """An attested actor view; lifecycle ownership stays with the root journal."""
    def __init__(self, journal, actor, author, instruction=None):
        self.owner, self.actor, self.author, self.instruction = journal, actor, author, instruction

    @property
    def failed(self):
        return self.owner.failed

    def write(self, kind, data, **kwargs):
        return self.owner.write(kind, data, actor=self.actor, agent_author=self.author,
                                instruction=self.instruction, **kwargs)

    def flush_pending(self, force=False):
        return self.owner.flush_pending(force)

    def results(self):
        return self.owner.results_for(self.actor)
