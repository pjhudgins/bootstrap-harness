"""Harness logging into the wiki ledger (replaces task 2/3's JSONL run log).

Every record becomes one named body, written by the harness author and tagged:
    name   log/<session stamp>/<seq, 6 digits>   (unique across sessions; sorts in order)
    body   {"kind": ..., "agent": ..., "turn": ..., **fields}  (JSON-safe via to_jsonable)
    labels harness            (protection: agent writes refuse any name carrying it)
           log.<kind>         (type, e.g. log.tool_call)
           agent.<id>         (task 5: which agent the record is about)

The body write and its tags happen with no await in between, so no agent tool
call can run while an entry exists untagged.

Message text to and from the user (founder direction, 2026-09-25) gets its own entry,
written first so the harness's `message` entry can link to it:
    name   log/<session stamp>/<seq>        (same sequence as every other record)
    body   the text itself, a JSON string
    author whoever wrote it: HUMAN_AUTHOR for the session user, the agent's designation
           for its replies (the harness names both; never taken from model output)
    labels harness, log.text               (tagged by the harness author)
The message entry then carries "[[log/<session stamp>/<seq>]]" where the text was.

If the scribe refuses a body (e.g. a NaN float), a fallback body with repr() is
written instead, so the event is never silently dropped. If a write fails outright
(WriteFailed), the ledger session is dead and `failed` latches. The harness then
fails closed (task 5, peer review): no agent tool runs and no new message is accepted
(agent.AgentCore.check, session.AgentSession.send). Records made after the failure
are marked `ledger_error` and still reach the UI, and the failure is printed to stderr.
"""

import dataclasses
import datetime
import sys
from types import ModuleType
from typing import Any

PROTECTED_LABEL = "harness"
KIND_LABEL_PREFIX = "log."
LOG_PREFIX = "log/"
TEXT_KIND = "text"
AGENT_LABEL_PREFIX = "agent."  # task 5: agent.pilot, agent.pilot.1, ... on every record about that agent
HUMAN_AUTHOR = "human:session-user"  # founder: "just log as human user of session for now"


def wikilink(record: dict[str, Any]) -> str | None:
    """"[[name]]" for a record that reached the ledger, else None."""
    return f"[[{record['name']}]]" if record.get("name") else None


def to_jsonable(obj: Any) -> Any:
    """Convert SDK dataclasses/TypedDicts into plain JSON values (from task 2's runlog.py).

    Dataclasses keep their type name under "_type". Anything unknown becomes repr().
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {"_type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            out[f.name] = to_jsonable(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return repr(obj)


class LedgerLog:
    def __init__(self, scribe_module: ModuleType, ledger: Any, author: str):
        self._sm = scribe_module
        self.ledger = ledger  # an open scribe.Scribe
        self.author = author
        self.seq = 0
        self.failed: str | None = None

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        """A harness record: body {"kind", ...fields}, authored by the harness. Callers pass agent and
        turn explicitly (agent.AgentCore.pub): agents can be active at once, so there is no shared context."""
        payload = to_jsonable(fields)
        record = self._record(kind, payload)
        fallback = lambda e: {"kind": kind, "agent": fields.get("agent"), "unrecordable": repr(fields)[:20000],
                              "refused": str(e)}
        return self._commit(record, kind, {"kind": kind, **payload}, self.author, fallback)

    def write_text(self, text: str, *, author: str, direction: str, **fields: Any) -> dict[str, Any]:
        """Message text as its own entry: body is the text, author is whoever wrote it.

        The UI record also carries the text, author, direction and any extra fields
        (task 5: agent, turn), so the page can draw the chat from these records alone.
        """
        record = self._record(TEXT_KIND, {**to_jsonable(fields), "direction": direction, "author": author,
                                          "text": text})
        fallback = lambda e: repr(text)[:20000]  # e.g. a lone surrogate the scribe cannot encode
        return self._commit(record, TEXT_KIND, text, author, fallback)

    def _record(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        return {
            "seq": self.seq,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            **payload,
        }

    def _commit(self, record: dict[str, Any], kind: str, body: Any, author: str,
                fallback: Any) -> dict[str, Any]:
        if self.failed:
            record["ledger_error"] = self.failed
            return record
        name = f"{LOG_PREFIX}{self.ledger.session}/{record['seq']:06d}"
        try:
            try:
                entry_id = self.ledger.write(name, body, author=author)
            except self._sm.Refused as e:
                if e.code != "bad_body":
                    raise
                entry_id = self.ledger.write(name, fallback(e), author=author)
                record["ledger_note"] = f"body refused ({e}); fallback written"
            # Tags are always the harness's, whoever wrote the body.
            self.ledger.tag(name, PROTECTED_LABEL, author=self.author)
            self.ledger.tag(name, KIND_LABEL_PREFIX + kind, author=self.author)
            if record.get("agent"):  # task 5: which agent the record is about, for filtering
                self.ledger.tag(name, AGENT_LABEL_PREFIX + str(record["agent"]), author=self.author)
            record["id"], record["name"] = entry_id, name
        except self._sm.ScribeError as e:
            self.failed = f"{e.code}: {e}"
            record["ledger_error"] = self.failed
            print(f"LEDGER WRITE FAILED, later records are not recorded: {self.failed}", file=sys.stderr)
        return record
