"""One scribe, attested authors, protected tags, and no secondary log files."""

from dataclasses import asdict
import copy
import importlib.util
from pathlib import Path
import re
import sys
import threading

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
    def __init__(self, root, run_id, observer=None):
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
        self.turn_users = {}
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

    def _event(self, kind, data):
        """Caller holds lock. Text dependencies must already be durable."""
        self.sequence += 1
        name = f"harness/events/{self.sequence:08d}"
        for tag in log_tags(kind, data):
            self._call(self.scribe.tag, name, tag, author=HARNESS_AUTHOR)
        return self._call(self.scribe.write, name,
            {"sequence": self.sequence, "kind": kind, "data": data}, author=HARNESS_AUTHOR)

    def _text(self, text, role, key=None, fragment=False):
        """Immutable text entry; cache by provenance and text, never text alone."""
        cache_key = (role, key, text) if key is not None else None
        if cache_key in self.text_refs:
            return self.text_refs[cache_key]
        self.text_sequence += 1
        group = "fragments" if fragment else role
        name = f"messages/{group}/{self.text_sequence:08d}"
        # Tags are assigned by the harness; body author is the attested speaker.
        for tag in ("transcript", "protected", "message-fragment" if fragment else "message-text", "from-" + role):
            self._call(self.scribe.tag, name, tag, author=HARNESS_AUTHOR)
        author = HUMAN_AUTHOR if role == "human" else AGENT_AUTHOR
        entry_id = self._call(self.scribe.write, name, text, author=author)
        ref = {"text": f"[[{name}]]", "text_id": entry_id, "author": author}
        if cache_key is not None:
            self.text_refs[cache_key] = ref
        return ref

    def _message(self, ref, role, **metadata):
        if ref["text_id"] not in self.message_refs:
            self._event("message", {**metadata, **ref,
                "direction": "from-user" if role == "human" else "to-user",
                "complete": True})
            self.message_refs.add(ref["text_id"])

    def _message_links(self, kind, data, human_id):
        """Transform known message slots only, never arbitrary tool arguments."""
        if not isinstance(data, dict):
            return
        if kind == "user_message":
            ref = self._text(data["text"], "human", ("ui", data["id"]))
            self._message(ref, "human", source=kind, message_id=data["id"])
            data["text"] = ref["text"]
            return
        if kind not in ("send", "receive", "shutdown_receive"):
            return
        method = data.get("method")
        params = data.get("params") or {}
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        turn = params.get("turn") or (data.get("result") or {}).get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id", turn_id)
            if human_id is not None:
                self.turn_users.setdefault((thread_id, turn_id), human_id)
        human_id = self.turn_users.get((thread_id, turn_id), human_id)

        def human_text(text, fallback):
            key = ("ui", human_id) if human_id is not None else fallback
            return self._text(text, "human", key)

        def item_links(item, complete):
            if not isinstance(item, dict):
                return
            item_id = item.get("id")
            # This journal belongs to one thread. RPC snapshots may omit its
            # thread id; the turn/item identity still denotes the same message.
            key = ("item", turn_id, item_id)
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                if item["text"] or complete:
                    ref = self._text(item["text"], "agent", key)
                    if complete:
                        self._message(ref, "agent", source=method or "rpc-result", item_id=item_id,
                                      thread_id=thread_id, turn_id=turn_id, phase=item.get("phase"))
                    item["text"] = ref["text"]
            elif item.get("type") == "userMessage":
                for index, content in enumerate(item.get("content", [])):
                    if content.get("type") == "text" and isinstance(content.get("text"), str):
                        ref = human_text(content["text"], (*key, index))
                        if complete:
                            self._message(ref, "human", source=method or "rpc-result", item_id=item_id,
                                          thread_id=thread_id, turn_id=turn_id)
                        content["text"] = ref["text"]

        if kind == "send" and method == "turn/start":
            for index, content in enumerate(params.get("input", [])):
                if content.get("type") == "text" and isinstance(content.get("text"), str):
                    ref = human_text(content["text"], ("request", data.get("id"), index))
                    content["text"] = ref["text"]
        elif kind in ("receive", "shutdown_receive"):
            if method == "item/agentMessage/delta" and isinstance(params.get("delta"), str):
                # Keep exact fragments even if the turn ends without a completed item.
                params["delta"] = self._text(params["delta"], "agent", fragment=True)["text"]
            if method in ("item/started", "item/completed"):
                item_links(params.get("item"), method == "item/completed")
            if isinstance(turn, dict):
                for item in turn.get("items", []):
                    item_links(item, turn.get("status") == "completed")

    def write(self, kind, data, *, human_id=None):
        data = redact(data)
        with self.lock:
            stored = copy.deepcopy(data)
            self._message_links(kind, stored, human_id)
            entry_id = self._event(kind, stored)
        if self.observer:
            # Storage links must not replace what the user sees or the model receives.
            self.observer(kind, data)
        return entry_id

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

    def agent_write(self, name, body, prev, tags):
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
            if current and (current.author != AGENT_AUTHOR or "agent" not in existing_tags):
                raise ValueError("Protected author or missing agent tag: write refused.")
            if (current and prev != current.id) or (current is None and prev is not None):
                raise ValueError("stale_prev: read the current entry and cite its id; use null for a create.")
            for tag in sorted({"agent", *tags}):
                if tag not in existing_tags:
                    self._call(self.scribe.tag, name, tag, author=AGENT_AUTHOR)
            entry_id = self._call(self.scribe.write, name, body, author=AGENT_AUTHOR, prev=prev)
            return {"ledger": self.scribe.ledger, "id": entry_id, "name": name,
                    "author": AGENT_AUTHOR, "tags": sorted(self.scribe.labels(name))}

    def close(self):
        with self.lock:
            if not self.failed:
                self._call(self.scribe.close)
