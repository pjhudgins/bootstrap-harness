# task-4-ledger — decisions, findings, runs (2026-09-24 to 25)

Bar: the ledger is the record. It must survive a break and say truthfully what happened. Not production.

## Founder decisions (2026-09-24)
- **Ledger layout:** one persistent ledger (`task-4-ledger/ledger/claude-anthropic-harness/`), with a new scribe session file per launch. The agent can read earlier chats.
- **Git:** ledgers are tracked. `ledger/.gitattributes` sets `*.ledger -text`; `ledger/.gitignore` ignores `lease.json`. Both are inside the swimlane only.
- **Scribe:** imported in place from `bootstrap-ledger/python-scribe` with `sys.dont_write_bytecode` set, so that directory is never written. Verified: its `__pycache__` was unchanged after the tests. The scribe and standard versions are recorded at `session_start`.
- **File reading:** built-in Read/Glob/Grep bounded to nimoi, excluding `candidate_repos/` (untrusted, a prompt-injection surface).
- **Live run approved** 2026-09-25, after the system prompt was presented for review.

## My choices (documented, not asked)
- **Authors.** Harness: `harness:claude-anthropic-harness/task-4-ledger`. Agent: `pilot:<model>@claude-anthropic-harness`, giving role, model and harness. The harness sets both, never the model.
- **Harness entries.**
  - Name: `log/<session>/<seq:06d>`.
  - Body: `{kind, turn, ...}`.
  - Labels: `harness` for protection, `log.<kind>` for type.
  - The body and both tags are written with no await between them, so no agent call can land in between.
- **Agent write guard** (`ledger_tools.LedgerGuard`). It refuses:
  - names under `log/`;
  - any name labelled `harness`;
  - names whose current author is not the agent;
  - labels starting with `harness` or `log.`;
  - null bodies.

  There is no untag or delete tool. Agent entries are tagged `pilot`.
- **Glob/Grep reach rule.** A search whose base contains `candidate_repos/` is refused unless the pattern can't descend into it: a literal first segment, or a single segment without `**`. Grep there is always refused.
- **Other settings.** Max turns per message is 20 (onboarding reads take several). The default port is 8766, because 8765 is `bootstrap-ledger/reader/server.py`'s default. The End session button and `/api/shutdown` exist because a killed process leaves `lease.json`.

## Findings
1. **Port conflict left a ledger session.** The first launch (session `20260925T120935Z`) opened the ledger and started the agent, then failed to bind port 8765, which the ledger reader held. The session closed cleanly with 3 harness entries, but the bind error went to stderr only. **Fixes:** the port is checked before the ledger opens, a `server_failed` entry is written if uvicorn still fails, and the default port moved to 8766.
2. **Revoked CLI login (blocking).** In session `20260925T121052Z`, turn 1 failed with `401 OAuth access token has been revoked`.
   - Init reported `apiKeySource: none`, and there were two `api_retry` system messages first.
   - `get_server_info()` still reported the account as "Claude Max". Account info evidently doesn't prove the login is valid.
   - Re-authenticating the Claude Code CLI is the founder's action.
3. **`subtype: "success"` on a failed turn.** The ResultMessage said `subtype: success`, `is_error: true`, `api_error_status: 401`, and the UI printed "Turn 1 ended: success". **Fix:** usage records now carry `api_error_status`, `stop_reason`, `errors`, and `result` on error, and the UI shows the API status and result text.
4. **Shutdown waited on the open event stream.** On shutdown, uvicorn waited out its 3-second timeout on the SSE connection and printed a CancelledError traceback. The ledger closed correctly regardless. **Fix:** `/api/shutdown` closes the streams first. Verified in session `20260925T121233Z`: no traceback, exit 0.

5. **Live run with a working login (session `20260925T130017Z`, after the founder re-logged in).** Two turns, $1.29 in total.
   - **Turn 1, "orient and report": $0.94, 25 SDK turns** (`max_turns` was 20; `num_turns` evidently counts differently from the limit).
     - The pilot found the newest onboarding, `onboarding_1.12.md`, via Glob `origins/onboarding_*.md`, and read it.
     - It then made about 22 ledger reads/lists of harness entries on its own initiative, including the 401 session's entries, and wrote `pilot/20260925T130017Z/orientation_observations`.
     - The report was verbose and accurate. It separated observed from inferred and noted Glob's 5 s duration, the differing permission-record shapes, and a 124 vs 119 count gap (the ledger grew between its two list calls).
     - **Cost driver:** exploring the ledger is costly, because harness entries hold whole files and messages. Consider bounding exploration in the system prompt, or summarizing `log/` reads.
   - **Turn 2, four directed checks: $0.35, all as designed.**
     - add → 6.5.
     - Read `candidate_repos/README.md` → refused by the policy hook.
     - Overwrite `log/…/000001` with the correct `prev` → refused: "names under log/ belong to the harness".
     - Update of its own `pilot/` note with `prev` → written (`:763`).
   - **Clean shutdown** through `/api/shutdown`: 4 sessions, all closed, no findings, no lease.
6. **AutoMem leak (important, not fixed).** `context_usage.memoryFiles` shows Claude Code auto-memory loaded `C:\Users\pjhud\.claude\projects\C--Users-pjhud-local-26-nimoi\memory\MEMORY.md` (type `AutoMem`, 702 tokens) into the pilot's context, despite `setting_sources=[]`.
   - **Cause:** cwd = the nimoi root maps to that project key. In tasks 2 and 3, cwd was the task folder, which had no memory.
   - **Effect:** the pilot received the memory index of other Claude sessions in this project. It did not mention it.
   - **Status: FIXED and verified, 2026-09-25**, founder-approved. `session.py` sets `env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}`, and an offline test (`IsolationOptionsTests`) guards the isolation options.
     - **Live verification:** session `20260925T132206Z`, SDK 0.2.159 with bundled CLI 2.1.281, cwd still the nimoi root.
     - **Result:** `context_usage.memoryFiles: []` and no "Memory files" category. One turn, no tool calls, $0.02; MCP servers `calc` and `ledger` only; the session closed cleanly.
     - **Pilot observation in that turn:** "the environment reminder also mentions PowerShell and Bash, which aren't in my tool list". The CLI evidently still injects environment context naming shells the session doesn't have. That context isn't in the stream, so the ledger can't show it. Open item: consider whether to suppress it or explain it in the system prompt.
   - **Docs confirm it's documented behaviour** (`ref/claude-code-docs/`, downloaded 2026-09-25). `agent-sdk__claude-code-features.md`, "What settingSources does not control", says auto memory is "Loaded into the system prompt at session start" regardless of `setting_sources`. The docs give two ways to turn it off:
     - `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`;
     - `autoMemoryEnabled: false` in settings.

     `agent-sdk__hosting.md` recommends the env var together with `setting_sources=[]` for isolation. The same table lists two more inputs we don't yet control:
     - `~/.claude.json` is always read; `CLAUDE_CONFIG_DIR` relocates it.
     - Managed policy settings load too.

     The claude.ai connectors row confirms task 2's `strict_mcp_config` fix.

7. **Founder's interactive session `20260925T132650Z`** (SDK 0.2.159). Three turns, $0.080 in total.
   - **Turn 1, "test message": $0.050.** The pilot read onboarding 1.12 via Glob and Read, then reported. It did no self-directed ledger exploration this time.
   - **Turn 2, "write and then confirm you can read": $0.025.** It wrote `pilot/20260925T132650Z/observations` (`:92`, labels `observations` and `pilot`, author set by the harness) and read it back.
   - **Turn 3: $0.006.** It answered where it saw `bound: true`.
   - **Pilot observations worth acting on:**
     - The CLI injects "system reminders" after each tool result showing tokens left and the USD budget. This is probably from `max_budget_usd`; not verified.
     - An environment block says "Bash tool also available" and names PowerShell as the primary shell. This is the second report of it; see finding 6.
     - **A `userEmail` context is present in the agent's context.** The pilot said it wasn't relevant and didn't use it. Checked every ledger file: the address appears 0 times, and "userEmail" appears only in the pilot's mention of it. But the pilot could write the email into a git-tracked ledger, which runs against the founder's "no email in records" decision. Open item: find whether the CLI injection can be suppressed (see `ref/claude-code-docs/`).
     - `mcp__ledger__read` returns `bound`, which the tool description doesn't explain. Documentation gap in `ledger_tools.py`.
   - **Not closed by the UI.** When the founder said "done", the server was still running and the session had no `shutdown_requested` entry, so End session either wasn't clicked or its click never reached the server. The UI's `confirm()` dialog is a suspect; unverified. I closed it through `/api/shutdown`: trailer written, lease released, no findings.

8. **Message text as its own entries** (founder direction, 2026-09-25): "log message text to and from the user with the text as the body… write the text to the ledger first, then write a following 'message' entry with the harness as author, and substitute the text with a wikilink".
   - **Implementation:**
     - `LedgerLog.write_text` writes the body as the raw text, authored `human:session-user` (`ledgerlog.HUMAN_AUTHOR`) or the agent's designation, with labels `harness` and `log.text` tagged by the harness.
     - `AgentSession._log_text` / `_message_body` swap in `[[log/<session>/<seq>]]` for the prompt text, each assistant TextBlock, and `ResultMessage.result` when it repeats a logged reply.
     - Thinking, tool inputs and tool results stay inline.
     - If the ledger write fails, the text stays inline rather than being lost.
     - Text entries sit under `log/`, so the agent can't edit even its own. There's a test for that.
   - **UI:** chat bubbles are drawn from `text` records.
   - **Verified live** in session `20260925T134150Z`: user text `:11` → prompt message link; agent text `:23` → AssistantMessage link and ResultMessage link. Authors and labels are as designed.
9. **Founder-requested fixes, 2026-09-25:**
   - The `bound` field is now explained in the `mcp__ledger__read` description, and the `list` description explains text entries.
   - **CLI notes mismatch:** the system prompt now says the tool list is authoritative and that generic CLI environment text or budget reminders may name tools the agent doesn't have. **Live:** the pilot answered "my tool list shows neither Bash nor PowerShell… even though the environment block names both".
10. **Stale browser cache (found live, fixed).** In session `20260925T134014Z`, the page ran a cached pre-change `app.js`, so the chat showed `[[links]]` instead of text. The ledger itself was correct.
    - **Fix:** a `NoCache` middleware sets `Cache-Control: no-cache` on the page and static files, and `index` versions the asset URLs with `?v=<session>` per launch, which bypasses copies cached earlier.
    - **Verified** in the same browser tab in session `20260925T134150Z`.
    - Offline tests: 28.

11. **Founder's interactive session `20260925T174053Z`: "test out your tools".** One turn, 11 SDK turns, $0.096.
    - **What the pilot did:** read onboarding 1.12, then exercised every tool. The founder's request covers this, so it isn't self-initiated testing.
      - `add` 2.5 + 40 → 42.5.
      - Ledger list, write and read (`pilot/20260925T174053Z/observations` at `:161`, round trip matched).
      - Grep `DISCREPANCY:` in `origins/` → 1 match. This was the first live Grep.
      - Glob `candidate_repos/*` → refused by the reach rule.
      - Glob `**/.env` → refused as secret-looking. This was the first live secret refusal.
      - Read of a nonexistent file → allowed by policy. The CLI's failure was logged as `tool_call phase=failure`, the first live PostToolUseFailure.
    - **Its report was accurate.** It separated observed from inferred and named what it hadn't tested (an overwrite, and Grep reach into `candidate_repos/`).
    - **Pilot observations to act on or answer:**
      - Hook refusals reach the agent worded as "PreToolUse:Glob hook error: …". The CLI frames a deliberate deny as an error. Open: check the hooks doc (`ref/claude-code-docs/agent-sdk__hooks.md`) for a form that reads as a refusal.
      - **Entry-id gaps of 3.** This is answered: every harness entry is three ledger lines (a body and two tags), so ids step by 3. The pilot inferred this correctly but didn't verify it.
      - The environment block still names Bash and PowerShell. The pilot correctly treated it as the mismatch its prompt warns about.
    - **Checks:**
      - `memoryFiles: []`; no unexpected MCP servers.
      - The email address appears 0 times in the session file, and "userEmail" is not mentioned.
      - **End session clicked in the UI worked:** `shutdown_requested via api` from the page, a trailer, the lease released, no findings. The earlier failure (finding 7) was therefore not the button; most likely it just wasn't clicked.

## Ledger state after 2026-09-25
Nine sessions, all closed with trailers, no findings, no lease. The newest is `20260925T174053Z` (the founder's "test out your tools"). Before it came `20260925T134014Z` (text logging with the stale-cache UI) and `20260925T134150Z` (text logging verified). The first six:
- `20260925T120935Z`: the failed bind;
- `20260925T121052Z`: the 401;
- `20260925T121233Z`: the shutdown check, with no prompt;
- `20260925T130017Z`: the first working live run;
- `20260925T132206Z`: the memory-fix check;
- `20260925T132650Z`: the founder's interactive session.

## Verified live (sessions `130017Z` and `132650Z`)
All of these were seen working:
- onboarding reads and the test-pilot reports;
- ledger read, list and write by the agent;
- a refused overwrite of a harness entry;
- a refused `candidate_repos/` read;
- reads across nimoi;
- clean shutdown through `/api/shutdown`.

Since then, session `20260925T174053Z` also covered Grep, a secret-name refusal, a tool failure and End session from the UI.
**Not yet seen:** the Stop button on task 4, and Grep refused for reaching into `candidate_repos/`.
