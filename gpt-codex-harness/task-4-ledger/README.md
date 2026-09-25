# Task 4: ledger-backed test pilot

Bar: a useful NIMOI prototype whose failures are informative and whose records
survive. This is not production isolation.

Preserves the task-3 conversation UI, ChatGPT authentication, Python addition,
tool restrictions, streamed messages, and token/account limits. Task-4 records
go through the existing bootstrap-ledger scribe into a fresh ledger per launch.
The pilot also has session-ledger tools and bounded filesystem reads.

## Run

From the NIMOI root in a normal terminal, with Python 3.10+ and an already
ChatGPT-authenticated `codex` executable on PATH:

```powershell
python bootstrap-harness/gpt-codex-harness/task-4-ledger/serve.py
```

The driver prints its loopback URL and ledger path and opens the default browser.
Use `--no-browser` to open the printed URL yourself, or `--port 8765` for a fixed
port. Existing authorization for normal-terminal harness-driver execution in
this swimlane applies. No packages or persistent Codex settings are installed.

Wait for **Ready**, then send a message. One turn runs at a time. Enter sends;
Shift+Enter inserts a newline. Refreshes and tabs share that launch's conversation.
**End session** or Ctrl+C closes the driver and ledger. Closing a browser alone
leaves the driver running. Relaunch creates a new conversation and ledger.

The pilot reads the latest `origins/onboarding_*.md` on its first user turn.
`system_prompt.md` is supplied through App Server `baseInstructions`, reinforced
by `developerInstructions`. It names the NIMOI/test-pilot role, requires verbose
observations and truthful failures, forbids self-initiated tests, and permits
ledger writing while forbidding code execution and filesystem writes. The
driver starts no model turn until a user submits a message.

## Ledger storage and identities

The importer loads the exact sibling file
`bootstrap-ledger/python-scribe/scribe.py`. Keep the NIMOI directory layout;
there is no copied scribe or package fallback. Bytecode writes are disabled
while importing that sibling. Its version and source hash are recorded at startup.

Each launch exclusively creates:

```text
task-4-ledger/ledgers/chat-<UTC time>-<unique suffix>/<scribe stamp>.ledger
```

The scribe adds a temporary `lease.json` alongside the file. One launch uses one
scribe and one ledger file; there is no resume or roll. The swimlane's
`.gitattributes` adds `*.ledger -text`, and `.gitignore` excludes leases and runtime
databases. No git commands are performed; the human owns git.

| Record | Attested author | Names | Tags |
| --- | --- | --- | --- |
| Harness events | `gpt-codex-harness` | `harness/events/<sequence>` | `harness`, `log-<kind>`, plus applicable category tags |
| Human message text | `human-user-of-session` | `messages/human/<sequence>` | `transcript`, `protected`, `message-text`, `from-human` |
| Agent message text | `gpt-codex-test-pilot` | `messages/agent/<sequence>` | `transcript`, `protected`, `message-text`, `from-agent` |
| Streamed reply fragments | `gpt-codex-test-pilot` | `messages/fragments/<sequence>` | `transcript`, `protected`, `message-fragment`, `from-agent` |
| Agent notes | `gpt-codex-test-pilot` | `agent/<name>` | `agent`, plus chosen `agent-*` tags |

Category tags include `message`, `tool`, `usage`, `account-limits`, `protocol`,
`lifecycle`, `restriction`, and `error`. The event body retains the original
kind/data and a monotonically increasing harness-event sequence. These category
tags supplement `log-send`, `log-receive`, `log-python_tool`, and other exact kinds.

Message text is a plain string body attributed to its speaker. The harness
writes that text first, then a `kind: message` event linking to it, for example:

```json
{"author":"human-user-of-session","name":"messages/human/00000001","body":"Hello"}
{"author":"gpt-codex-harness","name":"harness/events/00000001","body":{"sequence":1,"kind":"message","data":{"source":"user_message","message_id":"ui-message-id","text":"[[messages/human/00000001]]","text_id":"20260925T140000Z:6","author":"human-user-of-session","direction":"from-user","complete":true}}}
```

This abbreviated example omits timestamps and preceding tag lines. The exact
text entry id is recorded alongside the wikilink. Completed commentary/final
replies use the same pattern with direction `to-user` and the agent author.
Known message fields in protocol records contain wikilinks as well. UI and wire
messages still contain real text. Protocol echoes reuse references using message
identity, not just matching text; two identical human submissions remain two
authored entries. Text entries are immutable and protected from agent edits.

Stream deltas each get their own authored fragment body before the corresponding
protocol event. A completed reply additionally gets its full-text entry; deltas
are not presented as completed messages. If a turn is interrupted, its fragments
remain. A failure between text and metadata can leave an unreferenced text entry;
the driver stops rather than rolling back or rewriting evidence. Earlier ledgers
retain their original format. Startup records `authored-text-links-v1` for new runs.

System instructions, reasoning, tool arguments/results, usage snapshots,
failures and exit status retain their harness-authored event treatment. Extraction
only visits defined message slots; tool payloads cannot assert a human speaker.
A deliberate `ledger_write` note is agent-authored. Tools cannot
choose an author, modify harness names, add protected tags, or update an entry
with a protected author/tag. Agent revisions must cite the current `prev` id.
History remains intact; no delete/untag tool is exposed. Tags precede each new
body, so a crash may leave a tagged name with no body. Scribe does not promise
multi-line atomicity; the harness does not pretend otherwise.

**Download session ledger** returns a consistent copy under the writer lock.
A download while the session is active has no closing trailer yet. The final
file on disk gets its trailer on clean shutdown. The UI shows tool calls,
including denials; expand a card for arguments/results (preview capped at
4,000 characters; the complete record is in the ledger).

## Tools

| Tool | Capability |
| --- | --- |
| `add` | Two finite numbers; fixed Python arithmetic, not an evaluator. |
| `ledger_list` | Session-ledger names, ids, authors and tags; filter/paginate. |
| `ledger_read` | Current entry or history, including ids; paged JSON text. |
| `ledger_write` | Create/revise text under `agent/`; `prev:null` for create or current id for update. The harness adds `agent` automatically; including it explicitly is valid. Other supplied tags must start `agent-`. |
| `fs_list` | One NIMOI directory; sorted, paginated names. |
| `fs_read` | Ordinary UTF-8 text under NIMOI, up to 2 MB/file and 24,000 characters/page; reports next offset and source SHA-256. |

Filesystem paths can be NIMOI-relative or absolute within NIMOI. Read tools
reject parent traversal, outside paths, UNC/device/alternate-stream paths,
symlinks/reparse points, multi-linked files, and known credential/runtime
locations. Excluded components include `.git`, `.codex`, `.claude`, `.ssh`,
`.aws`, `.azure`, `.runtime`, `.venv`, `node_modules`, `__pycache__`, `run`, and
`runs`; `.env*`, lease/auth/token/secret/credential filenames and private-key
extensions are also excluded. On Windows the opened file handle's final path
is checked before reading. Lists report an excluded count without returning the
excluded names. These are local prototype checks, not an OS sandbox or a proof
against hostile concurrent filesystem mutation.

Read text is redacted before pagination so credential patterns cannot straddle
page boundaries. Known credential patterns are also redacted from prompts,
tool records and agent ledger text. Full configuration/account identity replies
are omitted as in task 3. Redaction is not a universal secret detector; keep
secrets out of messages and notes. Ledger files contain conversation and
account-limit information and stay local unless the human exports them.

## Failure behavior and inherited limits

A failed ledger write stops work: the driver does not dispatch unrecorded user
messages or continue without logging. The scribe retains its partial file and
lease, and the UI exposes the failure. No replacement JSONL log is created;
a dead scribe cannot persist its own error or final trailer. Review the UI/error
and existing ledger. A human must review a stale lease before removing it,
following the scribe interface's recovery procedure. Never scrub the evidence.

Other task-3 limits remain: App Server requests use a 180-second deadline; one
conversation, plain-text replies, no history UI/resume/attachments/model selector.
The listener binds to 127.0.0.1, restricts routes and validates Host/Origin.
Local processes under the same user are not isolated from one another.

The child requires ChatGPT sign-in and removes API-key environment variables.
It inherits the configured model. Conversation token totals are cumulative;
cached input is included in input. Account limits are shared with other Codex
activity, not a per-chat cost estimate. Missing measurements remain unknown.

**Inherited execution caveat:** the tested runtime reports `unified_exec=true`
despite disable overrides. Shell/code/JS, browser/computer, plugins, apps, hooks,
subagents, MCP and web-search controls carry forward from task 3, with an empty
environment list and read-only sandbox request. `code_mode_host` stays enabled
because disabling it broke dynamic tool dispatch in task 2. Feature checks and
unexpected-tool detection remain; the pilot is not claimed to be securely
isolated against adversarial behavior.

## Verification and extension

```powershell
python -m unittest discover -s bootstrap-harness/gpt-codex-harness/task-4-ledger -p test_task4.py -v
```

Nineteen offline tests use the actual scribe and disposable local fixtures.
They exercise histories, authors/tags, failed writes, path boundaries, streaming,
usage, dispatch, HTTP/ledger download, transcript links and text/metadata ordering.
Live evidence and the first pilot's tag-interface discrepancy are preserved in
`../mem/task-4-record.md`; the message-author change is in `../mem/message-authorship.md`.

`ledger_store.py` owns the scribe and policies; `agent_tools.py` owns tool schemas
and handlers; `session.py` owns the one App Server worker; `protocol.py` handles
transport; `serve.py` and `static/` provide the UI. No changes to bootstrap-ledger
are needed to add another harness tool.

References: `bootstrap-ledger/python-scribe/interfaces.md`, spec v0.3, ledger
standard v0.4, installed App Server schema, and the
[official App Server documentation](https://learn.chatgpt.com/docs/app-server).
