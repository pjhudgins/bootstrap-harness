# Task 5: bounded tools and harness-owned subagents

Bar: useful parent/child behavior, informative failures, and surviving ledger
records. These prototype restrictions are not adversarial OS isolation.

Task 5 retains task 4's single conversation UI, ChatGPT authentication, addition,
ledger notes and histories, attributed transcript bodies, streaming, tool records,
token usage, shared account limits, and ledger download. Earlier tasks are intact.

## Run

With the existing ChatGPT-authenticated Codex executable on PATH and Python:

```powershell
python bootstrap-harness/gpt-codex-harness/task-5-subagent/serve.py
```

`--no-browser` prints the address without opening a browser. `--port 8765` selects
a fixed port; otherwise a free loopback port is chosen. Existing normal-terminal
authorization for harness drivers applies. No API-key fallback or credential
extraction is used, and persistent Codex settings are not changed.

Each launch creates one conversation, one shared ledger and a fresh directory
under `workspace/`. The parent inherits the configured model. The UI shows parent
usage, child states/models/usage, tool activity by actor, and effective bounds.
Refresh retains the active session. End session or Ctrl+C stops parent and
children before closing the ledger. Closing a tab alone does not stop the driver.

## One bounds notation

Both parent and child receive the same validated JSON object. This is the parent:

```json
{
  "fs": {
    "read": ["nimoi:/"],
    "write": ["workspace:/"],
    "execute": ["scripts:/"]
  },
  "ledger": {
    "read": ["/"],
    "write": ["agent/"]
  }
}
```

- `[]` denies. A trailing `/` selects a subtree; otherwise the selector is an
  exact file or ledger name. There are no globs. Ledger `/` means all names.
- `nimoi:/` is the NIMOI root; `workspace:/` is this launch's fresh workspace;
  `scripts:/` is the task-5 harness scripts directory. The injected settings show
  their concrete locations. File tools also accept NIMOI-relative and in-root
  absolute paths. Bounds themselves use mount notation.
- A child's grants must each fit a parent grant. Filesystem comparisons account
  for mount aliases and path segments. Write and execute selections cannot overlap.
  Paths are checked again at use; links/reparse points, hard links for file access,
  traversal, special/credential/runtime locations and raw ledger files are refused.
- Ledger bounds select names, not authors or arbitrary query expressions. Existing
  protected-author/tag rules apply in addition to name bounds. Reading permission
  is required independently of writing permission.
- Every child must have `nimoi:/origins/` in its read coverage to finish mandatory
  onboarding, and read access to its instruction entry. Invalid requests are
  refused; the harness does not silently enlarge permissions.

Use narrow selectors such as `workspace:/drafts/`, `scripts:/smoke.py`, and
`agent/review/` when those are sufficient. Empty write/execute lists give a child
no filesystem write/execute tools. Visible tools still refuse targets outside their bounds.

## Ledger-to-file writes

`fs_write` takes `source: {name, id}`, `path`, and `expected_sha256`.

The source must be a readable **exact revision** whose body is text. Its body is
written as UTF-8, with provenance recorded in the ledger. A later revision of the
source does not change the selected body. The destination must fit `fs.write`.

Use `expected_sha256: null` to create a new file. Replacing an existing file
requires its current SHA-256, obtained from `fs_read`; a stale hash is refused.
Parent directories are created as needed within the target path. Writes are
limited to 2 MB. A disk failure may leave a partial file, with the attempted write
recorded; no atomic replacement or rollback is promised.

Agents can draft Python in `workspace:/`. They cannot write into `scripts:/`,
change harness code, or promote drafts through any supplied tool.

## Approved Python execution

`python_execute` takes `path` and a fresh `output_name` under `agent/`. The output
name must fit both the caller's ledger read and write bounds. It is reserved and
protected before execution, and cannot be reused as a second execution target.

Only a `.py` file in `fs.execute` can run. There is no code-string, argument,
environment, shell, or working-directory override. `scripts:/smoke.py` is the
supplied safe example: fixed arithmetic and JSON output, with no file operations
or subprocesses. Isolated Python import mode and disabled bytecode writes keep
workspace drafts out of the normal import path. The child process receives a
small OS environment without the driver's account/API credentials.

Execution has a 30-second deadline and a 128,000-byte cap per output stream.
Cancellation, timeout and overflow stop the process. Captured stdout, followed
by a labeled stderr section when present, becomes a plain-text ledger body with
author `gpt-codex-harness` and protected `script-output` tags. Metadata records
the script hash, output reference, return code, termination reason and truncation.
Read the output through `ledger_read`; a nonzero exit is a recorded failed run.

**Trust boundary:** approved scripts run with the driver's normal process rights.
They must be reviewed programs that do not load/evaluate agent drafts or create
subprocesses. Directory separation and import settings do not sandbox malicious
approved code. Human promotion/review is external to this prototype.

## Custom children

`subagent_start` takes `model`, `instructions: {name, id}`, and `bounds`.

1. The parent writes a brief to its ledger, including a `Bar:` line, and retains
   the returned name/id. The brief must be authored by that parent.
2. It chooses a model from the account's discovered catalog and supplies complete
   bounds. Example for a child allowed to draft and run the smoke script:

   ```json
   {
     "fs": {
       "read": ["nimoi:/origins/", "workspace:/child/", "scripts:/smoke.py"],
       "write": ["workspace:/child/"],
       "execute": ["scripts:/smoke.py"]
     },
     "ledger": {
       "read": ["agent/child-brief", "agent/child/"],
       "write": ["agent/child/"]
     }
   }
   ```

3. Start returns a child id promptly. The parent can continue independent work.
   Use `subagent_status` with that id and optional `wait_seconds` from 0 to 20.
   On completion, read the returned ledger references for the actual replies.

The harness creates an independent App Server connection and ephemeral thread
for each child, injects the same permission notation, and supplies the pinned
brief body as its one turn. Each child reads onboarding before substantive work.
No native Codex subagent or desktop task API is used. The requested model must be
available and must match the model actually returned by the runtime.

There are at most two active and six total children per launch. Children get one
turn and cannot delegate further. A child failure is reported to the parent/UI;
a ledger failure stops the whole family. Status is not completion until cleanup
has run. A shutdown that cannot stop a child leaves the ledger open for review.

## Authors, shared records, and access

The parent author remains `gpt-codex-test-pilot`; children are attested as
`gpt-codex-test-pilot-child-0001`, etc. Human text remains
`human-user-of-session`. One scribe serializes the shared ledger's writes.

Each protocol/tool record identifies its actor. Child messages and fragments have
the child author; instruction messages reference the parent-authored brief, not a
fictional human message. Completed replies have harness message records with
wikilinks and exact text identities. Parent and child histories do not collide.

Agent notes remain under `agent/`, with automatic `agent` plus optional `agent-*`
tags and current `prev` required for revisions. Agents cannot revise another
author's notes or any protected transcript/output/harness entry. Ledger reads,
lists and histories apply read bounds before returning content. Direct `.ledger`
file reads and ledger directories are excluded from filesystem tools to prevent
that route around narrower ledger access. Existing transcript and redaction rules
are inherited from task 4; old ledgers are not migrated.

Script outputs and some other harness records can legitimately lie outside a
child's read grants. Only its explicitly readable entries are available through
ledger tools. The human's local UI and harness supervisor can inspect the full
session record. Bounds govern agent tool access, not host-process isolation.

## Limits and verification

Rules.md 5g allows Codex JavaScript `exec`. Model choice is preserved. The model
catalogue determines whether its dispatch host is needed: it stays enabled for
code-mode-only models and is disabled (then checked per thread) for direct-tool
models. Unknown catalogue metadata keeps dispatch available and records the
uncertainty. These are runtime requests and observations, not an isolation proof.

The harness disables native shell/patch access, execution environments, external
MCP servers, apps, plugins, hooks, browsers and native delegation where supported.
Every approval request is declined. It requests raw model events so JavaScript
calls are visible, and stops the family after observing a call outside its approved
set. This monitor is **after model output**, not a pre-execution interception hook.
Native collaboration is forbidden even if a future model/runtime offers it.
Built-in clock, goals, input-request and skills helpers may remain offered; our
filesystem/ledger bounds govern our tools, not those helpers or the V8 runtime.
Do not interpret those bounds as OS containment.

`audit_surface.py` runs the real App Server against a local scripted Responses
provider. It saves tool names, raw calls and callback receipts to the ledger; no
model-service request or API key is needed. It can load saved settings without
logging config bodies or authentication headers. This follows the peer
`claude-codex-harness` fake-provider approach (source attribution in fake_model.py).

```powershell
python -B -X utf8 audit_surface.py --model gpt-6-astra --real-config
python -B -X utf8 audit_surface.py --model gpt-5.5
python -B -X utf8 inspect_ledger.py <launch-name> --closed
```

[checked: captures on codex-cli 0.155.0-alpha.9.2, 2026-09-25] Our gpt-6-astra
captures included exec plus the harness tools, but no native collaboration,
shell/patch, browser or external MCP tools. A gpt-5.5 capture offered direct tools,
with exec host confirmed off. Both performed full onboarding and a real add
callback returning 42. The peer's earlier capture *did* expose collaboration;
that discrepancy is preserved in mem/task-5-improvements.md. Recheck after runtime
or model changes; a catalogue or disabled flag alone does not establish the offer.

File-path checks do not prove safety against hostile concurrent filesystem
mutation. Approved scripts are trusted. Known credential redaction is not a
universal secret detector. Keep secrets out of messages and notes. Account windows
are shared across parent, children and other Codex work; per-agent token counts
are not a subscription-cost allocation.

```powershell
python -B -X utf8 -m unittest discover -s bootstrap-harness/gpt-codex-harness/task-5-subagent -p "test*.py" -v
```

The tests use the real scribe, temporary fixtures, real bounded Python execution,
and fake child model connections for lifecycle assertions. Forty-five checks
passed; the ten new checks cover the review changes. App Server requests retain a
180-second deadline. Original receipts are in `../mem/task-5-record.md`; review
changes, live receipts and discrepancies are in `../mem/task-5-improvements.md`.

App Server model selection and discovery follow the
[official documentation](https://learn.chatgpt.com/docs/app-server) and were
checked against schemas generated by the installed executable.


## Peer-review improvements (2026-09-25)

Bar: informative prototype failures, clear ownership, and records that survive.

`bounds.json` is the human-editable parent configuration. It uses the same mount
notation as tool calls, prompts and child bounds. It can narrow the fixed task-5
ceiling (NIMOI reads, workspace writes, scripts execution, agent/ ledger writes);
an expansion is rejected before opening a ledger. Parsed bounds are immutable.
`--bounds <file>` selects another configuration at launch. Agents cannot edit this
file through their workspace grant. Each launch has its own runtime folder.

Parent and child agents share `AgentRuntime` for startup, turns, usage and cleanup.
`AgentRecord` groups each child's status, worker and completion event. Transport
notifications are interpreted once in events.py; the UI consumes semantic events.
Completed message envelopes populate a result index as they commit, so child
completion does not rescan the ledger. Authored message text is also substituted
with links in raw model response records.

The registry omits tools whose grant is empty. Every handler still checks bounds.
A per-agent gate opens substantive tools only after a contiguous full read of the
selected onboarding file. An edit between pages resets that progress. Filesystem
listings filter each child before counts and pagination; an exact-directory grant
alone reveals no descendant names.

### Streaming record modes

The default `--stream-log compact` batches unfinished agent text into authored
checkpoints at 2,048 characters or on a reader tick after one second. Completion,
failure and orderly shutdown flush pending text. Long synchronous callbacks can
delay the time-based flush. The UI can show provisional text before a checkpoint;
an abrupt process death may lose the uncheckpointed suffix (fewer than 2,048
received characters per open message), plus data not yet received by the driver.
Complete messages still have their text body followed by a harness message link.
Reasoning/plan delta notifications are opted out; completed items and raw tool
calls remain recorded. All retained records stay in the ledger.

Use `--stream-log detailed` for a protocol investigation: each received agent delta
gets its own authored fragment and protocol entry; reasoning/plan deltas are not
opted out. Neither mode claims power-loss durability beyond scribe's behavior.

The read-only inspector checks scribe findings, closure/lease state, authored text
references, child start/finish pairs and bounds, script output protection, and
materialization hashes against their exact ledger source. It reports token usage,
refused tools and whether parent tool work occurred while a child ran. It reads
neither credentials nor lease contents, and changes nothing.
