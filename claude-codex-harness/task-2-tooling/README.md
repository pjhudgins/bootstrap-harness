# task-2-tooling (claude-codex-harness)

A Codex agent, driven through Codex App Server from Python, that can add numbers
through a Python tool and has no code-execution tool. Python 3.10+ standard library
only. It uses the saved Codex login in `~/.codex`.

```powershell
python driver.py               # live demo, model gpt-5.5: add turn + execution probe; exit 0 = all hard checks pass
python driver.py --preflight   # restrictions only: a thread, no model turn, no tokens
python driver.py --fake-model  # scripted local stand-in for the model: no tokens; records the exact tools Codex offers
python driver.py --fake-model --baseline   # same, with every restriction dropped, for comparison
python -m unittest discover -s tests -t .  # offline tests (13), including real Codex + fake model
```

Other options: `--model` (overrides the policy model), `--codex-home`, `--runs-dir`,
`--echo-stderr`, and `--codex-arg` (extra app-server arguments for experiments; the
policy's `--disable` flags win over `--enable`). stdout is a JSON summary; progress
and checks go to stderr. Each run writes one append-only journal:
`runs/<UTC stamp>-<demo|preflight|fake|baseline>-<random>.jsonl`.

| Capability | Where | Journal records |
|---|---|---|
| a. messages to and from the agent | `codex_client.py` journals every JSON-RPC message both ways and app-server stderr; raw model items via `experimentalRawEvents` | `send`, `recv`, `stderr`, `message` |
| b. restrict code-execution tools | `policy.py`: model choice, 20 features disabled, MCP servers disabled per thread, no execution environment; then decline-all approvals, read-only sandbox | `model_catalog_entry`, `features`, `mcp_servers`, `offered_tools` (fake model), `declined` |
| c. Python `add` tool | `tools.py`, registered as an app-server dynamic tool | `tool_call` |
| d. tool calls | the Python callback, every tool call in the model's raw output, every tool-type thread item | `tool_call`, `model_call`, `tool_item` |
| e. usage and rate limits | token usage per update and per response, `account/rateLimits/read` before and after plus rolling updates, `account/usage/read` | `usage`, `response_usage`, `rate_limits`, `thread_usage` |

Every run also records changes under CODEX_HOME (`codex_home_changes`), each marked
by whether it happened while the app-server was running. This is the founder's
condition for live runs.

## Why the model is pinned
Codex builds the model's tool surface from its model catalogue. `gpt-6-astra` and
the gpt-5.6 family are `tool_mode: code_mode_only`. They are offered `exec`, which
runs model-written JavaScript, and they reach dynamic tools such as `add` only from
inside it; observed live, `gpt-6-astra` calls `add` by writing
`await tools.add(...)`. They also get sub-agent tools. No feature flag removes
these while `add` keeps working, so the restricted policy uses `gpt-5.5`, whose
catalogue entry has neither. The `model_tool_mode_direct` check fails for any
code-mode-only model.

## Checks
**Hard** (they set the exit code): the model's catalogue tool mode; `add` called
with the right operands and its sum reported; no execution item; no approval
request; every tool call in the model's raw output is `add` or reviewed; usage
logged; rate limits logged (live only); and, with the fake model, every tool Codex
**offers** is `add` or on `policy.REVIEWED_MODEL_TOOLS`. **Soft** notes: what the
server reports for each feature (`unified_exec` always reads on; its tool is not
offered) and for each MCP server. None of this is a verified security boundary.

What the journal leaves out: values under credential or email keys are
`<redacted>`, and the `config/read` payload is omitted at source; only MCP server
names are kept. Results, findings and corrections: `../mem/task-2-record.md`.

Modules `codex_client.py`, `tools.py`, `policy.py` and `fake_model.py` are meant
for reuse in task 3.
