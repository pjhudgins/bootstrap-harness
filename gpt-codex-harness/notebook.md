# GPT / Codex harness

## Current: task-2-tooling complete at the prototype bar

2026-09-23: `task-2-tooling/tooling.py` demonstrated all five capabilities with
ChatGPT authentication: message logging, restricted default execution tooling,
a Python `add` call returning 42.0, tool-call logging, and token/account-limit
records. The second turn reported no shell/Python execution tool; no execution
event was observed. Seven offline checks passed.

Entry point: `task-2-tooling/README.md`. Live evidence:
`task-2-tooling/runs/20260923T202252Z-f73168cf.jsonl`. Decisions, failed attempts,
and verification: `mem/task-2-record.md`.

Two findings matter for successors: `unified_exec` reports true despite disable
overrides (shell disabled + empty environments restricted the observed run),
and disabling `code_mode_host` breaks even the Python tool callback. Code mode
itself is disabled. These are experimental/version-dependent controls, not a
proven security boundary. All run journals, including failures, are retained.

Next: await founder review or the next assigned task.

## Task 1: hello

2026-09-23: Founder assigned this swimlane after changing rules.md to specify
Codex App Server called from Python. Read the updated rules. Work is confined
to hello world; the broader requirements discussed in chat are not this task.

Bar: one real agent response and an honest record of verification or failure.

Implemented a Python standard-library stdio client in `task-1-hello/hello.py`.
Task 1 complete: on 2026-09-23 the standalone driver passed its ChatGPT account
check, received `hello world`, printed it, and exited 0. Normal terminal
permissions were necessary; the restricted command sandbox prevented startup.
The installed protocol requires `sandbox: read-only`. Nonfatal plugin/shell/MCP
startup diagnostics remain documented in `mem/task-1-record.md`.

The founder authorized normal-terminal execution outside the command sandbox
for all harness-driver scripts under development in this swimlane. This does
not change an agent's own sandbox. The original authorization is in the record.

Task 1 run instructions are in its README; the optional proxy path remains
unverified outside the sandbox. Task 2 adds the tooling prototype separately.
