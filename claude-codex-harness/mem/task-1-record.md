# Task 1 record: hello through Codex App Server

2026-09-23, Claude session (Opus 5.5), claude-codex-harness.

**Bar:** one real agent reply, obtained through Codex App Server driven from Python,
printed to stdout. Any failure exits nonzero with the server's own error on stderr.
The record says honestly what ran and what it touched. Not a production client.

## Assignment
- Founder: "begin work on gpt-codex-harness/task-1-hello", corrected in the same
  message to `claude-codex-harness`. That matches rules.md: the first element of the
  swimlane name is my lineage (Claude).
- rules.md, task 1: "Create a harness that prompts an agent to say hello world. Print
  the agent's response and exit."

## Founder answers, 2026-09-23 (in chat)
- **Standing authorization for this lane:** live driver runs against `~/.codex` for
  every task in claude-codex-harness, as gpt-codex has. Each run's `~/.codex`
  changes are still recorded. Chosen over "this run only". It does not cover
  bypassing this shell's sandbox; I said I would stop and report instead.
- Assumption 1 below confirmed: one agent, no pydantic reading.

## Assumptions (consequential ones, documented per rules.md)
1. The task's sentence "For pydantic, both gpt and claude agents should say hello
   separately" has no swimlane in the current rules. Read here as not applying to
   the codex architecture: one agent (Codex's configured model) says hello.
   Confirmed by the founder.
2. Auth is the saved Codex login in `~/.codex`. The harness never logs in, never
   reads, copies or moves credentials, and uses no API key.
3. Model: inherit Codex's configured default; not pinned. The model that answered
   is logged on every run.
4. Any run against the real `~/.codex` writes outside the swimlane (see
   Observations), so each live run needs founder authorization. Offline runs point
   CODEX_HOME at `../.runtime/offline-codex-home`, inside the swimlane.
5. No transcript files for task 1. Logging messages is task 2a. `--verbose` echoes
   the raw protocol to stderr instead, with emails masked.

## Decisions
- Standard-library stdio JSON-RPC client in `task-1-hello/hello.py`. It is
  independent of gpt-codex's `hello.py` but uses that lane's verified
  `sqlite_home` redirect and `ephemeral` threads.
- Approval policy `untrusted` rather than gpt-codex's `never`. Any command that is
  not known-safe must ask. `approvalsReviewer: "user"` routes those requests to
  this client, not to an auto-reviewer, and the client declines every one. Any
  tool attempt is therefore refused and shown on stderr. Other server requests get
  a JSON-RPC error; the turn continues either way. This is task guidance, not a
  verified execution boundary; restricting tools is task 2b.
- Print only messages with phase `final_answer`, or every agent message if none
  carries a phase. Commentary goes to stderr, so nothing is silently dropped.
- stdout/stderr are reconfigured to UTF-8. A reply outside the Windows code page
  must not crash the print after a successful turn.
- A missing login is a warning, not a stop. The server's own 401 is the more
  informative failure, and it lets the offline check reach `thread/start` and
  `turn/start`.
- Protocol reference generated from the installed binary into
  `ref/app-server-0.155.0-alpha.9.2/`: `ts/` (725 files, 0.4 MB) and
  `json-schema/` (310 files, 3.5 MB). Whether `ref/` is tracked is the founder's
  call. Regenerate with
  `codex app-server generate-ts --out DIR` (or `generate-json-schema`).

## Observations (environment, 2026-09-23)
- `codex` is not on PATH in this shell. The Codex desktop app (MSIX `OpenAI.Codex`
  26.915.4065.0) extracts `codex.exe` to `%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\`.
  The original under `C:\Program Files\WindowsApps\...` refuses direct launch
  ("Access is denied"). Both files have the same SHA-256. Version `codex-cli
  0.155.0-alpha.9.2`, as in gpt-codex.
- Every codex invocation, including `--version`, writes `CODEX_HOME/tmp/arg0/...`.
- App-server startup in an empty CODEX_HOME wrote `installation_id`, six system
  skills under `skills/.system/`, `.tmp/plugins.sync.lock`, and
  `.tmp/git-<random>/` (`HEAD` = `ref: refs/heads/main`, empty `objects/` and
  `refs/`). That last one is a git repository skeleton, apparently from plugin
  sync. The missing `config`/`hooks` files suggest a library created it rather than
  the git CLI; that is an inference, not verified. All of it was inside the
  swimlane. I issued no git commands.
- The 16.8 s offline run also left `.tmp/plugins-clone-<random>/` in the offline
  home: a plugin marketplace tree (`.agents/`, `plugins/`, `README.md`), 3,134
  files, 32.4 MB, with paths up to 301 characters. That is past the Windows
  MAX_PATH limit, and PowerShell 5.1 `Get-ChildItem` errors on them. The tree has
  **no `.git` directory**, and the `git-<random>` skeleton has no hooks or config.
  That points to a library fetch or archive download rather than the git CLI; an
  inference, not verified. The sync looked interrupted when the process exited.
  It is left in place (gitignored) as evidence. Removing it needs a long-path-aware
  tool, e.g. Python with a `\\?\` prefix. Live runs against `~/.codex` showed no
  new files, so the real cache was already current.
- `sqlite_home` receives `goals_1`, `logs_2`, `memories_1`, `queue_1`, `state_5`
  SQLite databases (about 2.6 MB with WAL files).
- `thread/start` echoed: sandbox `{"type": "readOnly", "networkAccess": false}`;
  `ephemeral: true`, `path: null` (no rollout file), `gitInfo: null`; thread
  `source: "vscode"`, which the server assigns to app-server clients.
- Thread cwd in `task-1-hello/` → `instructionSources` =
  `bootstrap-harness/AGENTS.md`. bootstrap-harness is its own git root, so the
  Codex agent is told to read rules.md.
- With no config, the default model is `gpt-6-astra` (provider `openai`).

## Verification
1. Offline, 2026-09-23 20:42:57 UTC (server timestamp), empty in-swimlane
   CODEX_HOME (an earlier version of the script, which stopped at the login check):
   handshake completed, and `account/read` returned `account: null,
   requiresOpenaiAuth: true`. Exit 1 in 0.3 s with a clear message.
2. Offline, full path, current script, turn ended 20:45:00 UTC: `thread/start`
   accepted every parameter.
   The turn hit `401 Unauthorized` (no credentials), Codex retried 5 times, the turn
   failed after 16.6 s, and the harness printed the server's error and exited 1.
3. A before/after metadata diff of `~/.codex` (7,162 files; names and mtimes only)
   showed **0 changed, 0 new, 0 removed** across both offline runs. The Codex
   desktop app was running throughout. A best-effort scan of `%TEMP%` (4,875
   entries) and `%LOCALAPPDATA%\OpenAI` found no item with codex, arg0 or
   app-server in its name modified 20:30–20:50 UTC. A name filter can miss files;
   this is not proof.
4. Live run 1, below: **passed**.

## Live run 1 — 2026-09-23 20:51:53 UTC, under the standing authorization
`python task-1-hello/hello.py` (not verbose), run from this session's PowerShell.
No sandbox bypass was needed or used.
- stdout `Hello world.`; exit 0; 6.7 s wall clock.
- Account `chatgpt`, plan `prolite`. Model `gpt-6-astra` (openai), effort
  `medium`. Approvals `untrusted`, reviewer `user`. Sandbox read-only with network
  off. `instructionSources` = `bootstrap-harness/AGENTS.md`.
- No approval requests, no commentary messages, no error notifications.
- Codex's own stderr: six `rmcp::transport::worker ... "error decoding response
  body"` errors between 20:51:55 and 20:52:00. These are MCP transport failures
  from the inherited config; gpt-codex saw the same with `codex_apps`. They did not
  affect the reply.
- Before/after diff of `~/.codex` (7,162 files, names and mtimes): **3 changed, 0
  new, 0 removed**: `models_cache.json`,
  `plugins/cache/openai-curated-remote/openai-templates/.codex-remote-plugin-install.json`,
  `plugins/cache/openai-curated-remote/plugin-management/.codex-remote-plugin-install.json`.
  `auth.json` was untouched and no session file was written. A before/after diff
  cannot see files created and removed within the run (e.g. `tmp/arg0`). The Codex
  desktop app was running, so some of these writes could be its own.
- SQLite in `task-1-hello/.runtime/`: the ephemeral thread left no thread,
  memory, queue or goal rows. Codex's `logs` table has **no rows from this run**.
  Its log sink flushes in batches and loses a process's tail at exit; the 16.8 s
  offline run kept only its first 10 s. Do not rely on it for short runs (task 2).
- **Unobserved:** whether the agent ran any known-safe command, such as reading
  rules.md as AGENTS.md instructs. Under `untrusted`, those run without approval,
  and non-verbose output reports only approval requests. `--verbose` shows every
  item.

**Task 1 passed at the bar.**
