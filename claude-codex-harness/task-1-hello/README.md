# task-1-hello (claude-codex-harness)

Prompts one Codex agent to say hello world through Codex App Server, driven from
Python; prints the agent's reply; exits. Python 3.10+ standard library only: no
packages, no API key.

```powershell
python hello.py             # live: uses the saved Codex login in ~/.codex
python hello.py --verbose   # also echo raw protocol messages to stderr (emails masked)
python hello.py --codex-home ..\.runtime\offline-codex-home   # contained check; turn fails with 401
```

stdout carries only the reply. Diagnostics go to stderr. Any failure exits 1.

Flow: find `codex` (`--codex`, then PATH, then the Codex desktop app's extracted
copy under `%LOCALAPPDATA%\OpenAI\Codex\bin`) → start `codex app-server` on stdio
with its SQLite state redirected to `.runtime/` here → `initialize` →
`account/read` (warns if there is no login) → ephemeral `thread/start` (cwd = this
folder, read-only sandbox, approval policy `untrusted` with approvals routed to
this client, which declines every request) → one `turn/start` → print the final
agent message.

A live run writes outside the swimlane, because Codex keeps its login and temp
files in its own home. The founder granted standing authorization for live runs
in this lane on 2026-09-23, on condition that each run's `~/.codex` changes are
recorded. Runs so far: `../mem/task-1-record.md`. Verified: `Hello world.`, exit
0, 6.7 s.
