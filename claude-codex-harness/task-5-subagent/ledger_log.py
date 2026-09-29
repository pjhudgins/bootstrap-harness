# Task 5 version of ../task-4-ledger/ledger_log.py: its own ledger; the text-plus-link
# pattern is general (messages and script output); records name the agent they concern.
"""The harness's record, kept in a wiki ledger through bootstrap-ledger's scribe module.

One ledger for this harness (LEDGER_NAME); every chat opens one scribe session, which
writes one new session file. The ledger is append-only: writing a name again supersedes
its current entry and keeps every version.

Each record the harness makes is one body entry, authored by the harness, named
`harness/<session>/<seq>.<kind>` and labelled `log.<kind>`; records made for one agent
carry its id. A text worth reading on its own is its own entry, authored by whoever
wrote it, followed by a harness record that links to it:
  - messages:       `transcript/<session>/<n>-<agent>-<role>`, by the human or the agent,
                    labelled `transcript.<role>`;
  - script output:  `exec/<session>/<n>-<script>`, by the harness, which ran it,
                    labelled `exec`.
A subagent's task is not copied: its message record links to the exact version of the
instructions entry its parent pinned, whoever wrote that version.
Every entry the harness writes carries exactly one label (founder, 2026-09-25). The
agents' ledger tools never write these names or labels (ledger_tools.py).

Standard: bootstrap-ledger/standard/wiki_ledger_v0.4.md. Module contract:
bootstrap-ledger/python-scribe/spec_v0.3.md and interfaces.md.
"""

import hashlib
import re
import sys
import threading
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
NIMOI_ROOT = TASK_DIR.parents[2]  # task-5-subagent < claude-codex-harness < bootstrap-harness < nimoi
SCRIBE_DIR = NIMOI_ROOT / "bootstrap-ledger" / "python-scribe"

# Import scribe in place, never modified (spec §3). Importing would write __pycache__
# into bootstrap-ledger, outside this swimlane, so bytecode caching is switched off.
sys.dont_write_bytecode = True
if str(SCRIBE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIBE_DIR))
import scribe  # noqa: E402

import codex_client as cc  # noqa: E402

LEDGER_ROOT = TASK_DIR / "ledgers"          # as task 4: inside the task folder
LEDGER_NAME = "claude-codex-subagents"      # a new harness, so its own ledger
HARNESS_AUTHOR = "harness:claude-codex-harness/task-5-subagent"
# Founder, 2026-09-25: "no need to identify, just log as human user of session for now".
HUMAN_AUTHOR = "human:session-user"
# 1: task 5 as first built (two labels per entry, every streamed fragment recorded).
# 2: one label per entry; fragments summarised per message (founder, 2026-09-25).
RECORD_FORMAT = 2

HARNESS_PREFIX, TRANSCRIPT_PREFIX, EXEC_PREFIX = "harness/", "transcript/", "exec/"
LOG_LABEL_PREFIX = "log."
TRANSCRIPT_LABEL, EXEC_LABEL = "transcript", "exec"
PROTECTED_PREFIXES = (HARNESS_PREFIX, TRANSCRIPT_PREFIX, EXEC_PREFIX)
PROTECTED_LABELS = (TRANSCRIPT_LABEL, EXEC_LABEL)  # and "<label>.*", and every "log.*"


def is_protected_name(name):
    return any(name == p.rstrip("/") or name.startswith(p) for p in PROTECTED_PREFIXES)


def is_protected_label(label):
    return (label.startswith(LOG_LABEL_PREFIX) or label in PROTECTED_LABELS
            or any(label.startswith(p + ".") for p in PROTECTED_LABELS))


# Key-like strings never reach the ledger, whoever wrote them (rules.md, Secrets): a
# human could paste one, or an agent could quote one. A key starts a token: without
# that, "task-5-hello-safe-subagent-instructions" read as an "sk-" key (live, 2026-09-25).
_TOKEN_START = r"(?<![A-Za-z0-9])"
KEY_LIKE = re.compile("|".join([
    _TOKEN_START + r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    _TOKEN_START + r"gh[pousr]_[A-Za-z0-9]{30,}",
    _TOKEN_START + r"github_pat_[A-Za-z0-9_]{30,}",
    _TOKEN_START + r"xox[abprs]-[A-Za-z0-9-]{10,}",
    _TOKEN_START + r"AKIA[0-9A-Z]{16}",
    _TOKEN_START + r"AIza[0-9A-Za-z_-]{35}",
    _TOKEN_START + r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]))
KEY_REDACTION = "<redacted: key-like string>"


def has_key_like(text):
    return bool(KEY_LIKE.search(text))


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


def agent_author(role, model, agent_id):
    """The designation the harness attests for an agent's own ledger writes: its role,
    its model, the harness, and its place in the tree (`pilot`, `pilot.1`, `pilot.1.2`)."""
    return f"{role}:{model}@claude-codex-harness#{agent_id}"


def scribe_provenance():
    """Which scribe wrote this session: its id, its ledger version, and its source's hash."""
    source = Path(scribe.__file__).resolve()
    return {"id": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION,
            "source": source.relative_to(NIMOI_ROOT).as_posix(),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


def _segment(text):
    """A string made safe as one ledger-name segment."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text).strip(".") or "_"


class LedgerFailure(RuntimeError):
    """A ledger write failed: the session is over (spec §5.4) and nothing more is logged."""


class LedgerRecord:
    """`write(kind, **fields)` appends a harness record. `message(...)` and
    `exec_output(...)` record a text under its writer's name, then a harness record that
    links to it; `link_message(...)` records a message whose text is already an entry."""

    def __init__(self, root=LEDGER_ROOT, ledger=LEDGER_NAME, author=HARNESS_AUTHOR):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)  # the scribe never creates a root (spec §4.1)
        self.author = author
        self.scribe = scribe.Scribe.open(root, ledger, session_author=author, create=True)
        self.session = self.scribe.session
        self.path = root / ledger / f"{self.session}.ledger"
        self.failed = None
        self._lock = threading.RLock()  # _linked() holds it across its two entries
        self._seq = 0
        self._texts = 0

    def _guarded(self, action):
        """Run one scribe action under the lock; a WriteFailed ends the session."""
        with self._lock:
            if self.failed:
                raise LedgerFailure(self.failed)
            if not self.scribe.is_open:
                return None
            try:
                return action()
            except scribe.WriteFailed as error:
                self.failed = f"ledger write failed: {error!r}"
                print(f"ledger: {self.failed}", file=sys.stderr, flush=True)
                raise LedgerFailure(self.failed) from error

    def _linked(self, prefix, suffix, label, text, author, kind, **fields):
        """Two adjacent entries: `<prefix><session>/<nnnn>-<suffix>` with the text as its
        body, by `author`, labelled `label`; then a harness `kind` record whose `text` is a
        wikilink to it and whose `text_id` is its exact id. Returns (text name, text id,
        record id); None once closed. Raises LedgerFailure like write()."""
        def action():
            self._texts += 1
            name = f"{prefix}{self.session}/{self._texts:04d}-{suffix}"
            try:
                text_id = self.scribe.write(name, scrub(text), author=author)
            except scribe.Refused as error:
                raise LedgerFailure(f"ledger refused a {kind} text ({error.code})") from error
            self.scribe.tag(name, label, author=self.author)
            record_id = self.write(kind, text=f"[[{name}]]", text_id=text_id,
                                   text_author=author, **fields)
            return name, text_id, record_id
        return self._guarded(action)

    def message(self, agent, role, text, author, **fields):
        """One message: to the root agent from the human (`user`), or from any agent
        (`agent`). A subagent's task (`task`) is a link_message() to its instructions."""
        return self._linked(TRANSCRIPT_PREFIX, f"{_segment(agent)}-{role}",
                            f"{TRANSCRIPT_LABEL}.{role}", text, author, "message",
                            agent=agent, role=role, **fields)

    def link_message(self, agent, role, name, text_id, author, **fields):
        """A message whose text is the existing entry version `text_id` of `name`, written
        by `author`: recorded as a link, never copied."""
        return self.write("message", text=f"[[{name}]]", text_id=text_id, text_author=author,
                          agent=agent, role=role, **fields)

    def exec_output(self, agent, script, text, **fields):
        """The output of one script run, by the harness, which ran it."""
        return self._linked(EXEC_PREFIX, _segment(Path(script).stem), EXEC_LABEL, text,
                            self.author, "python_exec", agent=agent, script=script, **fields)

    def write(self, kind, **fields):
        """Append one record. Returns its id; None once the session has been closed.
        Raises LedgerFailure if the ledger cannot be written: never hides a failure."""
        def action():
            self._seq += 1
            name = f"{HARNESS_PREFIX}{self.session}/{self._seq:06d}.{kind}"
            body = {"kind": kind, **scrub(fields)}
            try:
                entry_id = self.scribe.write(name, body, author=self.author)
            except scribe.Refused as error:
                if error.code != "bad_body":
                    raise LedgerFailure(f"ledger refused a harness record ({error.code})") \
                        from error
                # A value that does not survive a JSON round trip: record that it
                # happened rather than lose the record entirely.
                entry_id = self.scribe.write(name, {
                    "kind": kind, "unrecordable": error.code, "repr": repr(body)[:4000]},
                    author=self.author)
            self.scribe.tag(name, LOG_LABEL_PREFIX + kind, author=self.author)
            return entry_id
        return self._guarded(action)

    def close(self):
        """Write the trailer and release the lease. Safe to call twice."""
        with self._lock:
            if self.failed or not self.scribe.is_open:
                return
            self.scribe.close()


class AgentRecord:
    """The record as one agent's app-server client sees it: every record names the agent."""

    def __init__(self, record, agent):
        self.record, self.agent = record, agent

    def write(self, kind, **fields):
        return self.record.write(kind, agent=self.agent, **fields)
