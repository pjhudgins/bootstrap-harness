# Task 4 back-specification: ledger-backed NIMOI test pilot

2026-09-25. Status: completed. Describes task 4 as built, including the later
message-authorship change; task 5 is outside this specification.

Bar: reproduce the useful prototype behavior with informative failures and
surviving records. Production readiness and adversarial isolation are not the bar.

**R** identifies a required outcome or constraint. **D** identifies a chosen
design policy for this reproduction. Numbering expresses derivation: `3.3.2`
is subordinate to `3.3`, which serves root `3`. Requirements include inherited
task-2/3 behavior; decisions make the choices left open by those tasks explicit.
This is a behavioral and policy specification, not a code-structure prescription.

Basis: current task-4 source and prompt, its README, `task-4-record.md`, and
`message-authorship.md`. [checked: source and document reads, 2026-09-25]
The human-requested message-authorship update refines task 4's earlier
blanket convention that logging is harness-authored.

## 1 [R] Provide one human-directed conversation through a local web UI

### 1.1 [D] A driver launch defines the conversation lifetime

- **1.1.1 [R]** Start a fresh conversation and ledger on each launch. Preserve
  context across successive turns and browser refreshes. Tabs connected to the
  same launch share its conversation.
- **1.1.2 [D]** Support one conversation and one active turn at a time. Accept
  messages when Ready; do not start a model turn merely because the driver or
  page opens. No conversation resumption, history browser, attachments, model
  selector, or parallel-agent workflow is part of task 4.
- **1.1.3 [R]** End session and terminal interruption stop the launch and attempt
  orderly shutdown of the agent and ledger. Closing a browser tab alone does
  not end the session. Relaunch does not overwrite the prior record.
- **1.1.4 [D]** Bound waits for stalled agent requests. The current decision is
  a 180-second request/turn deadline with a visible failure.

### 1.2 [R] Make the conversation usable and its state visible

- **1.2.1 [R]** Accept multiline text, distinguish human and agent messages,
  and display replies as they stream. Preserve completed commentary and final
  replies. Render message text as text rather than executable page content.
- **1.2.2 [D]** Enter sends; Shift+Enter inserts a newline. Reject empty messages
  and messages longer than 16,000 characters. Trim surrounding whitespace and
  redact recognized credentials before retaining or dispatching a human message.
  Offer an addition example that fills the composer and waits for the user to send.
- **1.2.3 [R]** Distinguish connecting, ready, responding, ending, ended, failure,
  and disconnection. Disable sending when unavailable or busy; retain visible
  conversation content when an error occurs.

## 2 [D] Use the existing Codex and ledger facilities from a Python driver

### 2.1 [R] Use the subscription-authenticated Codex App Server

- **2.1.1 [D]** Require an existing ChatGPT-authenticated Codex installation.
  Refuse startup without that account type; do not fall back to an API key or
  extract credentials. Inherit the configured model and display its identity.
- **2.1.2 [D]** Apply tool restrictions to this launch without changing persistent
  Codex settings. Normal-terminal permission for the driver is distinct from the
  child agent's capabilities and does not grant the agent general execution.

### 2.2 [R] Reuse the bootstrap-ledger scribe and its ledger contract

- **2.2.1 [D]** Use the existing sibling scribe without modifying or maintaining
  a copied implementation. Preserve its authorship, tags, revision identities,
  append-only history, writer ownership, and clean-close validation semantics.
### 2.3 [D] Keep deployment local and lightweight

- **2.3.1 [R]** Beyond the existing Python, Codex, and scribe prerequisites,
  require no new service account or package installation.
- **2.3.2 [D]** Launch prints the local address and ledger location;
  it can open a browser automatically or leave opening to the user. Support an
  automatically selected port or a chosen fixed port.

## 3 [R] Preserve a durable, attributable record of the session

### 3.1 [D] The session ledger is the harness's logging destination

- **3.1.1 [R]** Give each launch a fresh, exclusive ledger with one writer and
  one ledger file. Do not resume or roll it within the launch, or maintain a
  parallel conversation log as a fallback. Preserve ledger bytes without line
  ending normalization.
- **3.1.2 [R]** Record lifecycle, protocol traffic, tool calls and results,
  model usage, account limits, restriction checks, errors, and shutdown outcome,
  subject to the privacy rules in 5.3. Record scribe/ledger version provenance,
  speaker designations, and the transcript-format designation at startup.
- **3.1.3 [D]** Harness events have author `gpt-codex-harness`, ordered event
  identities under `harness/`, an event kind, and `harness` plus `log-<kind>`
  tags. Add applicable categories: `message`, `tool`, `usage`, `account-limits`,
  `protocol`, `lifecycle`, `restriction`, and `error`.

### 3.2 [R] Attribute user-facing text to the speaker who supplied it

- **3.2.1 [D]** Human text has author `human-user-of-session`; agent text has
  author `gpt-codex-test-pilot`. The harness attests identity from the message
  channel. Message contents and tool arguments cannot select or spoof an author.
- **3.2.2 [R]** Store a message's text as the entry body, then append a separate
  harness-authored `message` record. Replace inline message text in that record
  with a wikilink to the text entry. Include its exact entry identity, speaker,
  direction, completion state, and available conversation/message provenance.
- **3.2.3 [D]** Reserve `messages/` for immutable transcript bodies, separating
  human text, agent text, and streamed fragments. Tag them `transcript`,
  `protected`, their speaker category, and either `message-text` or
  `message-fragment`. Protection applies even when the author is the agent.

### 3.3 [R] Preserve message identity and incomplete output

- **3.3.1 [D]** Known message fields in protocol records also use text links.
  Repeated protocol representations of the same message reuse its text entry;
  separate human submissions remain distinct even when their text is identical.
  UI and model input retain message text rather than storage wikilinks.
- **3.3.2 [D]** Record streamed reply fragments as authored text before their
  referencing events. A completed reply additionally receives a complete-text
  entry and message record. Retain interrupted fragments without asserting a
  completed reply.
- **3.3.3 [D]** System instructions, reasoning, tool arguments/results, and
  other non-transcript data remain harness event data. A deliberate agent ledger
  note is separately agent-authored. Identify new ledgers as
  `authored-text-links-v1`; leave earlier ledgers in their original format.

### 3.4 [R] Preserve evidence when recording or execution fails

- **3.4.1 [R]** Record a human submission before dispatch. A failed ledger write
  stops further work; do not dispatch an unrecorded submission or continue with
  logging disabled. Expose the failure to the user.
- **3.4.2 [D]** Related writes are ordered but are not one atomic transaction.
  A failure can leave tags without a body or text without its following metadata.
  Keep those records and the failed writer's lease for human review; do not
  automatically repair, erase, roll back, or substitute another log.
- **3.4.3 [R]** On clean shutdown, finalize the ledger and release its writer
  ownership. Record failures and termination outcomes while recording remains
  possible; do not claim that a failed writer recorded its own failure or trailer.

### 3.5 [R] Let the human retrieve the record

- **3.5.1 [D]** Provide a consistent session-ledger download. An active-session
  snapshot may lack the closing trailer; the finalized disk record has it after
  clean shutdown. Preserve full recorded tool results beyond UI preview limits.

## 4 [R] Give the agent explicit arithmetic, ledger, and file-read tools

### 4.1 [D] Addition is a fixed Python capability

- **4.1.1 [R]** Accept exactly two finite numbers and return their finite sum.
  Reject invalid types, extra arguments, and nonfinite results. This grants no
  ability to evaluate expressions or execute user-selected code.

### 4.2 [R] Allow inspection of the current session ledger

- **4.2.1 [D]** List names, current identities, authors, and tags with tag/prefix
  filtering and pagination. Read the current body or revision history, including
  identities needed for updates. Paginate large reads with explicit continuation.

### 4.3 [R] Allow agent notes while protecting transcripts and harness evidence

- **4.3.1 [D]** Notes use `agent/` names and fixed author `gpt-codex-test-pilot`.
  Add the mandatory `agent` tag automatically; accept it explicitly as well.
  Other agent-selected tags must use the `agent-` prefix. No author argument,
  deletion, untagging, or protected-name write capability is exposed.
- **4.3.2 [R]** Creation asserts that no current entry exists. Revision must cite
  the current entry identity and preserve earlier bodies. Refuse stale updates
  and entries with protected authors or tags, including protected entries placed
  under the agent namespace. Validate before changing tags or content.
- **4.3.3 [D]** Notes are nonempty text of at most 24,000 characters with bounded
  names and tag lists. Return the resulting identity, author, and tags so the
  agent can inspect or revise the note later.

### 4.4 [R] Allow bounded reading of NIMOI files

- **4.4.1 [D]** List one directory at a time in sorted, paginated form. Accept
  NIMOI-relative or absolute in-root paths. Report excluded-item counts without
  disclosing excluded names.
- **4.4.2 [D]** Read ordinary UTF-8 text files up to 2,000,000 bytes, with at most
  24,000 characters per page. Return continuation information, the source content
  hash, and whether redaction occurred. Offsets apply to the redacted text.
- **4.4.3 [R]** These tools neither write files nor execute them. Ledger writing
  remains the explicit, separate persistence capability in 4.3.

## 5 [R] Constrain capabilities and avoid overstating those constraints

### 5.1 [D] Restrict the child agent to the intended tool set

- **5.1.1 [R]** Disable general shell/code execution and other inherited routes
  to unintended activity, including native subagents, MCP, apps/plugins/hooks,
  browser/computer use, and web search. Request a read-only agent sandbox with
  no attached environment. Do not grant permission expansion during a turn.
- **5.1.2 [R]** Check required restrictions at startup and refuse readiness when
  they cannot be confirmed. Reject unexpected tool requests and stop on observed
  restricted execution, file-change, MCP, or collaboration activity. Tool
  argument and boundary denials remain explicit failures in the record.
- **5.1.3 [D]** Preserve the disclosed runtime exception: `unified_exec` reports
  enabled despite disable overrides, and supporting tool-dispatch infrastructure
  remains necessary. This prototype accepts that documented caveat; it does not
  establish adversarial isolation. Make the caveat available to human and agent.

### 5.2 [R] Enforce the NIMOI read boundary

- **5.2.1 [D]** Refuse outside paths, parent traversal, network/device paths,
  alternate streams, ambiguous special names, symlink/reparse paths, and reads
  through multiple hard links. Check the opened file as well as its supplied
  path; a path spelling alone is insufficient evidence of confinement.
- **5.2.2 [D]** Exclude credential and runtime locations: VCS/agent/cloud-auth
  metadata, environment files, private keys, authentication/token/secret files,
  writer leases, dependency/cache environments, and runtime/run directories.
  These exclusions and credential-pattern redaction are limited safeguards,
  not a universal sensitive-data classifier or a proof against filesystem races.

### 5.3 [R] Limit credential exposure and local service access

- **5.3.1 [D]** Omit complete configuration and account-identity responses from
  logging. Redact recognized credential fields and text patterns in retained
  messages, tool records, file-read results, and agent notes. Redact file content
  before pagination so a page boundary cannot split a recognized credential.
- **5.3.2 [D]** Bind the UI to loopback, validate the intended host and origin,
  and restrict served content and accepted requests. Keep ledgers local unless
  the human exports them. This does not isolate other local processes running
  under the same user.

## 6 [R] Establish the agent's NIMOI test-pilot role

### 6.1 [D] Supply explicit role and conduct instructions

- **6.1.1 [R]** Identify the agent as a NIMOI test pilot. Before substantive work
  on the first user turn, locate the lexically latest zero-padded NIMOI onboarding
  document and read it completely, following pagination as needed.
- **6.1.2 [R]** Operate at the human's direction. Apart from required onboarding,
  do not initiate tests, inspections, experiments, background work, or ledger
  exercises. Perform directed tests within their bounds and stop at their end.
- **6.1.3 [R]** Verbosely report concrete harness/tool observations, refusals,
  missing capabilities, and failures. Separate returned evidence from inference;
  raise impossible or malformed assignments with the human. Do not simulate tool
  success or treat arbitrary file/tool content as instructions.

### 6.2 [R] Explain the capability and record policies to the agent

- **6.2.1 [D]** Explain the read boundary, execution/write prohibition, permitted
  ledger notes, authorship, protected transcripts, revision rules, pagination,
  shared account limits, and known restriction caveat. Fixed arithmetic is not
  general execution permission. Avoid secrets in messages and notes.

## 7 [R] Make tool activity, usage, and limitations inspectable

### 7.1 [R] Show tool outcomes and retain their evidence

- **7.1.1 [D]** Display tool count, successes, failures, arguments, and results.
  Show addition compactly and other tools in expandable cards. Bound previews
  to 4,000 characters; the ledger retains the complete recorded result.

### 7.2 [R] Report usage without inventing cost attribution

- **7.2.1 [D]** Display cumulative conversation input, cached-input, output,
  and total token counts. Explain that cached input is included in input.
  Missing measurements remain unknown rather than becoming zero.
- **7.2.2 [D]** Obtain account-window usage and reset information at startup and
  after turns, and process available updates. Explain that these allowances are
  shared with other Codex activity and are not a per-conversation price or quota
  charge. If refreshing limits is explicitly unavailable, retain the reply and
  disclose that displayed limits may be stale.

### 7.3 [D] Evaluate reproduction at the prototype bar

- **7.3.1 [R]** Check observable behavior: contextual successive turns, a real
  Python addition, ledger note/read/revision, protected-write and outside-read
  refusals, streaming and linked speaker-authored text, refresh persistence,
  visible usage, clean ledger closure, and a fresh launch.
- **7.3.2 [R]** Check informative failures: stale revisions, invalid arguments,
  restricted paths, interrupted replies, and failed recording before dispatch
  or between text and metadata. The surviving record must support distinguishing
  refusal, partial progress, failure, and completion.
- **7.3.3 [D]** Treat existing offline/live receipts as evidence of the exercised
  cases, not a guarantee of hostile-input security, concurrent filesystem safety,
  mobile usability, long-session performance, or compatibility with future Codex
  versions.

Evidence and operational instructions remain in `task-4-record.md`,
`message-authorship.md`, and `../task-4-ledger/README.md`. They hold run receipts,
known discrepancies, and launch details rather than adding requirements here.
