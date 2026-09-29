# claude-codex-harness — notebook

Swimlane: Claude lineage building the codex architecture ("Codex App Server called
from python script"). Rules: `../rules.md` is authoritative: no git commands, no
writes outside this folder without founder authorization, keys only in env or
`.env`. Re-read it at the start of every task, because it changes. Institution-wide
conventions (a `Bar:` line, claim labels, `DISCREPANCY:` lines, state words) come
from the latest `origins/onboarding_*.md`, v1.12 as of 2026-09-24.

## Environment (observed 2026-09-23/24)
- Python 3.13.3 (`C:\Python313`). Nothing installed by this swimlane.
- `codex` is not on PATH. Use the Codex desktop app's extracted copy,
  `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe` (`codex-cli
  0.155.0-alpha.9.2`). The hash folder changes on app updates; the drivers find it.
- Saved Codex login exists in `~/.codex`. No `OPENAI_API_KEY` or `CODEX_API_KEY`.
- Any codex run writes into its CODEX_HOME. For contained work, point CODEX_HOME at
  `.runtime/offline-codex-home` (gitignored, like all of `.runtime/`). App-server
  startup there downloads a plugin marketplace: now 32 MB, with paths over 260
  characters. See `mem/task-1-record.md`.
- Protocol reference for this exact version: `ref/app-server-0.155.0-alpha.9.2/ts/`
  (stable) and `ts-experimental/` (dynamic tools, `environments`).
- The founder's Codex config has an MCP server `node_repl` (JavaScript REPL) that
  any default Codex thread gets. A Codex desktop session is often active in
  parallel: it writes to `~/.codex` during runs, and git objects in
  `bootstrap-harness/.git` were re-written at times when no harness of this lane was
  running (`mem/task-4-record.md`). Attribute changes by time.
- To test a UI in the Claude browser pane: start `ui.py --no-browser --port N` in a
  background shell, then `preview_start` with its URL. `preview_start` by name reads
  the project-root `nimoi/.claude/launch.json` (outside this lane).
- On Windows, a Python `http.server` binds a port another one is already serving,
  silently (SO_REUSEADDR). Task 5's page binds exclusively; tasks 3–4's pages do not,
  so give them a port nothing else uses (`mem/task-5-record.md`, finding 3).

## Founder authorization (2026-09-23, standing)
Live driver runs against `~/.codex` are authorized for every task in this lane.
Condition: record each run's `~/.codex` changes, as a before/after diff of names
and mtimes. This does not authorize bypassing the shell sandbox. Drivers from task 2
on record the diff themselves (`codex_home_changes`, marked by run window).
2026-09-28, standing: live Claude Agent SDK runs (the bundled Claude CLI on the
founder's Claude login, writing under `~/.claude`), with each run's `~/.claude` changes
recorded the same way (founder: "Yes, standing for this lane"; `mem/task-6-plan.md`).

## Tasks
### task-1-hello: completed, pending founder review
Bar: one real agent reply through Codex App Server, printed; failures informative.
`task-1-hello/hello.py`. Live 2026-09-23: `Hello world.`, exit 0. Details:
`mem/task-1-record.md`.

### task-2-tooling: completed (policy v2), pending founder review
Bar: a–e demonstrated live, with evidence at the strength observed; never a
security claim. `task-2-tooling/driver.py`; 13 tests. Key finding: the model
catalogue's `tool_mode` decides the tool surface. `gpt-6-astra` always has a
JavaScript `exec` tool and reaches dynamic tools only through it, so policy v2 pins
`gpt-5.5` (the founder's to overturn). The first demo's claim was wrong and is
corrected in `mem/task-2-record.md`.

### task-3-ui: completed, pending founder review
Bar: a person can hold one conversation per launch in a browser, with task 2's
record. `task-3-ui/ui.py`; 7 tests; verified with the fake model and live.
`mem/task-3-record.md`.

### task-4-ledger: completed at the bar, pending founder review; 3 questions open
Bar: task 3 with its record in a wiki ledger; the pilot reads the ledger and nimoi,
and writes only its own ledger entries; a corrupted ledger is worse than a failure.
`task-4-ledger/ui.py` (README maps rules 4a–4f). Ledger:
`task-4-ledger/ledgers/claude-codex-pilot/`, one session file per chat; harness
`harness:claude-codex-harness/task-4-ledger`, pilot
`test-pilot:<model>@claude-codex-harness`. 12 tests. Live 2026-09-24: the pilot read
onboarding v1.12, reported its environment, wrote its observations, and its
directed attempt to overwrite a harness entry was refused. 2026-09-25 (founder):
message texts are now `transcript/` entries by their writers (`human:session-user` or
the pilot), linked from harness `message` records; 13 tests. Open for the founder:
streamed deltas are about 84% of the ledger; the `harness` label collides with
topic use; whether ledger sessions are committed as they are.
`mem/task-4-record.md`.

### task-5-subagent: completed at the bar, revised after peer review; pending founder review
Bar: a parent can delegate to a harness-owned subagent whose permissions are provably a
subset of its own. Agents draft files and run only vetted scripts, never both in one
place, and all of it is in the ledger. A leaking permission is worse than a refusal.
`task-5-subagent/ui.py` (README maps rules 5a–5f and lists the record kinds). Bounds:
five keys of entries (`bounds.py`), the same notation for every agent; the fixed rules
are stated once, in `prompts/bounds.md`. One app-server per agent; subagents run in
parallel with `subagent_wait` (founder: "Parallel, with wait"; "Nested, capped" at
depth 2). Onboarding is enforced by a tool gate. `fs_write` = an exact ledger entry
version into `workspace/`, create or replace-by-hash; `python_exec` = `scripts/*.py`
only (human promotion), in a job object. Agents write the ledger under `agent/`. Own
ledger: `task-5-subagent/ledgers/claude-codex-subagents/`; `check_ledger.py` checks a
session after a run.
2026-09-25 revision (founder: "proceed with proposed improvements"): the peer review's
gaps closed; record format 2 (one label per entry, fragments summarised per message);
supersession by anyone within bounds (founder: authorship is doctrine, not harness
rules); Stop reaches every subagent. 50 tests. Live: delegation, write by id, refusals
and Stop; checker clean. Two live findings are fixed but verified offline only (the key
detector matched "task-…"; Stop missed a subagent still starting). `mem/task-5-record.md`.

### task-6-hybrid: active — phase 1 (offline) built, paused before live runs
Bar: a person chats with an Opus governor that dispatches task owners (Claude or GPT) on
tickets, sees every agent's activity, and approves what exceeds the governor's bounds;
task owners delegate under task 5's rules and ask the governor, blocking, for what they
may not do. A leaking permission, or an action taken without the approval it needed, is
worse than a refusal.
`task-6-hybrid/ui.py` (README maps rules 5a-g, 6a-f). Governor: claude-opus-5-5 on the
Claude Agent SDK (new here; drawn from the two Anthropic lanes); task owners and
subagents on either engine, models by layer (founder, 2026-09-28). Requests: the
governor grants up to its own bounds, the human approves the rest on the page. Rule 5g:
code-mode GPT `exec` allowed and recorded; Codex's own sub-agent tools cannot be
removed and stop the turn. Offline stand-ins for both engines (`--fake-model`). 55
tests. Next: live runs (standing authorizations for `~/.codex` and `~/.claude`).
`mem/task-6-plan.md`.

## Pointers
- `mem/task-1-record.md` … `mem/task-4-record.md`: each task's bar, assumptions,
  founder answers, decisions, verification, findings and DISCREPANCY lines.
- `mem/task-4-spec.md`: a back-specification of task 4 (tiered requirements and design
  decisions, with sources), for reproducing it elsewhere.
- `mem/task-5-record.md`: task 5 bar, founder answers, design, verification, findings.
- Ledger lease left by a killed process on 2026-09-25 was cleared at the founder's
  direction the same day; session `20260925T132745Z` stays unclosed, as a finding.
  The harness is not running. See `mem/task-4-record.md`.
- Cross-lane: `../gpt-codex-harness/` did tasks 1 and 2 on the same architecture.
  Its `mem/task-2-record.md` has the `code_mode_host` finding; its task-2
  restriction claim probably has the same `exec` gap (`mem/task-2-record.md` here).
- bootstrap-ledger: `../../bootstrap-ledger/notebook.md` and
  `python-scribe/interfaces.md`, for the scribe this lane imports in place.
