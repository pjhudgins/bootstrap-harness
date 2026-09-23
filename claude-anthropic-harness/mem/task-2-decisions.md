# task-2-tooling — decisions, findings, runs (2026-09-23)

Bar: a prototype that shows each capability working, with a record that shows it. Not a hardened sandbox.

## Founder decisions (2026-09-23)
- Tools: keep read-only built-ins (Read, Glob, Grep) plus `add`; nothing that executes code or writes files.
- Logs: `task-2-tooling/runs/*.jsonl.log`, kept out of git by the existing `*.log` rule.
- Account info: log only `subscriptionType` and `apiProvider`; never email or organization.
- Model: `claude-sonnet-5`, the same pin as task 1.

## Design (see module docstrings for details)
- `runlog.py`: JSONL records, flushed one at a time. SDK dataclasses keep their type name in `_type`.
- `policy.py`: one `ToolPolicy.check()` used by two enforcement points:
  - the `PreToolUse` hook, which runs on every call;
  - `can_use_tool`, which runs only when the CLI asks for permission.
  Read/Glob/Grep are confined to the task folder and refuse secret-looking names; the list mirrors the `.gitignore` secrets block. This matters because the swimlane `.env` sits one level up.
- `calc_tool.py`: `@tool` add runs in-process as `mcp__calc__add`. Numbers only; bools and numeric strings are rejected.
- `driver.py`: four scenarios, each in its own session, all written to one log per invocation.

## Findings
1. **Account connectors leak into SDK sessions (important).** With `setting_sources=[]` and `tools=[...]`, the CLI still attached the logged-in account's claude.ai connectors: Gmail (23 tools), Google Drive, Google Calendar and Claude Docs, about 23.5k tokens. The init message doesn't list them, because they attach in the background, but `get_context_usage()` and `get_mcp_status()` show them. **Fix: `strict_mcp_config=True`.** Verified with a probe that sent no prompt. The driver now logs `mcp_status` for every scenario and warns on unexpected servers. In the first run the hook and `can_use_tool` would still have denied any connector call, and none happened.
   - **Task 1 is affected.** `hello.py` had the same connectors attached and has no deny layer. It made no tool call, but its "isolated" claim was incomplete. Not patched pending founder decision.
2. **`max_budget_usd` is checked after the fact.** The first `add` run cost $0.3067 against a $0.25 cap. The whole answer was produced, then the run was flagged `error_max_budget_usd`. Treat it as a tripwire, not a ceiling.
3. **Claude Code auto-approves Read inside cwd without calling `can_use_tool`.** No `permission` record appears for `Read` in `read_add`. Path policy must therefore sit in the `PreToolUse` hook; `can_use_tool` alone would miss it.
4. **Rate-limit info on a Max subscription:** one `RateLimitEvent` per session (`five_hour`, `allowed`, `resets_at`), with `utilization` null. `ResultMessage.model_usage` gives per-model tokens and cost.
5. In `exec`, the agent had no way to run code. It said so, then computed 6×7 as `add(21, 21)` and said that was what it had done.

## Runs
- `runs/run-20260923T201820Z.jsonl.log`: before the fix. `add` failed at $0.3067 (connectors plus cache creation). The other three passed. Total about $0.38.
- `runs/run-20260923T202108Z.jsonl.log`: after the fix. All four passed, $0.041 total, `unexpected: []` in every scenario.
