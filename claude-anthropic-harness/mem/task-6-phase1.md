# task-6-hybrid — phase 1 record: the Codex backend (2026-09-28)

**Bar:** a GPT agent runs the task-5 toolset under the same checks and records as a Claude agent. Whatever Codex does outside those checks is either switched off, refused, or detected, recorded, and made to stop the agent. What breaks must be informative, and its record must survive. Not production.

**State:** parked. Phase 1 is complete and awaits founder review (pause 1 of 3; plan in `task-6-plan.md`).

## What was built (all in `task-6-hybrid/`)
- **`codex_server.py`** — the App Server over stdio, in asyncio:
  - one JSON message per line;
  - server requests are served in their own tasks;
  - every message goes to the recorder.
  It also holds `CodexSettings` (binary, CODEX_HOME, per-agent state folder, catalogue) and `CodexHomeWatch` (the `~/.codex` before/after list). Adapted from claude-codex `task-5-subagent/codex_client.py`.
- **`backend_codex.py`** — `CodexBackend`, with the same interface as `ClaudeBackend`: `async with`, `server_info`, `run_turn`, `interrupt`. It covers:
  - the restriction set: gpt-codex `policy.py`, plus the additions below;
  - the preflight, and the checks before the first turn (fail closed);
  - tool calls through `AgentCore.call_tool`;
  - stops, limits, and records.
- **`live_turn.py`** — runs one live turn with any model, or, with `--fake-model`, the surface check against the real `~/.codex` configuration.
- **`app.py`** — the task-5 UI can now run a GPT pilot (`--model gpt-...`):
  - Codex is configured whenever it is installed (`--no-codex` turns it off);
  - the `~/.codex` diff is recorded at shutdown;
  - the port is 8768.
- **Prompts** — lineage-aware (`{session_kind}`, `{runtime_notes}`). A GPT agent is told how to reach the harness tools (`tools.<server>__<name>` inside `exec`), and that no other Codex tool is part of its toolkit.
- **`audit.py`** — check 6 also covers GPT replies: agentMessage links must resolve to the agent's own entries.
- **Tests** — `tests/test_codex.py`: 15 tests on the real codex binary, the fake model and the isolated home. `tests/fake_model.py` is copied from claude-codex. The suite is 65 tests, about 20 s.
- **Housekeeping** — `harness.py`: the ledger is `claude-anthropic-harness-t6`, the author is `harness:.../task-6-hybrid`, and `RUNTIME` and the `codex` parameter are added. `bounds.txt` now points at task 6's workspace and scripts.

## Codex 0.158 findings, from captures against the fake model
1. **Dynamic tool names may not start with `mcp__`.** Codex refuses them ("dynamic tool name is reserved"). So the harness offers one namespace per server:
   - the model sees `tools.fs__read` inside `exec`, or `fs.read` for a direct-tool model;
   - calls come back as (namespace, tool) and map to the harness name `mcp__fs__read`;
   - records, allowlists, prompts and audits keep the harness names.
2. **Tool surface under `RESTRICTIONS`** [checked: `tests/test_codex.py` `test_offered_tools_per_model`, isolated home; `live_turn.py --fake-model`, real `~/.codex` config]:

   | Model | Offered |
   |---|---|
   | gpt-6-astra, gpt-6-sol | `exec` (the harness tools plus `clock__curr_time`), `wait`, `request_user_input`, `request_user_input_async` |
   | gpt-5.6-sol, gpt-5.6-terra | `exec` (the harness tools), `wait`, `request_user_input` |
   | gpt-5.5 (direct tools; not on the model lists) | `request_user_input` and the harness tools as `fs.read` etc. |

   None of these was offered: shell, `apply_patch`, `view_image`, web search, native delegation (`collaboration.*`), goals, sleep, MCP servers or plugins.
3. **`environments: []` is the setting that removes `apply_patch` and `view_image`.** Without it, 0.158 offers both whatever the feature flags say. The backend passes it on `thread/start` and on every `turn/start`.
4. **`features.unified_exec=false` still reads back as on,** globally and at thread level. No shell tool is offered either way. It is recorded on every GPT agent as `codex_restrictions.stays_on`.
5. **Context Codex injects unless told not to:**
   - the skills list (3–5.5 KB, with paths under `CODEX_HOME/skills`);
   - a permission text ("The harness will require user approval before running commands…");
   - the collaboration-mode text;
   - the goal and sleep tools.

   These are removed by: `skills.config` (every listed skill disabled by name), `include_permissions_instructions=false`, `include_collaboration_mode_instructions=false`, `include_apps_instructions=false`, `features.goals=false` and `features.sleep_tool=false`. What remains:
   - our system prompt (a developer message; for direct-tool models, the `instructions` field);
   - a 126-character `environment_context` (date and timezone);
   - the message itself.
6. **The live `~/.codex/config.toml`** (key names only were read, never values) sets:
   - `notify`: a program run after every turn;
   - 10 plugins;
   - the MCP server `node_repl`;
   - `features.js_repl`;
   - nimoi as a trusted project, which would load `AGENTS.md`.

   The overrides:
   - `notify=[]`;
   - each plugin and MCP server disabled by name;
   - `js_repl` off;
   - `project_doc_max_bytes=0`, with `instructionSources` checked to be empty (fail closed).
7. **The user-input tools cannot be switched off, but neither reaches the harness.**
   - Codex refuses `request_user_input` outside Plan mode; the model gets the error.
   - `request_user_input_async` becomes an ordinary agentMessage (`delivery: "async"`), recorded as the agent's text.
8. **`rawResponse/completed` arrives once per model response.** The backend counts model rounds with it.
9. **Codex's per-agent state** goes to `task-6-hybrid/.runtime/<session>/<agent>/` (gitignored, via `sqlite_home`): its state, goals, memories, queue and log databases.

## Live runs (ledger `claude-anthropic-harness-t6`; all three audit ok)
| Session | What | Result |
|---|---|---|
| 20260928T190505Z | Surface check: real `~/.codex` config, fake model, gpt-5.6-sol | Offered exactly `exec` (the 11 harness tools inside), `wait` and `request_user_input`; nothing unexpected. Input: system prompt, environment context, message. `~/.codex`: 3 files removed under `tmp/arg0`. |
| 20260928T190527Z | **One live GPT turn**: gpt-5.6-sol on the ChatGPT login | Success: `fs.list /origins`, then `fs.read` of onboarding_1.12 in full, then `calc.add` → 42.25, then the report. 4 model rounds, 36,136 tokens, 23 s. No refusals, no stops. `~/.codex`: `logs_2.sqlite`, `logs_2.sqlite-wal` and `models_cache.json` changed during the run. |
| 20260928T190810Z | UI launch with a GPT pilot, no message | Idle; clean shutdown; Codex exit 0. `~/.codex`: `logs_2.sqlite-wal` and `models_cache.json`. |

Which `~/.codex` changes were ours [working]:
- **`logs_2.sqlite`:** our agents' logs go to their own `sqlite_home`, so these changes most likely came from other Codex processes. The desktop app and the other lanes were running at the same time.
- **`models_cache.json`:** it changed in both authenticated runs and not in the fake-model run, so it is likely ours (the model list refresh).

What the live agent reported:
- it described its tools correctly;
- it noted the naming difference (`mcp__fs__read` in the prompt, `tools.fs__read` in `exec`), which the prompt documents;
- it flagged that the message had no `Bar:` line, as onboarding 1.12 requires.

## Secrets check of `.runtime` (2026-09-28)
[checked: byte scan of `.runtime`, except the offline home, for bearer, authorization, access/refresh-token, `sk-` and JWT patterns, counts only]
- No bearer, authorization, token or key patterns were found.
- Each live session's Codex log database holds one JWT-shaped string. It is the value of a `set-cookie: __oailb=` response header: claims `aud`, `exp`, `host`, `iat` and `iss`, with a 65-minute life. That is OpenAI's load-balancer cookie, not an account credential.
- Codex's log database records response headers, cookies included.
- `.runtime` is gitignored. The state folders are kept for now (open question 2).

## Decisions made in phase 1 (assumptions to confirm)
- **Namespaced tools,** as finding 1 forces.
- **The restriction set is wider than the peers'.** Additions: goals, sleep_tool, image_generation, view_image, skill_search, collaboration_modes, multi_agent_v2, memories, tool_search and request_permissions_tool; the injected-text switches; skills disabled by name; `notify=[]`.
- **What stops a GPT agent:**
  - any model call other than harness tools and Codex's runtime helpers (`exec`, `wait`, `request_user_input[_async]`);
  - any thread item other than `userMessage`, `agentMessage`, `reasoning`, `dynamicToolCall` and `contextCompaction`;
  - any approval request.

  A stop is recorded; the harness then interrupts the turn and closes the App Server. Later turns are refused.
- **Limits for GPT agents:**
  - model rounds per message: `max_turns`, 20 in the UI;
  - model time per turn: 900 s, not counting time inside harness tools, since a subagent or a governor request can take long;
  - tokens are recorded per agent, and there is no dollar figure.
- **The `~/.codex` diff is per launch.** One snapshot is taken before the first App Server starts, and one diff at the end, with `while_running` flags. Other processes' writes are included, and a diff cannot tell them apart.
- **The ChatGPT login is required for live runs.** `OPENAI_API_KEY` and `CODEX_API_KEY` are removed from Codex's environment, so there is no API-key fallback.
- **`app.py` configures Codex by default.** So a Claude pilot may spawn GPT subagents in the UI.

## Discrepancies
- DISCREPANCY: codex 0.158 feature `unified_exec`. Expected: off, requested with `-c features.unified_exec=false` and in the thread config. Found: on, via `experimentalFeature/list`, global and thread. 2026-09-28.
- DISCREPANCY: `task-6-hybrid/bounds.txt`. Expected: task 6's paths. Found: task-5-subagent's workspace and scripts, copied with the folder; fixed in this lane. 2026-09-28.
- DISCREPANCY: the live-turn message, session 20260928T190527Z. Expected: a `Bar:` line (onboarding 1.12). Found: none. The agent noticed, and proceeded under "operate as directed". 2026-09-28.

## Open questions for the founder
1. Should dispatch (governor → owner) and spawn (owner → subagent) refuse instructions that have no `Bar:` line? Recommendation: yes, enforced by the harness.
2. Should `.runtime`'s per-agent Codex state (state and log databases, with cookies) be kept, or deleted at the end of each launch? Recommendation: keep while developing; it is gitignored.
3. Proceed to phase 2 (roles and the governor/owner protocol)?
