# Task 5 peer-informed improvements

Bar: useful prototype behavior, informative failures and truthful surviving records; not production.

2026-09-25 — active. Founder authorized the proposed improvements in chat.

Scope: implement the eight recommendations from the peer review in our task-5
directory: actor-bound ledger access, explicit agent context and Markdown prompts,
readable bounds/diagnostics and fresh workspaces, consolidated tool definitions,
subprocess cancellation and shared ledger-failure shutdown, readable output
contracts, scripted SDK-boundary tests, and child/usage visibility.

Consequential decisions: preserve nonblocking children, pinned instruction IDs,
explicit grants, create-only file export, and the no-write/execute-overlap rule.
The optional hash-checked replacement feature is deferred: the accepted core
refactoring does not require changing file overwrite semantics. Short mounts map
to fixed paths for a launch; existing NIMOI-relative read paths remain accepted.
Execution and child results will require explicit fresh pilot/ destinations,
checked for readability before work starts. No implicit grant expansion.

Peer sources: GPT/Codex access facade, mount names, result reservation and stop
signal; Claude/Codex bounds diagnostics and scripted provider testing;
Claude/Anthropic prompt files, shared context and per-agent cumulative usage.

DISCREPANCY: task-5-subagent/capabilities.py — expected cancellation to stop associated work; source review found cancelling asyncio.to_thread leaves the subprocess running until its timeout, 2026-09-25.
DISCREPANCY: task-5-subagent/capabilities.py — expected returned output to be consumable; source review found restricted ledger.read grants may exclude generated execution/ names, 2026-09-25.

## Implemented

Actor-bound LedgerAccess centralizes ledger grants and pinned resolution. SDK
normalization is in messages.py; AgentContext separates identity/role/delegation.
Markdown prompts and a single tool registry replace embedded prose and parallel
schema/description/dispatch structures. Bounds parse selectors once, report all
child subset failures, and resolve fixed workspace:/ and scripts:/ mounts.
Each launch gets a new workspace. Shared filesystem listing removes duplicated
filter/pagination code. Tools requiring empty grants are omitted but handlers
continue enforcing bounds. Tool refusal, argument error and operational failure
have distinct events; initial hooks explicitly report dispatch validation.

Execution and child result destinations are reserved before work and must be
readable under explicit grants. Output placeholders, final text, authorship and
exact references survive in the ledger. Subprocesses observe shared shutdown and
per-agent cancellation; awaiting tasks wait for cleanup. Typed public child jobs
exclude internal task handles. Ledger failure stops the family. UI shows labelled
child text, child state/details and per-agent/family costs; expanded details stay
open during polling. Session changes clear stale activity/cost/error display.

## Verification

[checked: python -B -X utf8 -m unittest discover -v, normal terminal, 2026-09-25]
22 tests passed in 12.639 seconds. The nine new SDK-boundary tests use installed
SDK types/options and a scripted client, with no provider calls. They cover
interleaved identities and text references, cumulative cost aggregation, error
text authorship, native-tool rejection, cancellation during a real subprocess,
output readability before effects, mounts/diagnostics/tool availability, child
result contracts/cleanup, shared ledger failure/late callbacks, and idle teardown.
(Several checks share a test method.) Final prompt/teardown follow-up tests also
passed after separating prompt role from delegation availability.

[checked: two directed live_smoke.py runs; second inspect_ledger.py --smoke --closed]
The first integration ledger chat-20260925t211547z-c353f7e485 passed all directed
assertions and closed validation but emitted an ignored Windows subprocess
transport warning during process teardown. The repeated run after the stop-race
fix, chat-20260925t211931z-e36e4a0e79, passed and exited 0 without the warning.
It has 657 lines, 201 harness events, a valid closed head, no lease and no findings.
All nine tools succeeded, both directed refusals were recorded, and parent add
ran while the independent Haiku child was active. Parent Sonnet-5 had nine tools;
child Haiku-4.5 had five (no write/execute/delegation tools without grants).
Parent's two turns shared a session; the child had a different session.
Child result pilot/child-result at 20260925T211931Z:540 is child-authored and
readable by both actors. Family reported cost was $0.1327895, consisting of
parent $0.0889994 plus child $0.0437901; not a subscription charge.

Evidence: task-5-subagent/.runtime/improvements-live-smoke.txt and
.runtime/improvements-summary.json, plus the two preserved ledgers. The second
live run verifies the shutdown fix, not arbitrary interruption of a live remote
model call; active cancellation was tested through the scripted SDK boundary.

[checked: browser accessibility, screenshot and read-only preview_ui.py]
The populated UI rendered labelled child prose, completed state, expandable
instruction/bounds/result references and the expected parent/child/family costs.
The temporary preview server/tab were closed. The fresh UI was reloaded and Ready.

## Discrepancies encountered

DISCREPANCY: test_runtime.py failure fixture — expected teardown of a synthetic failed ledger; mocking public scribe.write bypassed scribe's real crash handling and left its Windows file handle open. Moved injection to scribe._write_all so its crash path runs; test then passed. First failed temporary fixture directory tmpwdgzgdcf remains as test evidence, 2026-09-25.
DISCREPANCY: first live smoke process teardown — expected quiet exit after SDK cleanup, found ignored closed-pipe transport warnings despite successful/closed ledger. Review found the shared stop watcher could cancel the parent during idle SDK teardown. Added a closing guard and slow-teardown regression; repeated live smoke exited without warnings, 2026-09-25.
DISCREPANCY: browser after server restart — expected fresh UI state, found prior activity/cost/error text retained by conditional DOM updates despite the new conversation ID. Reset empty sections and session identity/reconnection errors; refreshed UI verified clean, 2026-09-25.
DISCREPANCY: second live smoke parent prose — called the child's onboarding a "no-op" and later claimed it could not see the child's transcript. The recorded child reads/onboarding event and parent's ledger.read ** contradict these claims. Preserved as pilot reasoning errors, not harness access failures, 2026-09-25.

## Handoff

Fresh UI: http://127.0.0.1:52186, PID 40288, conversation
1c9d5a7d015b42a5a6fbec0d44c6eae0, ledger chat-20260925t212032z-6aea821330.
[checked: /api/state, launcher output and browser Ready] No model prompt was sent
in this handoff session. Its workspace uses the same ledger-name suffix.
The previous user session chat-20260925t193058z-f3464d0fbd was gracefully closed
and validated with no findings or lease. No task-4 files or peer lanes changed.

State: completed — the eight core peer-review improvements are implemented and
verified to the prototype bar. Optional file replacement remains deferred;
fs_write is still create-only. No hard family spending cap was introduced.
