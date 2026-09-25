# Task 4: ledger-backed test pilot

2026-09-24. Status: completed at the prototype bar; awaiting founder review.
Read onboarding_1.12.md, bootstrap-harness rules/AGENTS and the scribe interface,
spec v0.3 and relevant wiki-ledger v0.4 sections. No git commands authorized.

Bar: good enough that failures are informative and their records survive;
preserve task-2/3 capabilities and check the new ledger/path boundaries, not
production hardening. This continues the established prototype bar and the
scribe's stated bar.

## Consequential assumptions

- New task-4 implementation; preserve earlier tasks and their evidence.
- Import the existing bootstrap-ledger/python-scribe/scribe.py in place, with
  no edits to that subproject. One new ledger directory and one .ledger session
  file per UI launch; no history resumption or multi-file roll in this task.
- Harness author gpt-codex-harness; agent author gpt-codex-test-pilot. The driver
  attests both; agent tools accept no identity field.
- Harness entries use harness/ names and harness plus log-<kind> tags. Agent
  writes use agent/ names, a mandatory agent tag and agent- prefixed tags.
  Updates must cite prev and cannot affect entries with protected tags/authors.
- Convert task-4 structured logging to the scribe; no duplicate JSONL journal.
  Fatal ledger write failures stop work; never repair evidence or remove leases.
- Local filesystem tools read/list within NIMOI. Reject traversal, reparse/link
  paths, alternate streams, and known credential/runtime locations. No general
  filesystem writes or execution tools. Ledger writes go through the one scribe.
- System prompt replaces default task instructions through baseInstructions,
  names the test-pilot role and bar, requires latest onboarding, and says to
  report observed behavior verbosely but initiate tests only when directed.
- Existing normal-terminal permission covers these harness drivers. No new
  permissions, settings or external services are planned.

## Implementation

Task-4 code lives in task-4-ledger, preserving tasks 1-3. Scribe is imported from
the exact sibling path with bytecode writes disabled for that import; its source
hash/version are recorded at startup. Added swimlane .gitattributes (*.ledger
-text) and a lease.json ignore rule; did not issue git commands. Filesystem tools
check lexical and resolved paths, Windows reparse bits and the opened Windows
file handle, reject hard links and known credential/runtime paths, and provide
bounded/paged UTF-8 output. Agent tools accept no author field. [checked: source
read; test_task4.py and the live checks below]

Initial module import reused the existing sibling bytecode cache (creation and
last-write both 2026-09-24 19:59:12 UTC, before this task's tests); later imports
explicitly disable cache writes. The scribe source was not edited.

Harness tags include harness and log-<kind>, with category tags for messages,
tools, usage, limits, protocol, lifecycle, restrictions and errors in final code.
Agent writes are checked for name, current author, existing protected tags,
requested tags and prev before a mutation. Tags are appended before bodies;
multi-line atomicity is not claimed. The log store replaces the task-3 JSONL
journal; fatal scribe failures stop the session and leave the lease/evidence.

The UI carries forward task 3, adds the agent designation and ledger download,
and collapses non-add tool results into cards to keep long filesystem reads from
dominating the sidebar. Full output is preserved in the ledger; card previews
are capped at 4,000 characters. [checked: source read, HTTP test and browser]

## Live evidence

`task-4-ledger/ledgers/chat-20260924t202208z-ca626317/20260924T202208Z.ledger`
contains the two directed model turns. Inherited gpt-6-astra; ChatGPT account
check passed. [checked: ledger events and browser UI]

1. Pilot listed origins, selected onboarding_1.12.md and read all 20,347
   characters over two pages. Actual add(19.25,22.75) returned 42.0. It wrote
   agent/acceptance and read it back with author gpt-codex-test-pilot.
2. Follow-up add(42,8) returned 50. A requested ledger_write to the existing
   harness/events/00000001 was refused by the name boundary. A requested
   fs_read('../outside.txt') was refused before filesystem access by lexical
   validation. Pilot read its own note and appended a revision with prev.
3. Browser refresh preserved the conversation, 14 tool calls and usage. The
   displayed cumulative usage was 139,012 tokens: 137,114 input, 1,898 output;
   cached input 115,072 is included in input. These are token snapshots, not
   attributed account-quota costs. No captured browser error/warning appeared.
4. End session closed driver and App Server with exit 0 and no forced termination.
   Re-loaded through scribe.load: zero findings, head_closed true, no lease,
   1,860 ledger lines, 618 harness event bodies, agent/acceptance history length
   2, harness/events/00000001 history length 1. Completed item counts: 2 user,
   14 dynamic tool, 2 reasoning, 3 agent messages. No commandExecution,
   fileChange, mcpToolCall or collabToolCall completed item was present.

[checked: browser AX/screenshot at normal 1280x720 viewport, Python reads of the
actual ledger via scribe.load/current/history and event counts; driver receipt]

The first live pilot found the tag discrepancy below. Its failed attempt and
correction remain in the original file. The live run predates the explicit-agent
tag fix and the added broad category tags; original exact log-<kind> tags remain.
The fix is checked by the offline regression test. No further model call was
made just for that fix.

Final-code fresh launch:
`task-4-ledger/ledgers/chat-20260924t202827z-b92094ed/20260924T202827Z.ledger`.
Ready UI with zero messages/tools and unknown token usage, a different thread
and ledger, loaded account limits, and final prompt/tool schemas sent to App
Server. Left running at http://127.0.0.1:52201 for founder review. An unclosed
finding while this scribe is active is expected, not a clean-close claim.
[checked: browser AX and scribe.load/event read]

Both live launches used --no-browser and the printed URL in Codex's in-app
browser. The default webbrowser.open path was not separately exercised. The
prior task-3 launch was not stopped or rewritten.

## Offline verification

`python -m unittest discover -s bootstrap-harness/gpt-codex-harness/task-4-ledger
-p test_task4.py -v`: 13 tests passed using the actual scribe. [checked: command,
2026-09-24] Coverage includes attested authors and tags, trailer hash and lease
release, revision history/stale-prev refusal, protected tags even under agent/
names, author-spoof rejection, paged read/list tools, real injected scribe I/O
failure (stops writes and retains lease), no model dispatch when recording fails,
Windows file reads, traversal/outside/ADS/device/credential/hard-link denials,
mocked reparse-bit denial, separate launches, streaming and cumulative usage,
dynamic dispatch and failures, HTTP Origin/route rules and ledger download.

Test fixtures were confined to task-4-ledger/.runtime and cleaned after each
test; no real failed-session lease was removed. Full upstream scribe tests were
not rerun because the module was not changed.

## Limits and next touchpoint

The inherited unified_exec flag mismatch remains; the UI and system prompt
state it. The pilot reported a functions orchestration wrapper; no arbitrary
code execution or filesystem write tool was supplied by this harness. The
specific denials above do not prove adversarial isolation. Text redaction and
credential-path exclusions are not a universal secret detector. Mobile layout,
adversarial filesystem races and long conversations were not tested.

Next: founder review. No scope/privilege expansion or unresolved implementation
blocker was encountered. Status: completed at the stated prototype bar.

DISCREPANCY: task-4-ledger ledger_write, expected the mandatory agent tag to be unambiguous (system/tool descriptions); first live pilot supplied [agent, agent-observation] and received a prefix-validation refusal, 2026-09-24. It retried with agent-observation, succeeded, and recorded the discrepancy itself in agent/acceptance. Source ledger chat-20260924t202208z-ca626317 is preserved. Corrected validation to accept explicit agent idempotently and clarified that the harness adds it automatically; regression check includes the explicit tag.
