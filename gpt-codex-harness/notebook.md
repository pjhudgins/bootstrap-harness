# GPT / Codex harness

## Current: task-4-ledger completed at the prototype bar

2026-09-24: Integrated the existing bootstrap-ledger scribe into task-4-ledger.
Fresh ledger per conversation, attested harness/agent authors, protected tags,
agent ledger tools, bounded NIMOI read tools, and an onboarding/test-pilot system
prompt. Earlier tasks preserved.

Bar: informative prototype failures with surviving records, maintaining tasks
2/3 and checking ledger/path boundaries.

Live: onboarding read, 42 then contextual 50, agent note/read/revision, refused
harness overwrite and outside-root read, refresh persistence, clean close and
fresh relaunch. Scribe reader found no issues in the closed ledger. Thirteen
offline tests passed. [checked: tests/browser/ledger receipts in the record]

The pilot discovered an explicit-agent-tag rejection; it recorded the failure,
which is preserved. Fixed validation and added a regression check. Existing
unified_exec limitation remains; no general isolation guarantee is claimed.

Entry: `task-4-ledger/README.md`. Evidence, assumptions and discrepancy:
`mem/task-4-record.md`. New session left ready at http://127.0.0.1:52201 (while
driver runs), ledger `chat-20260924t202827z-b92094ed`. No git commands performed.
Next: founder review or next assignment. Status: completed at the stated bar.

## Task 3: local UI

2026-09-23: The founder corrected task 3 to reference task 2, resolving its
self-reference. `task-3-ui/serve.py` now launches a local conversation UI with
the task-2 Python add tool, message/tool journals, usage/limits, and the same
execution restrictions. One new ephemeral thread per driver launch; page
refresh keeps the active conversation. No new dependencies or persistent
configuration changes.

Live UI checks: addition returned 42; a follow-up used context and returned 50;
refresh retained both; an execution probe reported no Python execution tool.
No restricted execution item was observed. End session shut down with exit 0;
relaunch produced a different, empty thread. Eleven offline checks passed.
The unified_exec caveat below still applies.

Entry point: `task-3-ui/README.md`. Evidence and exact scope/limitations:
`mem/task-3-record.md`. Model-turn journal:
`task-3-ui/runs/20260923T210356Z-af07467c.jsonl`. Fresh final-code launch:
`task-3-ui/runs/20260923T211100Z-254b58d2.jsonl`, left ready for founder review
at http://127.0.0.1:50063 (temporary, while that driver remains running).

Next: await founder review or the next assigned task.

## Task 2: tooling

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
