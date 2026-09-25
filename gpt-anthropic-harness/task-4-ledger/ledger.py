"""Task-local adapter for the shared scribe; no other project is modified."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
import threading
from uuid import uuid4

from redaction import Redactor

NIMOI = Path(__file__).resolve().parents[3]
SCRIBE_PATH = NIMOI / "bootstrap-ledger" / "python-scribe" / "scribe.py"
spec = importlib.util.spec_from_file_location("nimoi_bootstrap_scribe", SCRIBE_PATH)
scribe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scribe
old_bytecode = sys.dont_write_bytecode
try:
    sys.dont_write_bytecode = True
    spec.loader.exec_module(scribe)
finally:
    sys.dont_write_bytecode = old_bytecode

HARNESS_AUTHOR = "harness"
AGENT_AUTHOR = "agent.claude.test-pilot"
AGENT_TAGS = frozenset({"pilot.note", "pilot.observation", "pilot.question"})
AGENT_NAME = re.compile(r"pilot/[A-Za-z0-9_-][A-Za-z0-9._/-]{0,180}")


class LedgerFailed(RuntimeError):
    pass


class LedgerLog(Redactor):
    def __init__(self, root):
        super().__init__()
        self.lock = threading.RLock()
        self.failed = False
        self.seq = 0
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.name = "chat-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz") + "-" + uuid4().hex[:10]
        # Never reopen an existing chat, even if its name somehow collides.
        (self.root / self.name).mkdir()
        self.scribe = scribe.Scribe.open(self.root, self.name, session_author=HARNESS_AUTHOR)
        self.path = self.root / self.name / (self.scribe.session + ".ledger")

    def check(self):
        if self.failed or not self.scribe.is_open:
            raise LedgerFailed("Ledger unavailable; stop and preserve its files/lease for human review.")

    def write(self, kind, **fields):
        with self.lock:
            self.check()
            name = f"harness/{self.seq + 1:08d}"
            body = {"seq": self.seq + 1, "kind": kind, **self.clean(fields)}
            try:
                entry_id = self.scribe.write(name, body, author=HARNESS_AUTHOR)
                self.scribe.tag(name, "harness", author=HARNESS_AUTHOR)
                self.scribe.tag(name, "log." + kind, author=HARNESS_AUTHOR)
            except Exception as exc:
                self.failed = True
                raise LedgerFailed("Ledger event write failed; partial records and lease are preserved.") from exc
            self.seq += 1
            return entry_id

    def agent_write(self, name, body, tags, prev=None):
        with self.lock:
            self.check()
            if (not isinstance(name, str) or not AGENT_NAME.fullmatch(name)
                    or any(part in ("", ".", "..") for part in name.split("/"))):
                raise ValueError("Agent names must be under pilot/ with ordinary path segments.")
            if self.clean(name) != name:
                raise ValueError("Entry name resembles a credential and cannot be stored.")
            if not isinstance(body, str) or not 1 <= len(body) <= 16000:
                raise ValueError("Ledger body must be 1–16,000 characters of text.")
            if (not isinstance(tags, list) or not tags or not all(isinstance(t, str) for t in tags)
                    or not set(tags) <= AGENT_TAGS):
                raise ValueError("Only pilot.note, pilot.observation and pilot.question tags are writable.")
            current = self.scribe.current(name)
            labels = self.scribe.labels(name)
            if labels - AGENT_TAGS or (current and (current.author != AGENT_AUTHOR or not labels)):
                raise ValueError("Entry is protected: agent author and writable tags are required.")
            try:
                entry_id = self.scribe.write(name, self.clean(body), author=AGENT_AUTHOR, prev=prev)
            except scribe.Refused as exc:
                raise ValueError(f"Ledger refused {exc.code}; read current entry and cite its id as prev.") from exc
            except Exception as exc:
                self.failed = True
                raise LedgerFailed("Agent ledger write failed; preserve ledger/lease.") from exc
            try:
                for tag in sorted(set(tags)):
                    self.scribe.tag(name, tag, author=AGENT_AUTHOR)
            except Exception as exc:
                self.failed = True
                raise LedgerFailed("Agent body was written but tagging failed; preserve ledger/lease.") from exc
            return {"name": name, "id": entry_id, "author": AGENT_AUTHOR,
                    "tags": sorted(self.scribe.labels(name)), "body": self.clean(body)}

    def read(self, name=None, tag=None, offset=0, limit=30, body_offset=0):
        with self.lock:
            self.check()
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError("offset >= 0 and limit 1–50 are required.")
            if type(body_offset) is not int or body_offset < 0:
                raise ValueError("body_offset must be a nonnegative integer.")
            if name is not None:
                if not isinstance(name, str):
                    raise ValueError("name must be a string.")
                entry = self.scribe.current(name)
                if entry is None:
                    raise ValueError("Entry not found.")
                result = {"name": name, "id": entry.id, "author": entry.author,
                          "prev": entry.prev, "tags": sorted(self.scribe.labels(name))}
                body = self.clean(entry.body)
                encoded = json.dumps(body, ensure_ascii=False)
                if len(encoded) <= 20000 and body_offset == 0:
                    result["body"] = body
                else:
                    result.update(body_json_slice=encoded[body_offset:body_offset+20000],
                                  body_offset=body_offset, total_chars=len(encoded),
                                  next_body_offset=body_offset+20000 if body_offset+20000 < len(encoded) else None)
                return result
            if tag is not None and not isinstance(tag, str):
                raise ValueError("tag must be a string.")
            names = self.scribe.labelled(tag) if tag else self.scribe.names()
            selected = names[offset:offset+limit]
            return {"ledger": self.name, "total": len(names), "offset": offset,
                    "next_offset": offset+limit if offset+limit < len(names) else None,
                    "entries": [{"name": n, "id": self.scribe.current(n).id,
                                 "author": self.scribe.current(n).author,
                                 "tags": sorted(self.scribe.labels(n))} for n in selected]}

    def close(self):
        with self.lock:
            if not self.failed:
                self.scribe.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
