"""Approved smoke script: fixed arithmetic and output; no files or subprocesses."""

import json

print(json.dumps({'message': 'Approved harness script ran.', 'sum': 19.25 + 22.75}))
