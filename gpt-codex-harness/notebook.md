# GPT / Codex harness

## Current: task-6 hybrid parked for founder guidance

Bar: useful three-layer behavior, informative failures and surviving records.
2026-09-28: read latest onboarding and rules; built task-6-hybrid separately.
Both runtime adapters, role/model policy, subset delegation, blocking owner
requests, governor decisions, human approval UI and agent visibility are present.

[checked: receipts in mem/task-6-record.md] 16 offline tests passed; four focused
SDK checks passed after a streaming identity fix. Real App Server tool capture
passed on Codex 0.158.0-alpha.2. Live Opus governor -> Astra owner -> Sonnet child
completed onboarding, add, approved Python, a blocking governor request, and
ledger-to-file materialization. Human approvals are tested offline only.

DISCREPANCY: live governor exceeded the intended task-6 role by inspecting the
owner's artifact and critiquing report wording beyond safety/governance. Stopped
under rules.md Authority and escalated. No role fix attempted after discovery.

Status: parked pending founder guidance about this prototype role-enforcement
defect and the boundary between governance checks and quality review. Driver
stopped normally; live ledger hybrid-20260928t184543z-230a8f03 is closed/unleased
with no structural inspector findings. There is no fresh running task-6 session.
The streaming fixes have fake-SDK coverage but await a final-code live run.

Entry: task-6-hybrid/README.md. Full record, receipts, limitations, proposed next
step and remaining work: mem/task-6-record.md. No git commands used.

## Task-5 peer-review improvements completed

Bar: informative prototype failures, clear ownership and surviving records; no
production isolation claim. 2026-09-25: founder authorized improvements and rules
5g permits Codex exec with restrictions and documented limits.

Fixed exact-directory listing visibility. Added immutable bounds.json configuration,
bounds-selected tools, a full-read onboarding gate, shared AgentRuntime/AgentRecord,
semantic events/result indexing, compact checkpoints (--stream-log detailed for
full fragments), raw-call monitoring and a read-only ledger inspector.

[checked: test receipts in mem/task-5-improvements.md] 45 offline checks passed.
Actual local-provider captures: gpt-6-astra needs exec; gpt-5.5 works with exec host
confirmed off. Neither current capture offered native collaboration, shell/patch,
browser or external MCP tools. Earlier peer captures differed. The live mixed-model
parent/child run passed; both processes exited normally, ledger closed without
findings/lease. Two pilot receipt-parsing errors were refused and recovered; kept
as evidence. Restriction flags, offered tools and observed calls are separate facts.

Entry: task-5-subagent/README.md. Editable parent bounds: task-5-subagent/bounds.json.
Run capture: audit_surface.py --model <model> [--real-config]. Inspect a saved launch:
inspect_ledger.py <launch-name> --closed. Run tests from the task directory with
python -B -X utf8 -m unittest discover -v.

Fresh driver Ready at http://127.0.0.1:57406/; ledger
chat-20260925t212502z-3785c800. [checked: launcher and browser AX; no prompt sent]
Next: founder review. Status: completed at the stated bar. No git commands used.

Original task-5 delivery and 35-test/live receipts: mem/task-5-record.md.
Review decisions, discrepancies, captures and final live receipts:
mem/task-5-improvements.md. Prior task directories and ledgers remain records.

## Task 4: completed at the prototype bar

2026-09-25 back-specification completed: `mem/task-4-spec.md` describes current
task-4 requirements and design decisions in a three-level hierarchy, including
the message-authorship change. Bar: enough behavioral and policy detail to
reproduce the prototype and preserve its known limits. [checked: current source,
prompt, README and task records; documentation only]

2026-09-25 follow-up completed: transcript text bodies are authored by the human
session user or agent, followed by harness message records linking to that text.
Bar: correct authorship and durable ordering without losing stream/failure
records. Nineteen offline checks and one live conversation passed; the closed
test ledger has no reader findings. [checked: tests/browser/ledger receipts in
`mem/message-authorship.md`] Existing ledgers remain in their original format.

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
driver runs), relaunched after the logging update on 2026-09-25 with ledger
`chat-20260925t135031z-d637a55a`.
Fresh empty UI showed Ready. [checked: driver output and browser AX] No git commands performed.
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
