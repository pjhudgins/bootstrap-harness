# Task 6: hybrid governance

Bar: useful prototype behavior, informative failures and truthful surviving records; not production.

Derived from this lane's task 5. Opus is the human-facing governor. It dispatches
task owners; owners can dispatch strictly subordinate workers. Every actor has
an independent provider session and the same harness-owned bounds/tool layer.

## Roles and models

| Role | Responsibility | Allowed models |
| --- | --- | --- |
| Governor | Communication, dispatch, safety and governance; no task execution or quality grading | Claude Opus |
| Owner | Task quality/completion, letter and intent of restrictions, institutional standards | Claude Opus/Fable; GPT Astra/Sol |
| Worker | Assist its owner within narrower grants; no delegation | Claude Sonnet or above; GPT Terra or above |

Explicit IDs/aliases live in models.py. No automatic fallback. Codex checks
account model/list before creating a thread; Claude checks the initialization
model against the requested pin. Only Opus 5.5, GPT 6 Sol and Sonnet 5 were
live-verified in this handoff. Other allowed models depend on account availability.

The governor holds a delegation ceiling but has no add, file-write or execute
tool. These actions belong to owners/workers. Governor turns are event-driven:
after dispatch or a decision, return to the human. Owner requests/completion
automatically queue a new governor turn; repeatedly polling delays that queue.

## Blocking requests and human decisions

1. An owner writes its justification as its own pilot/ note.
2. governor_request pins that note's ID and reserves a fresh response_name.
   The owner is blocked; other owner tools are refused. Existing workers may finish.
3. The governor receives a harness notification, reads the justification and
   writes a separate response note. governor_resolve fills the reserved response.
4. Approve/deny releases the waiting tool. needs_human instead shows a decision
   card; it does not release the owner.
5. The human chooses approve/deny and explains the decision. That text has the
   human.session-user author. The governor gets another notification and resolves.
   A human denial cannot be overridden.

Approval never changes fixed permissions or claims that an action executed.
The governor may separately dispatch authorized work within its ceiling.
Actions outside the ceiling require human changes outside the agent tools.
Active-turn deadlines pause for the blocking request; provider connections
still need to survive. There is no durable restart/resume of live agents.

## Bounds and retained capabilities

All grants use the same six keys:
fs.read, fs.write, fs.execute, ledger.read, ledger.write, ledger.tags.
Children must have subsets, plus explicit onboarding/instruction/result reads.
Two active direct children and eight total dispatched actors are allowed per launch.

Paths accept NIMOI-relative paths, nimoi:/, workspace:/ and scripts:/.
Selectors are exact names, **, or prefix/**. Onboarding must be read in order
before work. File reads remain bounded, exclude protected files and raw ledgers,
and reject link escapes. File writes export an exact readable ledger text ID,
create-only, into this launch's workspace.

Only scripts:/safe_test.py is executable by default. Agents cannot write into
scripts, nor execute drafts from workspace. Execution uses isolated cwd/env,
no arguments, a 10-second/64-KiB bound, and a reserved readable ledger output.

Each launch creates a scribe ledger. Human and agent prose is a text body with
its actual author; harness envelopes link pinned text IDs. Notes cannot overwrite
another author or protected output/log entries. Requests, decisions, tool calls,
restrictions, usage and failures are recorded. Shutdown joins provider/tool/script
cleanup before closing the ledger. Failed owners cancel their outstanding workers.
A ledger failure stops the launch and preserves its lease for human review.

## Provider boundaries and limitations

Claude uses a single in-process MCP server, no native tools or native delegation,
an exact initialized inventory check and handler authorization. Runtime options
are not a general OS sandbox.

Codex uses a task-local adaptation of GPT/Codex task 5's App Server transport
and policy, with executable discovery informed by Claude/Codex task 5.
Protocol reference: https://learn.chatgpt.com/docs/app-server.
Existing subscription sign-ins are used; API-key fallback is disabled.

Native exec is permitted by rule 5g when needed for dynamic-tool dispatch.
The live GPT 6 Sol runtime confirmed code_mode_host=true and unified_exec=true
despite the requested unified_exec=false; shell_tool, js_repl, code_mode,
multi_agent, apps, hooks, plugins, browser_use and computer_use were false.
Runtime approvals are declined and disallowed observed calls stop the actor.
These observations are not proof of pre-execution isolation. Harness tool
bounds are enforced independently. No native delegation was used.

Claude's SDK usage can include auxiliary runtime model accounting (Haiku appeared
in governor model_usage); this is distinct from the selected Opus governance
session and the harness-owned role/model assignments. Full reported accounting
is retained. Codex reports tokens/rate windows but no comparable USD amount here.
Family cost sums only available reported cumulative USD, not subscription billing.

Prototype limits: no ticket system, persistent live-agent recovery, privilege
promotion, hard family budget or long-duration human-wait reliability claim.
Human gate behavior is deterministic-test verified and its UI was reviewed using
a clearly labelled synthetic fixture; the live smoke used an already-authorized
governor approval. Other provider/role combinations were not separately live-tested.

## Run and verify

Run Python with normal terminal permissions under the founder's standing
authorization; the sandbox Python lacks the installed Claude SDK.
From this directory:

- python -B -X utf8 app.py --no-browser
- python -B -X utf8 -m unittest discover -v
- python -B -X utf8 live_smoke.py (real model requests)
- python -B -X utf8 inspect_ledger.py <ledger-name> --smoke --closed
- python -B -X utf8 preview_ui.py <snapshot.json> --approval-demo (read-only fixture)

Close session stops agents/scripts, closes the ledger, and exits the server.
Every launch gets a new loopback port, ledger and workspace. Task 5 is preserved.

## Code map

- conversation.py: human admission, governor turn queue, shared UI state.
- hierarchy.py: three-layer jobs, ownership, requests, human decisions, cleanup.
- context.py / models.py / prompts/: actor identity, model roles, role instructions.
- tools.py: one shared tool registry, schemas, role/grant filtering and dispatch.
- runtime.py / codex_runtime.py: provider sessions and message observations.
- codex_protocol.py / codex_policy.py: stdio transport and native-surface restrictions.
- bounds.py / access.py / capabilities.py / filesystem.py: authorization and effects.
- ledger.py / messages.py / redaction.py: durable records and provenance.
- app.py / app.js / index.html / style.css: local UI and same-origin endpoints.

Evidence and discrepancies are recorded in ../mem/task-6-record.md.

