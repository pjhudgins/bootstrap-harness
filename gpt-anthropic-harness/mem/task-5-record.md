# Task 5 — subagents

Bar: useful prototype behavior, informative failures and truthful surviving records;
not an operating-system sandbox or production service.

2026-09-25: read the updated immutable rules and task-5 assignment. Work stays in
`task-5-subagent/`; task 4 and its live conversation remain intact.

Design assumptions: retain task-4 capabilities including message text authorship.
Use one notation for paths and ledger names: exact names, `prefix/**`, or `**`;
empty lists deny. Filesystem bounds are NIMOI-relative. Parent writes only into
this task's workspace, executes only approved task scripts, and cannot promote
drafts to the scripts directory. Child bounds and tags must be subsets, with no
implicit additions for onboarding. Refuse a child whose bounds cannot read its
pinned instructions and latest onboarding. Independent SDK clients are owned by
the harness; native SDK delegation stays disabled. Initially one child generation,
at most two running and four total children per launch, so workload is bounded.
Scripts accept no agent-supplied arguments, run with Python isolation and a small
environment, and are trusted code reviewed by the human/supervisor. Directory
separation is the safety mechanism; Python isolation alone is not a sandbox.

## Delivered behavior

Task 5 preserves the local chat, ledger-backed messages/diagnostics, addition and
usage/rate reporting. `fs_write` exports a pinned text body to a new workspace
file; overwrite and directory creation are deliberately absent. `python_execute`
runs a granted approved script with no arguments, saves combined stdout/stderr
as a text body and records exit status, timeout/truncation and script hash.
Parent and children use the same immutable bounds language. Reads are filtered
before counts/pagination; writes enforce bounds, note authorship and allowed tags.
Raw `.ledger` reads through filesystem tools are denied, so grants cannot bypass
ledger.read. General peer-ledger reads are consequently not exposed in task 5.

Parent/child onboarding is gated until ordered full reading of the selected latest
onboarding file. Child preflight requires parent-authored pinned instructions,
subset bounds and onboarding/instruction read access. Children are separate SDK
sessions, not native SDK subagents. They can run while the parent works or waits
for its next user turn. Start/status/result/failure/cancellation preserve actor
identity and lineage. The UI cost panel explicitly shows parent cost only; child
usage is separately recorded. Shutdown cancels outstanding children before closing
the shared ledger. See `task-5-subagent/README.md` for contracts and limitations.

## Verification

[checked: `python -B -X utf8 -m unittest discover -v`, 2026-09-25]
13 offline tests pass (11.125 seconds). Real-scribe checks cover:

- Bounds subset/grammar, no directory-prefix collision, and forbidden promotions.
- Pinned-version export, existing-file preservation, protected destinations.
- Execution output, output cap, timeout with surviving output and no draft import.
- Onboarding order/completion gate; filtered ledger visibility and actor ownership.
- Child concurrency, immutable instruction IDs, distinct authors, failures,
  cancellation including before the child coroutine's first instruction.
- Raw ledger-file bypass refusal; retained HTTP origin/admission and message authorship.

[checked: live browser test, then `inspect_ledger.py chat-20260925t192451z-ad462293ea --smoke --closed`]
Test ledger `ledgers/chat-20260925t192451z-ad462293ea/20260925T192451Z.ledger`:
621 lines, 192 harness events, clean trailer, no lease and no validator findings.
The directed smoke assertions also pass. Evidence:

- Parent model alias `sonnet` resolved to `claude-sonnet-5` in this run. Its two
  completed turns shared SDK session `a621b6d9-7cc2-4540-b45a-a5ccdf610231`.
- Parent wrote `pilot/draft` at `:86`; export created the 20-byte
  `workspace/smoke-draft.py`. Executing the draft was refused by execute bounds.
  The draft is preserved as test evidence, not promoted to approved scripts.
- Approved `safe_test.py` exited 0. Its merged output body is at `:151`, named
  `execution/4dd1a7d1334c4a2c9779ea77daae0e2c`, and includes sum 42 plus diagnostic
  text. Streams are merged in observed capture order, not source print order.
- Parent instructions at `:174` started `child-06e72559f7` with the requested
  narrower grants: origins reads, no filesystem writes/execution, pilot reads,
  writes only under `pilot/child/`, and only `pilot.note` tags.
- Child alias `haiku` resolved to `claude-haiku-4-5-20251001`; independent SDK
  session `b33314a4-d16c-4bd3-9e6b-4ae7cdddba75`, author
  `agent.claude.child-06e72559f7`. Nine parent tools and seven child tools were
  observed; neither inventory included native delegation.
- Parent completed add(20,22) after child start and before child completion.
  Child completed onboarding, add(19,23), and received the expected refusal for
  reading `bootstrap-harness/rules.md` outside its origins-only read bound.
- Child note `pilot/child/report` at `:488` and result at `:520` carry child
  authorship. Parent retrieved both in its second turn. All nine tool kinds had
  successful uses; the two directed refusal probes are preserved.
- Final SDK cost by actor: parent $0.1344516; child $0.0429556. These are SDK
  accounting figures, not measured subscription charges.

## Discrepancies and limits

DISCREPANCY: task-5-subagent/filesystem.py, reviewed after live test — expected ledger.read to constrain ledger access, found a potential raw-file bypass through broad fs.read grants (source review); added .ledger to protected filesystem patterns and a passing regression test; 2026-09-25.
DISCREPANCY: test ledger 20260925T192451Z:597 — parent claimed it had “no call log”, though its ledger.read ** grant permits harness tool-log records (source/grant review); preserved as a pilot reasoning error, not a capability failure; 2026-09-25.
DISCREPANCY: subagent shutdown implementation review — cancellation before first coroutine execution skips its finally block and could leave status running; added explicit terminal recording and a passing immediate-shutdown test before the live run; 2026-09-25.

The raw-ledger guard and its prompt explanation were added after the live model
test; the final guard was verified offline. No additional model call was needed
to test that deterministic refusal. Live shutdown was tested with a completed
child; shutdown cancellation was tested with synthetic runners, not an active
remote SDK call. Approved scripts are trusted; hostile OS races, descendant
processes, arbitrary code sandboxing and power-loss durability remain outside the
verified bar. Plaintext copies of information deliberately exported to readable
files are not subject to global information-flow tracking.

## Handoff

[checked: fresh browser state and launcher output] UI Ready at
http://127.0.0.1:52186, PID 29860, conversation 55108bbf, ledger
`chat-20260925t193058z-f3464d0fbd`. This fresh session has no model prompt sent by
the supervisor. It loads the final guard and prompt. The task-4 UI and records
were not changed. URL/PID are temporary observations, not configuration.

State: completed — task-5 prototype implemented and verified to the stated bar;
fresh UI left running for founder review.
