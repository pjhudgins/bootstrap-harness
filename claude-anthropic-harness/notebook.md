# claude-anthropic-harness — notebook

Swimlane: Claude lineage, Claude Agent SDK (`claude-agent-sdk`), Claude models only.
Rules: `../rules.md` (authoritative; no git; write only inside this swimlane). Re-read it at the start of each task; it changes.

## Environment (observed 2026-09-23)
- Python 3.13.3 (`C:\Python313`); `claude-agent-sdk` 0.2.101 in user site-packages. Nothing has been installed by this swimlane.
- Claude Code CLI 2.1.251. `ANTHROPIC_API_KEY` is not set; auth uses the CLI login (Claude Max, firstParty). Founder-approved.
- Model pinned to `claude-sonnet-5` (founder decision).

## Isolation recipe for SDK sessions (learned the hard way; see mem/task-2-decisions.md)
`setting_sources=[]` + `strict_mcp_config=True` + an explicit `tools=[...]` + `--no-session-persistence`.
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

### task-4-ledger — built, offline tests pass (22), awaiting founder review of system prompt before first live run
Task 3's UI with the wiki ledger as the only record. `scribe` is imported in place from `bootstrap-ledger/python-scribe`, with bytecode writing off. There is one persistent ledger, `task-4-ledger/ledger/claude-anthropic-harness/`, with a new session per launch. It is tracked in git: `ledger/.gitattributes` sets `*.ledger -text` and `ledger/.gitignore` ignores `lease.json`.
Authors: harness `harness:claude-anthropic-harness/task-4-ledger`; agent `pilot:<model>@claude-anthropic-harness`.
Agent tools: Read/Glob/Grep across nimoi except `candidate_repos/`; add; ledger read/list/write (writes refused under `log/`, on names labelled `harness`, on others' entries, and for protected labels).
**End every run with the UI's End session button** (or Ctrl+C). A killed process leaves `lease.json`, which blocks the next launch until a human clears it.

## Pointers
- `mem/task-1-decisions.md` — task 1 assumptions and founder answers.
- `mem/task-2-decisions.md` — task 2 decisions, findings (connector leak, budget overshoot, Read auto-approval), run index.
- `mem/task-3-decisions.md` — task 3 design, findings (cumulative cost, stale-tab replay, interrupt), verification and gaps.
