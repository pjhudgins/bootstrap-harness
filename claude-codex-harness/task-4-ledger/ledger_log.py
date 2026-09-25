"""The harness's record, kept in a wiki ledger through bootstrap-ledger's scribe module.

Task 4 replaces task 3's JSONL journal with this. One ledger for this harness
(LEDGER_NAME); every chat opens one scribe session, which writes one new session file.
Each record the harness makes is one body entry, authored by the harness, named
`harness/<session>/<seq>.<kind>` and tagged `harness` and `log.<kind>`. The agent's
ledger tools refuse to touch either (tools.py).

Standard: bootstrap-ledger/standard/wiki_ledger_v0.4.md. Module contract:
bootstrap-ledger/python-scribe/spec_v0.3.md and interfaces.md.
"""

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
HARNESS_LABEL = "harness"
LOG_LABEL_PREFIX = "log."


def agent_author(model):
    """The designation the harness attests for the agent's own ledger writes."""
    return f"test-pilot:{model}@claude-codex-harness"


class LedgerFailure(RuntimeError):
    """A ledger write failed: the session is over (spec §5.4) and nothing more is logged."""


class LedgerJournal:
    """Drop-in for task 3's Journal: `write(kind, **fields)` appends a harness entry."""

    def __init__(self, root=LEDGER_ROOT, ledger=LEDGER_NAME, author=HARNESS_AUTHOR):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)  # the scribe never creates a root (spec §4.1)
        self.author = author
        self.scribe = scribe.Scribe.open(root, ledger, session_author=author, create=True)
        self.session = self.scribe.session
        self.path = root / ledger / f"{self.session}.ledger"
        self.failed = None
        self._lock = threading.Lock()
        self._seq = 0

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
            body = {"kind": kind, **cc.redact(fields)}
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
