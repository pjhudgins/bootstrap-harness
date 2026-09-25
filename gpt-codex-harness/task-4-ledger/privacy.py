"""Credential redaction inherited from task 3; not a universal secret detector."""
import re


def redact(value):
    """Defense in depth; auth/config payloads are omitted separately at source."""
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.sub(r"[_-]", "", key).lower() in {
            "apikey", "accesstoken", "refreshtoken", "idtoken", "authorization",
            "password", "secret", "email", "authtoken", "bearertoken",
        } else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED_KEY]", value)
        value = re.sub(r"\bBearer\s+\S+", "Bearer [REDACTED]", value, flags=re.I)
        return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                      "[REDACTED_JWT]", value)
    return value

