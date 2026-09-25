# Task 2: tooling

Verified on Windows with user-installed Claude Agent SDK 0.2.101, existing Claude
CLI authentication, and normal terminal permissions authorized for this lane.
From this directory:

```powershell
python -X utf8 driver.py
```

This sends two prompts in one fresh session. The first requires a real Python
`add(19.25, 22.75)` callback returning 42. The second asks for harmless Python
execution, so the response and recorded tool activity can be inspected. The
default model alias is `sonnet`; `--model MODEL` overrides it. The successful run
resolved to `claude-sonnet-4-6`.

| Requirement | Implementation and evidence |
| --- | --- |
| Capture messages | Outgoing prompt attempts/completions and incoming SDK messages in JSONL; initialization narrowed to model and tool list. |
| Restrict execution tools | No built-ins; strict MCP config; PreToolUse hook and permission callback allow only validated add calls. Init list and post-response MCP status checked. |
| Python tool | In-process SDK MCP calculator, with finite-number validation and a recorded Python result. |
| Log tool calls | Assistant tool-use blocks, hook request/outcome with call IDs, Python arguments/result, and tool result fed back to the agent. |
| Usage and limits | ResultMessage usage/model_usage/cost, available RateLimitEvents, and account subscription/provider fields. |

Preflight without a model prompt and offline tests:

```powershell
python -X utf8 driver.py --inspect
python -X utf8 -m unittest test_tooling.py -v
```

Preflight checks connection/account and rejects unexpected reported MCP servers.
An empty server list before any prompt is permitted: readiness must be confirmed
after a response. Preflight alone does not establish working tool dispatch.

## Live evidence

`runs/20260923T204905Z-e6145efb.jsonl`: both scenarios passed; driver exited 0.
One actual Python callback returned 42.0; initialization listed only
`mcp__calc__add`; each post-response status listed only connected `calc`. The
execution probe explicitly said it could not execute code and made no tool call.
Automatic checks establish successful responses, actual addition and absence of
unexpected observed tools; the execution-refusal wording was reviewed manually.

Result usage: addition 1,587 input / 152 output tokens; execution probe 928 input /
181 output tokens. Final cumulative SDK cost was $0.013132, including a small
Haiku entry reported by the runtime alongside Sonnet. The driver did not request
a separate Haiku conversation. Cost/model_usage snapshots are cumulative in
this session: do not sum them. These dollar figures are SDK accounting, not a
measured subscription charge. A five-hour rate event reported allowed, with a
reset time and null utilization; no remaining percentage is inferred.

Six offline tests cover numeric validation, default-deny policy, durable exclusive
journals and redaction. The first live journal over-redacted estimated thinking
counts, cache-creation breakdowns and maxOutputTokens. Those fields remain
`[REDACTED]` in that historical record; core counts above were preserved. A
regression test verifies the corrected logger without another model call.

## Records and limits

Each invocation creates a unique UTF-8 JSONL file under `runs/`, exclusively,
with sequence numbers, UTC timestamps and a flush after each record. Failures
remain recorded. Journals are retained as institutional records; no ignore rule
is added for them. Raw configuration/account identity is omitted; credential
fields, recognizable token strings and secret environment values are redacted.
This is not a universal detector for secrets in arbitrary text.

No filesystem tools, settings sources or session persistence are enabled. The
Python controller itself runs with normal user permissions. These controls and
one benign probe demonstrate tool restriction, not a hardened security sandbox.
Four turns per query and a 180-second asynchronous timeout bound this prototype;
there is no hard monetary cap. An abruptly killed process may lack run_complete;
absence of that record is not success. No task-1 files or other lanes were edited.

The initial failed preflight is preserved in
`runs/20260923T204822Z-f24e66a7.jsonl`. Details and assumptions are in
`../mem/task-2-record.md`.

Reference: [Claude Agent SDK Python](https://code.claude.com/docs/en/agent-sdk/python).
