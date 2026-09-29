# Task 6 plan: hybrid three-layer harness

2026-09-28, Claude session (Opus 5.5), claude-codex-harness. rules.md re-read on the
founder's instruction ("re-read rules and proceed with task-6-hybrid"). Changes since
task 5: the swimlane's second element is its *initial* architecture, combined for hybrid
tasks; task 5 gained 5g ("exec restriction is a known limitation for codex agents. The
exec tool is allowed. Restrict it where possible, document where not."); task 6 is new.

**Bar:** a person chats with an Opus governor that dispatches task owners (Claude or GPT)
on written tickets, sees every agent's activity, and approves what exceeds the
governor's bounds; task owners delegate to subagents under task 5's rules, and ask the
governor, blocking, for what they may not do. Good enough that what breaks is
informative and its record survives in the ledger. A permission that leaks, or an
action taken without the approval it needed, is worse than a refusal. Prototype.

## Reading of rules.md task 6 (recorded before building)
- (a) Derived from task 5 (after its peer-review revision): the same tools, bounds
  notation, ledger record, onboarding gate, page; the same requirements 5a-5g.
- (b) My lane has used Codex (GPT) only; the Claude side (Agent SDK) draws on
  claude-anthropic-harness and gpt-anthropic-harness.
- (c) Three layers (the rules label them b1-b3 under c; read as c1-c3):
  - governor: Claude Opus only; talks to the human; dispatches task owners; does not do
    their work or grade it beyond safety and governance;
  - task owners: Claude Opus/Fable or GPT Astra/Sol; own a ticket's quality and
    compliance; keep NIMOI's standards;
  - subagents: Claude Sonnet or higher, GPT Terra or higher; strictly subordinate.
- (d) The page is a chat with the governor, with every other agent's activity visible.
- (f) A task owner can make a self-blocking request to the governor (the list has no
  item e).
- 5g applies to task 6: the GPT models allowed here are all code-mode (below). Task 5
  itself is left as it is unless the founder asks for 5g there.

## Founder answers, 2026-09-28 (in chat, to my questions)
1. Claude live runs: **"Yes, standing for this lane"** — like the Codex authorization,
   each run records its ~/.claude changes (names and times); offline work uses stand-in
   models where possible.
2. Models: **"As proposed"** — governor: claude-opus-5-5. Task owners: claude-opus-5-5,
   claude-fable-5-1, gpt-6-astra, gpt-6-sol. Subagents: claude-sonnet-5,
   claude-opus-5-5, claude-fable-5-1, gpt-5.6-terra, gpt-5.6-sol, gpt-6-sol,
   gpt-6-astra.
3. Requests: **"Grant up to its own bounds; beyond needs you"** — the governor may widen
   a task owner's bounds up to its own after its safety assessment; anything past its
   own bounds (new paths, promoting a script into scripts/) becomes an approval card on
   the page, and the harness acts only on the human's approval; every step is in the
   ledger.
4. Task owners: **"Until the governor closes it"** — a ticket (a ledger entry the
   governor writes, with a Bar: line) starts it; it works over as many turns as the
   governor sends, reports, and is closed by the governor. Subagents stay one turn.
   The human talks only to the governor.

## Facts gathered (2026-09-28)
- Codex catalogue [checked: `codex debug models`, offline home]: gpt-6-astra, gpt-6-sol,
  gpt-5.6-sol, gpt-5.6-terra are all `tool_mode: code_mode_only`, multi-agent v2. From
  task 2: such a model reaches dynamic tools only inside JavaScript `exec` (a V8
  isolate, limits unverified), and is offered Codex's six `collaboration.*` sub-agent
  tools whatever the `multi_agent` flag says. With the shell and patch features off,
  exec's nested tools were ours plus clock, goals and skills.
- claude_agent_sdk 0.2.159 imports from this machine's Python (user site-packages).
- No peer lane has a task 6 yet; onboarding is still v1.12.

## Design (derived from the answers; recorded before building)
- **Agents.** One interface, two engines: Codex App Server (task 5's agent) and the
  Claude Agent SDK (new). Ids show the tree: `gov`, task owners `gov.1`, subagents
  `gov.1.1`. Depth cap 2: subagents do not spawn (layer 3 is the leaf).
- **Governor tools:** ledger tools (writes under `tickets/` and `gov/`), fs_list/fs_read
  (for safety assessments), add, and governance tools: start a task owner on a ticket,
  message it, get its status, close it; list and answer requests; ask the human. No
  fs_write, python_exec or subagents: it does not do task owners' work.
- **Task-owner tools:** task 5's set (ledger, fs read/write/exec within bounds,
  subagents from the subagent models), plus the request tool. Offered whole from the
  start (a thread's tools cannot change later); the bounds decide each call, so a grant
  takes effect at once.
- **Requests.** The owner's request is a ledger entry by the owner; the harness records
  it, shows a card, and tells the governor. The owner's tool blocks up to a limit, then
  says "pending" and the owner waits again. The governor grants within its bounds,
  refuses, answers, or asks the human; the human's approve/deny comes from the page. The
  harness applies an approved grant (bounds; a script promotion copied byte for byte
  with its hash) and records who decided.
- **Governor turns** come from the human, or from the harness when a request or a task
  owner's report arrives while the governor is idle (a harness-authored message).
- **5g (code-mode GPT models).** `code_mode_host` stays on (exec is how they reach our
  tools); everything else of task 5's 20 disabled features stays off. `exec` and its
  helper tools become reviewed, and their JavaScript is recorded from raw events. The
  `collaboration.*` tools cannot be removed: a call to one stops the turn (unreviewed).
  Documented, with a fake-model capture of what is offered.
- **Claude isolation** (from claude-anthropic's findings): no setting sources, strict
  MCP config, auto memory off, only our in-process MCP tools, no session persistence;
  every tool use outside our tools stops the turn.

## Phases (pauses with the founder)
1. Offline: shared core from task 5; Claude adapter; governance, requests and layers;
   page; tests with stand-in models (Codex's fake model; a fake Messages API for the
   Claude CLI if it can be pointed at one). **Pause:** show the offline demo.
2. Live runs under the standing authorizations, then the record and back-spec.

## Phase 1 built (2026-09-28): offline, paused before live runs
`task-6-hybrid/` (README maps rules 5a-g and 6a-f). New modules: `claude_agent.py` (the
Claude engine), `codex_agent.py` (task 5's Codex engine, 5g added), `agent.py` (what
both share), `governance_tools.py`, `fake_claude.py`; `conversation.py`, `policy.py`,
`toolkit.py`, `prompt.py` + `prompts/`, `check_ledger.py`, `ui.py` + `static/` reworked.
Carried from task 5: `bounds.py`, `paths.py`, `runner.py`, `ledger_tools.py`,
`fs_tools.py`, `codex_client.py`, `fake_model.py`, `ledger_log.py` (own ledger
`claude-codex-hybrid`, and `requests/`).

Decisions made while building, beyond the design above:
- Offline runs use a local stand-in for the Anthropic Messages API: the SDK's bundled CLI
  pointed at it with ANTHROPIC_BASE_URL, a dummy key and CLAUDE_CONFIG_DIR in
  `../.runtime/offline-claude-home` (neither peer lane had one). `--fake-model` also
  defaults Codex to the offline home.
- The harness strips every `CLAUDE*`, `ANTHROPIC_*` and `MCP_*` variable from its own
  environment at start, recording their names only (finding 2).
- The governor holds task 5's root bounds plus `tickets/` as what it may delegate; its
  tools are governance and reading only. Task owners are offered their whole layer's
  tools from the start (a Codex thread's tools cannot change later); bounds decide.
- Requests block with a timeout (default 300 s) and "pending" + governor_wait, not
  without limit. The human's approvals are applied by the harness (a bounds grant even
  beyond the governor's bounds, never past the fixed rules; a promotion copies a
  workspace `.py` into the scripts folder, never over an existing script).
- News for the governor (reports, requests, the human's decisions) is batched into one
  harness-authored message per governor turn when it is idle.
- Subagents are the leaf layer (depth 2 = task 5's cap): no spawning, no requests.
- Claude agents: `include_partial_messages` for streaming, `verbatim_prompts` (the CLI
  expands `@path` otherwise: from the peer brief), a PreToolUse hook and can_use_tool
  refusing tools that are not ours, the init inventory checked each turn.

Verification:
- **55 tests pass** [checked: `python -B -W error::ResourceWarning -m unittest discover`,
  17.6 s], including a real three-layer run on both engines with the stand-ins
  (governor on the Claude CLI; a gpt-6-astra task owner on Codex; its request granted;
  a claude-sonnet-5 subagent running `hello_safe.py`), `check_ledger --closed` clean.
- **Page, fake models** (`.runtime/fake-ledgers/…/20260928T190213Z.ledger`): a request
  beyond the governor's bounds referred to the human; Approve clicked on the page; the
  grant applied; the task owner continued; the governor told. `check_ledger --closed`:
  clean. Both offline homes unchanged.

Findings:
1. The Claude CLI runs fully offline against a local Messages API stand-in: only
   `HEAD /api/hello` and `POST /v1/messages?beta=true` arrive; `~/.claude` untouched
   [checked: before/after listing]. With `tools=[]` and strict MCP config the model is
   offered exactly our `mcp__nimoi__*` tools [checked: captured request].
2. Started from inside a Claude Code session, a harness process carries 27 `CLAUDE*` /
   `ANTHROPIC_*` / `MCP_*` variables (the session's API endpoint, messaging socket and
   token, a child-session marker); the SDK passes the whole environment to its CLI, so an
   agent would have run as the session's child. Stripped at start [checked].
3. Blocking tool calls past 60 s work on both engines: 75 s through the Claude CLI's
   in-process MCP and through Codex dynamic tools [checked: offline]. The Claude
   engine's idle clock now pauses while one of our tools runs.
4. Rule 5g surface [checked: fake capture, offline Codex home, gpt-6-astra and
   gpt-5.6-terra]: `exec` (our tools nested, plus goals and clock), `wait`,
   `request_user_input(_async)`, `clock.sleep`, and six `collaboration.*` tools that no
   flag removes. The harness allows the first, and stops the turn on the last.
5. What the Claude CLI adds around the system prompt [checked: fake capture]: a billing
   header, "You are a Claude agent, built on Anthropic's Claude Agent SDK.", an
   `# Environment` reminder (438 chars) and a token-budget reminder. With the live login
   the peers found the account email added too: not yet checked here.
6. The scribe refused a `run` record holding tuples (they do not survive its JSON round
   trip); only a repr survived. The checker found it; tuples are now lists, and the
   checker flags any record that survived only as a repr.
7. Another Claude session was active in nimoi during these runs (`~/.claude/projects/…/
   386f9a1f…`): `claude_home_changes` of live runs will include its writes; attribute by
   name and time.
