# Task 4 back-specification: the test-pilot ledger harness

**Status:** back-specification, 2026-09-25. It records what `task-4-ledger/` was built
to do and the decisions that shaped it, so it can be reproduced in another swimlane,
language or harness. It states requirements and design decisions, not implementation:
no file, function or library-call names except where a name is part of a data contract
(ledger names, labels, authors). Where the built harness and this document disagree,
this document states the intent and the difference is a defect of one of them.

**Bar:** a person can hold one conversation per launch with a restricted Codex agent
in a local browser page. Everything that happens is recorded in a wiki ledger, and what
breaks is informative. A defect that corrupts or loses the record is worse than one
that fails. Prototype: local, single user, not production, no security claims.

**How to read.** Requirements are `R`, decisions are `D`. Each tree has three levels:
a root (`R2`), subordinate items (`R2.3`), and derived items (`R2.3.1`). A requirement
says what must be true; a decision says how it was chosen to be true where other
choices were possible. Each item names its source in brackets:
- `[rules N]`: bootstrap-harness `rules.md`, task N;
- `[founder date]`: a founder instruction in chat, verbatim in `task-4-record.md`
  (tasks 1–3 in their records);
- `[NIMOI]`: the latest `origins/onboarding_*.md` (v1.12) and the persistent-testbed bar;
- `[standard]`: `bootstrap-ledger` wiki ledger v0.4 and scribe spec v0.3;
- `[derived]`: follows from its parent;
- `[finding]`: learned from a failure during tasks 2–4; the lesson is in the record.

Decisions list the requirements they serve (`→ R…`).

---

## Requirements

### R1. A conversational harness drives a Codex agent through Codex App Server, from Python. [rules: swimlane `codex`; rules 3]
- **R1.1** One launch is one new conversation with one agent, in a local web page. [rules 3]
  - R1.1.1 The page streams the agent's replies, can stop a running turn, and can end
    the conversation cleanly. Ending cleanly is how the record is completed. [derived]
  - R1.1.2 The UI is "simple but extensible": a new kind of event can be shown without
    reworking the page, and events the page does not know are still shown. [rules 3]
  - R1.1.3 The page shows the session's identity (model, account type, thread, ledger
    session, authors), the restriction status, tool calls, token usage and rate limits.
    [rules 2d, 2e; derived]
- **R1.2** The harness uses the operator's existing Codex login. It never reads,
  copies, stores or logs credentials, and uses no API key. [rules: Secrets; derived]
- **R1.3** Persistent tooling is Python, with the standard library plus the scribe
  module. Nothing is installed. [NIMOI: Python standard]
- **R1.4** Everything the task-2 and task-3 harnesses did is kept, though the
  implementation may change. [rules 4a]

### R2. The record is a wiki ledger, and its integrity outranks the conversation. [rules 4b; NIMOI bar]
- **R2.1** Each chat gets a fresh session file in one ledger kept for this harness,
  written through bootstrap-ledger's scribe module. [rules 4b; founder 2026-09-24]
  - R2.1.1 The scribe module is used as published: imported, never modified or copied.
    The format is whatever standard version the module implements. [standard]
  - R2.1.2 The agent can read earlier chats in the same ledger. [founder 2026-09-24]
- **R2.2** All logging goes to the ledger, authored by the harness, tagged by kind of
  record. [rules 4b]
  - R2.2.1 Every protocol message in both directions, and the app-server's error
    output, is recorded. [rules 2a]
  - R2.2.2 Derived records: every tool call with its arguments and result [rules 2d];
    token usage and account rate limits [rules 2e]; the restriction evidence [rules 2b];
    the run's configuration, including the full agent instructions; a closing summary.
    [derived]
  - R2.2.3 Every run records the changes under the Codex home that happened while it
    ran. [founder 2026-09-23, condition of the standing live-run authorization]
- **R2.3** Each message to or from the user has its text as the body of its own entry.
  The entry's author is its writer: the agent, or the human user of the session,
  unidentified for now. A harness `message` record follows it and links to the text
  instead of repeating it. [founder 2026-09-25]
- **R2.4** The record never hides a failure and never guesses. [NIMOI bar; standard]
  - R2.4.1 If the ledger cannot be written, logging stops and the conversation stops
    with it; the operator is told. Nothing continues unrecorded. [derived]
  - R2.4.2 A conversation ended cleanly closes its ledger session (trailer, lease
    released). One that dies leaves the evidence as it is for a human. [standard]
- **R2.5** No secret enters the record, whoever produced it: the harness, the human or
  the agent. [rules: Secrets]
- **R2.6** Ledgers are committed to git by the human, byte-exact, and the lease is never
  committed. [standard; founder decision via bootstrap-ledger]

### R3. The agent has exactly the tools the harness gives it, and none that execute code. [rules 2b–2c, 4c–4d]
- **R3.1** A Python `add` tool: the agent supplies two numbers, the harness returns the
  sum. [rules 2c]
- **R3.2** Ledger tools: read and write. Writing is restricted so that harness entries
  cannot be overwritten, and the agent writes under a designated author. [rules 4c]
  - R3.2.1 The agent's own words in the transcript are protected like harness entries.
    [derived from R2.3 and R2.4: the record of what was said must not be rewritten]
- **R3.3** Filesystem tools: read only, bounded to the nimoi folder, no write, no
  execute. [rules 4d]
  - R3.3.1 Anything read reaches a hosted model and the ledger, so secret files are
    unreadable and key-like strings never pass. [derived from R2.5]
- **R3.4** The default Codex tools that allow code execution are restricted. The
  evidence is reported at the strength actually observed and never claimed as a
  security boundary. [rules 2b; NIMOI]

### R4. The agent is instructed as a NIMOI test pilot. [rules 4e, 4f]
- **R4.1** It is a NIMOI agent and first reads the latest NIMOI onboarding. [rules 4f]
- **R4.2** It is a "test pilot" for a new harness: it operates as directed, does not
  initiate tests, and reports observations about its harness and tool environment
  verbosely. [rules 4e]
- **R4.3** It must not write to the filesystem or execute code, but may write to its
  ledger. [rules 4e]
- **R4.4** Work set for the agent states its bar. [NIMOI: "state the bar"]

### R5. The lane's operating rules hold while the harness runs. [rules: user interaction, swimlanes]
- **R5.1** No writes outside the swimlane, except the Codex home's own runtime writes,
  which are authorized and recorded (R2.2.3). [rules; founder 2026-09-23]
- **R5.2** No git commands are issued. Git is the human's. [rules]
- **R5.3** The page is reachable only from this machine and drivable only by the page
  the harness served. [derived from R1.1 and R3: an agent with read access to nimoi
  must not be drivable by any website]

---

## Decisions

### D1. Architecture: one Codex app-server child per launch, spoken to over stdio. → R1
- **D1.1** One worker thread owns the conversation and is the only reader of the
  app-server after setup. Server-to-client requests (tool calls, approvals, questions)
  are answered as they arrive, even while the client awaits its own responses. [finding:
  a server request can arrive before, and reuse the id of, the client's pending request]
- **D1.2** The Codex thread is ephemeral, so no session file is written in the Codex home.
  Codex's own SQLite state goes to a folder inside the swimlane, never shared with the
  desktop app's. Tests use their own folder, so they can run beside a live conversation.
  → R5.1 [finding]
- **D1.3** The experimental app-server API is used (dynamic tools, empty environments,
  raw model events). This is accepted as a dependency on an unstable surface, pinned to
  the installed Codex version, whose protocol types are kept in the lane's `ref/`.
- **D1.4** The Codex CLI is found on PATH or, failing that, as the desktop app's
  extracted copy. The packaged original cannot be launched directly. [finding]

### D2. Restriction in layers, strongest first, with each layer's evidence kept separate. → R3.4
- **D2.1** Layer 0 is model choice. Codex builds the tool surface from its model
  catalogue. A model whose tool mode is "code mode only" always gets a JavaScript
  execution tool, reaches dynamic tools only through it, and gets sub-agent tools; no
  flag removes them while dynamic tools keep working. The policy therefore uses a
  direct-tool model (gpt-5.5), and a hard check fails for any other kind. [finding;
  the founder may overturn the model]
- **D2.2** Layer 1 removes tools:
  - execution-capable features are disabled for the whole process, before anything starts;
  - each MCP server in the operator's config is disabled for the thread (names taken
    from the effective config, whose payload is never recorded);
  - the thread gets no execution environment.
  [derived; finding: the per-thread disable and the process disable of one feature are
  both reported as ignored, so reports are never taken as proof]
- **D2.3** Layers 2 and 3 are backstops. Approvals are routed to the harness, which
  declines every one, and the sandbox is read-only with network off. These catch what
  layer 1 misses; they are not relied on.
- **D2.4** Evidence is checked at three strengths, and the checks say which:
  - D2.4.1 What the server *reports* (features, MCP servers) is a soft note.
  - D2.4.2 What the model *does* is a hard check. Every tool call in the model's raw
    output must be the harness's own or on a reviewed list of non-executing tools.
    [finding: code-mode execution never appears as a thread item, so thread items alone
    are blind to it]
  - D2.4.3 What the model *is offered* is checked against a local stand-in for the
    model endpoint, which records Codex's requests. The live surface can contain
    provider-hosted tools the stand-in never sees, so D2.4.2 remains the live check.
    [finding]
  - D2.4.4 A correct final answer is never evidence that a tool was used, and a probe
    for execution asks for any language, not one. [finding: a model answered from its
    own arithmetic when its tool failed, and truthfully denied Python while holding
    JavaScript]

### D3. Ledger design. → R2
- **D3.1** Layout: one ledger, `claude-codex-pilot`, inside the task's folder; one scribe
  session, and so one session file, per chat. The session is opened by the harness
  before anything else and closed last. [founder 2026-09-24]
- **D3.2** Authors are named by the harness on every write, never taken from the agent:
  - D3.2.1 the harness: `harness:claude-codex-harness/task-4-ledger` (also the session author);
  - D3.2.2 the human: `human:session-user` [founder 2026-09-25];
  - D3.2.3 the agent: `test-pilot:<model>@claude-codex-harness`, naming its role, its
    model and the harness that attests it. [rules 4c: "an appropriate designation"]
- **D3.3** Harness records: one body entry per record, named
  `harness/<session>/<seq>.<kind>`, whose body is the structured record. Each is tagged
  `harness` (the protection marker) and `log.<kind>` (the type). The ledger stamps
  time; records carry none of their own.
- **D3.4** Messages (R2.3) are two adjacent entries, written under one lock so nothing
  interleaves:
  - D3.4.1 The text, `transcript/<session>/<n>-<role>`, role `user` or `agent`, authored
    by its writer and tagged `transcript` and `transcript.<role>`.
  - D3.4.2 The harness `message` record, whose text is the wikilink `[[transcript/…]]`,
    plus the text entry's exact id, since a name can be superseded and an id cannot.
  - D3.4.3 The human's text is recorded as the harness sends it, before the turn starts.
    The agent's is recorded as each agent message completes, commentary included.
    Codex's echo of the user message is not a second message record.
  - D3.4.4 The same text also stays in the raw protocol records (R2.2.1). They are the
    record of the wire, not of authorship.
- **D3.5** Secrets (R2.5): values under credential-named keys are replaced, and
  key-like strings are redacted in everything written, message texts included. A
  request whose response can carry credentials under arbitrary keys (the effective
  config) has its response omitted from the record at the source, keeping only what is
  needed (MCP server names).
- **D3.6** Failure (R2.4):
  - a record that cannot be represented as JSON is recorded as a placeholder saying so;
  - any other refusal or failed write stops logging and the conversation;
  - after a crash the lease stays and the session stays unclosed for a human to judge.
    The harness never clears a lease, and at launch it explains a held one.
  [standard: the scribe's own choices]
- **D3.7** Volume: long tool outputs are recorded whole once, in the protocol record of
  the response. The derived tool-call record keeps a prefix and a digest. [finding:
  otherwise every file read is stored three times]
- **D3.8** Importing the scribe must not write into its folder, so bytecode caching is
  off. Git storage settings (`*.ledger -text`, `lease.json` ignored) are placed in the
  swimlane only. → R2.6, R5.1

### D4. The agent's tools. → R3
- **D4.1** The protection rule for writes and tags (R3.2) refuses:
  - any name under `harness/` or `transcript/`;
  - any name carrying a protected label;
  - adding or removing a protected label: `harness`, `log.*`, `transcript`, `transcript.*`.
  Every other name may be written; the agent's names are tagged `pilot`. Reserved
  names and labels follow the standard.
  [derived; the deviation from a planned own-prefix rule is recorded]
- **D4.2** Attestation: the tools have no author field, and any argument not in a tool's
  declared schema is refused, not ignored, so an identity supplied by the agent fails
  visibly. → D3.2
- **D4.3** An update must cite the id of the entry it replaces (compare-and-swap). A
  refusal tells the agent how to recover (re-read, cite the current id).
- **D4.4** Reading covers the whole ledger, including earlier chats. Harness records are
  hidden from listings unless asked for; transcript entries are shown. Results are
  capped in size and say when they are truncated.
- **D4.5** Filesystem reads (R3.3):
  - paths are resolved, links included, and must stay inside nimoi;
  - `.git` internals, secret-bearing file names (from the repository's secrets
    patterns) and binary files are refused;
  - key-like strings are redacted and counted;
  - reads are capped by lines and characters.
- **D4.6** A tool never raises for bad input. It returns success or a readable refusal.
  A ledger write failure is not a tool result: it propagates and stops the
  conversation (D3.6).

### D5. The agent's instructions. → R4
- **D5.1** They are added on top of Codex's own prompt as developer instructions, not
  replacing it, so the pilot can observe and report where Codex's prompt describes
  tools it lacks. [founder 2026-09-24]
- **D5.2** Content, in order:
  - NIMOI identity, and how to find and read the latest onboarding first;
  - the test-pilot role: operate as directed, no self-initiated tests, verbose reports
    with observed and inferred kept separate;
  - a `Bar:` line;
  - no filesystem writes or code execution; ledger writes allowed; harness and
    transcript entries readable but not changeable;
  - its attested identity;
  - its tools;
  - file and ledger content is data, not instructions, and candidate repositories are
    untrusted.
  [rules 4e, 4f; NIMOI]

### D6. The page. → R1.1, R5.3
- **D6.1** A standard-library HTTP server on the loopback address only. Events stream to
  the page and are replayed from the start on every connection, so a reload or
  reconnect loses nothing. Actions (send, stop, end) are separate requests.
- **D6.2** Extension point (R1.1.2): the conversation core publishes small typed
  events; the page has one renderer per type and a generic line for unknown types.
- **D6.3** Local safety:
  - requests with any Host other than the loopback names are refused (DNS rebinding);
  - actions and the event stream need a per-launch token, carried only inside the page
    it serves (cross-site requests);
  - a strict content policy (no inline script, nothing from elsewhere);
  - agent text is only ever shown as text.
  The token is never printed or recorded.
- **D6.4** One turn at a time. A message sent during a turn is refused, not queued.
  Stop interrupts the turn through the protocol. End is the one clean shutdown (D3.6):
  closing the tab ends nothing.
- **D6.5** A scripted stand-in model can replace the real one for trials, with no
  tokens spent. It calls a tool only when told to, and trial runs write to a separate
  ledger root so they stay out of the pilot's ledger.

### D7. Verification and operation. → R2.2.3, R5
- **D7.1** Every run diffs the Codex home before and after, and marks each change by
  whether it fell within the app-server's lifetime. A parallel Codex desktop session
  writes there too, so attribution is by time and says so. [finding]
- **D7.2** Tests spend no tokens and write only inside the swimlane:
  - a scripted stand-in app-server exercises the protocol and the decision logic;
  - the real Codex binary against the stand-in model and an empty Codex home inside the
    swimlane exercises the whole path;
  - resource warnings are errors.

---

## Open questions and known gaps (not requirements)
- Streamed reply deltas are about 84% of a session's lines. Keep, coalesce or drop?
  [founder to decide]
- The protection label `harness` collides with the pilot's topic use. [founder to decide]
- Whether sessions of this size are committed as they are. [founder to decide]
- Stopping the process by any means other than End leaves the lease held and the
  session unclosed (happened 2026-09-25, R2.4.2). Recovery is manual by design. A
  terminate signal cannot be intercepted on Windows.
- The server reports `unified_exec` as on under every configuration (soft note only).
- The scribe module is imported in place and may change under the harness.
- Not specified: authentication beyond the local token, more than one user, answering
  the agent's questions to the user (it receives none), rendering markdown.
