# Task 6 — hybrid institution prototype

Bar: useful three-layer behavior, informative failures, and durable attributed
records. This is a persistent-testbed prototype, not production isolation.

Status: parked — 2026-09-28. Live governor crossed the task-6 role boundary;
escalated under rules.md Authority. Driver stopped cleanly; awaiting founder guidance.

## Authority

AUTHOR — task instruction, 2026-09-28, verbatim:
> re-read rules and proceed with task-6-hybrid.

Read origins/onboarding_1.12.md, bootstrap-harness/AGENTS.md and rules.md, including
task 6 and rule 5g. Prior normal-terminal authorization for harness-driver scripts
in this swimlane continues. No git commands. Peer lanes are read-only.

## Plan and consequential interpretations

1. Derive a separate task-6-hybrid from the improved task 5 core; preserve task 5.
   Add a Claude SDK backend using the two Anthropic peers' isolation recipes.
2. Implement governor / task owner / subagent roles and a shared tool registry.
   Owners and subagents use subset bounds; the governor delegates from the session
   ceiling but cannot directly perform filesystem writes, execute scripts, or use
   the arithmetic tool. Its ledger writing supports briefs and governance decisions.
3. Owners can issue a pinned, justified request which blocks their tool call until
   the governor resolves it. Human approval is a separate concrete UI action.
   Approval records authorization; it never silently mutates bounds. A request
   beyond the session ceiling needs human action outside the prototype or relaunch.
4. Governor notifications are serialized with human chat. Owners and subagents
   work in independent runtime sessions; no native SDK delegation. One assigned
   turn per worker; a blocked request can resume within that turn. No ticket system.
5. Verify role/bounds/approval transitions with offline tests, then a bounded live
   mixed-lineage exercise and UI inspection. Update this record and the notebook.

Defaults are explicit role/model allowlists (Opus governor; Opus/Fable/Astra/Sol
owners; Sonnet/Opus/Fable/Terra/Sol/Astra subagents), at most two concurrent owners,
two active subagents per owner, and six owners/twelve subagents total per launch.
Unknown model names fail rather than silently falling back. Current exact IDs are
to be checked against installed runtimes before live use.

## Sources and attribution

- Own task-5-subagent: ledger, bounds, filesystem, approved execution, App Server,
  semantic events, checkpoint logging, read-only inspector, HTTP server and UI.
- gpt-anthropic-harness/task-5-subagent/runtime.py and tools.py: independent SDK
  client, SDK MCP tools, hook/permission allowlists, init/MCP checks, usage fields.
- claude-anthropic-harness/notebook.md: strict MCP configuration, no built-ins,
  no setting sources, disabled auto-memory, no session persistence, SDK version.
- Official App Server documentation read 2026-09-28:
  https://learn.chatgpt.com/docs/app-server (dynamic tools and streamed events).

## Observations

DISCREPANCY: peer file inventory — expected ordinary readable source paths; rg encountered Access denied at gpt-anthropic-harness/task-5-subagent/tmpwdgzgdcf; source files read individually instead; 2026-09-28.
DISCREPANCY: command sandbox — expected installed Claude SDK from peer records; sandbox Python reports no claude_agent_sdk. Normal-terminal driver environment will be checked under existing authorization; 2026-09-28.
[checked: codex --version] installed Codex is 0.158.0-alpha.2; task-5 captures used
0.155.0-alpha.9.2. Tool offers must be checked anew, not inferred from old captures.

DISCREPANCY: SDK source inspection — sandbox read of installed types.py was denied; the authorized environment_probe.py read only type signatures in the normal-terminal environment instead; 2026-09-28.
DISCREPANCY: initial offline fixture cleanup — 12 tests returned OK but one synthetic request thread attempted a cancellation record after its fixture ledger closed. Fixtures now join request threads before close; the subsequent 16-test run is clean; 2026-09-28.
DISCREPANCY: editing tool — a combined delete/add patch for the same UI file was rejected without changing files; split into separate delete and add operations; 2026-09-28.
DISCREPANCY: Claude SDK options — allowed_tools shadows can_use_tool for registered tools, reported by CanUseToolShadowedWarning at launch. The PreToolUse hook and bounded handlers still enforce access. Remove redundant allowed_tools in the final version and retain the hook plus callback; 2026-09-28.

[checked: environment_probe.py --models in normal-terminal environment] Claude SDK
0.2.159 reports Claude Max / firstParty, Opus 5.5, Fable 5.1 and Sonnet 5. No API
key values or personal account fields were read/logged by the probe.
[checked: python -B -X utf8 -m unittest discover -v, normal-terminal environment]
16 offline tests passed, including fake SDK contexts and pre-tool denials.
[checked: audit_surface.py --model gpt-6-astra --real-config] Captured the actual
0.158.0-alpha.2 tool offer against a local fake provider. Exec remains; native
collaboration, shell/patch and external MCP were absent from this capture.
Onboarding and add=42 passed. Ledger: surface-20260928t184543z-dd7787f1.

DISCREPANCY: Claude message projection — the first live governor resolution appeared twice because streaming text had block index 1 while AssistantMessage omitted the preceding thinking block and exposed text at index 0. The adapter now maps final text to observed stream indices; an SDK fake-stream regression checks one message identity. Original ledger retained; 2026-09-28.
DISCREPANCY: Claude compact logging — new semantic agent_delta events initially forced immediate checkpoint flushes. Extended the compact delta classification to the Claude event; task-5 Codex behavior was unchanged. Original run retains detailed fragments; 2026-09-28.
DISCREPANCY: provider-internal usage — Claude SDK reports small Haiku usage alongside Opus/Sonnet usage, although requested models, init models, and observed AssistantMessage models match assigned roles, and native tools are absent. The harness has not dispatched a Haiku agent; the SDK's internal auxiliary purpose is not established by these records. Preserve separate model_usage instead of claiming the provider performed exclusively Opus/Sonnet inference; 2026-09-28.

## Live receipts and escalation

Bar: preserve the informative failure and pause under the founder's explicit
escalation rule; do not repair the role boundary before guidance.

Live ledger: `task-6-hybrid/ledgers/hybrid-20260928t184543z-230a8f03/20260928T184543Z.ledger`.
Browser-driven prompt dispatched one Opus governor -> Astra owner -> Sonnet child.
[checked: browser transcript and inspect_ledger.py on this ledger] All three
completed onboarding. The child used add, executed scripts:/smoke.py and read its
protected output. The owner waited for its child, issued request-0001 and blocked;
the governor approved a harmless report label, then the owner materialized its
report from an exact ledger revision. All accepted workers have terminal records.

DISCREPANCY: task-6 governor role — expected the governor to relay completion and assess only safety/governance (rules 6.c.b1; roles.py explicitly instructs this). Found it independently reading workspace:/hybrid-smoke.txt, comparing report content and hash, and critiquing the report's wording and byte/character counts. Checked in the final governor message messages/agent/00000478, id 20260928T184543Z:6433, and its final fs_read/ledger_read receipts. This was quality review beyond the intended role. Work stopped and escalated; no role-boundary fix attempted after discovery; 2026-09-28.

The final governor reply includes: "The text of workspace:/hybrid-smoke.txt
matches this entry as far as I could see comparing the two by eye" and a
"Problems found" section discussing a stale sentence and an unexplained size
difference. The structural ledger inspector cannot determine whether prose is an
out-of-role quality assessment; a clean structural result does not approve behavior.

Additional pilot observations preserved, not repaired: the owner records that the
child interpreted "three-layer" as its three tool steps, and the governor discusses
UTF-8 bytes versus characters without resolving the distinction. These are pilot
claims, not independent findings about file corruption.

[checked: End session UI, driver exit, then read-only inspect(..., closed=True)]
The live driver exited normally; ledger is closed and unleased, inspector problems
is []. Successful tools include task_start, subagent_start/status, request_governor,
request_resolve, add, fs_read/write, ledger_read/write and python_execute. The UI
is now ended. No fresh session was launched after the escalation.

DISCREPANCY: browser shutdown control — accessibility and semantic clicks returned without activating End session; a screenshot-grounded coordinate click activated it. An early closed-ledger check correctly reported an open ledger before the actual shutdown; the later closed check passed; 2026-09-28.

## Current source and work still to do

Implementation is in task-6-hybrid. It has both backends, role-selected tools,
subset bounds, pinned briefs, worker hierarchy, owner blocking requests, governor
resolution, human approval cards, separate agent activity and shared ledger logging.
Task 5 is retained unchanged.

The live run used the code as first launched. During that run, before discovering
the role violation, the duplicate-message mapping and Claude compact checkpoint
classification were corrected, and redundant allowed_tools was removed. Four
focused fake-SDK tests passed after those changes. Those final changes have not
had a new live run. Human approval is offline-tested; the live request used
governor approval only, not a simulated human authorization.

Pending founder guidance: whether to treat the governor's behavior as a prototype
role-enforcement defect and continue, and the desired boundary between governance
verification and task-quality review. A possible next change would narrow default
governor file reads to onboarding and make completion handling relay owner reports
without independently reviewing artifacts; this is a proposal, not implemented.

Then remaining verification: Claude task owner -> GPT subagent (including a Claude
owner's blocking MCP request), final-code live UI/stream check, broader HTTP and
cleanup cases as needed, documentation and a fresh user session. The model-policy
allowlist includes both families but live role coverage is currently the one
Opus/Astra/Sonnet arrangement described above. No completeness claim is made.

DISCREPANCY: record preparation — initially inferred the final text name from fragment counts instead of looking it up by receipt. Reading scribe.line at 20260928T184543Z:6433 verified messages/agent/00000478; corrected the draft record. The first lookup requested an id key which physical lines do not carry and raised KeyError. Two documentation patches failed on unnecessary context lines before the correction was written successfully; 2026-09-28.
