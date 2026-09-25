# Task 2 record — 2026-09-23

## Scope, bar, assumptions

Founder authorized task 2. Re-read rules.md; task 3 is not in scope. Standing
normal-terminal authorization from task 1 applies. Bar: demonstrate the five
capabilities and preserve informative failures, not a production/security release.

Keep SDK 0.2.101, existing authentication and the configurable `sonnet` alias.
Disable all built-in tools; expose only Python add via an SDK MCP server. Task 2
does not require retaining read-only tools, so omit filesystem access entirely.
Use one session with two sequential prompts (addition and a benign execution
probe), max 4 turns per query, and a 180-second run timeout. No retries of failed
model requests. SDK cost is usage information, not measured subscription spend.

Retain exclusive-create UTF-8 JSONL journals under task-2-tooling/runs, with
sequence numbers, UTC timestamps and a flush per event. No ignore rule for these
journals is added. Limit account info to subscriptionType/apiProvider; omit raw
server-info configuration. Redact credential-named fields, recognizable token
strings and secret-valued environment variables before writing. These measures
are not a general detector for arbitrary secrets in arbitrary user text.

## Prior work and reference

Read the Claude Anthropic task-2 driver, logging and calculator code, its decision
record, and GPT Codex task-2 README. Adopt strict_mcp_config to exclude account
connectors, and a PreToolUse hook as well as can_use_tool because some tools can
bypass the latter. Verify the actual Python callback, not just a correct answer.
Check MCP status before/after prompts and tool names in initialization messages.

Official SDK reference consulted:
https://code.claude.com/docs/en/agent-sdk/python

## Checkpoints

1. Implement and check journal fidelity, numeric validation and deny policy offline.
2. Live run under authorized terminal permissions; inspect the journal and report
   evidence/limitations for human review. Pause if new ambiguity materially changes
   the work or the requested capabilities cannot be demonstrated within this scope.

## Verification so far

- Five offline tests passed: valid addition; invalid/nonfinite operands and overflow;
  default-deny policy; exclusive flushed journals with message types and Unicode;
  credential redaction preserving usage counts.
- Initial no-model preflight: runs/20260923T204822Z-f24e66a7.jsonl, failed because
  get_mcp_status returned no servers immediately after connection. No model prompt
  sent. Account fields: Claude Max / firstParty. This was an overly early readiness
  assumption, not observed permission leakage. Allow an empty pre-prompt status;
  require exactly the connected calc server after each completed response. The
  actual callback and init tool list remain required to pass the live addition.

## Successful live run and record defect

runs/20260923T204905Z-e6145efb.jsonl: driver exit 0, both scenarios passed.
One real Python callback returned 42.0 for 19.25 + 22.75. Init listed only
mcp__calc__add; each post-response status listed only calc/connected/dynamic.
The execution response explicitly stated that code execution was unavailable;
no tool call occurred in that probe. Reviewed the actual response, not only the
automated pass flag. The add PreToolUse hook allowed the call; no separate
can_use_tool callback was needed for that call.

The sonnet alias resolved to claude-sonnet-4-6. Result usage per prompt was
1587/152 and 928/181 input/output tokens. Cumulative final cost: $0.013132.
model_usage also included claude-haiku-4-5-20251001 (522 input, 14 output,
$0.000592); the driver's own prompts target Sonnet and it did not create an
additional Haiku conversation. Preserve this runtime-reported detail without
inventing its purpose. Rate-limit information: five_hour, allowed, reset time,
utilization null. Account: Claude Max / firstParty.

Found record defect: the credential-field redactor also removed estimated_tokens,
estimated_tokens_delta, ephemeral_1h/5m_input_tokens, and maxOutputTokens. These
fields cannot be recovered from this journal. Core usage/cost values survived.
Preserve the journal unchanged; expanded explicit usage-field exceptions and
added a regression test that writes and rereads those exact field shapes while
still redacting refresh_token. This post-live change affects logging only; no
extra model call is needed to validate the deterministic correction.

Final verification: all six offline tests pass after the logger correction.
Task 2 is ready for founder review. No task-3 work initiated.
