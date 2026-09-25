"""Redaction shared by ledger, UI and agent-visible tool results."""
import dataclasses
import os
import re

SECRET_FIELD = re.compile(r"api[_-]?key|token|secret|password|authorization|credential", re.I)
USAGE_FIELDS = {"inputtokens", "outputtokens", "totaltokens", "cachedtokens",
                "cachereadinputtokens", "cachecreationinputtokens", "reasoningtokens",
                "tokens", "tokencount", "tokenusage", "tokenlimit", "maxtokens",
                "maxoutputtokens", "estimatedtokens", "estimatedtokensdelta",
                "ephemeral1hinputtokens", "ephemeral5minputtokens"}
TOKEN = re.compile(r"\b(?:sk-ant-|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|Bearer\s+\S+", re.I)


class Redactor:
    def __init__(self):
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
        return {"_unsupported_type": type(value).__name__}
