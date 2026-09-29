# Task 5 peer-review improvements — completed at the prototype bar

Bar: useful prototype behavior, informative failures and surviving records; not production isolation.

2026-09-25: Founder authorized the proposed peer-review improvements and added
rules.md 5g: Codex exec is allowed; restrict it where possible and document where not.
The earlier listing discrepancy is authorized for repair. Work remains in this
swimlane's task-5 directory; prior tasks and peer implementations remain records.

Plan: (1) repair listing visibility, immutable configurable bounds, bounds-driven
tool inventory and full-read onboarding gate; (2) share agent lifecycle and normalize
events, with direct result references; (3) compact streaming records with an explicit
detailed diagnostic mode; (4) inspect actual model-facing tools through a local fake
provider, observe raw calls, and add a reusable ledger inspector; (5) regression,
real App Server/fake provider, directed live and browser checks, then report.

Consequential choices: preserve the configured model and independent parallel
children. Native delegation remains forbidden by prompt and monitored; runtime
feature flags alone are not proof of its removal. No extra packages. Keep short
mount notation, exact ledger revisions and hash-checked replacement. Compact logging
will checkpoint unfinished text at bounded intervals and flush on orderly shutdown;
document the possible uncheckpointed suffix on abrupt process death. Detailed mode
retains each received fragment. All persistent diagnostics stay in the ledger.

Sources: all three active peer task-5 implementations and records (previous review);
official App Server docs https://learn.chatgpt.com/docs/app-server (read 2026-09-25).
Protocol details will be checked against the installed binary and local generated types.

DISCREPANCY: task-5-subagent/capabilities.py listing | expected child names filtered by fs.read | found directory checked but child names not filtered (source review) | 2026-09-25.
DISCREPANCY: task-5 restriction claims | expected flags/thread items establish offered tools | found peer request captures expose exec/native collaboration despite disabled flags; our own capture pending | 2026-09-25.

## Founder task instruction

AUTHOR — founder, 2026-09-25, task instruction (not a new standing directive):

> Thank you for reporting the discrepancy. Re-read rules, I have added a new rule to allow for this. Proceed with proposed improvements, restricting exec where possible and documenting where not

## Delivered decisions and behavior

- Directory listing filters child grants before pagination and exclusion counts.
  Exact-directory read permission alone reveals no descendant names.
- Bounds are immutable after parsing; bounds.json (or --bounds) can narrow the fixed
  task-5 ceiling. Short mounts and exact ledger revisions remain unchanged.
- Empty grants omit corresponding tools. Parent/child substantive tools are gated
  until a contiguous full read of onboarding; a changed file resets partial progress.
- AgentRuntime owns the shared start/turn/usage/close behavior. AgentRecord groups
  each child's state, worker and done event. Runtime storage is separate per launch.
- events.py handles protocol-to-semantic translation; UI/children consume those
  events. Committed completed messages populate a direct result-reference index.
  Raw model message text also becomes authored body references.
- Compact mode records unfinished text at 2,048 characters or the next reader tick
  after one second, and flushes on completion/failure/clean shutdown. It omits raw
  agent deltas and opts out of reasoning/plan deltas. Abrupt death may lose a pending
  suffix below 2,048 received characters per message, plus unread transport data.
  A synchronous callback may delay time-based flushing. --stream-log detailed keeps
  each received fragment and protocol notification. Complete text authorship and
  text-before-envelope ordering are retained in both modes.
- policy.py disables available native execution/delegation/external-integration
  flags, declines approvals, and observes raw model calls. Unexpected calls stop
  the family after observation. This is detection, not pre-execution interception.
  Model choice is preserved. Direct-tool models disable code_mode_host; models
  requiring exec retain it. Catalogue absence is a disclosed uncertainty.
- audit_surface.py captures actual offers through the real App Server and local
  fake provider, with a real Python callback. No paid model service is called by it.
  The scripted kickoff is harness-authored; config bodies/credential headers are
  not recorded. inspect_ledger.py checks surviving records read-only.
- UI displays the selected model's exec policy and keeps child details expanded
  during live updates. Receipt guidance explains that exec tool results may need
  JSON parsing before extracting ledger references.

Peer attribution: claude-codex supplied the fake provider implementation (copied
into our task with attribution), the real-transport test technique, shared agent
and bounds-selected tool ideas. gpt-anthropic supplied the filtered-listing,
immutable grants, full-read gate and ledger-inspector patterns. claude-anthropic's
AgentCore/event-bus/config-file separation informed the shared runtime and semantic
event changes. No peer files were edited. Prior task directories remain records.

## Verification receipts

[checked: `python -B -X utf8 -m unittest discover -v`, task-5 directory]
45 checks passed in 1.754 seconds. After later inspector/UI/prompt refinements,
the ten targeted improvement checks passed again (0.160 s), and final provider
capture, inspector and fresh UI checks passed. The original 35 tests retain their
ledger/tool/lifecycle coverage; their fixture explicitly bypasses the onboarding
gate where that gate is unrelated to the tested behavior. Dedicated gate tests
exercise refusal, ordered pagination, changed-file detection and completion.

[checked: real App Server, local fake Responses provider]
Installed binary: codex-cli 0.155.0-alpha.9.2.
- gpt-6-astra, isolated home: surface-20260925t211205z-8a347eb9.
- gpt-6-astra, saved user configuration: surface-20260925t211319z-c238e3d7.
- gpt-5.5, isolated home: surface-20260925t211320z-24c7d867.
- Final gpt-6-astra saved-config capture (including raw text links and correctly
  authored scripted kickoff): surface-20260925t212346z-838a3d48.
The astra captures offered functions.exec with our registered tools and runtime
helpers. No native collaboration, shell/patch, browser or external MCP tools were
listed. The gpt-5.5 capture offered direct tools and confirmed exec host false.
All four completed onboarding and produced add's Python callback result 42.
Actual offered tools, raw calls and outcomes are recorded in their ledgers.

[checked: live browser, then inspect_ledger.py --closed plus targeted scribe reads]
Live ledger: chat-20260925t211858z-4c3fe3bd/20260925T211858Z.ledger.
- Parent gpt-6-astra and child gpt-5.5 each completed onboarding; independent
  runtime evidence reports exec host true and false respectively.
- Child bounds were precisely those requested. fs_write and subagent tools were
  absent from its dynamic inventory. fs_list of exact nimoi:/bootstrap-harness
  returned entries [], excluded_count 0 and next_offset null.
- Child add returned 42 and approved smoke.py returned exit 0. Its protected
  output body and agent/review-child/report were read by the parent.
- The parent materialized review.txt from a pinned ledger revision; actual file
  SHA-256 matched the recorded body. Event sequences: child start 140, parent
  file-write completion 149, child finish 324. This demonstrates overlap.
- 23 tool calls, including two correct refusals for malformed reference objects.
  17 raw model-call records; observed call names were exec and supplied harness
  tools, with no native collaboration call.
- Five completed message envelopes and 11 compact checkpoints. Parent usage
  153,335 tokens; child 81,399, as runtime token accounting, not subscription cost.
- Browser refresh retained the session; screenshot at 1280x720 inspected. End
  session closed both App Server processes normally (exit 0, no forced stop).
  Inspector: closed true, leased false, no problems; materialization/output/
  authorship/child references checked. This was a completed-child shutdown;
  active cancellation remains covered by deterministic offline runners.

DISCREPANCY: initial refactor verification | expected tests import and exercise the child lifecycle | found one indentation error and missing onboarding files in synthetic child fixtures; fixed, then all 35 retained tests passed | 2026-09-25.
DISCREPANCY: imported TestCase in test_improvements | expected ten new checks only | found unittest also collected the imported 16-test class; switched to module import, final discovery 45 distinct checks | 2026-09-25.
DISCREPANCY: peer native-tool inference | expected our astra capture might expose collaboration based on the peer's same-version report | found none under our current isolated or saved-config restrictions; configurations differ and the specific controlling difference was not isolated | 2026-09-25.
DISCREPANCY: live ledger chat-20260925t211858z-4c3fe3bd | expected usable references from the pilot's first tool chain | found it treated JSON-text receipts as objects, causing subagent_start/fs_write refusals; explicit-reference retries succeeded, failures preserved; added prompt guidance | 2026-09-25.
DISCREPANCY: live pilot's intended overlap | expected parent addition/note during the child run | found those preceded successful child creation after its first refusal; parent file materialization did occur between child start/finish (140 < 149 < 324) | 2026-09-25.
DISCREPANCY: one targeted unittest command | expected task-local module import | found invocation from the repository root could not import test_improvements; reran from task-5 directory, ten checks passed | 2026-09-25.

## Limits and handoff

Do not generalize these captures to every model/runtime version. Runtime helpers
(clock/goals/input requests/skills) can remain offered; our fs/ledger bounds govern
our tools, not the V8 host or those built-ins. Approved Python scripts are trusted
normal-privilege processes, not sandboxed arbitrary code. Raw monitoring cannot
promise an unexpected native call is prevented before effects. Flags/catalogue
metadata, captured offers and observed calls remain distinct evidence categories.

[checked: launcher output and browser AX] Fresh final driver ready at
http://127.0.0.1:57406/ with ledger chat-20260925t212502z-3785c800. No prompt sent
to that fresh conversation. All remaining changes after the live run were the
read-only inspector, UI policy label, receipt guidance and documentation; final
capture/targeted tests/fresh UI checks cover the affected deterministic behavior.

Status: completed at the stated bar; ready for founder review. No git commands.
