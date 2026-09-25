"""The harness's record, kept in a wiki ledger through bootstrap-ledger's scribe module.

Task 4 replaces task 3's JSONL journal with this. One ledger for this harness
(LEDGER_NAME); every chat opens one scribe session, which writes one new session file.
Each record the harness makes is one body entry, authored by the harness, named
`harness/<session>/<seq>.<kind>` and tagged `harness` and `log.<kind>`. The text of each
message to or from the user is its own entry, `transcript/<session>/<n>-<role>`,
authored by the human user of the session or by the agent; the harness's `message`
record links to it. The agent's ledger tools refuse to touch any of these (tools.py).

Standard: bootstrap-ledger/standard/wiki_ledger_v0.4.md. Module contract:
bootstrap-ledger/python-scribe/spec_v0.3.md and interfaces.md.
"""

import re
import sys
import threading
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
NIMOI_ROOT = TASK_DIR.parents[2]  # task-4-ledger < claude-codex-harness < bootstrap-harness < nimoi
SCRIBE_DIR = NIMOI_ROOT / "bootstrap-ledger" / "python-scribe"

# Import scribe in place, never modified (spec §3). Importing would write __pycache__
# into bootstrap-ledger, outside this swimlane, so bytecode caching is switched off.
sys.dont_write_bytecode = True
if str(SCRIBE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIBE_DIR))
import scribe  # noqa: E402

import codex_client as cc  # noqa: E402

LEDGER_ROOT = TASK_DIR / "ledgers"          # founder, 2026-09-24: inside task-4-ledger/
LEDGER_NAME = "claude-codex-pilot"          # founder: one ledger, a new file per chat
HARNESS_AUTHOR = "harness:claude-codex-harness/task-4-ledger"
# Founder, 2026-09-25: "no need to identify, just log as human user of session for now".
HUMAN_AUTHOR = "human:session-user"
HARNESS_LABEL = "harness"
LOG_LABEL_PREFIX = "log."
# Message texts, to and from the user, authored by whoever wrote them (founder,
# 2026-09-25). Protected from the agent's writes like harness entries (tools.py).
TRANSCRIPT_PREFIX = "transcript/"
TRANSCRIPT_LABEL = "transcript"

# Key-like strings never reach the ledger, whoever wrote them (rules.md, Secrets): a
# human could paste one, or the agent could quote one.
KEY_LIKE = re.compile("|".join([
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]))
KEY_REDACTION = "<redacted: key-like string>"


def redact_keys(text):
    """(text with key-like strings replaced, number replaced)."""
    return KEY_LIKE.subn(KEY_REDACTION, text)


def scrub(value):
    """Credential-named keys (codex_client.redact), then key-like strings, recursively."""
    return _scrub_strings(cc.redact(value))


def _scrub_strings(value):
    if isinstance(value, str):
        return redact_keys(value)[0]
    if isinstance(value, dict):
        return {k: _scrub_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_strings(v) for v in value]
    return value


def agent_author(model):
    """The designation the harness attests for the agent's own ledger writes."""
    return f"test-pilot:{model}@claude-codex-harness"


class LedgerFailure(RuntimeError):
    """A ledger write failed: the session is over (spec §5.4) and nothing more is logged."""


class LedgerJournal:
    """Drop-in for task 3's Journal: `write(kind, **fields)` appends a harness entry, and
    `message(role, text, author)` records one message's text under its writer's name."""

    def __init__(self, root=LEDGER_ROOT, ledger=LEDGER_NAME, author=HARNESS_AUTHOR):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)  # the scribe never creates a root (spec §4.1)
        self.author = author
        self.scribe = scribe.Scribe.open(root, ledger, session_author=author, create=True)
        self.session = self.scribe.session
        self.path = root / ledger / f"{self.session}.ledger"
        self.failed = None
        self._lock = threading.RLock()  # message() holds it across its two entries
        self._seq = 0
        self._messages = 0

    def message(self, role, text, author, **fields):
        """Record one message to or from the user, as two adjacent entries:
          1. `transcript/<session>/<nnnn>-<role>`: the text as the body, authored by its
             writer (the human user of the session, or the agent), tagged `transcript`
             and `transcript.<role>`;
          2. a harness `message` record whose `text` is a wikilink to entry 1, with the
             exact id of the text in `text_id`.
        Returns the id of entry 2 (None once closed). Raises LedgerFailure like write()."""
        with self._lock:
            if self.failed:
                raise LedgerFailure(self.failed)
            if not self.scribe.is_open:
                return None
            self._messages += 1
            name = f"{TRANSCRIPT_PREFIX}{self.session}/{self._messages:04d}-{role}"
            try:
                try:
                    text_id = self.scribe.write(name, scrub(text), author=author)
                except scribe.Refused as error:
                    raise LedgerFailure(f"ledger refused a message text ({error.code})") \
                        from error
                for label in (TRANSCRIPT_LABEL, f"{TRANSCRIPT_LABEL}.{role}"):
                    self.scribe.tag(name, label, author=self.author)
            except scribe.WriteFailed as error:
                self.failed = f"ledger write failed: {error!r}"
                print(f"ledger: {self.failed}", file=sys.stderr, flush=True)
                raise LedgerFailure(self.failed) from error
            return self.write("message", role=role, text=f"[[{name}]]", text_id=text_id,
                              text_author=author, **fields)

    def write(self, kind, **fields):
        """Append one record. Returns its id; None once the session has been closed.
        Raises LedgerFailure if the ledger cannot be written: never hides a failure."""
        with self._lock:
            if self.failed:
                raise LedgerFailure(self.failed)
            if not self.scribe.is_open:
                return None
            self._seq += 1
            name = f"harness/{self.session}/{self._seq:06d}.{kind}"
            body = {"kind": kind, **scrub(fields)}
            try:
                try:
                    entry_id = self.scribe.write(name, body, author=self.author)
                except scribe.Refused as error:
                    if error.code != "bad_body":
                        raise LedgerFailure(f"ledger refused a harness record ({error.code})") \
                            from error
                    # A value that does not survive a JSON round trip: record that it
                    # happened rather than lose the record entirely.
                    entry_id = self.scribe.write(name, {
                        "kind": kind, "unrecordable": error.code,
                        "repr": repr(body)[:4000]}, author=self.author)
                for label in (HARNESS_LABEL, LOG_LABEL_PREFIX + kind):
                    self.scribe.tag(name, label, author=self.author)
            except scribe.WriteFailed as error:
                self.failed = f"ledger write failed: {error!r}"
                print(f"ledger: {self.failed}", file=sys.stderr, flush=True)
                raise LedgerFailure(self.failed) from error
            return entry_id

    def close(self):
        """Write the trailer and release the lease. Safe to call twice."""
        with self._lock:
            if self.failed or not self.scribe.is_open:
                return
            self.scribe.close()
