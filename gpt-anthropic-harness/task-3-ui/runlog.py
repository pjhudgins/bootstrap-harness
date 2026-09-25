"""Exclusive-create, flushed JSONL journals with credential redaction."""

import dataclasses
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading


SECRET_FIELD = re.compile(r"api[_-]?key|token|secret|password|authorization|credential", re.I)
# Usage fields such as input_tokens are measurements, not credentials.
USAGE_FIELDS = {"inputtokens", "outputtokens", "totaltokens", "cachedtokens",
                "cachereadinputtokens", "cachecreationinputtokens", "reasoningtokens",
                "tokens", "tokencount", "tokenusage", "tokenlimit", "maxtokens",
                "maxoutputtokens", "estimatedtokens", "estimatedtokensdelta",
                "ephemeral1hinputtokens", "ephemeral5minputtokens"}
TOKEN = re.compile(r"\b(?:sk-ant-|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|Bearer\s+\S+", re.I)


class RunLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("x", encoding="utf-8")
        self.seq = 0
        self.lock = threading.Lock()
        self.secrets = sorted({v for k, v in os.environ.items()
                               if SECRET_FIELD.search(k) and len(v) >= 8}, key=len, reverse=True)

    def clean(self, value):
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            value = {"_type": type(value).__name__, **{
                field.name: getattr(value, field.name) for field in dataclasses.fields(value)}}
        if isinstance(value, dict):
            return {str(k): "[REDACTED]" if SECRET_FIELD.search(str(k))
                    and re.sub(r"[^a-z0-9]", "", str(k).lower()) not in USAGE_FIELDS else self.clean(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return TOKEN.sub("[REDACTED]", value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        # Preserve the existence/type of an unsupported value without arbitrary repr.
        return {"_unsupported_type": type(value).__name__}

    def write(self, kind, **fields):
        with self.lock:
            self._write(kind, **fields)

    def _write(self, kind, **fields):
        record = {"seq": self.seq + 1, "utc": datetime.now(timezone.utc).isoformat(),
                  "kind": kind, **self.clean(fields)}
        line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        self.file.write(line + "\n")
        self.file.flush()
        self.seq += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()
