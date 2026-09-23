"""JSONL run log: one JSON object per line, flushed after every record.

Files are named *.jsonl.log so the bootstrap-harness .gitignore (*.log) keeps
them local (founder decision, 2026-09-23). Flushing per record means a run
that crashes still leaves everything up to the crash on disk.
"""

import dataclasses
import datetime
import json
from pathlib import Path
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Convert SDK dataclasses/TypedDicts into plain JSON values.

    Dataclasses keep their type name under "_type" so the log shows which SDK
    message class produced a record. Anything unknown is kept as repr() rather
    than dropped.
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


class RunLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8")
        self.seq = 0
        self.context: dict[str, Any] = {}  # merged into every record, e.g. {"scenario": "add"}

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        self.seq += 1
        record = {
            "seq": self.seq,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            **self.context,
            **to_jsonable(fields),
        }
        self._f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._f.flush()
        return record

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
