"""Harness logging into the wiki ledger (replaces task 2/3's JSONL run log).

Every record becomes one named body, written by the harness author and tagged:
    name   log/<session stamp>/<seq, 6 digits>   (unique across sessions; sorts in order)
    body   {"kind": ..., "turn": ..., **fields}  (JSON-safe via to_jsonable)
    labels harness            (protection: agent writes refuse any name carrying it)
           log.<kind>         (type, e.g. log.tool_call)

The body write and both tags happen with no await in between, so no agent tool
call can run while an entry exists untagged.

If the scribe refuses a body (e.g. a NaN float), a fallback body with repr() is
written instead, so the event is never silently dropped. If a write fails outright
(WriteFailed), the ledger session is dead: records are then marked with
`ledger_error` and still reach the UI, and the failure is printed to stderr.
"""

import dataclasses
import datetime
import sys
from types import ModuleType
from typing import Any

PROTECTED_LABEL = "harness"
KIND_LABEL_PREFIX = "log."
LOG_PREFIX = "log/"


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
        self.context: dict[str, Any] = {}  # merged into every record, e.g. {"turn": 3}
        self.failed: str | None = None

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        self.seq += 1
        payload = {**self.context, **to_jsonable(fields)}
        record = {
            "seq": self.seq,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            **payload,
        }
        if self.failed:
            record["ledger_error"] = self.failed
            return record
        name = f"{LOG_PREFIX}{self.ledger.session}/{self.seq:06d}"
        try:
            try:
                entry_id = self.ledger.write(name, {"kind": kind, **payload}, author=self.author)
            except self._sm.Refused as e:
                if e.code != "bad_body":
                    raise
                fallback = {"kind": kind, **self.context, "unrecordable": repr(fields)[:20000],
                            "refused": str(e)}
                entry_id = self.ledger.write(name, fallback, author=self.author)
                record["ledger_note"] = f"body refused ({e}); fallback written"
            self.ledger.tag(name, PROTECTED_LABEL, author=self.author)
            self.ledger.tag(name, KIND_LABEL_PREFIX + kind, author=self.author)
            record["id"], record["name"] = entry_id, name
        except self._sm.ScribeError as e:
            self.failed = f"{e.code}: {e}"
            record["ledger_error"] = self.failed
            print(f"LEDGER WRITE FAILED, later records are not recorded: {self.failed}", file=sys.stderr)
        return record
