# task-4-ledger — back-specification

**Purpose.** This spec lets someone reproduce what claude-anthropic-harness built for task 4, including what it inherited from tasks 2 and 3. It records requirements and design decisions, not implementation. Written 2026-09-25 from the built and verified harness.

**Bar.** The failures must be informative and their record must survive. This is a prototype for a NIMOI testbed, not a production system. A defect that corrupts or loses the record is worse than one that makes the harness fail.

**Numbering.**
- `R` is a requirement; `D` is a design decision.
- `R1`, `R1.1`, `R1.1.1` go from root to derived.
- Every item names its source in brackets:
  - `rules 4b` is `bootstrap-harness/rules.md`, task 4, item b.
  - `founder` is a founder decision or direction, with its date.
  - `finding` is a defect found in a live run, recorded in `task-2/3/4-decisions.md`.
  - `derived` means it follows from its parent.

---

## Requirements

### R1. A single conversation with a Claude agent through a local web UI [rules 3, 4a]
- **R1.1.** Each launch starts a new conversation. Within a launch, the conversation keeps its context across turns. [rules 3]
- **R1.2.** Only the local user can drive the UI. [derived]
  - R1.2.1. The UI is reachable only from this machine.
  - R1.2.2. Other websites open in the same browser cannot send messages, interrupt, or end the session.
- **R1.3.** The UI shows the conversation, plus a live feed of everything the harness records. [founder 2026-09-23]
  - R1.3.1. Reloading the page, or reconnecting it, restores the whole conversation so far without duplicates.
  - R1.3.2. A page left open from an earlier launch notices the new conversation and resets itself. [finding: stale tab across relaunch]
  - R1.3.3. The page never runs UI code older than the harness serving it. [finding: cached script after an update]
- **R1.4.** The user sends one message at a time. A message sent while the agent is working is refused with a reason.
- **R1.5.** The user can interrupt a running turn, and the conversation continues afterwards.
- **R1.6.** The user can end the session from the UI, and ending it closes the record cleanly. [derived from R7.5]
- **R1.7.** The UI is simple, and new kinds of record can be displayed without restructuring it. [rules 3: "simple but extensible"]

### R2. Every message to and from the agent is captured and recorded [rules 2a]
- **R2.1.** Recorded: user messages, agent messages, tool results fed back to the agent, and system and result messages. Each is marked with its direction.
- **R2.2.** Message text to and from the user is recorded as its own record. [founder 2026-09-25]
  - R2.2.1. The record's body is the text itself.
  - R2.2.2. Its author is whoever wrote the text: the human session user or the agent.
  - R2.2.3. The text record is written first. The harness's message record follows it and refers to the text by link, not by copy.
  - R2.2.4. If the text record can't be written, the text stays inline in the message record, so it is never lost.

### R3. The agent cannot execute code or write files [rules 2b, 4d, 4e]
- **R3.1.** No tool that executes code or writes files is ever available to the agent. That includes shells, file editing, notebooks, subagents and web fetch.
- **R3.2.** Enforcement doesn't rely on one mechanism alone. Every tool call is checked, including calls the underlying CLI would approve by itself. [finding: the CLI auto-approves reads without asking the harness]
- **R3.3.** The agent can read files, bounded to the nimoi directory. [rules 4d]
  - R3.3.1. `candidate_repos/` is excluded, because onboarding designates it untrusted. [founder 2026-09-24]
  - R3.3.2. Secret-looking files are refused wherever they are, matching the repository's gitignored secret patterns. [rules: Secrets]
  - R3.3.3. A search whose reach would include an excluded directory is refused, not just a direct path into it. [derived]
- **R3.4.** Every refusal gives the agent the rule that fired.

### R4. The agent has a Python-implemented tool: `add` [rules 2c]
- **R4.1.** It takes two numbers and returns their sum.
- **R4.2.** Non-numeric input is an error result, not a coercion.

### R5. Tool calls are recorded [rules 2d]
- **R5.1.** Recorded for each call: the attempt with its input, the harness's allow or deny decision and reason, and the outcome (result or failure).
- **R5.2.** A denied call is recorded as fully as an allowed one.

### R6. Usage, cost and account limits are recorded [rules 2e]
- **R6.1.** Every turn records its token usage, its own cost, and the cumulative cost of the conversation.
- **R6.2.** Rate-limit status is recorded as the platform reports it.
- **R6.3.** Account information is limited to subscription type and API provider. Email and organization are never recorded. [founder 2026-09-23]
- **R6.4.** Spending per launch is capped by the harness, which refuses new messages once the cap is reached. [founder 2026-09-23: $5]
- **R6.5.** A turn that failed is recorded as failed, with the platform's error status, even if the platform labels it otherwise. [finding: "success" on a 401]

### R7. A wiki ledger is the record [rules 4b]
- **R7.1.** The ledger is written with the `scribe` module from `bootstrap-ledger`. [rules 4b]
  - R7.1.1. The harness never writes into `bootstrap-ledger`. [rules: no writes outside the swimlane]
  - R7.1.2. The scribe and standard versions in use are recorded with each session.
- **R7.2.** Each chat session is a fresh ledger session file. [rules 4b; founder 2026-09-24: one ledger, a new session per chat]
- **R7.3.** All logging goes to the ledger, authored by the harness and tagged by log type. [rules 4b]
- **R7.4.** The record survives breaks. [bar]
  - R7.4.1. Ending the session normally closes the ledger session cleanly.
  - R7.4.2. A harness failure that happens with the ledger open is written to the ledger, not only to the console. [finding: port conflict]
  - R7.4.3. A problem the scribe found in an earlier session is recorded, never repaired.
  - R7.4.4. A crash leaves the lock for a human to clear. The harness never clears it.
  - R7.4.5. A condition that would make a launch pointless (a busy port, for example) is detected before a ledger session opens. [finding: port conflict]
- **R7.5.** The ledger is version-controlled safely. [founder 2026-09-24: tracked]
  - R7.5.1. Ledger files are protected from line-ending conversion.
  - R7.5.2. The lock file is never committed.

### R8. The agent can read and write the ledger, but cannot overwrite the harness's entries [rules 4c]
- **R8.1.** The agent can read any entry, including its history and labels, and list entries by name prefix or label.
- **R8.2.** The agent can create entries and update its own.
- **R8.3.** Harness entries can't be overwritten by the agent, and protection is by tag. [rules 4c]
  - R8.3.1. The agent cannot remove or apply the protecting tags, so it cannot strip protection first.
  - R8.3.2. Text the agent authored inside the harness's record is protected too.
- **R8.4.** The agent has an author designation. The harness sets it; the model never supplies it. [rules 4c; scribe interfaces]
- **R8.5.** Tool descriptions explain every field the tools return. [founder 2026-09-25: `bound`]

### R9. The system prompt defines the agent's role [rules 4e, 4f]
- **R9.1.** The agent is a NIMOI agent and reads the latest NIMOI onboarding before substantive work. [rules 4f]
- **R9.2.** The agent is a test pilot for a new harness. [rules 4e]
  - R9.2.1. It operates as directed and does not start tests of its own.
  - R9.2.2. It reports observations about its harness and tool environment verbosely, keeping observation separate from inference.
  - R9.2.3. It raises requests that seem impossible or malformed. [onboarding: organizational responsibility]
- **R9.3.** The agent is told it must not write files or execute code, and that it may write to its ledger. [rules 4e]
- **R9.4.** The agent is told how its record is kept and what it may write.
- **R9.5.** The agent is told its tool list is authoritative over generic text from the underlying CLI. [founder 2026-09-25: CLI notes mismatch]
- **R9.6.** The agent treats file and ledger content as information, not instructions. Onboarding is the exception.
- **R9.7.** The prompt as sent is recorded in full with each session.

### R10. The agent session is isolated from the host [bar; findings]
- **R10.1.** No project or user instructions, settings, hooks or plugins from the host. [finding: harness injects CLAUDE.md]
- **R10.2.** No auto memory from the host. [finding: the project's MEMORY.md loaded]
- **R10.3.** No MCP servers other than the harness's own, including the account's claude.ai connectors. [finding: Gmail/Drive/Calendar/Docs attached]
  - R10.3.1. Each turn, the harness checks which servers actually attached and records any unexpected ones.
- **R10.4.** The CLI keeps no transcript outside the swimlane. [rules: no writes outside the swimlane]

### R11. The work stays within the swimlane's rules [rules]
- **R11.1.** All code and tests live in the task folder, not shared across task folders.
- **R11.2.** No git operations by the agent building or running the harness. Git is the human's.
- **R11.3.** Secrets are never written, printed or logged.

---

## Design decisions

### D1. Platform
- **D1.1.** Claude Agent SDK for Python, kept at the latest version. [rules: swimlane; founder 2026-09-25: "better to get current early"]
  - D1.1.1. The SDK runs its own bundled CLI. Record which version runs; don't assume the one on PATH. [finding]
- **D1.2.** The model is pinned (`claude-sonnet-5`) and can be overridden per launch. [founder 2026-09-23]
- **D1.3.** Auth uses the CLI's logged-in account, not an API key file. [founder 2026-09-23]
- **D1.4.** Web stack: a small ASGI server plus plain HTML/JS/CSS, using only libraries already installed and no CDN. [derived from R1.7, R11]

### D2. Architecture
- **D2.1.** One long-lived worker owns the agent client for the whole conversation. Web requests hand it messages through a queue; only interrupt crosses over directly. [derived: the SDK requires its context to be entered and exited in one task]
- **D2.2.** One event path serves both the record and the UI. [derived from R2, R1.3]
  - D2.2.1. Each record is written to the ledger first, then pushed to the page, so the page never shows what the ledger lacks. The exception is a record explicitly flagged as not written.
  - D2.2.2. Records stream to the page over server-sent events. The stream opens by naming the conversation, and supports resuming from the last event seen. [R1.3.1, R1.3.2]
  - D2.2.3. Pages and static files are served uncacheable, with asset URLs versioned per launch. [R1.3.3]
- **D2.3.** The page draws each record kind with its own renderer. Unknown kinds still appear as raw records. [R1.7]
- **D2.4.** Local-only access: [R1.2]
  - bind to loopback;
  - reject Host headers other than loopback names, which prevents DNS rebinding;
  - state-changing requests must be JSON POSTs from a same-origin page.
- **D2.5.** An End session control, and an equivalent endpoint, stop the server gracefully. Open event streams are closed first so shutdown doesn't stall. [R1.6, R7.4.1; finding: shutdown waited on the stream]

### D3. Ledger layout and authorship
- **D3.1.** There is one persistent ledger for this harness, and each launch opens a new session in it. [founder 2026-09-24]
  - D3.1.1. The agent can therefore read earlier chats. This is accepted.
  - D3.1.2. The ledger root sits inside the task folder, carrying its own git attributes and ignore rules. [R7.5]
- **D3.2.** The scribe is imported in place, with bytecode writing suppressed for the import. [founder 2026-09-24; R7.1.1]
- **D3.3.** Harness records are entries named `log/<session>/<sequence>`, sequential within the session and unique across sessions. [R7.3]
  - D3.3.1. **Labels:** `harness` for protection, and `log.<kind>` for the type. The harness applies both. The body and its labels are written together, with no chance for an agent call to land in between.
  - D3.3.2. **Message text entries:** they take the same naming and labels, with kind `text`. The body is the raw text, authored by its writer. The following message entry links to it as `[[name]]`. [R2.2]
- **D3.4.** Authors: [R7.3, R8.4, R2.2.2]
  - harness: `harness:claude-anthropic-harness/task-4-ledger`;
  - agent: `pilot:<model>@claude-anthropic-harness`, giving role, model and harness;
  - human: `human:session-user`, not identified further for now. [founder 2026-09-25]
- **D3.5.** A ledger write failure is surfaced in the UI and on the console, and later records are marked as not in the ledger. [R7.4]

### D4. Agent ledger access
- **D4.1.** Three tools: read, list and write. There is no delete, tag or untag tool. [R8, R8.3.1]
- **D4.2.** The write guard refuses: [R8.3]
  - names under `log/`;
  - names carrying the `harness` label, whoever wrote them;
  - names whose current author is not the agent;
  - labels starting with `harness` or `log.`;
  - null bodies, which would be deletes.
- **D4.3.** Agent-written names are automatically labelled `pilot`, and the prompt suggests the `pilot/` namespace. [R8.2]
- **D4.4.** Large read results are truncated at a stated limit, with a note saying how to raise it.

### D5. Tool policy
- **D5.1.** The built-in tools are an allowlist of Read, Glob and Grep. Nothing else built in is loaded. [R3.1]
- **D5.2.** One policy is enforced at two points: a pre-call hook that sees every call, and the permission callback. [R3.2]
- **D5.3.** Paths are resolved, symlinks included, before containment checks. [R3.3]
- **D5.4.** The search-reach rule: [R3.3.3]
  - Glob from a base that contains an excluded directory is allowed only if the pattern can't descend into it: a literal first segment, or a single segment without `**`.
  - Grep from such a base is always refused.
- **D5.5.** The agent's working directory is the nimoi root, so relative paths in its prompts and tools work naturally. [R3.3] This choice is what exposed R10.2 and made it necessary.

### D6. Isolation settings [R10]
- **D6.1.** No setting sources are loaded. [R10.1]
- **D6.2.** Auto memory is disabled through the documented environment switch. [R10.2; SDK docs "What settingSources does not control"]
- **D6.3.** The MCP configuration is strict: only the harness's in-process servers. [R10.3]
- **D6.4.** CLI session persistence is off. [R10.4]
- **D6.5.** Accepted as not isolated, for now:
  - the CLI's global config;
  - managed policy settings;
  - the CLI's own environment text;
  - the account email the CLI injects into the agent's context.

  All are documented as read regardless.

### D7. Cost accounting [R6]
- **D7.1.** The SDK reports cost as a running total for the conversation. Per-turn cost is the difference between totals. A total that falls is recorded as an anomaly. [finding]
- **D7.2.** The harness's own cap is the real stop, because the SDK's budget check fires only after the fact and can overshoot. The SDK budget stays as a tripwire. [finding]
- **D7.3.** The per-message agent turn limit is set high enough for onboarding reads (20).

### D8. Operations
- **D8.1.** The default port is 8766, avoiding the ledger reader's 8765, and it is checked before the ledger opens. [finding; R7.4.5]
- **D8.2.** A lock left by a crash blocks the next launch, which prints the human recovery steps from the scribe's interface notes. [R7.4.4]
- **D8.3.** The system prompt is a template file, filled in at launch with the root, ledger, session, author and model. [R9.7]

---

## Open at time of writing
- **Email in context.** The account email reaches the agent's context through the CLI. It is in no ledger so far, but the agent could write it. It conflicts with the spirit of R6.3.
- **Refusals read as errors.** The CLI presents hook refusals as "hook error". R3.4 is met in content but not in framing.
- **Cost of self-directed reading.** Reading the ledger on its own initiative is costly for the agent, because harness entries hold whole files and messages. R9.2.1 limits initiative, but exploring the ledger has happened when the agent is only told to "orient".
- **Not yet verified live:** interrupting a task-4 turn from the UI, and Grep refused for reaching into `candidate_repos/`.
