# Task 3 record — 2026-09-24

Founder: "proceed to task 3". Read onboarding_1.11.md and current rules.md.
Task 3 now explicitly references task 2 (the earlier self-reference was corrected).
Standing normal-terminal authorization for Python harness drivers applies.

Bar: a usable conversation prototype with truthful, surviving records of failures.
Keep SDK 0.2.101, the sonnet alias and existing authentication. One SDK client per
process launch; no resuming saved sessions. Refreshing the browser retains the
current process's conversation. Each launch writes a fresh JSONL journal.

Consequential choices: loopback-only Python standard-library HTTP server, one
serialized conversation, all built-ins disabled and only validated Python add
exposed. Retain task-2 hook/permission controls and strict MCP config. UI consists
of local HTML/CSS and a small browser script; all harness mechanisms remain Python.
The browser polls state; polling never starts a model request. Only Send does.
No dependencies added. Task-2 files remain as their original experiment record;
copy policy/logger into task 3 and evolve them here.

Local HTTP requests require the exact loopback Host. Mutations require same-origin
JSON; no CORS. This protects against ordinary cross-site requests, not hostile
local processes. No external assets. Render agent/user strings as text.
Account display is limited to provider/plan. Journals keep redacted SDK messages,
tool events, usage and failure records. Arbitrary secrets pasted by a user cannot
be universally detected; the UI tells users not to enter credentials.

Checkpoints: implement UI/driver and offline tests, then inspect a real browser
conversation (addition, memory across turns, execution restriction), stop it and
verify a new launch is empty. Leave a fresh UI for founder review if verification
succeeds. Errors that can desynchronize the SDK session fail closed until restart.

## Verification

- Eleven offline tests passed: task-2 validation/redaction checks, one pending
  turn at a time, isolated snapshots, new-launch state, input/stop validation,
  concurrent journal sequencing, and actual local HTTP request/host/origin checks.
- Live browser run: runs/20260924T155746Z-569a7db6.jsonl. Launched with normal
  terminal permissions; inspected the rendered UI and submitted via Send, then
  via Enter. Model resolved to claude-sonnet-4-6. No extra dependency installation.
- First prompt asked to remember "copper fern" and use add(19.25, 22.75). Python
  callback returned 42.0, visible in the activity panel and journal. Reloading
  the page retained the transcript and conversation identifier.
- Second prompt asked for the remembered phrase and a benign Python execution.
  The agent recalled "copper fern" and explicitly said it lacked execution tools.
  No tool call occurred for that prompt. Both turn_complete records share SDK
  session 12ab10e7-8cc1-4ad5-adb6-38fabef97e9c. Init lists only mcp__calc__add;
  post-response MCP checks show only connected calc. No error records.
- Usage: first prompt 1745 uncached input, 0 cached, 210 output; second prompt
  3 uncached input, 1053 cache-creation input, 0 cache-read, 240 output. The UI
  initially displayed only input_tokens, making that second input appear as 3.
  Adjusted the panel to show total input plus the cache breakdown. Reload verified
  1056 input and 1053 cache-write tokens. Original raw journals already retained
  every one of those fields; no record was altered or lost.
- Final cumulative SDK cost $0.018904; five-hour rate status allowed, utilization
  null. No subscription percentage inferred. Journal sequence 1–63 is contiguous.
- Clicked Close session. Final journal records conversation_closed success=true
  and server_stopped worker_stopped=true. Original PID 39348 no longer exists;
  stderr file is empty. This verifies idle shutdown, not interruption of an
  actively generating reply or timeout cleanup.

## Limits for review

The UI renders Markdown as literal text. Error/timeout paths are implemented,
but a live network failure and the 180-second timeout were not forced. This is
tool configuration plus benign probe evidence, not proof against adversarial
escape. Local HTTP has no per-user authentication. Closing the browser tab alone
does not stop the server. Task-1/task-2 artifacts remain unchanged.

## Fresh launch for founder review

Started a new process (PID 36500) at http://127.0.0.1:55470; journal
runs/20260924T160156Z-ecb1ebce.jsonl. Browser shows Ready, an empty transcript,
no usage, and new conversation identifier ac3a0d51 (previous 53228d5b). No model
prompt sent in this fresh process. Left its browser tab and hidden Python process
running for the founder. Close session stops it; future launches choose a new
port unless --port is specified. This address/PID are observations, not stable
configuration. Task 3 ready for review.
