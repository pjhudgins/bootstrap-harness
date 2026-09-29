# claude-anthropic-harness — notebook

Swimlane: Claude lineage, Claude Agent SDK (`claude-agent-sdk`), Claude models only.
Rules: `../rules.md` (authoritative; no git; write only inside this swimlane). Re-read it at the start of each task; it changes.

## Environment (updated 2026-09-25)
- Python 3.13.3 (`C:\Python313`).
- **`claude-agent-sdk` 0.2.159** in user site-packages, upgraded from 0.2.101 on 2026-09-25 at the founder's direction ("better to get current early"). Only the SDK changed; `pip check` is clean. Details: `mem/sdk-upgrade.md`.
- **The SDK runs its own bundled CLI** (`claude_agent_sdk/_bundled/claude.exe`), now 2.1.281. Tasks 1–4 ran on bundled 2.1.177, not the PATH `claude` (2.1.251) this notebook used to cite.
- `ANTHROPIC_API_KEY` is not set; auth uses the CLI login (Claude Max, firstParty). Founder-approved. The login was revoked once (2026-09-25) and the founder re-logged in.
- Model pinned to `claude-sonnet-5` (founder decision).

## Isolation recipe for SDK sessions (learned the hard way; see mem/task-2-decisions.md)
`setting_sources=[]` + `strict_mcp_config=True` + `env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}` + `tools=[]` (task 5: no built-ins; every tool is harness-owned, so checks and opens use one resolver) + `--no-session-persistence`.
Auto memory loads regardless of `setting_sources`; it keys on cwd (task 4 finding 6). Still read unconditionally: `~/.claude.json` and managed policy settings (see `ref/claude-code-docs/`).
Without `strict_mcp_config`, the logged-in account's claude.ai connectors (Gmail, Drive, Calendar, Docs) attach silently.

## Tasks
### task-1-hello — obsolete, kept as a record
`task-1-hello/hello.py` prints `Hello world!` (first run 2026-09-23, $0.0048). Founder, 2026-09-23: it records what was learned in that step and will not be patched. It lacks `strict_mcp_config`, so do not copy it as a pattern; use task 2's options instead.

### task-2-tooling — done, pending founder review
`task-2-tooling/driver.py` covers requirements a–e in four scenarios (add, read_add, exec, outside). Modules: `runlog.py`, `policy.py`, `calc_tool.py`. Offline tests: `python -m unittest discover -s task-2-tooling/tests -t task-2-tooling` (11 pass).
Last run: `runs/run-20260923T202108Z.jsonl.log`, all four passed, $0.041.

### task-3-ui — done, pending founder review
`python task-3-ui/app.py` opens http://127.0.0.1:8765: one conversation per launch, chat plus a live event panel, Stop button, $5 cap per launch (`--budget`). Log: `task-3-ui/runs/conv-<UTC>.jsonl.log`.
Modules: `app.py` (web), `session.py` (the agent worker; task 2 options), `events.py` (log-first bus to SSE), `static/` (renderer registry in `app.js`). Offline tests: 12 pass.
Key finding: in multi-turn sessions `total_cost_usd` and `model_usage` are running session totals. Details: `mem/task-3-decisions.md`.

### task-4-ledger — live runs pass on SDK 0.2.159; AutoMem leak fixed; message text logged as its own entries (28 offline tests)
Message text: each text to or from the user is a `log.text` entry, body = the text, authored `human:session-user` or the agent. The harness's `message` entry follows it with `[[link]]`. Open: `userEmail` is injected into the agent's context by the CLI (not yet in any ledger).
Launch: `python task-4-ledger/app.py` → http://127.0.0.1:8766. Findings and ledger state: `mem/task-4-decisions.md`.
Task 3's UI with the wiki ledger as the only record. `scribe` is imported in place from `bootstrap-ledger/python-scribe`, with bytecode writing off. There is one persistent ledger, `task-4-ledger/ledger/claude-anthropic-harness/`, with a new session per launch. It is tracked in git: `ledger/.gitattributes` sets `*.ledger -text` and `ledger/.gitignore` ignores `lease.json`.
Authors: harness `harness:claude-anthropic-harness/task-4-ledger`; agent `pilot:<model>@claude-anthropic-harness`.
Agent tools: Read/Glob/Grep across nimoi except `candidate_repos/`; add; ledger read/list/write (writes refused under `log/`, on names labelled `harness`, on others' entries, and for protected labels).
**End every run with the UI's End session button** (or Ctrl+C). A killed process leaves `lease.json`, which blocks the next launch until a human clears it.

### task-5-subagent — done (2026-09-25; its back-spec is folded into `mem/task-6-spec.md`)
Hardened after the peer review (`mem/peer-review-2026-09-25.md`):
- harness-owned file tools only (no CLI Read/Glob/Grep);
- pinned, recorded-first writes;
- fail closed on ledger failure;
- a stricter spawn (instructions written by the parent and pinned; `!log` by default);
- the whole-file onboarding gate.
Tests: 49, using a fake SDK client (`tests/fake_client.py`). After each live session, run `python task-5-subagent/audit.py`.
Launch: `python task-5-subagent/app.py` → http://127.0.0.1:8767. Top-level bounds in `task-5-subagent/bounds.txt`, in the notation described in `bounds.py`. Own ledger: `claude-anthropic-harness-t5`. Plan, decisions and results: `mem/task-5-plan.md`.

### task-6-hybrid — completed 2026-09-29, awaiting founder review
Bar: a working prototype of a three-layer, two-lineage agent institution; every action and failure recorded; boundaries enforced where the platform allows, stated honestly where not. Not production.

There are three layers:
- **the governor**, `claude-opus-5-5`. It chats with the human, dispatches task owners and resolves their requests. It has no writes, no exec and no subagents.
- **task owners**: Opus, Fable, `gpt-6-astra`, `gpt-6-sol` or `gpt-5.6-sol`. They run in the background and ask the governor through a self-blocking `request`.
- **subagents**: Sonnet or higher, or Terra or higher; only owners spawn them.

GPT agents run on the Codex App Server. Codex's own tools are switched off where possible and detected where not: detection stops the agent.

**Launch:** `python task-6-hybrid/app.py` opens http://127.0.0.1:8768: a chat with the governor, an agent tree, activity, and approval cards. `--pilot` gives task 5's single agent. Every launch that starts Codex records a `codex_home_changes` diff of `~/.codex`. After each launch, run `python audit.py`. End sessions with the End session button (a killed process leaves `lease.json`).

**Scripted runs** author their messages `developer:claude-code-session`, never the human:
- `live_turn.py`: one turn; `--fake-model` gives the surface check against the real `~/.codex` config;
- `live_institution.py`: one three-layer run.

**Tests:** `python -m unittest discover -s tests -t .` from `task-6-hybrid/`: 81, all offline. The Codex ones run the real binary against a fake model, in the gitignored `.runtime/offline-codex-home`. `python tests/ui_demo.py` serves the UI with fake agents.

**Live runs** are all in ledger `claude-anthropic-harness-t6`, and all audit ok:
- phase 1: the GPT surface check and one GPT turn;
- phase 3: the three-layer run, session 20260929T123834Z, $1.59.

**Founder decisions** (2026-09-28/29) are in `mem/task-6-plan.md`, `mem/task-6-phase1.md` and `mem/task-6-record.md`. Among them:
- the governor passes rules, never a bar;
- keep `.runtime`;
- `~/.codex` is used live, with a recorded diff.

**Open for the founder** (details in `mem/task-6-record.md`):
- onboarding's "a worker with no bar stops and asks" versus the governor passing none;
- three discrepancies in `rules.md` recorded by a live owner;
- phase 1's live-turn message misattributed to the human, recorded and not repaired.

### task-7-project — completed 2026-09-29; packaged the same day into `nimoi/harness/prod`, with its projects `nimoi/harness` and `nimoi/doctrine`, and the notebook `nimoi/harness/dev/notebook.md` (details: `mem/task-7-record.md`)
Bar: the task-6 harness, runnable against any configured project folder, one server per project, each with its own record. Not production.

**Launch:** `python task-7-project/launch.py` starts one server per project in `task-7-project/projects.toml`. The default project is `default` on http://127.0.0.1:8769, with folder `task-7-project/projects/default/`, NIMOI onboarding 1.12 and nimoi as the read root. A single project can also run alone: `python app.py --project NAME`.

**Each project has:**
- a folder, which agents write in as the governor directs; nothing outside it can be written;
- `ledger/`, reachable only through the ledger tools; the launcher asks before creating it;
- `scripts/`, which only human-approved promotions fill;
- a fixed onboarding file;
- a read root, which is the notation's `/`.

The governor also writes in the project folder (founder, 2026-09-29).

**After a session:** `python audit.py --project NAME`.

**Tests:** `python -m unittest discover -s tests -t .` from `task-7-project/`: 99, all offline.

**Records:** plan and founder decisions in `mem/task-7-plan.md`; built items, live checks and findings in `mem/task-7-record.md`; back-spec in `mem/task-7-spec.md` (a delta on task 6's).

## Pointers
- `mem/task-7-plan.md`, `mem/task-7-record.md`, `mem/task-7-spec.md` — task 7 (project servers): plan and founder decisions, record, back-spec (a delta on task 6's).
- `mem/task-6-plan.md` — task 6 bar, reading of the rules, architecture, founder decisions (Codex auth, GPT exec, request kinds, governor powers), phases.
- `mem/task-6-phase1.md` — phase 1 (Codex backend): what was built, Codex 0.158 tool-surface findings, live runs, `.runtime` secrets check, decisions to confirm, open questions.
- `mem/task-6-record.md` — phases 2–4: governance, UI, the live three-layer run and its findings, discrepancies.
- `mem/task-6-spec.md` — back-specification of tasks 5 and 6 (R12–R20, D9–D15) on top of `task-4-spec.md`; start here to reproduce the harness.
- `mem/peer-review-2026-09-25.md` — what the three peer lanes do differently, what was found and changed here, open questions.
- `mem/sdk-upgrade.md` — 0.2.101 → 0.2.159: API surface check, tests, live smoke test, what changed.
- `ref/claude-code-docs/` — snapshot of the Claude Code / Agent SDK docs, 2026-09-25 (see its README; docs are newer than the installed SDK).
- `mem/task-1-decisions.md` — task 1 assumptions and founder answers.
- `mem/task-2-decisions.md` — task 2 decisions, findings (connector leak, budget overshoot, Read auto-approval), run index.
- `mem/task-3-decisions.md` — task 3 design, findings (cumulative cost, stale-tab replay, interrupt), verification and gaps.
- `mem/task-4-spec.md` — back-specification of tasks 2–4 as built: tiered requirements (R) and design decisions (D), each with its source; start here to reproduce the harness.
- `mem/task-4-decisions.md` — task 4 decisions, authors and guard rules, live-run findings (port conflict, revoked login, "success" on a 401, shutdown wait), ledger state.
