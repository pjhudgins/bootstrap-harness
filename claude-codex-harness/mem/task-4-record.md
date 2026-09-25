# Task 4 record: ledger harness

2026-09-24, Claude session (Opus 5.5), claude-codex-harness. rules.md re-read on the
founder's instruction ("re-read rules and proceed to task 4"): tasks 1–3 unchanged;
task 4 new.

**Bar:** task 3's conversation, with its record moved into a wiki ledger written
through `bootstrap-ledger/python-scribe/scribe.py`. The agent can read the ledger
and the nimoi tree and write only its own ledger entries. Good enough that what
breaks is informative and the ledger keeps the record of the break. A defect that
corrupts the ledger is worse than one that fails. Prototype.

## Sources read
`bootstrap-ledger/notebook.md`, `python-scribe/interfaces.md`, `spec_v0.3.md`
(§1–4, §8–9), `standard/wiki_ledger_v0.4.md`. Key constraints:
- A ledger is a directory. Each scribe session writes one new `<stamp>.ledger`
  file. The spec expects "one session per harness run".
- Every write names its author; the harness attests it, never the model (W§5.6).
- Names are ASCII segments separated by `/`. Labels are ASCII. Names starting
  `_ledger/` and labels starting `_` are reserved.
- The root is supplied by the caller, is never created, and must be absolute.
  Committed ledgers need `*.ledger -text` in `.gitattributes` and `lease.json`
  ignored.
- Import in place: harnesses import `scribe.py` and never modify it. Importing
  would write `__pycache__` into bootstrap-ledger (outside this lane), so the
  harness sets `sys.dont_write_bytecode` before the import.

## Founder answers, 2026-09-24 (in chat, to my questions)
1. Ledger shape: **"One ledger, new file per chat"**: one ledger for this harness;
   each chat is a new session file; the pilot can read earlier chats' entries.
2. Prompt: **"Add to Codex's own prompt"**: thread `developerInstructions`, keeping
   Codex's base prompt.
3. Root: **"Inside task-4-ledger/"**: `task-4-ledger/ledgers/`, with
   `.gitattributes` (`*.ledger -text`) and `lease.json` ignored, both inside this
   swimlane.

## Plan (before founder answers)
- Build on task 3 (UI, conversation core, policy v2, add tool, fake model). The
  JSONL journal is replaced by ledger entries authored by the harness, each tagged
  `harness` plus a label for its log kind.
- Agent tools: `ledger_read`, `ledger_list`, `ledger_write` (and tag/untag within
  its own namespace); `fs_list`, `fs_read`, bounded to nimoi, read-only, with
  secret files refused and key-like strings redacted.
- Write protection: the agent may write only names under its own prefix. It may
  never touch a name under `harness/` or one carrying the `harness` tag, and may
  never add or remove protected labels.
- System prompt (test pilot; NIMOI agent; read the latest onboarding) as described
  in rules.md 4e–4f.

**Deviation from this plan:** the agent may write *any* name that is not protected, not
only names under its own prefix. Protection is what the task asks for ("harness
entries cannot be overwritten"), and attribution already comes from the attested
author and the `pilot` tag.

## Built
`ledger_log.py` (LedgerJournal: a drop-in for task 3's journal), `tools.py`
(Toolbox: add, 4 ledger tools, 2 filesystem tools), `prompt.py`, and task 3's
`conversation.py`, `ui.py`, `static/` and policy, adapted. The JSONL Journal was
removed from the copied `codex_client.py`, so nothing can log outside the ledger.
In this swimlane only: `.gitattributes` (`*.ledger -text`) and `lease.json` in
`.gitignore`. See `../task-4-ledger/README.md` for the point-by-point mapping.

## Verification, 2026-09-24
- **12 tests pass** [checked: `python -W error::ResourceWarning -m unittest
  discover`, three suites rerun after the last change: task 2: 13, task 3: 7,
  task 4: 12]:
  - ledger journal (authors, tags, trailer, lease refusal, bad-body fallback, a new
    session file per chat);
  - ledger tools (attested author; undeclared `author` argument refused; prev
    discipline; 10 protection cases, all refused with nothing written);
  - filesystem tools (bounds, `..`, absolute paths outside, `.git`, secret names,
    binary files, key redaction, the real `origins/` listing);
  - server safety;
  - real Codex + fake model end to end (ledger write, forged `harness/` write
    refused, fs_list, add; ledger checked afterwards).
- No `__pycache__` written into bootstrap-ledger [checked: its two `.pyc` files date
  from 19:58–19:59 UTC, 25 min before this task's first file (20:24:37); they are
  the bootstrap-ledger session's].
- **Browser, fake model**, scratch ledger `task-4-ledger/.runtime/fake-ledgers`: a
  pilot `ledger_write` was recorded as `20260924T202902Z:119` with author
  `test-pilot:gpt-5.5@claude-codex-harness` and labels `pilot`, `ui`; tagging it
  `harness` was refused. `scribe.py check`: no findings.
- **Browser, live**, gpt-5.5, the pilot's real ledger
  `ledgers/claude-codex-pilot/20260924T203001Z.ledger` [checked: `scribe.py
  check` exit 0; `load()`: `head_closed` true, no findings]:
  1. "Introduce yourself and describe your harness…" → the pilot ran
     `fs_list("origins")`, picked `onboarding_1.12.md` as the latest, read it with
     `fs_read`, and gave a long report with observed and inferred kept apart.
  2. "Record a concise summary… in your ledger" → first attempt used the label
     `harness`: refused; retried with other labels:
     `pilot/task4_observations_gpt-5.5_2026-09-24` = `20260924T203001Z:3188`,
     author `test-pilot:gpt-5.5@claude-codex-harness`, labels `observations`,
     `pilot`, `task4`.
  3. Directed test: update the first harness entry, citing its current id → the
     pilot listed, read `harness/20260924T203001Z/000001.run`, and made one attempt
     with `prev` `20260924T203001Z:2`: refused. It reported it and tried nothing
     else.
  - Summary: 3 turns, 7 Python tool calls, model calls all reviewed, 175,421
    tokens (142,080 cached). No email, bearer or `sk-` key in the file [checked:
    regex over the file].
  - `~/.codex`: 17 files changed during the run [checked: codex_home_changes].
    Most are the desktop app's state (`.codex-global-state.json`,
    `thread_history_1.sqlite`, the sandbox log, the rollout of thread
    `01a0cff6…`). This harness's SQLite is redirected to `.runtime/`, and the
    desktop session was active in parallel. Attribution is by time only.

## Findings
1. **Streamed deltas dominate the ledger.** 1,279 of the 1,517 harness entries in
   the live session are `item/agentMessage/delta` records. Each costs 3 lines (body
   and two tags), so about 84% of 4,557 lines and most of 1.4 MB for three turns
   [checked: counts from `load()`]. The completed item repeats the full text.
   Options are for the founder (below).
2. **The protected label `harness` collides with ordinary use.** A pilot writing
   about the harness reached for it as a topic label. The refusal worked and the
   pilot recovered, but a more specific protection label would avoid the snag.
3. **The fake-model capture is not the whole live surface.** The live pilot reported
   image generation and "a multi-tool wrapper" among its tools. Neither appeared in
   any fake-model capture for gpt-5.5. [working: image generation is added only for
   the OpenAI provider; the wrapper is the platform's parallel-call wrapper, not a
   Codex tool.] The live `model_calls_reviewed` check would still flag any call to
   either, since neither is on the reviewed list.
4. The thread's reasoning effort was `high` in this run, `medium` in every earlier
   run, with no change on this side [working: a change in the founder's Codex
   config].
5. Onboarding v1.12 appeared during this session (the pilot found it). Its new
   conventions were applied from then on: a `Bar:` line in the pilot prompt, claim
   labels here, DISCREPANCY lines, state words in the notebook.
6. `scribe.py` was modified at 19:59 UTC today, after bootstrap-ledger's notebook
   was written. The module is imported in place, so it can change under this
   harness. The tests exercise the version present at 20:27.

## Git objects in bootstrap-harness (escalated to the founder, not fixed)
Read-only inspection of `bootstrap-harness/.git` (file listing plus object headers;
no git command). Refs unchanged since 2026-09-23 20:36; no `refs/codex`. Three loose
objects had their modification time refreshed today, each created at 2026-09-23
20:36:23–43 during the founder's commit: blob 11 bytes and tree 39 bytes at 16:01:14
and 16:01:17, and the empty blob `e69de29…` at 20:33:21 [checked: `os.stat`, zlib
header]. A refresh means something wrote an object that already existed (a git
write operation). No process of this harness was running at either time: the task-2
preflight exited 16:00:59 and the demo spawned 16:01:29; task 4's app-server exited
20:33:02.095 [checked: journal and ledger timestamps]. Its many other runs today
refreshed nothing. [working: the writer is the Codex desktop session active at both
times, through its internal git snapshots.]

## Discrepancies
DISCREPANCY: task-4-ledger fake-model capture | expected: the offered tool surface
equals the live one (task-2 method) | found: the live pilot reports image generation
and a multi-tool wrapper absent from the capture (its own report, 20260924T203001Z) |
2026-09-24
DISCREPANCY: bootstrap-harness/.git/objects | expected: no git write operations while
the human owns git | found: three objects re-written at 16:01:14, 16:01:17 and
20:33:21 UTC by an unidentified process (os.stat; see above) | 2026-09-24
DISCREPANCY: origins/onboarding_1.12.md | expected: v1.11 latest (read at session
start) | found: v1.12 created mid-session (the pilot's fs_list) | 2026-09-24

## Change 1: message texts as entries by their writers (founder, 2026-09-25)
Founder, in chat, verbatim (a task instruction, not a standing directive):
> adjust harness logging so that it logs message text to and from the user with the
> text as the body. The author should reflect the agent that wrote the text or the human
> session user (no need to identify, just log as human user of session for now.).
> Perhaps write the text to the ledger first, then write a following "message" entry
> with the harness as author, and substitute the text with a wikilink to the text entry

Implemented (`ledger_log.LedgerJournal.message`), under one lock, so the entries are
adjacent:
1. `transcript/<session>/<nnnn>-<role>` (role `user` or `agent`). The body is the text.
   The author is `human:session-user` (`ledger_log.HUMAN_AUTHOR`) or the pilot's
   `test-pilot:<model>@claude-codex-harness`. The harness tags it `transcript` and
   `transcript.<role>`.
2. A harness record `harness/<session>/<seq>.message` with `text: "[[transcript/…]]"`,
   plus `text_id` (the exact id: a name can be superseded, an id cannot),
   `text_author`, `turn`, and for agent texts `item_id` and `phase`.

Decisions and assumptions:
- The human's text is recorded when the harness sends it, just before `turn/start`.
  Codex's `userMessage` echo is no longer a `message` record; it stays in the raw
  `recv` record. Agent texts are recorded as each `agentMessage` item completes,
  commentary included.
- The raw protocol records (`send` turn/start, `recv` deltas and items, raw model
  items) still carry the same text. They are task 2a's record of the protocol, and
  were left as they were.
- The pilot's tools refuse `transcript/` names and the `transcript`/`transcript.*`
  labels, so even its own words cannot be rewritten after the fact.
  `ledger_list` shows transcript entries by default, including earlier chats'.
- Every string the harness records, message texts included, passes a key-like
  redaction (`ledger_log.scrub`), as `fs_read` output did. Human text is free text:
  a pasted key must not land in a committed ledger (rules.md, Secrets).
- `Conversation(runtime_dir=…)`: tests now use their own Codex SQLite folder, so they
  can run while a live conversation is open.

Verified: 13 tests pass [checked: unittest, 2026-09-25]. New: message entries (authors,
bodies, labels, links, `text_id`, adjacency, key redaction); transcript protection
(5 more refusal cases); the end-to-end run's 4 user texts by `human:session-user`
and 4 agent texts by the pilot, each linked from a harness `message` record.
The live session opened on 2026-09-25 at 13:27:45Z was started before this change,
so its `message` records use the old form.

## 2026-09-25: live session stopped without End
The live UI (session `20260925T132745Z`, launched on the founder's request "run it live
so that I can interact with it") was stopped from Claude Code's task list, which kills
the process. No turn had been taken; the file ends at 13:27:48 after the startup
records. Left as designed: `lease.json` held by pid 30160 (dead) on host paul25, and a
session with no trailer [checked: `Get-Process` finds no pid 30160; `scribe.py check`
exit 1, single finding `unclosed 20260925T132745Z.ledger`]. Not cleared by this
session: clearing a lease is a human's call (interfaces.md). Launches are refused
`lease_held` until then.
DISCREPANCY: task-4-ledger/ledgers/claude-codex-pilot | expected: every chat session
closed with a trailer (End) | found: 20260925T132745Z unclosed, lease left, process
killed from the task list (scribe check) | 2026-09-25

## Open questions for the founder
1. Deltas in the ledger: keep them as they are; coalesce the deltas of each message
   into one entry, which is lossless but loses the per-delta time; or leave them out,
   since the completed message carries the text.
2. The protection label: keep `harness`, or use something the pilot won't reach for
   (e.g. `harness-record`)?
3. Whether the ledger sessions (1.4 MB for three turns) are committed as they are.
