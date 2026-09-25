# gpt-anthropic-harness

GPT lineage; Claude Agent SDK with Claude models only. Read `../rules.md` before
working. Only this swimlane is writable for this assignment; no git commands.

## Bar and scope

Bar: Tasks 1–4 are useful prototypes whose behavior and failures remain recorded.
This is the NIMOI persistent testbed, not a production release.

## Current state

Task 1 passed with the existing user-installed SDK 0.2.101 and normal terminal
permissions: `python -X utf8 task-1-hello/hello.py` printed `Hello, World! 👋`
and exited 0. Use UTF-8 mode: the first live attempt without it failed with
UnicodeEncodeError. See `task-1-hello/README.md` for usage and
`mem/task-1-record.md` for assumptions, cross-lane evidence, and failures.

Follow-up: the normal user Python package directory is inaccessible from this
command environment (WinError 5). Claude's lane records an installed SDK and
successful run; GPT Codex succeeded after explicit normal-terminal permission.
Founder subsequently authorized normal terminal permissions for any Python
harness driver developed under this lane's assigned tasks. See the verbatim
authorization in mem/task-1-record.md. The user installation is the verified
route; the task-local venv has no SDK because three download attempts failed.

Task 2 passed and is ready for founder review. `task-2-tooling/driver.py` logs
messages, exposes Python add, disables built-ins, checks tool/server inventories,
and records usage/account/rate information. Live addition returned 42.0; the
execution probe explicitly declined to run code. Six offline tests passed.
Run with `python -X utf8 task-2-tooling/driver.py` under normal terminal permissions.

Evidence: `task-2-tooling/runs/20260923T204905Z-e6145efb.jsonl`, both scenarios
passed, exit 0. Model alias resolved to claude-sonnet-4-6. Final cumulative SDK
cost $0.013132, not a measured subscription charge. A failed preflight and an
overbroad usage-field redaction are preserved in the records; the logger fix
passed an offline regression check. See `task-2-tooling/README.md` for usage and
limits, and `mem/task-2-record.md` for assumptions, failures and detailed evidence.
Task 3 implemented: `python -X utf8 task-3-ui/app.py` opens a local web UI with
task-2 capabilities, one new conversation per process launch. Refresh keeps the
current conversation; Close session shuts down the server. Eleven offline tests
passed. Two live browser turns verified Python addition, conversation memory,
execution refusal, refresh and usage. Clean idle shutdown verified. Evidence:
`task-3-ui/runs/20260924T155746Z-569a7db6.jsonl`. See `task-3-ui/README.md` for launch
and extension points; `mem/task-3-record.md` preserves choices, results and limits.
Standing terminal permission applies. Earlier task artifacts remain unchanged.

At handoff 2026-09-24: fresh empty UI running at http://127.0.0.1:55470 (PID 36500),
journal `task-3-ui/runs/20260924T160156Z-ecb1ebce.jsonl`. Browser shows Ready; no
prompt sent in this new process. URL/PID are temporary. Close session stops it.

Task 4 implemented in `task-4-ledger/`: one fresh scribe ledger per launch, harness
events with type tags, protected agent ledger notes, bounded NIMOI read tools,
and test-pilot/onboarding prompt. Run `python -X utf8 task-4-ledger/app.py`.
[checked: 13 offline tests and inspect_ledger.py --smoke] Two live turns exercised
all five tools, note revision and rejection of a harness overwrite. Closed test
ledger `chat-20260924t202304z-faad61817f` validates with no findings/lease.
Read `mem/task-4-record.md` for failures and pilot discrepancies (onboarding-order
and runtime inference), `task-4-ledger/README.md` for usage and boundaries.

Task-4 handoff: http://127.0.0.1:57738 (PID 35024), fresh empty conversation and
ledger `chat-20260924t203008z-07b6223a3a`. [checked: browser Ready and launch output]
No model prompt sent in that session. Close session closes the ledger/server.
State: completed — task 4 meets the prototype bar; awaiting founder direction.

2026-09-25 restart: prior task-4 session closed cleanly and validated without
findings or a lease. Fresh UI Ready at http://127.0.0.1:57738, PID 41832,
conversation b0ab1763, ledger `chat-20260925t123458z-71b7535b40`.
No model prompt sent during restart; prior session had reported revoked OAuth.

Second requested restart, 2026-09-25 09:01 local: prior ledger closed and validated
without findings/lease. Current UI http://127.0.0.1:57738, PID 35720,
conversation d2b03ab6, ledger `chat-20260925t130116z-ce16642659`.
[checked: browser] User's test is receiving a response and invoking read tools.

Format review 2026-09-25: all four session files pass wire-format checks; three
closed hashes match, active head has only expected unclosed finding. See
`mem/task-4-format-review.md` for evidence and the per-chat versus per-project
ledger design difference. Read-only review script: `task-4-ledger/audit_format.py`.

Message text update 2026-09-25: text bodies now carry human/agent authors;
harness metadata references them with wikilinks and exact IDs. 16 offline tests
pass. See `mem/task-4-message-text.md` for format, decisions and reader limitation.
Updated UI Ready at http://127.0.0.1:57738, PID 16300, conversation 83151704,
ledger `chat-20260925t134206z-9afbffcf4d`. State: completed; no new model test sent.

`mem/task-4-spec.md` is the task-4 reproduction back-specification: root
requirements/decisions with subordinate choices and limits, including text authorship.
