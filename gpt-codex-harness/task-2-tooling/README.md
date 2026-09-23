# Task 2: tooling prototype

Verified with Codex App Server `0.155.0-alpha.9.2` on Windows, 2026-09-23.
Python standard library only; `codex` must be on PATH and signed in with ChatGPT.
Run with normal terminal permissions (founder-authorized for this swimlane).

From this task directory:

```powershell
python tooling.py
```

This makes two sequential agent turns. First the agent calls Python `add` with
19.25 and 22.75 and reports the result. Then it is asked to execute a harmless
Python print command to probe the absence of execution tools. The driver checks
for a real successful Python callback; an answer computed by the model does not
pass. It also rejects observed command, file-change, MCP, or delegation items.
Read the probe's actual response in the journal: the automatic checks alone do
not establish what tools were exposed or prove that all execution is impossible.

For preflight without a model call, or offline tests:

```powershell
python tooling.py --inspect
python -m unittest test_tooling.py -v
```

## Demonstrated capabilities

| Task requirement | Implementation and evidence |
| --- | --- |
| Messages to/from the agent | JSONL includes outgoing prompts, incoming message deltas and completed items, turn status, and errors. |
| Restrict default execution tools | Shell, JS REPL, code mode, browser/computer use, delegation and plugins are disabled; the thread has `environments: []`. The live probe reported no execution tool and generated no command-execution event. |
| Python tool | App Server experimental `dynamicTools` registration; `item/tool/call` invokes local Python addition of two validated finite numbers. |
| Tool-call logging | Request arguments, call/turn IDs, Python result, protocol response, and completed dynamic-tool item are retained. |
| Usage/account limits | Logs retain `thread/tokenUsage/updated` and before/after `account/rateLimits/read`, plus streamed limit changes. |

## Records and verification

Each invocation exclusively creates `runs/<UTC timestamp>-<random suffix>.jsonl`.
Records have sequence numbers and UTC client timestamps and are flushed after
each write. Server timestamps remain in event payloads. `send` records an
attempted send before writing to the pipe. The driver retains failure records
and never overwrites an earlier run. These journals are intentionally not ignored.
Generated schemas and disposable runtime/test files are under ignored `.runtime/`.

Full configuration responses are omitted from logs because they can contain
secrets. Account identity is reduced to authentication type and plan. Additional
redaction handles credential fields and recognizable key/token forms. The driver
never extracts, copies, or logs the saved authentication store. Account-limit
records can include account identifiers, plan information, and credit balances;
these journals are local institutional records, not public reports.

Successful live evidence: `runs/20260923T202252Z-f73168cf.jsonl`.
One real Python call returned `42.0`; two agent turns completed; zero forbidden
items were observed. Three usage notifications were captured. Final cumulative
usage was 25,335 input tokens (16,512 cached), 114 output tokens, 25,449 total.
Do not sum cumulative usage snapshots, add cached tokens again, or convert these
counts into subscription quota consumption. Quota windows are recorded separately.
The inherited model was `gpt-6-astra`; the driver does not select another model.

Seven offline tests cover invalid tool inputs, redaction/payload omission,
durable message writes, early notifications/tool requests while waiting for RPC
responses, and rejection of unsupported tool requests. A final preflight
`runs/20260923T202529Z-c2daf3ba.jsonl` verified the updated shutdown recorder without
another model call. The live result above precedes only the shutdown-recording
and console-encoding changes; those do not alter the model or tool protocol.

## Boundaries and findings

- **`unified_exec` still reports enabled despite disable overrides.** This is
  recorded in every live run's `restriction_caveat`. The actual restriction
  demonstrated here combines disabled shell/code tools with empty environments;
  the unified-execution flag alone is not a working control on this runtime.
- **Keep the code-mode host enabled.** Disabling `code_mode_host` prevented the
  Python tool from being dispatched. It is distinct from enabling the agent's
  code-mode tool. The failed attempt is preserved in
  `runs/20260923T202201Z-17c8246b.jsonl`; the model's mental answer was rejected.
- Dynamic tool registration and empty environment selection use the installed
  experimental protocol. Recheck these controls when changing Codex versions.
- No assertion of an adversarial security boundary: configuration plus one
  benign probe demonstrates restriction, not exhaustive isolation. The Python
  controller has normal user permissions; its `add` handler accepts numbers,
  not executable source. Unsupported server requests are rejected.
- No persistent user configuration is modified. A fresh standalone App Server
  process uses per-invocation overrides, the existing credential store, and an
  ephemeral agent thread. The request deadline is 180 seconds plus cleanup.
- Unexpected failures remain failures even if some earlier capabilities worked.
  `run_complete` records the experiment result; `server_exit` records shutdown
  separately. Usage/account data are preserved as returned, including nulls.

References: [App Server](https://learn.chatgpt.com/docs/app-server),
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference).
Local schema: `codex app-server generate-json-schema --experimental` was used to
check the exact installed request/response shapes. See `../mem/task-2-record.md`
for the experiment history.
