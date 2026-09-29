"""Approved demonstration: deterministic stdout/stderr; no inputs, writes or imports from drafts."""
import json
import sys

print(json.dumps({"script": "safe_test", "sum": 19 + 23, "filesystem_writes": False}))
print("safe_test diagnostic: completed", file=sys.stderr)
