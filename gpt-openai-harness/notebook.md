# GPT / OpenAI harness

## Current task: task-1-hello

2026-09-23: Read onboarding 1.11 and bootstrap-harness/rules.md. Assigned this
swimlane by the founder. No git commands permitted.

Bar: a minimal working experiment with an accurate surviving record of failures
and verification, not production readiness.

Implemented `task-1-hello/hello.py`, requirements, and run instructions.
Created a local `.venv`; Python syntax check passed. Dependency installation
failed twice with a transport `InvalidChunkLength` error. SDK import/runtime
checks could not run. Live verification is also pending: the starting process
environment had no OPENAI_API_KEY; this swimlane was absent. Task is not complete.

Next touchpoint: founder arranges credentials privately; resolve package download
failure, verify SDK imports, and perform the first live call. See
`mem/task-1-decisions.md` for assumptions and verification record.
