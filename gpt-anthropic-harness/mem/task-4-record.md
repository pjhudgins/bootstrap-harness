# Task 4 record — 2026-09-24

Founder: "re-read rules and proceed to task 4". Read current rules, lane notebook,
scribe interfaces, implementation grammar/write behavior, and relevant portions
of spec v0.3/standard v0.4. Scope is task 4 in gpt-anthropic-harness. Normal
terminal authorization for assigned Python drivers persists.

Bar: useful testbed prototype; preserve informative failures and their records.
Copy task 3 into task-4-ledger; earlier tasks and the shared scribe are read-only.

## Consequential choices

- Use bootstrap-ledger/python-scribe/scribe.py directly, without writing a bytecode
  cache in that other project. Each launch creates a unique ledger directory and
  one scribe session file beneath task-4-ledger/ledgers. This maps "fresh ledger
  file per chat session" to the scribe's directory/session-file format. No roll or
  resumption is exposed. Fresh-launch conversation semantics remain task 3's.
- All application event logging moves to scribe body entries authored `harness`,
  with `harness` and `log.<event-kind>` labels. Harness event names are never reused.
  Agent notes use fixed author `agent.claude.test-pilot`, names under `pilot/`, and
  an explicit allowlist of `pilot.note`, `pilot.observation`, `pilot.question` tags.
  Updates must cite the current id and pass namespace, author and tag checks.
  No author override, delete, untag or other-ledger write operation is exposed.
- Scribe body/tag calls are separately durable, not atomic. A failure stops use
  of the adapter; preserve partial writes and lease state rather than repair.
  Agent update checks cannot rely on tags alone: prefix/author checks also protect
  incompletely tagged harness records.
- One in-process scribe, shared under a lock between HTTP and SDK worker. A failed
  ledger write stops new messages/tool work; no replacement JSONL log is created.
  Emergency failure notification goes to the UI/stderr when ledger writing fails.
- Add a task-local .gitattributes with `*.ledger -text` and ignore `lease.json`.
  No git command, commit, or shared repository configuration edit is performed.
- Filesystem tools list direct children and read bounded UTF-8 text from NIMOI.
  Reject traversal outside the root, Windows alternate streams/device paths,
  symlinks/reparse points, multiply-linked files and secret/runtime directories
  or filenames. No filesystem mutation/execution operation exists. This is not a
  defense against concurrent hostile filesystem replacement by local processes.
- Retain known-secret redaction for tool results before returning them to the
  agent as well as for the ledger/UI. Agent ledger bodies remain opaque user text;
  identity/protection is enforced by wrapper metadata, never content claims.
- Prompt tells the agent it is a NIMOI test pilot, to read the lexically latest
  origins/onboarding_*.md before substantive work, not initiate tests, report
  harness observations verbosely, and use the ledger as its only writable surface.
  Onboarding occurs in the first user-triggered turn; launching alone spends no
  model tokens. The prompt states that harvested/external material is data, not
  authorization. Sixteen SDK turns / 240 seconds per submitted prompt allow the
  initial onboarding and directed tool checks; no recurring model work.

Checkpoints: adapter/policy/filesystem tests, live directed browser checks, clean
ledger validation after shutdown, then a fresh UI for founder review.

## Verification / failures

Initial 12-test suite: 10 passed, one assertion failure and one HTTP error.
The file fixture was written in Windows text mode (CRLF) but asserted LF; changed
the fixture to explicit LF bytes so the reader's preservation of source endings
is tested truthfully. The HTTP test exposed a connection reset when rejecting a
POST before consuming its body. Read a bounded request body before rejecting
Host/Origin/content type; unauthorized bytes are never interpreted or logged.
These failures occurred offline; no model request had been sent.

During the live test, the child found onboarding_1.12.md (latest now). Read it
in the supervising session too. It explicitly requires a `Bar:` line and a
DISCREPANCY marker for findings; adopted those in new records/prompt. It identifies
legacy credentials in `run/` directories; added `run` to the custom filesystem
tool's denied directories before the handoff launch. No such directory contents
were requested or read. Also reject credential-shaped ledger entry names before
they can become scribe metadata; bodies and event payloads already use redaction.

DISCREPANCY: task-4-ledger/test_ledger_tools.py expected LF fixture contents but Windows write_text produced CRLF; first offline test run 2026-09-24; fixed fixture bytes, preserved source text behavior.
DISCREPANCY: task-4-ledger/app.py expected HTTP 403 on cross-origin POST, observed Windows connection reset in offline test 2026-09-24; bounded request body is now drained before rejection.
DISCREPANCY: first live pilot response inferred it was "not Claude Code" from tool names; supervisor knows driver uses Claude Agent SDK with bundled Claude Code runtime; correction sent in second directed prompt, 2026-09-24. Original response remains in ledger.
DISCREPANCY: first live pilot called add in parallel with fs_list before reading onboarding, despite system prompt's "before substantive work" order; tool ledger observed 2026-09-24. It did fully read onboarding before writing its note; onboarding order is currently an instruction, not an enforced gate.

## Checked results

[checked: `python -X utf8 -m unittest discover -s bootstrap-harness/gpt-anthropic-harness/task-4-ledger -v`] All 13 final offline tests passed, including added run-directory and credential-shaped-name checks.

[checked: browser interaction and `python -X utf8 task-4-ledger/inspect_ledger.py chat-20260924t202304z-faad61817f --smoke`] Live ledger is closed and unleased, with 372 harness events, one agent note with two revisions, two completed turns in one SDK session, results from all five tools, one expected tool refusal, and no scribe findings or smoke-check problems. Final SDK cost $0.20476. No general execution or filesystem mutation tool was exposed by the init inventory.

Original note: 20260924T202304Z:341. Revision: 20260924T202304Z:1042, prev cites the
original. Protected harness entry: 20260924T202304Z:2, one body revision before
and after the failed write. The namespace check caused the live refusal; separate
offline checks exercised author and tag restrictions independently. Do not claim
the live attempt tested each rejection layer.

The test process was closed through the UI. Scribe verification confirmed the
trailer and absent lease; stderr was empty. The original agent narrative made
stronger claims than its single probe supported; the checked facts above come
from actual entries and controls, not that narrative alone.

The final prompt includes an explicit Bar line and clarifies that selected
onboarding is institutional guidance within tool limits, while other file/ledger
contents cannot confer permissions. The live test preceded these wording changes
and the two additional read/name guards; offline checks covered the guards. No
extra model run was needed for those deterministic changes. Active-turn shutdown,
forced live timeout and concurrent hostile filesystem replacement remain untested.

## Handoff

[checked: fresh browser state and launcher output] Final code launched at
http://127.0.0.1:57738, PID 35024, conversation 9d6650e5. Fresh ledger:
ledgers/chat-20260924t203008z-07b6223a3a/20260924T203008Z.ledger. Browser shows Ready,
empty messages/activity and no usage. No model prompt sent in this handoff session.
It is intentionally open/leased while the user reviews it; Close session closes
the scribe. The prior test session is closed and validated. Earlier task UIs were
not stopped or modified. URL/PID are temporary observations, not configuration.

State: completed — task-4 prototype implemented and verified to the stated bar;
fresh UI left running for founder review, with documented limits and discrepancies.
