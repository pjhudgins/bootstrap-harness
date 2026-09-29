# Peer review of task-5 lanes, and what changed here (2026-09-25)

**Founder request:** "review all peer swimlane approaches and identify ideas to improve your solution, simplify, or improve legibility/maintainability". Then: "proceed with proposed changes".

**Method:** three read-only reviewer agents, one each for `gpt-anthropic-harness` (same SDK), `claude-codex-harness` and `gpt-codex-harness`. They ran no peer code and wrote nothing outside this lane. Their full reports were delivered in the session; the conclusions are below. Claims about this lane were checked against its code before being acted on.

## Where the lanes converged
- **All three peers own every file tool.** None uses a CLI's built-ins, so one resolver checks and opens the same path.
- **JSON bounds with no exclusions.** Mine kept text lines with `!` exclusions: they express "everything except X", and the child rule (exclusions inherited) stays simple.
- **Pinned ledger-to-file export** (gpt-anthropic, gpt-codex), and raw `.ledger` files denied to file tools (gpt-anthropic, gpt-codex).
- **Record before side effect, and fail closed on ledger failure** (all three).

## Found in this lane (checked by me)
1. Built-in Glob could list `candidate_repos` through a Windows 8.3 alias (`CANDID~1/**`). The search base was built from the pattern's text, never resolved.
2. Raw `.ledger` files were readable with fs.read, bypassing ledger.read.
3. `log/` records copy tool inputs and outputs, so ledger.read over `log/` exposes everything any agent has read.
4. Spawn didn't check who wrote the instructions.
5. A partial Read (`limit: 1`) opened the onboarding gate.
6. fs write took the current body (not pinned), overwrote without a check, and wrote before recording.
7. A ledger failure didn't stop the harness.

## What changed (all in task-5-subagent)
- **Files.**
  - `files.py`: harness-owned `mcp__fs__list/read/search/write`. The CLI's Read/Glob/Grep are no longer loaded (`tools=[]`).
  - One resolver resolves first (symlinks, junctions, 8.3 names). Then come the fixed denials: secret names, `*.ledger` and `lease.json`, and the scripts dir for writes. Bounds come last.
  - Search prunes excluded directories.
  - Writes are pinned to an entry id, with an `expected_sha256` precondition (null means create-only). `file_write_started` is recorded first: no record, no write.
- **`exec_tool.py`:**
  - `exec_started` is recorded first, with the script's sha256;
  - `-B`, and no console window;
  - output is capped while it streams, with a kill on overflow;
  - the process tree is killed (taskkill /T) on timeout or overflow, or when the pipe is held open.
- **`subagents.py`:**
  - instructions must be written by the parent, and are pinned by id;
  - the pinned body is delivered word for word as the child's first message, recorded as a link to the entry, not a copy;
  - the child's ledger.read gets `!log` unless the parent names a log/ entry;
  - `subagent_start` is recorded before the child starts.
- **`agent.py` (split out of session.py):**
  - check = fail closed, then the allowlist, then the onboarding gate. The gate now needs line 1 to the last line, read contiguously;
  - `tool_inventory_mismatch` and `unexpected_tool_use` records;
  - large tool responses are stored as a digest in post records;
  - a compact context-usage summary.
- **`session.py`:** the conversation worker only. It refuses messages once the ledger has failed (state `ledger_failed`).
- **`ledger_tools.py`:**
  - a pinned `read_line(id)`;
  - the read tool takes `id`;
  - reserved labels are the exact `harness` plus the `log.` and `agent.` prefixes, so `harness-notes` is allowed.
- **`web.py`, `harness.py`, `app.py`** (split from app.py): a Content-Security-Policy and nosniff header, and a POST body is read before any refusal (the Windows reset seen by two peers).
- **`prompts.py`:** the tools section is generated from the agent's actual tools, and there is a fixed-rules section.
- **`audit.py`:** a read-only post-session checker, the idea from gpt-anthropic's `inspect_ledger`.
- **Tests:** `tests/fake_client.py` stands in for the SDK client. It runs the real hooks, permission callback and tool handlers, so offline tests cover turns, spawns, the gate and fail-closed behaviour. There are 49 tests across 6 files, and they include the real `CANDID~1` alias.
- **Removed:** `policy.py`, `fs_exec_tools.py` and the old single test file.

## Live verification (session `20260925T213340Z`, $0.18)
- The session's tools were exactly the 10 harness tools, with no inventory mismatch.
- The raw `.ledger` read and `mcp__fs__list CANDID~1` were refused; the alias resolved to `/candidate_repos`.
- The pinned write was created, and the identical repeat was refused because the file exists.
- `safe_probe` exited 0, with 4 environment keys.
- The Haiku child read onboarding in full (262 lines) and succeeded.
- A spawn with the human's `log/` text entry as instructions was refused by the authorship rule.
- `audit.py --all`: all 5 sessions ok.

## Open (for the founder)
- **Subagent identity across sessions.** Child ids restart per session. The new session's `pilot.1` has the same author string as an earlier session's `pilot.1` and could update its entries (seen live: `pilot/tasks/out/sum` from `195145Z`). Option: put the session stamp into child ids or authors.
- **Exec working directory** is still the workspace, with args allowed: useful, but approved scripts can read agent-written drafts. The peers use cwd = scripts and no args.
- **Not done:** pointing the bundled CLI at a local model stand-in (`ANTHROPIC_BASE_URL`) for tests. The fake client covers the harness side; the CLI side remains live-only.
