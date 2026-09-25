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

## Founder authorization (2026-09-23, standing)
Live driver runs against `~/.codex` are authorized for every task in this lane.
Condition: record each run's `~/.codex` changes, as a before/after diff of names
and mtimes. This does not authorize bypassing the shell sandbox. Drivers from task 2
on record the diff themselves (`codex_home_changes`, marked by run window).

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

## Pointers
- `mem/task-1-record.md` … `mem/task-4-record.md`: each task's bar, assumptions,
  founder answers, decisions, verification, findings and DISCREPANCY lines.
- `mem/task-4-spec.md`: a back-specification of task 4 (tiered requirements and design
  decisions, with sources), for reproducing it elsewhere.
- **Ledger lease held since 2026-09-25 13:27Z** (process killed, not ended). A human
  clears it; see `mem/task-4-record.md`.
- Cross-lane: `../gpt-codex-harness/` did tasks 1 and 2 on the same architecture.
  Its `mem/task-2-record.md` has the `code_mode_host` finding; its task-2
  restriction claim probably has the same `exec` gap (`mem/task-2-record.md` here).
- bootstrap-ledger: `../../bootstrap-ledger/notebook.md` and
  `python-scribe/interfaces.md`, for the scribe this lane imports in place.
