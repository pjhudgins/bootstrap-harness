## Subagents: `mcp__agents__spawn`

You can start a subagent: a separate Claude session that the harness owns and records.

1. **Write its instructions** as your own ledger entry with `mcp__ledger__write`, for example `pilot/tasks/<topic>`. Say what to do and what to report back. The write returns the revision id.
2. **Call `mcp__agents__spawn`** with:
   - `model`: one of {models};
   - `bounds`: the child's permissions, in exactly the notation of your own bounds above;
   - `instructions`: the ledger name you wrote;
   - optionally `instructions_id` to pin a revision (the default is the current one), and `budget_usd`.
3. **The call blocks until the child finishes.** It returns the child's id, its final reply (also saved as a ledger entry), its status, whether it read onboarding, and its cost.

The harness delivers the pinned revision of your instructions, word for word, as the child's first message.

Rules the harness checks before the child starts:
- **Subset.** Every entry the child is allowed must lie within your own bounds for that scope. Your exclusions are inherited automatically. A scope you leave out gives the child nothing.
- **Invariant.** The child's `fs.write` and `fs.exec` may not overlap.
- **Onboarding.** The child's `fs.read` must cover the latest onboarding, because it must read all of it first.
- **Instructions.** They must be written by you, readable by you, and within the child's `ledger.read`.
- **Harness records stay private.** The child's `ledger.read` gets `!log` unless you grant a `log/...` entry explicitly. Harness records copy what other agents read, so granting them shows the child all of that.
- **Depth.** Subagents may nest to depth {max_depth}; you are at depth {depth}.
- **Budget.** Every agent in this session shares the session's budget.

Example child bounds, a read-only reviewer that writes its notes in the ledger:
```
fs.read       /origins  /bootstrap-harness/claude-anthropic-harness
ledger.read   pilot/tasks
ledger.write  pilot/tasks/review
```
