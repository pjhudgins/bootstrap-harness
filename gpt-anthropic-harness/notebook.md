# gpt-anthropic-harness

GPT lineage; initially Claude Agent SDK, now hybrid for task 6.
Read ../rules.md before work. Only this swimlane is writable; no git commands.

Bar: useful prototypes, informative failures and truthful surviving records; not production.

## Current state — 2026-09-28

Task 6 prototype completed. Current driver: task-6-hybrid/app.py.
Read task-6-hybrid/README.md for roles, contracts, limits and code map;
mem/task-6-record.md for decisions, discrepancies and verification receipts.

Opus governor handles human communication and dispatch. Claude/GPT owners own
quality and can dispatch bounded workers. Owners can make self-blocking requests;
governor approval/denial or explicit human decision cards govern release without
expanding bounds. UI separates governor chat from owner/worker messages.

[checked: 14 deterministic tests, mixed-provider live smoke, closed-ledger
inspector, browser] Opus 5.5 -> GPT 6 Sol owner -> Sonnet 5 worker completed;
all eleven tools exercised; both boundary refusals observed. Final live ledger:
chat-20260928t184225z-3f936b7634, 611 events, closed/unleased with no findings.
First run exhausted the governor's turn limit; event-driven prompting fixed the
polling behavior. Both runs survive. Details and test coverage limits are in mem.

Fresh UI Ready at http://127.0.0.1:64744, PID 34404, conversation 59e5155d,
ledger chat-20260928t184540z-05e6dcdaff. No prompt sent in this handoff session.
[checked: launcher/browser] URL/PID are temporary. Close session stops providers
and scripts before closing the ledger/server. Task-5 UI was not restarted or stopped.

## Running

Founder authorizes any Python harness driver developed under assigned tasks with
normal terminal permissions (verbatim in mem/task-1-record.md). The installed SDK
is only available there. Existing subscription sign-ins; no copied credentials.
From task-6-hybrid, run app.py, unittest discover, live_smoke.py, inspect_ledger.py
or preview_ui.py as documented in README. Live smoke sends real model requests.

Known Codex exec/native-isolation limit is explicitly allowed by rule 5g; see
README. Only the three models above were live-verified. Human decision behavior
has deterministic tests and a synthetic browser fixture, not a live human wait.
No automatic bound expansion, persistent recovery, or hard family budget.

## Earlier work and continuity

Tasks 1–5 remain separately preserved. Task 5 peer improvements: 22 offline
tests, live smoke, valid closed ledger chat-20260925t211931z-e36e4a0e79;
details in task-5-subagent/README.md, mem/task-5-record.md and
mem/task-5-improvements.md. Former UI was 52186 / PID 40288 (historical).

mem/task-4-spec.md is the requirements/design back-specification, including
human/agent text authorship and harness envelopes with exact references.
mem/notebook-history-20260925.md preserves earlier notebook history.
Individual mem/task-N-record.md files retain earlier decisions and receipts.

State: completed prototype — ready for founder review and the next iteration.

