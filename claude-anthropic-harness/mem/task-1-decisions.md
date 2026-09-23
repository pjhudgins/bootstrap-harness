# task-1-hello — decisions and assumptions (2026-09-23)

Bar: a minimal, legible harness whose behaviour and provenance are recorded, not a production client.

## Assumptions made (consequential ones flagged)
1. **Isolation from the host (consequential).** `setting_sources=[]` so the agent does not load the NIMOI CLAUDE.md, user memory, hooks, or plugins. Otherwise a "hello world" agent silently inherits NIMOI's onboarding and whatever else is under the cwd — see the NIMOI memory about a harness injecting a candidate repo's CLAUDE.md.
2. **No tools, one turn.** `tools=[]`, `max_turns=1`. The task needs no tools, and having none means the agent cannot touch files.
3. **No session persistence (consequential).** `--no-session-persistence` via `extra_args`. Without it the CLI writes a transcript under `~/.claude/projects/`, which is outside this directory. Tradeoff: that also means no automatic record of the run. Open question below.
4. **Model: CLI default unless `--model` given.** The model that actually answered is printed to stderr, so the record shows it even when it wasn't pinned.
5. **Auth: whatever the CLI already has.** The script does not load `.env`. No key is set now, so the run bills to the logged-in account.
6. **SDK usage: `query()` rather than `ClaudeSDKClient`.** One-shot is all task 1 needs.

## Correction (2026-09-23, found during task 2)
Assumption 1 was incomplete. `setting_sources=[]` and `tools=[]` do not stop the CLI from attaching the logged-in account's claude.ai connectors (Gmail, Drive, Calendar, Docs). The hello agent had them in context. It made no tool call. The fix is `strict_mcp_config=True`; see `task-2-decisions.md` finding 1. Founder decision: task 1 is obsolete, a record of what was learned in that step. It will not be patched.

## Founder answers (2026-09-23)
- Auth: use the CLI login. No `.env` loading.
- Model: pin `claude-sonnet-5`. This supersedes assumption 4; `--model` can still override it.

## Open questions for the founder
- Should the harness keep its own run record inside the swimlane (e.g. `task-1-hello/runs/`), since CLI session persistence is off?
- Tests: a no-network unit test with a fake transport is feasible. Add it now or defer?
