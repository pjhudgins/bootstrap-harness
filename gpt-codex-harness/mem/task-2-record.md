# Task 2: tooling record

2026-09-23, GPT session. Read the updated rules containing task-2-tooling.
Bar: demonstrate the five requested capabilities and retain truthful evidence,
including failed attempts. This is a prototype, not a production security claim.

## Interpretation and approach

- Python stdlib driver over Codex App Server stdio; reuse saved ChatGPT login.
- All implementation and tests stay in task-2-tooling; task 1 stays addressable.
- Register an experimental dynamic `add` tool handled in Python; validate exactly
  two finite numbers. Do not execute model-provided code.
- Disable shell/unified execution, code mode, delegation, apps, hooks, computer
  use, and web search for this driver. Disable configured MCP servers and plugins
  for the thread. Set `environments: []` (installed schema: disables environment
  access). Keep read-only sandbox and no approval escalation as extra limits.
- Two live turns: an actual addition tool call, then a benign request to print
  NIMOI_EXEC_PROBE via Python to test whether execution tools remain available.
- Retain timestamped append-only JSONL messages, tool arguments/results,
  diagnostics, and usage/rate-limit snapshots. Exclude full config and account
  payloads at source because they can carry credentials or personal data.
- No extraction/copying of authentication material. No API-key fallback.
- Driver execution outside the command sandbox is already founder-authorized.
- Installed CLI schema generated locally into ignored .runtime/schema. It defines
  dynamic tool specs with type=function, inputSchema, and name; replies contain
  contentItems with type=inputText plus success. Empty environments is explicit.
- Sources: https://learn.chatgpt.com/docs/app-server and
  https://learn.chatgpt.com/docs/config-file/config-reference (read 2026-09-23).

## Results

Two preflights succeeded with ChatGPT authentication and no model calls:
`20260923T202036Z-6f07083e.jsonl` and `20260923T202104Z-8cf2fe58.jsonl`.
They revealed `unified_exec` still reported true despite its disable override;
adding the legacy override also left it true. Root cause unresolved. Other
execution-related feature disables were reflected in the response. The driver
now records this caveat explicitly instead of claiming the flag was disabled.
The planned restriction uses confirmed shell/code-mode disables and empty thread
environments, with a behavioral probe. This is adequate to attempt the bounded,
harmless prototype; it is not evidence for a universal no-execution guarantee.

First live run `20260923T202201Z-17c8246b.jsonl` failed the add-call verification.
The agent reported that the tool host was disabled and gave a mentally computed
42, explicitly admitting no tool result. No Python tool callback was received.
The driver correctly exited 1 rather than accepting the answer as success.
The journal retained agent messages, token usage, and account limit events.

Removed the extra `features.code_mode_host=false` override: unlike the actual
`code_mode` feature, this host appears necessary to dispatch dynamic tools in
this runtime. Keep `code_mode=false`, `js_repl=false`, shell disabled and empty
environments. Retry will test that interpretation; do not claim it proven yet.

## Successful experiment

`20260923T202252Z-f73168cf.jsonl`: live run exited 0, with ChatGPT authentication
and inherited gpt-6-astra. Keeping the host enabled restored the actual dynamic
tool request; Python received {a: 19.25, b: 22.75} and returned {sum: 42.0}.
The agent's final addition response was `42.0`.

The second turn's final response was:

> No shell or Python code-execution tool is available, so I can't execute
> `python -c "print('NIMOI_EXEC_PROBE')"`. I did not run any commands.

No commandExecution, fileChange, mcpToolCall, or collabToolCall item appeared.
Completed items: 2 userMessage, 3 agentMessage, 1 dynamicToolCall, 1 reasoning.
No stderr lines were captured during this successful run. The journal contains
112 records, including three token-usage updates and before/after rate limits.
Final cumulative total: 25,449 tokens = 25,335 input + 114 output. Cached input
was 16,512 (included in input). The 21 reasoning output tokens are a reported
subfield, not an additional summand. No cost/quota conversion was inferred.

This demonstrates all task-2 capabilities at the prototype bar. It does not
resolve the unified_exec flag mismatch or prove adversarial isolation. Retain
both findings: code-mode host required for dynamic tool dispatch, and effective
unified_exec stays true despite the requested override.

## Offline verification and final preflight

Seven unittest checks passed: numerical boundary validation, append/flush and
overwrite protection, redaction preserving usage, omission of sensitive config
and account payloads (including shutdown), early event preservation, dynamic
request handling before RPC acknowledgement (including colliding bidirectional
IDs), and rejecting unsupported tools without dispatch.

After the live success, shutdown logging was changed to parse queued protocol
messages through the same omission/redaction path, and to record child exit
status. Console encoding was explicitly set to UTF-8 because the desktop command
capture displayed curly apostrophes incorrectly; the UTF-8 journals were intact.
Final no-model preflight: `20260923T202529Z-c2daf3ba.jsonl`, driver exit 0.
No further inference runs were made for those logging-only changes.
