# Task 5: bounded writes, approved Python and parallel children

Bar: useful prototype behavior, informative failures and surviving records.
This is a local testbed, not an operating-system sandbox or production service.

Run `python -B -X utf8 app.py` in a normal terminal with the installed Claude
SDK and existing authentication. Options: `--model`, `--port`, `--no-browser`.
A launch creates a conversation, a fresh scribe ledger and a fresh directory
under `workspace/<ledger-name>/`. Refresh retains the live conversation.
Close session stops admission, cancels active agents, and waits for associated
script/SDK cleanup before closing the shared ledger.

## Explicit grants and paths

The same six-key object is shown to parent and child:

```json
{
  "fs.read": ["**"],
  "fs.write": ["workspace:/**"],
  "fs.execute": ["scripts:/safe_test.py"],
  "ledger.read": ["**"],
  "ledger.write": ["pilot/**"],
  "ledger.tags": ["pilot.note", "pilot.observation", "pilot.question"]
}
```

An exact name selects one object; `prefix/**` selects that prefix and descendants;
`**` selects everything otherwise allowed; `[]` denies the capability.
Filesystem paths accept `nimoi:/`, `workspace:/`, `scripts:/`, or NIMOI-relative
paths. Mounts are fixed for the launch and recorded in configuration.
Filesystem selectors compare case-insensitively on this Windows prototype;
ledger names and tags are case-sensitive. Tags are a finite allowlist.
There are no exclusions, implicit onboarding grants, or general glob patterns.

A child's grants must fit inside the parent's corresponding grants. Diagnostics
identify all grants that exceed the parent. Reads of origins, the latest
onboarding file and the pinned instruction note must be explicitly granted.
Writes stay in this launch's workspace, execution stays in approved scripts,
and ledger writes stay under pilot/. Write and execute grants cannot overlap.

Credential names, metadata/runtime paths, symlinks, reparse points and multiply
linked files remain protected. Raw .ledger files are denied through filesystem
tools so broad file reads cannot bypass ledger.read. Directory and ledger
listings filter inaccessible entries before pagination and counting.
Tools requiring an empty capability are omitted; handlers still enforce bounds.

## Tools and result contracts

| Tool | Contract |
|---|---|
| add | Two finite numbers; Python sum. |
| fs_list | Bounded direct-child listing, up to 100 entries. |
| fs_read | UTF-8 line reads: file <=2 MB, response <=30k characters. Follow next_line. |
| ledger_read | Exact-name fetch or filtered listing; paginated large bodies. |
| ledger_write | Fixed author; own pilot/ text notes and permitted tags. Revision requires current prev ID. |
| fs_write | Pinned readable ledger text ID to a NEW file in an existing workspace directory. |
| python_execute | Approved Python path and fresh output_name; no supplied code, arguments or stdin. |
| subagent_start | Model, pinned instructions_id, all six bounds and fresh result_name. Returns immediately. |
| subagent_status | Owned child's state/reference; wait_seconds 0..10, default 10. |

Before substantive tools, each agent must read the latest onboarding completely
in line order. Tools carry no author override. Agent notes cannot replace another
actor's notes, messages, execution outputs or harness events.

**Execution output:** supply a fresh pilot/ output_name covered by both the
caller's ledger.read and ledger.write. The harness reserves it before launch,
then writes the captured text with harness authorship. Reservation creates a
visible placeholder body; failure leaves evidence rather than a dangling tag.
Returned references contain the name, exact body ID and wikilink.

**Child result:** supply a fresh pilot/ result_name readable by parent and child
and writable by the parent. This is a harness-managed protected destination,
not an expansion of the child's ledger-write grants. Completed prose has child
authorship; cancellation/failure text has harness authorship. The child must not
write the reserved name itself. The instruction note must belong to the parent
and contain a Bar: line. Its exact revision, not its mutable current body, is used.

## Drafts, scripts and shutdown

fs_write remains create-only: no overwrite, directory creation or script
promotion. A later ledger revision does not change the pinned text exported.
Hash-checked replacement is a possible future feature, not implemented here.

scripts/safe_test.py is the approved example. Execution uses isolated Python,
a scripts-directory working directory, and a minimal environment without
inherited credential variables or PYTHONPATH. It merges stdout/stderr, captures
at most 64 KiB, and stops after 10 seconds, overflow, cancellation or shared
ledger failure. The awaiting coroutine waits for the worker's cleanup on
cancellation; terminal child status follows SDK/tool cleanup.

Approved scripts are trusted programs, not sandboxed agent code. They must not
execute drafts, load indirect code inputs, spawn descendants, or exceed their
reviewed effects. Hostile concurrent filesystem replacement and process-tree
containment are outside this prototype's boundary.

Children use independent SDK sessions with readable IDs child-1, child-2, etc.,
and distinct authors. Two may run concurrently; four may start per launch.
Children cannot delegate. No native SDK delegation or default execution tools
are enabled; the initialized inventory is checked. Parent work continues while
children run. Failed/cancelled children, including cancellation before startup,
retain terminal records.

A shared ledger failure signals the entire launch to stop. No fallback ledger,
automatic repair, or close trailer is attempted after failure. A worker that
cannot be joined also prevents ledger closure; preserve its evidence for review.

## Text, UI and usage

Human message bodies are authored human.session-user, parent prose
agent.claude.test-pilot, and child prose its fixed child author. Text precedes
harness message envelopes containing wikilinks and exact IDs. SDK error text is
harness-authored. Repeated SDK results reuse already recorded prose references.
Existing task-4 and task-5 ledgers are not migrated.

The UI labels child messages and shows each child's model, status, instruction ID,
bounds and result. Costs show the parent, each child and the family sum of the
latest known cumulative SDK totals. Pending/missing costs are not counted; these
are reported estimates, not subscription charges or a hard spending cap.
Parent rate/account information remains visible.

Dispatch validation is recorded separately from handler authorization.
tool_denied identifies capability/policy refusals; tool_invalid identifies invalid
arguments; tool_failed identifies filesystem/operational failures. Script results
also report completion, failure, timeout, cancellation or output-limit status.

The server binds to loopback, requires same-origin POSTs, and has no user
authentication. Other local processes can reach it.

## Code map

- bounds.py: parsed selectors, mounts, containment and diagnostics.
- context.py / prompts/: explicit agent identity and readable prompt text.
- access.py: actor-bound ledger permissions; ledger.py: scribe storage/provenance.
- messages.py: SDK message normalization.
- tools.py: one registry for schemas, descriptions, dispatch and availability.
- capabilities.py / filesystem.py: writes, execution and shared path protection.
- runtime.py: one SDK session; subagents.py: typed job state and separate task handles.
- conversation.py: shared launch/UI state, usage and stop propagation.

Peer ideas adapted from GPT/Codex (access, mounts, output reservation, cancellation),
Claude/Codex (bounds diagnostics, scripted boundary tests), and Claude/Anthropic
(prompt files, explicit context, family usage). No shared cross-lane library.

## Verification and evidence

Run `python -B -X utf8 -m unittest discover -v` with the installed SDK.
The scripted runtime tests use real SDK types/options and injected clients:
no model calls. Other tests use the real scribe and local subprocesses.
This does not emulate the SDK transport or replace a live integration check.

`python -B -X utf8 live_smoke.py` is an explicitly directed live parent/Haiku-child
test; it sends model requests and preserves its ledger. It tests nine tools,
expected refusals, parent work during child execution, result readability and
closed-ledger validity. Do not run it as an unattended schedule.

Historical evidence: ../mem/task-5-record.md.
Current refactor decisions, discrepancies and verification:
../mem/task-5-improvements.md.
