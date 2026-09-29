# task-5-subagent and task-6-hybrid — back-specification

**Purpose.** This spec lets someone reproduce what claude-anthropic-harness built for tasks 5 and 6. It records requirements and design decisions, not implementation. It also covers task 5's back-spec, which was still pending. Written 2026-09-29 from the built and tested harness (`task-6-hybrid/`) and its live runs.

**Bar:** the failures must be informative and their record must survive. This is a prototype for a NIMOI testbed, not a production system. A defect that corrupts or loses the record is worse than one that makes the harness fail.

**Base.** `task-4-spec.md` (R1–R11, D1–D8) still holds, except where an item below says it supersedes one.

**Numbering and sources.** Numbering is as in `task-4-spec.md`: `R` is a requirement, `D` a design decision, and each is numbered from root to derived. Sources are in brackets:
- `rules 5c` is `bootstrap-harness/rules.md`, task 5, item c;
- `founder` is a founder decision, with its date;
- `finding` is a defect or fact found while building or running (records in `mem/`);
- `peer` is the peer review of 2026-09-25 (`peer-review-2026-09-25.md`);
- `derived` means it follows from its parent.

---

## Requirements

### R12. One notation for every agent's permissions [rules 5b, 5e]
- **R12.1.** Five scopes: `fs.read`, `fs.write`, `fs.exec`, `ledger.read`, `ledger.write`.
  - R12.1.1. An entry allows; an entry starting with `!` excludes. An entry covers itself and everything below it, by whole path segment.
  - R12.1.2. A missing scope, or one with no entries, permits nothing.
- **R12.2.** `fs.write` and `fs.exec` never overlap: an agent may not execute where it can write. [rules 5d]
- **R12.3.** A child's bounds lie within its parent's. The parent's exclusions are inherited. [rules 5e]
- **R12.4.** Targets are judged after resolution: symlinks, junctions and 8.3 short names become the real path first. [peer]
- **R12.5.** Fixed denials apply whatever the bounds:
  - secret-looking files;
  - raw ledger files;
  - writes into the scripts directory. [rules: Secrets, 5d]
- **R12.6.** Human-edited bounds files are refused at launch if they break R12.2. [derived]

### R13. Bounded file writing [rules 5c]
- **R13.1.** The agent writes the body of an exact ledger revision to a file within `fs.write`.
- **R13.2.** The write is recorded before and after, with its sha256. Replacing a file needs its current sha256. [derived]
- **R13.3.** Every written file is reproducible from the ledger. [bar]

### R14. Script execution [rules 5d, 5g]
- **R14.1.** Only scripts within `fs.exec` run. Their output is captured as a ledger entry. There is a harmless test script.
- **R14.2.** Agents can draft scripts but cannot promote them. Promotion is a human act: in task 6, a human approval card (R19.3).
- **R14.3.** For GPT agents, Codex's own execution tool is allowed where the model needs it, restricted where possible, and documented where not (R16.4). [rules 5g]

### R15. Subagents owned by the harness [rules 5e, 5f]
- **R15.1.** Neither SDK's native subagents are used; each child is a separate agent session that the harness owns. [rules 5f]
- **R15.2.** The parent chooses the model and the bounds, in the same notation as its own. The child's bounds are a subset of the parent's. [rules 5e]
- **R15.3.** The child's instructions are a ledger entry written by the parent. They are pinned to an exact revision and delivered word for word. [rules 5e]
- **R15.4.** The child reads the latest onboarding in full before any other tool. [rules 5e]
- **R15.5.** The parent's call blocks until the child finishes, and interrupting the parent interrupts the child. [founder 2026-09-25]
- **R15.6.** Harness records stay private to a child unless the parent grants them explicitly. They copy what other agents have read. [peer]
- **R15.7.** In task 6, only task owners spawn (R17.3). The brief may carry a bar; it need not. [founder 2026-09-29]

### R16. Two model lineages behind one harness [rules 6a, 6b]
- **R16.1.** Claude agents run on the Claude Agent SDK; GPT agents run on the Codex App Server. [rules 6b, swimlanes]
- **R16.2.** Every agent gets the same tools, the same checks and the same records, whichever backend runs it. [derived]
- **R16.3.** Codex's home directory: [founder 2026-09-28]
  - live GPT runs use `~/.codex`, the ChatGPT login;
  - each launch records a before/after list of what changed there;
  - offline tests use an isolated home inside the lane.
- **R16.4.** Every Codex native tool that can be disabled is disabled. Any native delegation, shell or patch call stops that agent. This is documented as detection, not prevention. [founder 2026-09-28; rules 5g]
- **R16.5.** There is no API-key fallback for GPT agents. [derived from R16.3]
- **R16.6.** No context reaches a GPT agent that the harness did not approve: [derived from R10]
  - no instruction files;
  - no user skills, plugins or MCP servers;
  - no Codex permission text.

### R17. Three layers [rules 6c]
- **R17.1. The governor** is `claude-opus-5-5` only.
  - It talks with the human, dispatches and supervises task owners, and resolves their requests. [rules 6c b1]
  - It does not do owners' work, and does not grade their quality beyond what safety and governance need. [rules 6c b1]
  - R17.1.1. Its powers are read access to nimoi, its own ledger area, dispatch and supervision, and resolving requests. It has no file writes, no execution and no subagents, and this is enforced by its toolset. [founder 2026-09-28]
- **R17.2. Task owners** are `claude-opus-5-5`, `claude-fable-5-1`, `gpt-6-astra`, `gpt-6-sol` or `gpt-5.6-sol`. [rules 6c b2]
  - Each pursues one assignment. It is responsible for the quality of the result, for compliance with the letter and intent of instructions and restrictions, and for NIMOI's standards.
  - R17.2.1. The governor passes the task and the rules under which success can be achieved. It never passes the success bar; the owner sets its own. [founder 2026-09-29]
  - R17.2.2. Owners read onboarding in full before any other tool. [derived from R15.4]
  - R17.2.3. Owners run in the background and in parallel. Dispatch returns at once, so the governor is never blocked by an owner. [derived]
- **R17.3. Subagents** are Claude Sonnet or higher, or GPT Terra or higher. They are strictly subordinate to their owner, and only owners spawn them. [rules 6c b3]

### R18. The UI is a chat with the governor, with visibility into every agent [rules 6d]
- **R18.1.** The page shows the conversation with the governor. The harness's messages to the governor are shown as such, never as the human's.
- **R18.2.** Every agent's activity is visible: a tree of agents with state and usage, and a feed that can be filtered by agent.
- **R18.3.** The human decides approvals in the UI.
- **R18.4.** A message sent while the governor works waits its turn, up to a small limit. This supersedes R1.4 of task 4.

### R19. Owners' self-blocking requests [rules 6f; founder 2026-09-28]
- **R19.1.** There are four kinds: `clarify`, `expand_bounds`, `promote_script` and `more_budget`.
- **R19.2.** The owner's call waits until the request is resolved. The governor receives the request between its own turns.
- **R19.3.** `promote_script` always needs the human. What the governor and the human reviewed is exactly what is promoted. [founder 2026-09-28; bar]
- **R19.4.** `expand_bounds` must stay within a ceiling that the human sets. Beyond it, only the human can act.
- **R19.5.** `more_budget` must stay within the launch budget. An owner's dollars cover its whole subtree: the owner and its subagents. [finding: live run 2026-09-29]
- **R19.6.** Every request is resolved or cancelled, and ends on the record.

### R20. Honest authorship [bar]
- **R20.1.** Every text is authored by whoever wrote it. A scripted run's message names its driver, never the human. [finding 2026-09-29: phase 1's live turn]
- **R20.2.** Every decision on an approval names who decided.

---

## Design decisions

### D9. One harness core, two backends [R16]
- **D9.1.** There is one tool registry. Each tool is defined once, with its name, schema and handler. Claude agents get the tools as in-process MCP servers; GPT agents get them as Codex dynamic tools. [R16.2]
- **D9.2.** One agent core holds the checks, records and limits:
  - the checks: fail closed, the allowlist, the onboarding gate;
  - the records: text entries by their writers, and message, tool, usage and limit records;
  - the limits.

  The Claude backend reaches the checks through SDK hooks; the Codex backend calls the core for each tool call.
- **D9.3.** Codex refuses dynamic tool names that start `mcp__`. So there is one Codex namespace per harness server:
  - the model calls `tools.fs__read` inside `exec`, or `fs.read` for a direct-tool model;
  - the call maps back to `mcp__fs__read`;
  - records, allowlists, prompts and audits use only the harness names.

  [finding: codex 0.158]
- **D9.4.** Each GPT agent gets its own App Server process, driven over stdio with asyncio.
  - Server requests are served in their own tasks, since a harness tool may wait for minutes.
  - Protocol messages are recorded in both directions.
  - Text with its own entry appears there as its link; long strings become digests; credentials are redacted; `config/read` is never recorded.

  [derived; peer: claude-codex transport]

### D10. Codex restrictions and checks [R16.4, R16.6]
- **D10.1. Requested.**
  - Features off: shell, unified exec, JS REPL, code mode, multi-agent (both versions), apps, hooks, plugins, browser, computer use, goals, sleep, image generation, view image, skill search, collaboration modes, and more (`backend_codex.RESTRICTIONS`).
  - `agents.enabled=false` and web search off.
  - No project docs.
  - No Codex permission, collaboration-mode or apps text.
  - Every listed skill disabled by name.
  - Configured MCP servers and plugins disabled by name.
  - `notify=[]`.

  [gpt-codex policy plus findings: captures against a fake model]
- **D10.2.** `environments: []` on thread start and on every turn. Without it, Codex 0.158 offers `apply_patch` and `view_image` whatever the flags say. [finding]
- **D10.3. Checked before the first turn, failing closed:**
  - the model Codex selected is the one requested;
  - no instruction files were loaded;
  - every requested feature reads back off.

  The one exception is `unified_exec`, which reads back on whatever the flag says. It is recorded as a caveat; no shell tool is offered. [finding]
- **D10.4.** The exec host stays on only for code-mode-only models, since they reach tools only through it. [rules 5g]
- **D10.5. Stops.** Each is recorded, then the harness interrupts the turn and closes the App Server, and later turns are refused:
  - any model call other than harness tools and Codex's runtime helpers;
  - any thread item other than messages, reasoning and harness tool calls;
  - any approval request, which is also declined.

  [founder 2026-09-28]
- **D10.6.** The user-input tools cannot be switched off. They do not reach the harness:
  - Codex refuses `request_user_input` outside Plan mode;
  - `request_user_input_async` becomes an agent message.

  [finding]
- **D10.7.** Each agent's Codex state and logs live in its own folder under `.runtime/`, which is gitignored and kept. The logs hold response headers, including a load-balancer cookie. No account credential was found there. [founder 2026-09-29: keep; finding]
- **D10.8.** `~/.codex` is snapshotted before the launch's first App Server and diffed at the end. The diff lists paths and times, never contents. Another process's write shows only as a time overlap. [founder 2026-09-28; finding]
- **D10.9.** A surface check runs the real `~/.codex` configuration against the fake model before a live run, and fails if anything beyond the harness tools and the runtime helpers is offered. [finding: the live config sets notify, plugins, an MCP server and js_repl, and trusts nimoi]

### D11. Identity [R8.4, R20]
- **D11.1.** Authors take the form `<id>@<ledger session>:<model>@claude-anthropic-harness`. The session resolves task 5's open question: a new session's first turn is not an earlier session's. [founder 2026-09-28]
- **D11.2.** Ids: `governor`; `owner.N` for task owners; `owner.N.M` for subagents; `pilot` for task 5's single agent.
- **D11.3.** Every record carries its agent and the label `agent.<id>`. Agent-written entries are labelled with the agent's role.
- **D11.4.** The human is `human:session-user`. A scripted run's driver is `developer:claude-code-session`, announced by a `run_driver` record. [R20]
- **D11.5.** Record fields may not use the names the log sets itself (`name`, `id`, `seq`, `ts`). The log refuses them. [finding: two records lost their fields on the UI]

### D12. Governance [R17, R19]
- **D12.1.** Dispatch checks, in order; every refusal gives its reason:
  1. the model is on the owner list, and fewer than 3 owners are open;
  2. the bounds are within the ceiling, with `!log` added unless a log entry is named;
  3. `fs.read` covers onboarding;
  4. the assignment was written by the governor, is pinned, has no `Bar:` line, and is readable by the owner;
  5. the budget allows a Claude owner.

  The record is written first; then the owner starts in the background.
- **D12.2.** Each owner has its own backend session and inbox. An idle owner keeps its session, so it keeps its context:
  - the governor's `message_owner` adds a turn;
  - `stop_owner` interrupts it, cancels its requests and closes it;
  - the launch's end closes every owner before the ledger.
- **D12.3.** Events reach the governor between its turns: an owner's turn ending (with its reply), a request, the human's decision, an owner ending. Events that arrive together are delivered as one harness-authored message. Human messages go first.
- **D12.4.** Owners' tools are built from the ceiling, and their bounds are applied live. An approved `expand_bounds` therefore takes effect at once, without new tools.
- **D12.5. Limits:**
  - the launch budget is the hard cap for Claude agents, and also the SDK's tripwire;
  - every owner, of either lineage, has a dollar allowance covering its subtree. It is checked when each of the owner's turns starts, and it caps each Claude subagent's budget at spawn. A GPT owner's dollars pay only for Claude subagents; GPT subagents have no dollar budget. [finding: live run 2026-09-29, where the cap was the launch budget alone]
  - model rounds per message are enforced by the harness for both lineages and can be raised during a turn: distinct `message_id`s (Claude), `rawResponse/completed` (Codex);
  - GPT agents are counted in tokens;
  - Codex model time per turn is capped, excluding time inside harness tools.
- **D12.6.** A promotion snapshots the draft's bytes when the request is made. On the human's approval, the harness writes exactly those bytes into the scripts directory, never replacing a file, and records their sha256. [R19.3]

### D13. UI [R18]
- **D13.1.** Three columns: the agent tree and the approval cards; the governor chat, with harness messages collapsible; and activity, filterable by agent.
- **D13.2.** An approval endpoint records the human's decision, and its author, before applying it.
- **D13.3.** An offline demo, with fake agents and a scripted scenario, serves the page for inspection without model calls.

### D14. Audit [bar]
The audit reads a session, from the ledger alone, and checks that:
1. the scribe loads it with no findings;
2. every allowed tool call ends;
3. every file write matches its revision;
4. every exec ends;
5. every spawn ends, within its parent's bounds;
6. message links resolve to the right author (Claude blocks and Codex agent messages);
7. agent writes stay within `ledger.write`;
8. every owner ends, stays within the ceiling (including later bound changes), and received no bar;
9. every request is resolved;
10. every promoted script matches the text and sha256 recorded with its request.

### D15. Operations
- **D15.1.** Launches:
  - `app.py` starts the governor UI (port 8768);
  - `--pilot` runs task 5's single agent;
  - `live_turn.py` runs one scripted turn;
  - `live_institution.py` runs one scripted three-layer run.
- **D15.2.** Every launch that starts Codex records the `~/.codex` diff before the ledger closes.
- **D15.3.** The tests run offline. The Codex tests use the real binary against a local fake model.

---

## Open at time of writing
- **Onboarding and the bar.** Onboarding 1.12 says a worker given no bar stops and asks. The governor passes none (R17.2.1), so each assignment tells the owner the bar is its own to set. In the live run, both owners set their own and neither stopped. Whether onboarding should say this is the founder's decision.
- **Owner allowance within a turn.** An owner's allowance binds between its turns and on its subagents. Within its own turn, a Claude owner is bounded only by the launch budget and by its model rounds, because the SDK reports cost only when a turn ends.
- **`unified_exec`.** It reads on whatever is requested. No shell tool has been offered in any capture, and no execution environment is given.
- **No mid-task channel to the human.** A question asked through `request_user_input_async` appears as an agent message. An owner should use `clarify`.
- **Attribution in `~/.codex`.** The diff cannot say which process wrote a file. The desktop app and other lanes write there too.
- **Misattributed message.** Phase 1's live GPT turn (session 20260928T190527Z) carries a message authored `human:session-user` that the developer agent wrote. It is recorded as a discrepancy and not repaired.
- **UI checked through the page, not by eye.** The page was verified through its content in the browser pane, because screenshots timed out.
