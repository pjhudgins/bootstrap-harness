# Task 3: local conversation UI

2026-09-23, GPT session. Re-read rules after the founder corrected the task-3
self-reference to task 2. That resolved the ambiguity; no other scope blocker.

Bar: a usable local prototype carrying all five task-2 capabilities, with an
accurate retained record. Not a production web service or security proof.

## Consequential assumptions and design

- One new ephemeral App Server thread per Python driver launch. All messages
  during that launch share it; browser refresh/tabs do not create new threads.
- Python standard-library server, bound only to 127.0.0.1; no dependencies,
  hosting, recurring jobs, or changes to persistent Codex configuration.
- Web UI launches in the default browser unless --no-browser is requested.
- Task-2 transport copied locally into task 3 and adapted; earlier prototypes
  are preserved. Tools register through a small Python schema/handler registry.
- Same authenticated ChatGPT path and tool restrictions as task 2, including
  documented unified_exec caveat. No environment access; Python add remains.
- One worker owns the protocol and serializes user turns; the browser polls a
  projection of recorded events to show streaming replies/tools/usage. New input
  while a turn runs is rejected rather than silently queued or duplicated.
- Each launch gets a unique JSONL journal; failed attempts also survive. Full
  config and account identity payloads are omitted using the task-2 policy.
- End session closes the App Server and web listener; closing only the browser
  leaves the driver alive. Relaunching the driver creates a fresh conversation.
- Driver normal-terminal privileges were already authorized for this swimlane.
- Official lifecycle reference consulted: https://learn.chatgpt.com/docs/app-server

## Implementation and verification

Task 3 is complete at the prototype bar. Entry point: `task-3-ui/README.md`.
The Python server serves plain local HTML/CSS/JS, keeps one protocol owner,
projects streamed messages/usage into state, and exposes the Python add tool.
No packages were installed and no persistent Codex configuration was changed.

First live UI run: `task-3-ui/runs/20260923T210356Z-af07467c.jsonl`.
Thread: `01a0d015-1ed7-73e3-89f8-007c77328afa`.

- Submitted the UI's suggested addition; actual Python arguments 19.25, 22.75
  returned 42.0. The tool card and agent reply displayed 42.
- Sent "Use add to add 8 to that result." Python received 42, 8 and returned 50.
  Both turns share the same thread, demonstrating conversation continuity.
- Refreshed the page; all messages, both tool calls and usage remained.
- Sent an execution probe asking Python to print TASK3_EXECUTION_PROBE without
  simulation. The reply said no Python execution tool was available.
- Journal contains 163 records: 3 user messages, 5 completed agent messages,
  2 dynamicToolCall items, 2 Python result records, 3 completed turns, usage and
  four rate-limit snapshots. No commandExecution, fileChange, mcpToolCall or
  collabToolCall item, stderr record, session_failed or cleanup_failed appeared.
- Final cumulative total: 42,536 tokens = 42,393 input + 143 output; 33,152
  cached input is included in input. These are recorded tokens, not inferred
  subscription costs. Account windows are shared with other Codex activity.
- Browser console had no captured error/warning. Visually inspected the local
  page and conversation at its normal 877 x 912 viewport. Added sidebar padding
  to keep values clear of the scrollbar; mobile breakpoints were not tested.
- Clicked End session: UI showed Ended, Python driver exited 0, App Server
  returncode 0 without forced termination. The original session_end entry says
  "stopping" because it preceded the final status transition. Code now records
  the terminal status after that transition; original evidence is preserved.

Second live launch: `task-3-ui/runs/20260923T211100Z-254b58d2.jsonl`.
Thread: `01a0d01b-8a23-7880-b599-c36699846047`.
Verified a different thread ID, Ready UI, zero messages/tools, unknown token
usage, and loaded account limits. Left this fresh session open at
http://127.0.0.1:50063 for founder review (only while the driver remains running).
This launch uses final code. Launch tests used --no-browser and opened the
printed URL in Codex's in-app browser; the default-browser launch path uses
Python webbrowser.open and was not separately exercised.

Eleven offline unittest checks passed: single-turn admission, malformed input,
stream completion without duplication, independent launches, detached state,
cumulative usage replacement, registered Python tool result/error handling,
wrong-thread rejection, optional quota errors vs broken transport, Host/Origin
validation, static route boundaries, and downloadable logs. Several checks are
grouped within one test. No models are invoked by this suite.

During live verification, the code was tightened to distinguish optional quota
RPC errors from transport failures, generalize Python tool results, serialize
journal downloads with writes, preserve error status during shutdown, and avoid
a pending UI poll overriding Ended. Registry/error behavior is covered offline;
final startup was verified on the second launch. The three model turns above
ran before these final transport/cleanup refinements.

## Limits and next touchpoint

Same task-2 runtime caveats remain: unified_exec reports enabled despite its
override, and code_mode_host must remain enabled to dispatch the Python tool.
Neither task 2 nor task 3 establishes an adversarial execution boundary.
The app displays this explicitly under Restriction details.

One launch is one shared local conversation; no history/resume, markdown
renderer, attachments, or model selection were added. Journals remain local.
No extra scope or privilege was needed. Await founder review/next assignment.
