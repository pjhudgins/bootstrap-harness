# Task 5: bounded writes, approved scripts, harness-owned subagents

2026-09-25. Status: completed at the stated prototype bar. Founder requested re-reading rules and proceeding
to task 5. Read onboarding_1.12.md and current rules; task 5 authorizes these
new capabilities. Task 4 and its evidence remain intact.

Bar: an informative prototype with surviving records, checked permission
containment and useful parallel agent behavior; not production isolation.

Consequential design assumptions:
- Retain task-4 ledger, transcript attribution, tools, UI and account behavior.
- One shared session ledger and scribe, with separate attested child authors.
  Harness-owned child App Server processes/threads run independently of the
  parent's model turn. No native subagent tool or desktop-task delegation.
- Bounds use explicit lists: fs.read/write/execute and ledger.read/write.
  Filesystem mounts are nimoi:/, workspace:/, scripts:/. A trailing slash
  selects a directory subtree; otherwise a selector is one exact file/name.
  Ledger / means all names. Empty lists deny; no wildcards or implicit grants.
  The same object is validated and injected for parent and child.
- Root reads NIMOI, writes only this launch's workspace, and executes only
  approved scripts in task-5-subagent/scripts. Write and execute selectors
  must not overlap after resolving mounts. Child selectors must be subsets.
- Ledger-to-file writes pin a readable entry revision and require explicit
  expected content hashes to replace existing files. No promotion tool exists.
- Python execution accepts an approved .py file, no arbitrary code, arguments,
  environment or working-directory overrides. Isolated Python imports, stripped
  environment, bounded time/output. Approved scripts are trusted programs, not
  an OS sandbox: they must not execute/import agent drafts or spawn processes.
- Script output is a protected harness-authored text body under a caller-chosen
  readable/writable agent name, with execution metadata in harness events.
- Parent provides a model and an immutable ledger instruction reference. The
  reference must be parent-authored, readable by both agents, and include Bar:.
  Child read bounds must cover origins/ for mandatory complete onboarding.
- Prototype scope: one-turn children, no grandchildren, at most two active and
  six total children per launch. Start returns promptly; status supports bounded
  waiting. End session stops children before closing the shared ledger.
- Ledger filesystem paths are excluded from raw file reads so narrowed ledger
  access cannot be bypassed by reading .ledger files directly.

Work sequence: implement and check bounds/materialization/execution, then child
lifecycle and UI; verify offline denial/failure paths and a bounded live parent/
child exercise; leave a fresh session for founder review. Existing authorization
for normal-terminal execution of harness-driver scripts applies.

Documentation basis: official App Server page confirms independent thread/start
with a model and model/list discovery; installed schemas will check compatibility.
https://learn.chatgpt.com/docs/app-server [checked: official page, 2026-09-25]

Progress: added bounds, revision-pinned ledger access, file materialization,
approved script execution, separate child runtimes, shared-author logging and
child status/usage UI. Thirty-three offline checks pass, including the nineteen
inherited task-4 checks. [checked: unittest discover test_task*.py, 2026-09-25]
Installed generated schemas confirm thread/start model, ephemeral and empty
environments, plus model/list pagination. [checked: local experimental schema]

DISCREPANCY: task-5-subagent inherited HTTP handler; expected rejected-origin POST to return readable 403 (task-4 test), found Windows connection resets when closing with unread body bytes in both sandboxed and normal-terminal checks, 2026-09-25. Task-5 handler now consumes a bounded body with a timeout before returning the denial; the same check passes. Task-4 source was preserved.
DISCREPANCY: task-5-subagent new tests; expected LF-only Python output and a bare reference from child status, found preserved Windows CRLF and a reference containing additional author metadata, 2026-09-25. Corrected test expectations to preserve actual output and explicitly select name/id when supplying a strict tool reference; production output/reference semantics retained.

DISCREPANCY: task-5-subagent/static; expected preserved UTF-8 labels from task 4, found mojibake in the first browser check because the editing script used the Windows default text encoding, 2026-09-25. Corrected the new files with explicit UTF-8 and verified labels and actor names after reload. No source files in earlier tasks were changed.
DISCREPANCY: task-5-subagent/subagents.py completion collection; expected failure status even after a failed ledger write, found by source review an assumption that a message-tagged name always has a body, 2026-09-25. A failed scribe can leave tags alone. The collector now skips bodyless names; a real injected scribe write failure verifies surviving child text, failed status, completed worker notification and family stop.

## Final verification

Thirty-five offline checks pass, including the nineteen inherited task-4 checks.
New checks cover grant containment and alias overlap, onboarding requirements,
filtered ledger lists/history/revisions, distinct child authors, parent/child
concurrency, cancellation and writer failure, stale file hashes, hard-link
refusal, draft execution/promotion denial, stripped script environment, Unicode
output, nonzero exit, timeout and bounded capture. Missing file errors are tool
refusals; they do not fail the session. [checked: unittest discover test_task*.py
-v, 35 tests OK, 2026-09-25]

Live acceptance ledger:
`task-5-subagent/ledgers/chat-20260925t192846z-c229e2b5/20260925T192846Z.ledger`.
One directed parent turn and one child turn, both using gpt-6-astra with ChatGPT
sign-in. This run exercised:
- Both agents completely read onboarding_1.12.md. Parent also read the project
  rules. Child received the exact parent-authored agent/child-brief revision.
- Parent performed add(40,2) and wrote agent/parent-progress between the child
  start and finish records, demonstrating independent parent activity.
- Child add(19.25,22.75) returned 42.0. Its agent/child/draft was materialized at
  workspace/<run>/child/draft.py with exact bytes `# draft only\nprint(42)\n`.
- Materialization to scripts:/blocked-promotion.py was refused by fs.write
  bounds; that file does not exist. Reading agent/parent-private was refused by
  ledger.read bounds. These are the two failed child tool calls, preserved.
- Approved smoke script returned exit 0. Its JSON output is the plain body of
  agent/child/smoke-output, authored by the harness with protected tags.
- Child had ten tool calls; parent and child together had 22. Parent token total
  was 138,609, child 119,191; these are usage snapshots, not account-quota charges.
- Five completed message envelopes resolve to earlier bodies with matching
  identities/authors. Exactly one is human input. The child's instruction
  envelope points to the parent-authored brief; the child's reply has author
  gpt-codex-test-pilot-child-0001. Completed child reply:
  messages/agent/00000438, id 20260925T192846Z:5325.
- UI refresh retained conversation, child result, usage and actor-labeled tools.
  Browser AX and a 1280x720 screenshot checked the layout.
- End session exited 0. Both App Server exits were 0 without forced termination.
  Scribe reader reports 1,400 bound names, head_closed true and no findings; its
  lease is gone. [checked: browser, process receipts, scribe reader assertions,
  direct draft bytes and promotion-path existence check]

The live run preceded the final missing-file/UTF-8 subprocess and bodyless-tag
failure-path refinements; those are verified by the final offline checks. No
additional model turn was spent on those edge-case changes.

Fresh final-code launch left Ready at http://127.0.0.1:53662 with no messages,
children or tool calls. Ledger: chat-20260925t193836z-4721856a. The intermediate
empty launch chat-20260925t193640z-2a1a9de6 was ended before this final restart.
The older task-4 session was not stopped. [checked: launch output and browser AX]

Entry and concise bounds notation: `task-5-subagent/README.md`.
Known limits: trusted approved scripts, inherited unified_exec mismatch,
non-adversarial filesystem checks, one-turn children without grandchildren,
two active/six total children, and no long-session or mobile-layout claim.
No git commands performed. Status: completed; next step is founder review.
