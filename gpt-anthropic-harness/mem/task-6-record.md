# Task 6 — hybrid governance

Bar: useful prototype behavior, informative failures and truthful surviving records; not production.

2026-09-28 — prototype completed. Founder: "re-read rules and proceed with task-6-hybrid."
Read onboarding 1.12 and current rules, including 6a–f and Codex exception 5g.

Plan: preserve task 5; derive task-6-hybrid in this lane. Retain common bounds,
ledger, filesystem and tools; route Claude through its SDK and GPT through a
task-local adaptation of GPT/Codex's App Server transport/policy. Consulted the
official protocol at https://learn.chatgpt.com/docs/app-server and peer source.
The other lanes remain read-only; no git commands.

Decisions: the governor is Opus and receives communication/dispatch/read/note
tools, not task execution tools. It holds the launch's delegation ceiling, while
role restrictions independently limit its own actions. Owners may be Opus/Fable
or GPT Astra/Sol; workers may be Sonnet/Fable/Opus or GPT Terra/Sol/Astra. Model
aliases resolve explicitly; unsupported models fail rather than silently falling
back. Local runtime catalogs establish availability, not assumed rankings.

Owner governor_request pins an owner-authored justification note and awaits a
future. New owner tools are blocked while that request is pending. Already
dispatched workers may finish within existing grants. The governor is notified
through its serialized turn queue and can respond or ask the human. Human
approval is recorded explicitly if the governor marks a request as needing it;
it cannot be supplied by another agent. Approval never widens immutable bounds.
A governor can dispatch separately authorized work within the launch ceiling;
actions beyond that ceiling require human changes outside the agent tool surface.

Lifecycle: owner completion waits for its workers and shutdown joins all actors
before ledger close. Governors do not grade task quality; owners remain accountable
for results and institutional faithfulness. UI keeps governor chat separate from
the owner/worker activity and shows requests and their decisions.

Limits: native Codex exec may remain required as a tool dispatcher (rule 5g).
Disable other native surfaces, decline runtime approvals, monitor raw calls and
document observation versus enforcement. No native SDK delegation is used.

## Verification and discrepancies — 2026-09-28

[checked: unittest] 14 tests passed. Role/model restrictions, grant subsets,
blocked-owner tool refusal, pause of the active deadline, explicit human gate,
human denial precedence, reserved/pinned outputs, file/execute separation,
owner/worker cleanup order, cancellation before startup, Codex tool cleanup,
HTTP admission and prose authorship were exercised. Human decisions were synthetic
test inputs; no live person was asked to approve a smoke-test action.

First live run: chat-20260928t183938z-2eb79b6017. Opus polled the GPT owner,
located its request by scanning logs, tried writing the protected reserved reply
once (refused), then properly approved it. The owner ran the approved script,
exported the pinned draft and received the expected execute refusal. The governor
exhausted its 16-turn limit before the worker phase. This was a harness turn-flow
failure, not completion. Shutdown stopped the owner; ledger closed with no findings.

Correction: governor instructions now explicitly finish the current turn after
dispatch/decision, allowing automatic queued notifications to run; no polling.
They also explicitly distinguish a new decision note from the reserved response.
The turn cap was retained. New deterministic cancellation coverage found that
re-cancelling a Codex tool during its finally block interrupted cleanup; shutdown
now joins an already-cancelling task without cancelling it again. Failed owners
also cancel and join their workers before finalizing, including before-start jobs.

Successful live run: chat-20260928t184225z-3f936b7634. Opus 5.5 governor,
GPT 6 Sol owner, Claude Sonnet 5 worker, three separate sessions. The owner
blocked on its pinned request; a separate governor turn approved within prior
human authorization; grants did not change. Owner resumed, executed safe_test,
exported the draft, received its expected execute refusal, dispatched the worker,
read the worker/script evidence and completed. Worker received its expected
out-of-bounds read refusal and returned add(19,23)=42. Governor relayed the report.

[checked: inspect_ledger --smoke --closed] 611 harness events, all eleven tools
successfully exercised, both expected refusals, no work by owner between request
and answer, worker finalized before owner, truthful text references and authors,
closed/unleased with no problems. Snapshot saved beside live_smoke.py. Reported
Claude-family SDK USD was 0.3281558; Codex USD unavailable and excluded, not zero.

[checked: runtime metadata] Codex code_mode_host=true and unified_exec=true
despite the requested unified_exec=false. Core shell/delegation/external surfaces
checked by startup were false; no disallowed call was observed. This is the rule
5g exception, not claimed OS isolation. Claude SDK usage also included auxiliary
Haiku accounting; selected governor model stayed Opus. This accounting is retained
and is not presented as an extra harness-owned agent. Other allowed models and
reverse provider-role combinations were not separately live-tested.

[checked: browser] Read-only smoke snapshot showed governor chat separately from
owner/worker transcripts, roles/models/status/bounds/results, incomplete cost
accounting and the labelled synthetic human approval card. Agent transcript open
state is preserved; unchanged request cards are not rebuilt during worker updates,
so typing a decision is not interrupted. Fixture server was stopped afterward.
Codex echoed task input now links the pinned assigning-agent note instead of being
mislabelled as fresh human text; completed-turn echoes link agent text records.
These final adapter changes have deterministic coverage; the full live run above
preceded the echo-normalization change.

Fresh handoff UI: http://127.0.0.1:64744, PID 34404, conversation 59e5155d,
ledger chat-20260928t184540z-05e6dcdaff. Ready; no model prompt sent. Existing task-5
UI left untouched. Full contracts and limitations: task-6-hybrid/README.md.

State: completed prototype; ready for human review. No ticket system, persistent
resume, permission promotion or hard budget was added. Human approval beyond a
provider connection's lifetime is not guaranteed.
