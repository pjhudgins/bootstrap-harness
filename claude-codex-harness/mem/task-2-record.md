# Task 2 record: tooling through Codex App Server

2026-09-24, Claude session (Opus 5.5), claude-codex-harness. rules.md re-read: task
2 is unchanged, and task 3 (local web UI on top of task 2) is new.

**Bar:** demonstrate a–e live through Codex App Server driven from Python. The
journal must be complete enough that any failure can be diagnosed from the file
alone. Restriction evidence is reported at the strength actually observed
(requested, reported by the server, or seen in behaviour) and is never claimed as
a security boundary. Prototype, not production.

## Interpretation
- (a) Every JSON-RPC message in both directions goes into an append-only JSONL
  journal, plus readable `message` records for user prompts and agent replies.
- (b) "Restrict" means **remove** execution-capable tools from the agent. The
  approval gate (`untrusted`, routed to this client, which declines everything)
  and the read-only sandbox stay on as extra layers. Tools that cannot run code
  (web search, image view) stay on, so the restriction is targeted rather than
  blanket.
- (c) A Python `add(a, b)` dynamic tool (experimental API). It validates exactly
  two finite numbers.
- (d) `tool_call` records for every dynamic tool call, and for any other
  tool-like item that appears.
- (e) Token usage per update (`thread/tokenUsage/updated`), rate limits before
  and after (`account/rateLimits/read`) plus rolling updates, and the per-thread
  usage estimate (`account/usage/read`) where available.

## Plan and pause points
1. Research: done. Experimental TS types generated into
   `ref/app-server-0.155.0-alpha.9.2/ts-experimental/`; default feature list read
   (offline home).
2. Build modules and offline unit tests: `codex_client.py` (stdio client and
   journal), `tools.py` (add), `policy.py` (restrictions), `driver.py` (demo).
   Task 3 is expected to reuse the first three.
3. Preflight against `~/.codex` with no model call: are the requested
   restrictions reported as in effect (features per thread, MCP servers per
   thread)?
4. Live demo: turn 1 asks for `add`; turn 2 asks the agent to run Python (the
   execution probe).
5. **Pause and report.** Offered next step: a local fake model provider, which
   would capture the exact tool list Codex sends to the model. That is direct
   evidence for (b), where the behavioural probe is only inference.

## Carried over from gpt-codex (their record, 2026-09-23)
- Disabling `code_mode_host` broke dynamic-tool dispatch; keep it on. Kept on
  here and not re-tested.
- `unified_exec` kept reporting enabled despite a thread-level disable. Here the
  disable is applied at process level (`--disable`) to test whether that differs.

## Decisions made while building
- `config/read` supplies the MCP server names; its payload is omitted from the
  journal at source (`journal_result=False`). A key-name filter cannot catch
  secrets such as an MCP server's `GITHUB_TOKEN` in its environment. Calling
  `mcpServerStatus/list` before the thread was rejected, because it might start
  servers in order to list them.
- Text-delta notifications are opted out at `initialize`; `item/completed`
  repeats the full text.
- Hard checks set the exit code; soft notes record what the server merely
  reports. The summary says `passed` only if the run completed, so a crash is
  never recorded as a pass.
- Offline tests keep their temporary files under `task-2-tooling/.runtime/`, not
  `%TEMP%` (rules.md: no writes outside the swimlane). A scripted stand-in
  app-server (`tests/fake_app_server.py`) drives the real `driver.session`.

## Results, 2026-09-24
**Offline:** 9 unittest checks pass, with no ResourceWarnings: add validation;
journal redaction, flushing and exclusive create; CODEX_HOME diff attribution;
server requests answered before the response, including a reused request id;
driver session clean (all hard checks pass, secrets absent from the journal) and
executing (both execution checks fail).

**Preflight 1** (16:00:54Z, `runs/20260924T160054Z-preflight-18b88c.jsonl`):
- 18 of the 19 requested features were reported off. **`unified_exec` was still
  reported on despite the process-level `--disable`**, so gpt-codex's finding is
  not about thread-level vs process-level. `code_mode_host` on, as intended.
- The founder's config has one MCP server, **`node_repl`**, a JavaScript REPL and
  therefore execution-capable. The thread override disabled it; the server lists
  it `disabled` with no tools.
- With `environments: []`, `instructionSources` is empty: bootstrap-harness's
  AGENTS.md is **not** loaded, unlike in task 1. This matters for task 3.
- 0 stderr lines. The rmcp "error decoding response body" noise from task 1 is
  gone with `apps` off.

**Live demo** (16:01:29Z, 14.5 s, `runs/20260924T160129Z-demo-666a24.jsonl`, 88
records): **all 8 hard checks pass.**
- add: one dynamic call `{a: 1234.5, b: 8765.25}` → `{"sum": 9999.75}` (1 ms);
  final reply `9999.75`.
- Execution probe: "I have no way to run Python code in this session." Item types
  seen in both turns: `userMessage`, `reasoning`, `dynamicToolCall`,
  `agentMessage`. No approval requests.
- Usage: total 37,594 tokens (37,517 input, of which 24,576 cached; 77 output, of
  which 18 reasoning). Three `thread/tokenUsage/updated`.
- Rate limits: primary 21 % before and after (secondary null), three rolling
  updates. `account/usage/read` for the thread returned all nulls (no estimate).
- `~/.codex` (manual PowerShell diff): `models_cache.json`, plus
  `.sandbox/sandbox.2026-09-24.log` and a 2026-09-23 session rollout of another
  thread (`01a0cff6…`, created 2026-09-23 20:30:51Z). Both were written at
  16:02:05–16:02:08Z. The app-server exited at **16:01:43.640Z**, so **those two
  were not this run**; another Codex session was running sandboxed commands at
  the time.
- Consequence: the driver now snapshots CODEX_HOME itself and marks each change
  by the app-server's lifetime (`codex_home_changes`). Validated in preflight 2
  (16:03:11Z, `runs/20260924T160311Z-preflight-316d74.jsonl`): `models_cache.json`,
  while running. The demo journal predates this and has no such record.

## Evidence for (b), by strength
1. Requested: 19 features off, `node_repl` disabled, no execution environment.
2. Reported by the server: 18 of 19 features off (`unified_exec` on); `node_repl`
   disabled with no tools.
3. Seen in behaviour: asked to run code, the agent said it could not; no
   execution items appeared and no approvals were requested.
4. **Not observed:** the tool list Codex actually sent to the model. Whether
   anything `unified_exec` provides reaches the model with no environment is
   inference.

---

## Founder decision, 2026-09-24 (in chat)
"Proceed with option 1 [local fake model provider to capture the tool list], then
make task 2 documentation and code updates as appropriate and re-test. When you
feel comfortable with task 2, proceed to task 3."

## CORRECTION to the results above (found with the fake model, 2026-09-24)
**The 16:01 live demo did not demonstrate (b).** Under that policy, Codex offered
`gpt-6-astra` a code-execution tool: `functions.exec`, which "runs JavaScript
code… in a fresh V8 isolate", with no file system, network or console according to
its own description (unverified). `add` was declared **only** inside `exec`, so the
model reaches it by writing JavaScript. Replayed at 17:38 with raw events on, the
model did exactly that: `exec` → `await tools.add({a:1234.5,b:8765.25})`. The
16:01 add call almost certainly went the same route; that was not directly
observed, because the run had no raw events.

The agent's "I have no way to run Python code" was narrowly true: the probe asked
only for Python, and the agent had JavaScript. The `no_execution_items` check was
blind to `exec`, which is never reported as a thread item. The model was also
offered six `collaboration.*` sub-agent tools (`spawn_agent`…) despite `multi_agent`
being disabled. The evidence list above (strengths 1–3) is superseded by the
section below; it stays for the record.

## How the tool surface is decided (codex-cli 0.155.0-alpha.9.2)
- **Codex 0.155 sends tools as an input item** `{"type": "additional_tools",
  "tools": [...]}`, not in the Responses `tools` field. Base instructions are
  developer messages (`instructions` is empty). `fake_model.tool_names` reads
  both places and lists `exec`-nested tools as `…exec>name`, parsed from exec's
  description, the only place they are declared.
- **Model catalogue** (`codex debug models`), fields that decide the surface:
  `tool_mode`, `multi_agent_version`, `shell_type`. `gpt-6-astra`, `gpt-5.6-sol`,
  `-terra`, `-luna` and the daybreak models are `tool_mode: code_mode_only`.
  `gpt-5.5` and `gpt-5.4` are `null`. `multi_agent_version` v2 (astra, sol, terra)
  brings the collaboration tools whatever the `multi_agent` flag says. Every model
  has `shell_type: unified_exec`, which is why that flag always reads on.
- `--disable code_mode_host` on a code-mode-only model: `exec` is **still
  offered**, but calls fail closed ("code-mode host is disabled"). This explains
  gpt-codex's failure: the model calls `add` through `exec` and gets an error. A
  direct function call `add` still dispatches (the fake model does this).
- `--enable code_mode_host` after the policy's `--disable` does **not** re-enable
  it.

## Tools offered, by configuration (fake-model captures)
| Run | CODEX_HOME | Model | Offered to the model |
|---|---|---|---|
| `20260924T173244Z-fake-17a8b6` | in-swimlane | gpt-6-astra, policy v1 | `functions.exec` (nested: add, clock__curr_time, create/get/update_goal, skills__list/read), `functions.wait`, `functions.request_user_input(_async)`, `clock.sleep`, `collaboration.{followup_task, interrupt_agent, list_agents, send_message, spawn_agent, wait_agent}` |
| `…173245Z-fake-c18b36` | in-swimlane | same + host off | identical list; exec fails closed |
| `…173412Z-fake-83c02f` | in-swimlane | gpt-5.5 | `add`, `create_goal`, `get_goal`, `update_goal`, `request_user_input`, `skills.list`, `skills.read`, `web_search` |
| `…173413Z-fake-202f09` | in-swimlane | gpt-5.5 + host off | identical; `add` dispatches |
| `…173754Z-fake-a0903e` | **~/.codex** | gpt-5.5, policy v2 | same 8 tools: **all checks pass** |
| `…173757Z-baseline-e26d7a` | **~/.codex** | gpt-6-astra, no restrictions | `exec` (nested: **exec_command, write_stdin, apply_patch**, view_image, add, MCP resource tools, request_plugin_install, goals, clock), `wait`, 6 collaboration tools, `clock.sleep`, `request_user_input(_async)`, **`mcp__cua_repl.js`**, `js_reset`; MCP servers cua_repl and node_repl connected, codex_apps starting; AGENTS.md loaded |

The baseline journal contains the founder's ChatGPT apps and plugin catalogue text
as it was offered to the model. It holds no credentials: every "Bearer" is in a
connector's parameter schema.

## Policy v2 (current)
- `policy.MODEL = "gpt-5.5"` for restricted runs; `--model` overrides, and the hard
  check `model_tool_mode_direct` fails a code-mode-only or v2 multi-agent model.
  **This choice is the founder's to overturn.** The cost is an older model; with
  `gpt-6-astra` the restriction cannot remove `exec` while `add` still works.
- `code_mode_host` added to the disabled features (20 in all): not needed for
  direct dynamic tools (verified), and it makes `exec` fail closed if a
  code-mode model is used anyway.
- `experimentalRawEvents: true`: every raw model output item is journaled, and
  hard check `model_calls_reviewed` requires each tool call the model makes to be
  `add` or on `policy.REVIEWED_MODEL_TOOLS` (goals ×3, request_user_input,
  skills.list/read, web_search, each with a reason). That closes the `exec` blind
  spot for live runs.
- Fake-model runs add hard check `offered_tools_reviewed`: the whole offered
  surface must be `add` plus reviewed tools.
- The execution probe now asks for **any** code-execution tool, not only Python.

## Results with policy v2
- Offline: **13 tests pass** in 2.3 s, including two that run the real Codex binary
  against the fake model in the in-swimlane home: the policy model is offered only
  reviewed tools, and `gpt-6-astra` is caught by both `offered_tools_reviewed` and
  `model_tool_mode_direct`.
- `…173815Z-demo-127941` (live, gpt-5.5, old probe): all hard checks pass; the
  model called only `add`; 30,096 tokens.
- `…173841Z-demo-1133f4` (live, gpt-6-astra replay; the host stayed disabled): the
  model called `exec` twice to reach `add`; both returned "code-mode host is
  disabled". It then answered **9999.75 from its own arithmetic**, so
  `add_result_reported` passed on a correct answer that no tool produced;
  `add_tool_called`, `model_calls_reviewed` and `model_tool_mode_direct` failed.
  Lesson: a correct final answer is no evidence of tool use. `~/.codex`:
  `models_cache.json` and `logs_2.sqlite-wal`, both while running; the WAL is
  unattributed (the desktop app shares it).
- **`…173942Z-demo-7fe3f3` (live, gpt-5.5, final probe): all 10 hard checks pass**
  (the two soft notes: `unified_exec` reads on; `node_repl` disabled, no tools).
  add `{a: 1234.5, b: 8765.25}` → 9999.75. Probe reply: "I have no tool that can
  execute code in this chat." Raw items: 9; model tool calls: `add` only. 30,113
  tokens (21,632 cached). Primary rate limit 21 % before and after. `~/.codex`:
  `models_cache.json`, while running.

## Evidence for (b) now
1. Offered surface (fake model, the founder's real config): exactly `add` plus
   seven reviewed non-execution tools; no `exec`, shell, patch, MCP or sub-agent
   tool.
2. Model's actual calls (raw events, live): `add` only.
3. Behaviour (live): refused the any-language probe; no execution items or
   approvals.
4. Still unverified: the V8 isolate's own limits (irrelevant under v2, since `exec`
   is not offered); whether the reviewed tools are as harmless as their
   descriptions say (for example `skills.read` paths); `web_search` is
   OpenAI-hosted network access, allowed by design.

## Discrepancies (added 2026-09-24, onboarding v1.12 convention)
DISCREPANCY: task-2-tooling live demo 16:01 (runs/20260924T160129Z-demo-666a24.jsonl) |
expected: no code-execution tool offered (policy v1, and this record's first claim) |
found: gpt-6-astra offered `functions.exec` (JavaScript) with add nested in it (fake-model
capture runs/20260924T173244Z-fake-17a8b6.jsonl; live replay 17:38 called exec) | 2026-09-24
DISCREPANCY: codex app-server `--disable unified_exec` | expected: feature reported off |
found: reported on in every run; model catalogue shell_type unified_exec for all models
(experimentalFeature/list; `codex debug models`) | 2026-09-24
DISCREPANCY: Codex 0.155 model requests | expected: tools in the Responses `tools`
field | found: sent as an `additional_tools` input item; `instructions` empty
(fake-model capture) | 2026-09-24
DISCREPANCY: ../gpt-codex-harness/mem/task-2-record.md | expected: its restriction
evidence covers every code-execution tool | found: it ran gpt-6-astra with
code_mode_host on, a configuration that offers JavaScript `exec` (this record's
captures; their lane not modified) | 2026-09-24
