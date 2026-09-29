# Task 5 record: harness-owned subagents, bounded write and execute

2026-09-25, Claude session (Opus 5.5), claude-codex-harness. rules.md re-read on the
founder's instruction ("re-read rules and proceed to task 5"): tasks 1–4 unchanged;
task 5 new.

**Bar:** a parent agent can delegate to a harness-owned subagent whose permissions are
provably a subset of its own. Agents can draft files and run only vetted scripts, never
both in one place, and every file written, script run and delegation is in the ledger.
Good enough that what breaks is informative and the ledger keeps the record. A
permission that leaks is worse than one that refuses. Prototype.

## Reading of rules.md task 5 (before founder answers)
- (a) "tasks 2 and 3": task 5 is read as building on task 4, since (c)–(e) all use
  its ledger, and (e) its onboarding requirement. **To confirm.**
- (b) One bound notation for filesystem read, write and execute and for ledger read
  and write. It must be concise, general and intuitive, with a subset test anyone can
  do by eye.
- (c) Filesystem write = "write the body of a ledger entry to a file", inside a
  workspace. Nothing reaches disk that is not first in the ledger.
- (d) Python execute = run a script from the harness scripts folder; output goes into
  a ledger entry. Invariant: no agent's write bound and execute bound ever overlap, and
  the scripts folder is never writable by an agent. Promotion from workspace to scripts
  is human-only. I write one safe test script.
- (e) A harness-owned subagent tool: the parent gives model, bounds and a pointer to an
  instructions entry it wrote; the child's bounds must be a subset of the parent's; the
  child reads onboarding first; bounds are injected with the same notation for both.
- (f) Codex's own sub-agent tools stay off (the policy model has none; see task 2).

## Founder answers, 2026-09-25 (in chat, to my questions)
1. Base: **"Build on task 4"**.
2. Run mode: **"Parallel, with wait"**. The spawn tool returns at once with a
   subagent id; the parent keeps working and checks or waits with a second tool.
3. Notation: **"Yes, prefixes"** (the proposal below, approved).
4. Nesting: **"Nested, capped"**. Children may spawn grandchildren with a subset of
   their own bounds, up to depth 2 and a per-conversation cap.

## Design (derived from the answers; recorded before building)
- **One Codex app-server process per agent**, each with its own worker thread, SQLite
  folder and dynamic tools bound to its bounds. A parent blocked in `subagent_wait`
  holds only its own connection, so the child it waits for keeps running. A shared
  app-server would need one reader for all agents, and a blocking tool would stall it.
- **A child runs one turn:** its instructions, delivered as its first message. When
  that turn completes, the child is done, and its last agent message is its report
  (a transcript entry). Follow-ups mean a new spawn.
- **Caps:** depth ≤ 2; at most 6 subagents per conversation and 3 running at once; a
  child's turn times out after 5 minutes.
- **Models:** a child's model must be a direct-tool model per Codex's catalogue (the
  task-2 finding: a code-mode model always has JavaScript `exec`). Others are refused.
- **Fixed rules outside the notation**, stated once and the same for every agent:
  - always readable: the latest onboarding file, and the agent's own instructions entry;
  - never readable: `.git`, secret-named files;
  - never writable in the ledger: `harness/`, `transcript/`, `exec/`, and the
    protected labels;
  - never executable: anything but a `.py` file inside `fs.exec`;
  - the scripts folder is never inside any `fs.write`.
- **Onboarding is enforced, not only instructed:** until an agent has read the latest
  onboarding file, every tool except `fs_list` and `fs_read` refuses. This applies to
  the root pilot too.
- **Own ledger:** task 5 is a new harness, so it gets its own ledger,
  `claude-codex-subagents` in `task-5-subagent/ledgers/`, one session file per chat
  (the task-4 decision).
- **Execution:** `python -I -B -X utf8 <script> [args]`, with the workspace as the
  working folder, a minimal environment (no inherited secrets), a timeout and output
  caps. The output is the body of an `exec/…` entry by the harness; a harness
  `python_exec` record links to it, following the message pattern.
- **File writes:** the body of a readable, text-bodied ledger entry is written byte for
  byte to a path inside `fs.write`. The harness records the entry id and the file's
  sha256.

## Built (2026-09-25)
Modules:
- `bounds.py`: notation, subset test, invariant, one rendering;
- `tools.py`: a per-agent Toolbox whose tools follow the bounds, plus the fixed rules
  and the onboarding gate;
- `agent.py`: one app-server, thread and turn loop per agent;
- `conversation.py`: the tree, spawn and wait, caps, the model rule, shutdown;
- `prompt.py`: the root and subagent instructions, with the same bounds block;
- `ledger_log.py`: own ledger; the text-plus-link pattern generalised to script output;
- `scripts/hello_safe.py`, `scripts/README.md`, `workspace/`.

Copied from task 4 unchanged or nearly: `codex_client.py`, `fake_model.py`,
`policy.py` (plus `direct_tool_model`), `ui.py`, `static/` (agent-aware renderers, a
bounds panel and a subagents panel).

Decisions made while building, beyond the design above:
- A child's first message is the instructions body exactly, at the pinned id, authored
  in the transcript by the parent. The framing (who, which entry) is in its developer
  instructions, so the transcript's authorship stays exact.
- `subagent_wait` works only on the caller's own children.
- The onboarding gate counts lines read contiguously from line 1, so reading only the
  first few lines does not open it.
- The model's arguments must match each tool's schema exactly (task 4); `bounds`
  accepts only the five keys.
- The fake model runs every `tool:` line of the latest message in order, so offline
  tests script parents and children alike.

## Verification
- **11 tests pass** [checked: `python -W error::ResourceWarning -m unittest discover`,
  1.9 s]:
  - bounds: semantics, malformed input, subset, invariant, one rendering;
  - toolbox: tools follow the bounds; the onboarding gate; ledger bounds and the own-
    instructions grant; `fs_write` exact bytes and 7 refusals, including into
    scripts/ even with bounds that allow it; `python_exec` output entry, stderr, exit
    code, no inherited environment, 3 refusals, and never where writable; spawn checks;
  - end to end, real Codex (two app-servers) with the fake model: parent reads
    onboarding, writes instructions, a code-mode model and non-subset bounds are
    refused, spawn, the child reads onboarding and runs the script, wait returns
    `done`; ledger authorship and record kinds checked.
- **Browser, fake model** (scratch ledger): the same flow on the page. Child events are
  tagged and indented; start and finish cards; the Subagents panel reads
  `pilot.1: done`; End is clean.
- **Browser, live**, gpt-5.5, `ledgers/claude-codex-subagents/20260925T192816Z.ledger`
  [checked: `scribe.py check` exit 0; `load()` closed, no findings]:
  1. Delegation. The pilot read onboarding and wrote a brief with its own `Bar:` line
     (`test-pilot/task-5/subagent_hello_safe_instructions`). It spawned `pilot.1`
     with minimal bounds `{"fs.read":[],"fs.write":[],"fs.exec":["…/scripts/"],
     "ledger.read":[],"ledger.write":[]}` and waited. The child read onboarding and
     its brief through the fixed grants, ran `hello_safe.py 4 5` (exit 0, 63 ms,
     output `exec/…/0008-hello_safe`) and reported in 18.7 s. The pilot relayed the
     output and described the mechanism correctly, including the fixed grants. Turn
     54 s.
  2. File write plus directed test. Draft `test-pilot/task-5/pilot-note`, then
     `fs_write` to `workspace/pilot-note.txt` (161 bytes; the file equals the ledger
     body [checked: byte comparison]). The single attempt to write the entry to
     `scripts/pilot-note.py` was refused, outside `fs.write`.
  - Authors [checked: `load()`]: `human:session-user`; `…#pilot`; the child's task by
    `…#pilot`; the child's replies by `subagent:gpt-5.5@claude-codex-harness#pilot.1`;
    the exec output by the harness. Tokens: pilot 157,470; pilot.1 62,130. No
    unreviewed calls; no email or keys in the file.
  - `~/.codex`: four `tmp/arg0/codex-arg0*` folders were left behind, one per Codex
    process started (the catalogue read and each agent's app-server). The rest is the
    desktop app's state and two of its session rollouts, while running.

## Findings
1. The pilot put a `Bar:` line in the brief it wrote for its subagent, unprompted.
   Onboarding v1.12's convention carried over through the ledger.
2. Minimal bounds plus the fixed grants (onboarding, own instructions) are enough for a
   child to do a useful task, and the pilot noticed and reported the grants.
3. One Codex process per agent leaves one `tmp/arg0` folder per process in the Codex
   home [working: Codex cleans stale ones at a later start]. Worth watching as
   subagent use grows.
4. Streamed deltas still dominate the ledger (task-4 open question): 1,226 of 1,376
   entries are raw protocol records, mostly deltas.

DISCREPANCY: ~/.codex/tmp/arg0 | expected: Codex removes its arg0 temp folder when it
exits (earlier runs showed none left) | found: four left after the task-5 live
conversation (codex_home_changes, 20260925T192816Z) | 2026-09-25

## Proposed bound notation (approved by the founder)
Five keys, each a list of prefixes. `"*"` means everything and `[]` means nothing.
```
fs.read       paths relative to nimoi/, e.g. "origins/", "bootstrap-ledger/"
fs.write      e.g. "bootstrap-harness/claude-codex-harness/task-5-subagent/workspace/"
fs.exec       e.g. "…/task-5-subagent/scripts/hello_safe.py"
ledger.read   entry-name prefixes, e.g. "*", "pilot/"
ledger.write  e.g. "pilot/"
```
- A prefix ending in `/` covers everything under it; otherwise it names one file or
  one entry name.
- **Subset:** every child prefix lies inside some parent prefix.
- **Invariant, for every agent:** no fs.write prefix overlaps any fs.exec prefix.
- **Never grantable:** harness-owned ledger names (`harness/`, `transcript/`, script
  output) and agent writes to the scripts folder.
- The same JSON appears in the parent's instructions, the child's instructions, the
  subagent tool's arguments and the ledger records.

## Revision after the peer review (2026-09-25)

The founder asked for a review of the peer swimlanes (in chat, 2026-09-25), then:
"proceed with proposed improvements". Three questions, answered the same day:
1. Streamed fragments: "Don't record the fragments, but do record metadata from the
   fragments as a separate ledger entry from the message".
2. Labels: "One label each (Recommended)".
3. Authorship (I had proposed that agents revise only their own entries): "There is no
   revision to actual ledger entries, ever. The ledger is append-only. Agents may use
   name supersession for any name they are allowed to write. [...] One author can also
   supersede another's entry in collaborative efforts, but the ledger maintains the
   multiple-author history and versioning. Actual rules for this are by
   convention/doctrine to be developed, not harness enforced beyond write prefix / tag
   restrictions". So no ownership rule; "supersede", not "revise", from here on.

**Bar:** the review's gaps closed, or left with a reason, each with a regression test,
and the harness still passes its own checks live. Prototype, as before.

### Decisions (derived from the answers; recorded before building)
- A subagent's instructions need not be the parent's own entry (authorship is doctrine,
  per answer 3). Its task message is recorded as a link to the exact version pinned at
  spawn, with that version's author and `instructed_by` = the parent, never a copy.
- Agents write under `agent/`: the root's `ledger.write` (an allow-list by bounds; the
  protected names and labels stay as a second guard).
- A `Bar:` line in subagent instructions is asked for in the prompt, not enforced.
- Record format 2: one label per harness-written entry (`log.<kind>`,
  `transcript.<role>`, `exec`); the `harness` label is no longer written or protected;
  fragments are summarised in one `message_stream` record per message.

### Changes
- **Bounds and fixed rules.** "Entries", not prefixes. `ledger.write` grants reading what
  it covers. `fs.write` may not reach into the folder of any `fs.exec` entry (rule 5d says
  directory). Ledger files, leases and `.runtime` are never fs-readable (the raw-ledger
  bypass, confirmed before the fix by a stub run). Paths are checked after resolving
  (short names, case), and names Windows rewrites (trailing dot or space, `:`, device
  names) and absolute paths outside nimoi (UNC included) are refused first. The fixed
  rules are stated once, in `prompts/bounds.md`, with each agent's always-readable items.
- **Files and scripts.** `fs_write(id, path, replace_sha256)`: an exact version; create
  only, or replace given the file's current sha256; `fs_write_started` before, `fs_write`
  after. `python_exec`: `python_exec_started` before; `runner.py` runs the script in a
  Windows job object (nothing it starts outlives it; no hang on a held pipe); output line
  ends normalised; its own output is always readable to the agent that ran it.
- **Subagents and Stop.** Stop interrupts the pilot's turn and every unfinished subagent;
  `subagent_wait` ends early when the waiter is being stopped or near its cap. Turns
  end after 300 s without events, or at a cap (pilot 1800 s, subagent 900 s). A model
  call to an unreviewed tool stops the turn.
- **Record.** Fragments are not recorded one by one (`AppServer(unrecorded=...)`); the
  run record carries `record_format`, the scribe's id and source sha256, and the limits.
  Key-like strings are refused in agents' ledger writes, not only redacted.
- **Structure.** `tools.py` split into `toolkit.py` (declare once, offer, dispatch, gate)
  and `ledger_tools.py`, `fs_tools.py`, `subagent_tools.py`; `paths.py`, `runner.py`;
  prompts as Markdown templates; `journal` renamed `record`; `check_ledger.py`, a
  read-only post-run checker; `ui.py` binds its port exclusively before opening the
  ledger; Conversation takes an agent factory, so tests run the tree without Codex.
- Size: non-test Python 2,379 → 3,261 lines [checked: wc]. The largest tools file is 204
  lines (was 471), but the total grew: the checker, runner and path rules are 532 lines
  of new capability. Not a simplification by size.

### Verification
- **50 tests pass** [checked: `python -B -W error::ResourceWarning -m unittest discover`,
  4.9 s], including real Codex with the fake model; every test's ledger is re-loaded
  with no findings; fault injection (a failing disk write) shows no file is written and
  nothing follows; the runner ends a script's leftover process and a timed-out tree.
- **Fake model on the page** (`.runtime/fake-ledgers/…/20260925T214404Z.ledger`):
  delegation, `fs_write` by id, its refused repeat, and a refused raw-ledger read.
  `check_ledger --closed`: clean.
- **Live**, gpt-5.5, `ledgers/claude-codex-subagents/20260925T214637Z.ledger`
  [checked: `check_ledger.py --closed` clean; `scribe.py check` exit 0]:
  1. The pilot delegated `hello_safe.py 6 7` to `pilot.1` with bounds of exactly that
     script in `fs.exec` and nothing else; the child read onboarding and its brief
     through the fixed grants and reported (21 s). The pilot noticed the fixed grants.
  2. A note in `agent/pilot/…`, written to `workspace/review-note.txt` by id (424
     bytes); the repeat was refused with the file's sha256; the raw ledger read was
     refused.
  3. Stop during `subagent_wait`: the wait returned at once and the pilot's turn was
     interrupted, but `pilot.2`, still starting, ran its task (finding 2).
  - 1,200 lines for 3 pilot turns and 2 subagents (the earlier live session: 4,133 lines
    for 2 turns and 1 subagent); 1,693 fragments in 19 `message_stream` records, all
    matching their text. Tokens: pilot 281,605; `pilot.1` 63,667; `pilot.2` 46,437.
  - `~/.codex`: two files changed while running (`logs_2.sqlite-wal`,
    `models_cache.json`); no `tmp/arg0` folder left this time.
- The fixes for findings 1 and 2 are verified offline only [checked: regression tests];
  no live run since.

### Findings
1. The key detector's `sk-` pattern matched inside words: the pilot's entry name
   `agent/pilot/task-5-hello-safe-subagent-instructions` was refused as a key. The same
   pattern redacts harness records, so a wikilink to such a name would have been
   corrupted. Keys must now start a token. The pilot reported the refusal and renamed.
2. Stop reached only agents in a turn; a subagent spawned a moment earlier was still
   starting, and ran its whole task. Stop now reaches every unfinished subagent, and a
   turn stopped before it begins never starts.
3. On Windows, `http.server` servers set SO_REUSEADDR, and a second one binds a port
   another is serving without error [checked: a two-server test]; requests may reach
   either. `ui.py` now binds exclusively. Other lanes' `http.server` pages may share
   this [inferred; not checked].
4. A console script started without a window gets a `conhost.exe` in its job; the
   runner leaves it out of `left_running`.
5. Besides our developer instructions, Codex sent the model one `<environment_context>`
   message and no `AGENTS.md` instructions [checked: fake-model `model_request` inputs,
   offline Codex home; not checked with the live home].
6. Lines fell by about 70%, bytes less (1.02 MB for the live session): large payloads,
   such as the onboarding text, are recorded several times per read (the tool
   response sent, the completed item, the raw output item).

DISCREPANCY: task-5-subagent/ledger_log.py KEY_LIKE | expected: matches only keys |
found: matched "sk-5-hello-safe-subagent-instructions" inside a ledger name (live,
20260925T214637Z) | 2026-09-25

DISCREPANCY: task-5-subagent/conversation.py interrupt() | expected: Stop reaches every
subagent in flight | found: a subagent still starting was not stopped (live,
20260925T214637Z, `pilot.2`) | 2026-09-25

### Left open
- The scribe writes each label as its own line; label lines are 66–79% of every lane's
  task-5 ledger. A labels-with-the-write option is bootstrap-ledger's to decide.
- Large payloads recorded several times (finding 6).
- Races accepted at the prototype bar: a script's bytes are hashed, then run; a process
  a script starts before it joins its job escapes the job.
- A live check of the fixes for findings 1 and 2.
