# task-5-subagent — plan (draft for founder review, 2026-09-25)

**Bar.** Same as task 4: the failures must be informative and their record must survive. The new rule, "cannot write and execute from the same directory", is a hard rule, not a preference. It is checked mechanically for every agent.

**Assumption.** 5a says "tasks 2 and 3", but items c, d and e depend on task 4's ledger. So task 5 builds on task 4, and everything in `mem/task-4-spec.md` carries over. Founder to confirm.

## 1. Bounds notation (5b)

One small notation describes every agent's permissions, whether top-level or subagent. The harness injects it into each agent's system prompt exactly as written. A parent uses the same notation when it spawns a child.

```
fs.read       /  !/candidate_repos/
fs.write      /bootstrap-harness/claude-anthropic-harness/task-5-subagent/workspace/
fs.exec       /bootstrap-harness/claude-anthropic-harness/task-5-subagent/scripts/
ledger.read   *
ledger.write  pilot/
```

- **Five scopes,** one per line: `fs.read`, `fs.write`, `fs.exec`, `ledger.read`, `ledger.write`.
- **Entries** are separated by spaces.
  - A plain entry allows.
  - An entry starting with `!` excludes.
  - A filesystem entry is a path from the nimoi root. `/` means nimoi itself, and a trailing `/` means a directory.
  - A ledger entry is a name prefix, and `*` means every name.
- **One rule for every scope:** a target is permitted if it falls under at least one allow entry and under no exclusion. A missing or empty scope permits nothing.
- **Subset rule for children:**
  - Every child allow entry must fall inside the parent's permission for that scope, or the spawn is refused with the reason.
  - The parent's exclusions are inherited automatically.
- **Invariant, checked for every agent:** no path may be in both `fs.write` and `fs.exec`. The harness refuses any bounds that overlap. This is the core safety rule in 5d.
- **Always on, whatever the bounds say:**
  - Secret-looking file names can't be read or written.
  - Harness entries (under `log/`, or labelled `harness`) can't be written by any agent.
  - No agent can write into `scripts/`. Promoting a script is a human action.
- **The top-level agent's bounds** live in a human-editable file in the task folder, written in this notation.

## 2. New tools

- **`fs.write` → write a ledger entry's body to a file (5c).** The agent names a ledger entry and a target path. A text body is written verbatim; any other JSON body is written as JSON.
  - Refused if the path is outside `fs.write`, or the entry is outside `ledger.read`.
  - Overwriting a file inside the bounds is allowed.
  - The harness records the entry id, the path and a hash, so every file is reproducible from the ledger.
  - Workspace contents are gitignored, because the ledger already holds the record.
- **`fs.exec` → run a Python script (5d).** The script must sit under `fs.exec` and end in `.py`.
  - It runs isolated: `python -I`, a stripped environment with no keys, a timeout, and the workspace as its working folder.
  - The harness writes the output (exit code, stdout, stderr, duration, timeout flag) to a ledger entry it authors, and returns the link to the agent.
  - A safe test script ships in `scripts/`. It prints facts only and touches no files or network.
- **The existing tools continue:** Read/Glob/Grep, now bounded by `fs.read`; ledger read/list, bounded by `ledger.read`; ledger write, bounded by `ledger.write` plus the harness protections; and `add`.

## 3. Subagent tool (5e, 5f)

- **Inputs:** a model from an allowlist of Claude models, the child's bounds in the notation above, and a ledger entry holding its instructions, which the parent writes first.
- **Checks before the child starts:**
  - the bounds are a subset of the parent's;
  - the write/exec invariant holds;
  - the instructions entry exists and is inside the child's `ledger.read`;
  - the child's `fs.read` covers the latest onboarding.
- **The child is a separate SDK session owned by the harness, not an SDK-native subagent (5f).**
  - It gets the same system prompt template, with its own bounds injected.
  - **Onboarding is enforced, not just requested:** until the child has read the latest onboarding, the harness refuses every tool except the reads needed to find it.
  - It's then told to read and carry out its instruction entry.
- **Identity:**
  - The agent ids form a tree: `pilot`, then `pilot.1`, `pilot.2` for its children.
  - Ledger authors follow it: `pilot.1:<model>@claude-anthropic-harness`.
  - Each record carries its agent id and a label `agent.<id>`.
- **Record:** the child's messages, tool calls and usage go to the same ledger session, in the same form as the parent's.
- **Cost:** children share the launch's $5 cap, and a child's spending counts toward it.
- **UI:** the chat shows the child's text as indented, labelled bubbles, and the side panel shows each record's agent id.

## Founder decisions (2026-09-25)
- **Bounds:** the line notation as above.
- **Subagents block:** the parent's spawn call waits for the child to finish.
- **Nesting is allowed, with a depth limit.** Mine: 2 below the top-level agent (`pilot` → `pilot.1` → `pilot.1.1`), configurable. The subset rule applies at each level.
- **Ledger:** a new one in the task-5 folder, named `claude-anthropic-harness-t5`.
- **Building on task 4:** stated in the plan and not objected to.

## Refinements made while building phase 1
- **An entry covers itself and everything below it.** Coverage goes by `/`-separated segments, so `pilot` covers `pilot/x` but not `pilot2`. A trailing `/` is optional, and filesystem comparison ignores case, as Windows does.
- **Searches:** the search base plus the pattern's literal leading segments must be permitted. If the pattern can descend, meaning a `**` or a separator after a wildcard, no exclusion may lie below that base.
- **Tools follow scopes:** a tool is loaded only if its scope is non-empty. An agent with no `fs.exec` doesn't see the exec tool.
- **The scripts folder is never writable** by any agent, whatever the bounds say. This is a second guard next to the write/exec invariant, and it also stops a write-only child from placing scripts for a parent to run.

## Phase 1 results (2026-09-25): done, paused for founder review
- **Built:**
  - `bounds.py`: the notation, its one rule, the child subset rule with inherited exclusions, and the write/exec invariant.
  - `policy.py`, bounds-driven: tools follow scopes; searches must be permitted and may not reach an exclusion; scripts are never writable.
  - `fs_exec_tools.py`: `mcp__fs__write` and `mcp__exec__python`.
  - Ledger tools with `ledger.read` and `ledger.write` bounds, and a reserved `agent.<id>` label.
  - `bounds.txt`, `scripts/safe_probe.py`, and `workspace/` (gitignored).
  - A new ledger, `claude-anthropic-harness-t5`, on port 8767.
- **Offline tests:** 25 pass, including a real isolated run of `safe_probe` in a temporary tree.
- **Fixed while building:** notation paths (`/work/x`) given to tools were read as Windows drive-root paths. `fs_target` now treats a leading `/` as the nimoi root unless the path has a drive letter or is already under the root.
- **Live run:** session `20260925T192147Z`, $0.094, one turn, 8 SDK turns.
  - Run `safe_probe.py one two` → exit 0 in 54 ms: isolated, 4 environment keys, cwd = workspace. The output went to a harness `exec` entry.
  - Draft entry → `workspace/hello_draft.py` → a `file_write` entry.
  - Running the workspace copy → refused (outside `fs.exec`).
  - Writing into `scripts/` → refused (no agent may write there).
  - Disk confirms: `scripts/` holds only `safe_probe.py`.
  - `memoryFiles: []`; no unexpected MCP servers; the ledger closed cleanly.
- **Pilot observation:** its refusals weren't visible as distinct ledger records. They are recorded only as tool results, and the pre-call hook logs "allowed" because it only checks the allowlist. Candidate fix: a `tool_denied` record from the handlers, so refusals are first-class in the ledger.

## Phase 2 results (2026-09-25): done, paused for founder review
- **Built:**
  - `subagents.py`: `Spawner` and `mcp__agents__spawn`. It checks, in order, the model allowlist, the depth limit (2), subset plus invariant, fs.read covering the latest onboarding, instructions readable by both parent and child, and the budget. It then runs one child turn, blocking, one spawn at a time per parent.
  - `session.py` was restructured into `HarnessEnv`, `BudgetPool` (one budget, running totals per agent), `AgentCore` (every agent) and `AgentSession` (top-level worker).
  - `prompts/`: `top.md`, `subagent.md`, and the shared `toolkit.md` and `spawn.md`, so the bounds block reads the same for parent and child.
  - **Onboarding gate:** a child can use no tool until a successful Read of the latest onboarding.
  - **`tool_denied` records** for refusals made inside tool handlers, fixing the phase-1 pilot observation.
  - Every record carries its agent and turn explicitly, with label `agent.<id>`, because agents can be active at the same time. There is no shared logger context.
- **Offline tests:** 35.
- **Found live, fixed:**
  1. **Startup crash.** The status snapshot's `turn` collided with `pub()`'s default (`TypeError`), and the error path hit it again. The ledger still recorded `session_error` and `server_failed` (session `20260925T195017Z`). `pub()` now lets fields override the defaults, and a test was added.
  2. **Policy bypass (security).** For the CLI's built-in Read, Glob and Grep, the policy resolved `/x` or `\x` as nimoi-relative notation, but the CLI resolves it as the drive root. So `Read \Windows\win.ini` with `fs.read /` would have been checked as nimoi/Windows but would have opened `C:\Windows\win.ini`.
     - Found because a child's `Read \origins\onboarding_1.12.md` passed the check and then failed in the CLI.
     - **Fix:** built-in tool paths are resolved as the CLI resolves them (`fs_target(native=True)`), and the gate does the same. Notation paths remain only for the harness's own tools, which open exactly what they checked.
     - The kickoff and the subagent prompt had told the child to "Read /origins/…". They now give the full path, and the toolkit explains the difference. Tests were added.
     - Tasks 2–4 were not affected; their policies resolved paths as the OS does.
- **Live runs:**
  - Session `20260925T195145Z`, $0.154:
    - a spawn with `fs.write /origins` was refused as not within the parent (`tool_denied`);
    - the Haiku child `pilot.1` got exactly the requested bounds;
    - the gate blocked its Glob before onboarding;
    - it wrote `pilot/tasks/out/sum` = 42.25 as `pilot.1:claude-haiku-4-5-20251001@…`;
    - its reply returned to the parent. The parent's report kept the child's claims separate from its own observations.
  - Session `20260925T195501Z` (after the fix): the child's first Read used the full path and opened the gate; success, $0.033.
  - All sessions closed cleanly, with no findings and no lease.
- **Unexplained:** one of my status polls was cancelled mid-run in session `195145Z`. It didn't recur in the re-check. Possibly a brief event-loop stall while the child started. Not investigated.

## Phases, with a pause after each
1. The bounds notation and its checks, plus the write and exec tools and the safe test script, for the top-level agent only. Offline tests first, then a live check. **Pause.**
2. The subagent tool, the onboarding gate, identity and UI display. Offline tests, then a live check. **Pause.**
3. A back-spec update (`task-5-spec.md`) and the notes.
