"""One scribe, attested authors, protected tags, and no secondary log files."""

from dataclasses import asdict
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
AGENT_NAME = re.compile(r"agent/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
AGENT_TAG = re.compile(r"agent-[A-Za-z0-9][A-Za-z0-9._-]{0,56}")


def log_tags(kind, data):
    tags = {"harness", "log-" + kind}
    if kind in ("send", "receive", "shutdown_receive"):
        tags.add("protocol")
    method = data.get("method", "") if isinstance(data, dict) else ""
    item = data.get("params", {}).get("item", {}) if isinstance(data, dict) else {}
    if kind == "user_message" or method == "turn/start" or "agentMessage" in method or item.get("type") in ("agentMessage", "userMessage"):
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

    def write(self, kind, data):
        data = redact(data)
        with self.lock:
            self.sequence += 1
            name = f"harness/events/{self.sequence:08d}"
            # Tags precede the body. A crash can leave an unbound tagged name,
            # but cannot leave a successfully recorded body without protection.
            for tag in log_tags(kind, data):
                self._call(self.scribe.tag, name, tag, author=HARNESS_AUTHOR)
            entry_id = self._call(self.scribe.write, name,
                {"sequence": self.sequence, "kind": kind, "data": data}, author=HARNESS_AUTHOR)
        if self.observer:
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
