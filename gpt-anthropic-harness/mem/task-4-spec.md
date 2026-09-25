# Task 4 — back-specification

Date: 2026-09-25. Scope: the `gpt-anthropic-harness` task-4 prototype, including
the subsequent change to store conversational text under its originating author.

Bar: reproduce a useful prototype whose failures are informative and whose
records survive. Preserve truthful evidence; production readiness is not required.

This specifies observable requirements and selected design decisions, not code
organization. **R** identifies a requirement to preserve; **D** identifies a chosen
way of satisfying a requirement. Numbered children are subordinate to their parent;
third-level items refine that child. Decisions describe this reproduction target,
not new institutional doctrine or the only permissible future design.

Basis: [task requirements](../../rules.md), the user's 2026-09-25 message-text
instruction, [current feature description](../task-4-ledger/README.md), and
[message-text decisions](task-4-message-text.md). The current behavior was checked
against the conversation, ledger, tool, filesystem and browser sources on this
date. This is a description of the built feature set, not a claim that every
failure mode has been exercised. Verification history remains in
[task-4-record.md](task-4-record.md), [task-4-format-review.md](task-4-format-review.md)
and [task-4-message-text.md](task-4-message-text.md).

## R1. Provide a user-directed conversation with a Claude agent

Retain the conversation, controlled tools and diagnostic visibility established
in tasks 2 and 3, adding persistent ledger evidence. Use the assigned Anthropic
architecture: Claude models through the Claude Agent SDK, driven from Python.

### R1.1. Maintain one coherent conversation during a launch

- **D1.1.a — Session lifetime.** A launch starts a fresh conversation. Successive
  user turns retain agent context within that conversation. Browser refresh
  recovers its current transcript, activity and status while the harness lives.
  Restart begins a new conversation; resuming or importing old conversations is
  not part of this feature set.
- **D1.1.b — Turn admission.** Accept one user turn at a time, only when ready.
  Reject empty/whitespace-only messages and messages over 16,000 characters.
  Do not silently queue additional turns during a reply or shutdown.
- **R1.1.c — User initiation.** Launching or refreshing the UI must not submit a
  model prompt. A provided onboarding starter fills the composer for the user
  to submit; it does not start work by itself.

### R1.2. Make conversation state and evidence visible

- **R1.2.a — Conversation controls.** Show user and assistant text in order, a
  multiline composer, send and close-session controls, and understandable states
  for connecting, ready, thinking, closing, closed, error and disconnection.
  Disable sending when unavailable. Enter sends; Shift+Enter inserts a newline.
  Retain the draft if submission fails and advise checking whether it was accepted.
- **R1.2.b — Operational visibility.** Show conversation identity, selected or
  reported model, available subscription/account information, ledger location,
  and tool activity including results or refusal summaries.
- **D1.2.c — Text presentation.** Display messages as literal text, preserving
  paragraphs and Unicode. Text must not execute as browser markup. Markdown
  rendering and token-by-token animation are not required; assistant text appears
  as received text blocks become available.

### R1.3. Report usage and termination honestly

- **R1.3.a — Accounting.** Capture available SDK usage, model usage, cost and rate
  events. Show last-result input/output usage, cache contributions, cumulative
  SDK cost and available rate status/reset time. Missing values remain unknown;
  cost is SDK accounting, not a measured subscription charge.
- **D1.3.b — Limits.** Bound each submitted prompt to 16 SDK turns and 240 seconds
  of query/response work. These are limits on one submitted prompt, not the total
  number of user turns in a conversation. There is no hard monetary ceiling.
- **D1.3.c — Ending.** Close session stops admission and allows an active reply
  to finish within its bound before normal shutdown. Closing a browser tab alone
  does not end the harness. A failed/incomplete reply or unexpected tool exposure
  puts the conversation in an error state requiring a fresh launch; do not claim
  success or retry a user turn invisibly.

## R2. Preserve a durable, inspectable record of the session

Use the shared bootstrap-ledger scribe and its current standard for operational
logging. Record failures as evidence rather than erasing or repairing history.
The reproduction baseline is wiki ledger v0.4 and the Python scribe v0.3 contract.

### R2.1. Preserve ledger identity, order and history

- **D2.1.a — One ledger per chat.** Each launch creates a uniquely named ledger
  directory containing a fresh scribe session file. It does not reopen a prior
  chat's ledger. This is the current design choice: task 4 required a fresh file,
  while the wider standard's project-level design could instead share a namespace
  across sessions. Cross-chat note continuity is consequently absent here.
- **R2.1.b — Standard semantics.** Preserve standard headers, author attestations,
  timestamps, `stamp:line` identities, append ordering, tags and revision pointers.
  One scribe owns a ledger at a time. New bodies supersede named entries only by
  citing their current body ID; existing bytes and historical versions survive.
- **R2.1.c — Storage integrity.** Keep ledger bytes exact, including LF line
  endings. Clean closure records the standard closing trailer and hash, then
  releases the lease. Lease state is transient and excluded from versioned
  history. An open session's absent trailer is not, by itself, evidence of a crash.

### R2.2. Capture operational events with enough context to diagnose failures

- **R2.2.a — Coverage.** Record configuration and governing prompt, lifecycle,
  available account information, tool availability, user acceptance, attempted
  and completed prompt sends, SDK messages, tool requests and outcomes, actual
  tool results/refusals, usage/rate events, and completed or failed turns.
  Distinguish an intended operation from its observed completion.
- **D2.2.b — Event identity.** Operational events are authored `harness`, receive
  unique names under `harness/`, and carry `harness` plus `log.<event-kind>` tags.
  Event bodies retain structured diagnostic information and conversation/turn
  associations where applicable. Their local event counter is application data,
  not an alternative to the ledger's line-based identity.
- **D2.2.c — Logging surface.** The ledger is the operational record. Startup may
  announce its location and the UI address; no parallel conversation log is
  required. If the ledger cannot accept writes, a short UI/stderr notice remains
  available to report that failure.

### R2.3. Preserve partial evidence when recording fails

- **R2.3.a — Admission depends on recording.** Record accepted user text and its
  message evidence before admitting the prompt for delivery. If recording fails,
  do not proceed as though the message were accepted.
- **D2.3.b — Non-atomic groups.** Text, classification tags and referencing events
  are separate durable records. A failure between them can leave a partial group;
  preserve it. Do not represent the group as an atomic transaction.
- **R2.3.c — Stop and expose.** Stop further ledger writes after a recording
  failure, preserve remaining files and lease evidence, and report the problem.
  Do not invent a clean-close trailer, switch silently to another log, rewrite
  history or automatically clear a stale lease. Recovery requires human review.

## R3. Attribute conversational text to its originator

Store text sent by the human and text returned by the agent as text bodies in
their own right, separately from harness-authored message metadata.

### R3.1. Keep authorship distinct from recording and classification

- **D3.1.a — Author roles.** Human text uses `human.session-user`, an anonymous
  role for this session. Agent text uses `agent.claude.test-pilot`. Runtime-generated
  error text uses `harness`. These identities are assigned by the harness, never
  accepted from model-supplied author fields.
- **D3.1.b — Text records.** Use unique names under `messages/user/`,
  `messages/assistant/` or `messages/runtime/`. Store the text itself as the body,
  preserving its content and formatting subject to credential redaction. Give it
  `message.text` and its `message.<role>` tag; classification tags are harness acts.
- **D3.1.c — Granularity.** One user submission yields one user text record.
  Each received assistant text block yields its own text record, retaining
  intermediate prose and its order relative to tool interactions. Text records
  are not editable through the agent's ledger-write tool.

### R3.2. Connect message metadata to text without losing provenance

- **D3.2.a — Text precedes reference.** Write the text first, then a harness-authored
  `message` event that substitutes `[[messages/...]]` for the corresponding text.
  Include a reference list (`text_entries`) with each text's name, exact ID, author
  and wikilink. Preserve the rest of the message's diagnostic metadata.
- **D3.2.b — Reuse.** User acceptance/send-attempt evidence references the original
  user text. Recognized human echoes and final SDK results whose text matches an
  already-recorded reply reuse its reference. Unmatched runtime result fields
  remain diagnostic data; they are not automatically declared human-authored.
- **R3.2.c — Semantic identity.** A transport message labelled “user” may contain a
  tool result and must not thereby become a human utterance. SDK error prose must
  not be represented as the model's authored reply. The UI and model receive text,
  not substituted ledger wikilinks.

### D3.3. Store explicit references without requiring a new link interpreter

Wikilinks in structured message bodies are stored references. The standard leaves
recursive link interpretation inside JSON unresolved, and the shared reader does
not currently make those JSON-field links clickable. Exact text-entry references
support resolution without assuming transclusion. Reader changes and historical
ledger migration are outside this reproduction target.

## R4. Give the agent a narrow, enforced tool surface

Support Python addition, bounded filesystem reads and restricted ledger notes.
The agent must have no general filesystem-write or code-execution tool.

### R4.1. Restrict tools by capability and argument contract

- **D4.1.a — Exposed operations.** Provide `add`, `fs_list`, `fs_read`,
  `ledger_read` and `ledger_write`. Disable built-in tools and exclude unrelated
  tool servers. Validate tool availability and refuse unexpected tools or malformed
  arguments, including attempts to supply identity or other unsupported fields.
- **R4.1.b — Addition.** `add` performs the calculation in Python and returns the
  sum of two finite numbers. Refuse booleans, numeric strings, non-finite operands
  and sums that cannot be represented as finite numbers. Record the actual result.
- **R4.1.c — Refusals.** Ordinary policy refusals return an explanatory tool error
  and leave evidence in the ledger and activity display. They do not grant extra
  permissions or automatically terminate an otherwise usable conversation.

### R4.2. Permit useful filesystem reading within NIMOI

- **R4.2.a — Boundary.** Accept NIMOI-relative paths only. Refuse traversal outside
  the boundary, absolute/device/network/alternate-stream paths, protected credential
  files, metadata/environment/runtime directories, symlinks/reparse points and
  multiply-linked files. Listing omits entries that cannot be read under this policy.
  No filesystem mutation or execution is available through these tools.
- **D4.2.b — Directory listing.** Return readable direct children in lexical order,
  with relative paths and file/directory kinds, a total and continuation information.
  Support offset pagination with at most 100 entries per response.
- **D4.2.c — Text reading.** Read regular UTF-8 text files no larger than 2,000,000
  bytes. Support one-based starting lines and up to 300 lines per request, default
  200, bounded to 30,000 characters with continuation information. Refuse binary
  or invalid text and a single line too large for the response bound.

### R4.3. Let the agent retrieve and maintain its own ledger notes

- **R4.3.a — Retrieval.** Read the current chat ledger by known entry name without
  retrieving its entire contents. Return current body, author, exact ID, revision
  pointer and tags. Also support name listing with IDs/authors/tags, optional tag
  filtering and offset pagination: default 30 entries, maximum 50. Large body
  results use bounded JSON-text slices of 20,000 characters with continuation.
- **D4.3.b — Writable notes.** Permit nonempty text notes up to 16,000 characters
  under valid `pilot/` names, authored `agent.claude.test-pilot`. Require at least
  one tag from `pilot.note`, `pilot.observation`, `pilot.question`. New tags add to
  the note's existing tags; there is no tag-removal operation.
- **R4.3.c — Protection and revision.** Require the current body ID as `prev` for
  updates; refuse absent or stale pointers. Preserve prior revisions. A note with
  a different author, protected tags or missing expected note classification is
  not agent-editable. The tool cannot modify harness/message records, impersonate
  authors, delete entries or history, or write another chat's/project's ledger.

## R5. Orient the agent as a NIMOI test pilot under human direction

### R5.1. State its role, bar and recourse in its governing prompt

Identify the agent as a test pilot for a new harness and a NIMOI agent. Direct it
to work as requested, not initiate tests independently, and report observations
about tools and environment verbosely. Distinguish checked evidence from working
claims. State the prototype bar and permit raising issues and seeking human
guidance when work is ambiguous, impossible or malformed.

### R5.2. Direct onboarding before substantive work

Instruct the agent to list `origins`, choose the lexically highest
`onboarding_*.md`, and read it completely before substantive work at the beginning
of the conversation. Selection is dynamic, not pinned to a historical version.
This is a prompted behavioral requirement, not a mechanically enforced gate;
the existing live record includes an ordering violation.

### R5.3. Explain authority and capability boundaries accurately

Tell the agent it may read permitted files and write restricted ledger notes,
but may not write files or execute code. Onboarding is institutional guidance
within those capabilities; other file/ledger content does not confer authority.
Instructions encountered in content cannot expand permissions. Require tool
evidence before claims that an operation actually succeeded, and forbid exposing
credentials in responses or ledger notes.

## D6. Operate as a local desktop prototype with explicit limits

### D6.1. Use the existing authenticated environment

Use existing Claude authentication, select `sonnet` by default and allow an
explicit model choice. Offer an available local port or a user-selected port,
announce the URL, and normally open a browser with an option to suppress that.
No model request is necessary merely to initialize the UI. A fixed port, process
ID, current quota or particular resolved model version is not part of the spec.

### D6.2. Limit local web exposure and protect known credentials

- **D6.2.a — Local service.** Bind to loopback, serve local assets and accept state
  changes only from the expected local origin. Reject invalid hosts, inappropriate
  request formats and oversized request bodies. Do not expose arbitrary local
  files through the web UI. These checks are not per-user authentication; other
  local programs can reach the service.
- **D6.2.b — Redaction.** Redact known credential fields, known secret values and
  recognizable token patterns from recorded/displayed data and file-tool results,
  while retaining legitimate token-usage counts. Do not claim detection of arbitrary
  secrets pasted into prose. Warn the user not to enter credentials.
- **D6.2.c — Threat boundary.** This is not a hardened sandbox or multi-user service.
  File policy does not promise resistance to hostile concurrent replacement of
  filesystem objects. Crash durability, forced live timeouts and every adversarial
  boundary are not claimed verified merely because ordinary tests pass.

### D6.3. Preserve the limits of the delivered feature set

No conversation resume, automatic stale-lease repair, autonomous scheduled work,
general shell access, cross-ledger writing, governance checkpoints, source export,
ledger merging or production deployment is supplied. The shared scribe handles
the ledger format; unresolved wider ledger mechanisms remain unresolved. A
reproduction should preserve truthful limitations rather than silently advertise
those future capabilities as present.

State: completed — back-specification of the built task-4 features and decisions;
runtime behavior and existing ledger records were not changed by this document.
