# Task 4: ledger-backed test pilot

Bar: a useful prototype with informative failures and truthful surviving records,
not production readiness or an adversarial security audit.

From this directory, in a normal terminal:

```powershell
python -X utf8 app.py
```

Uses the existing Claude Agent SDK 0.2.101 and CLI authentication. The driver
binds only 127.0.0.1 on an available port, prints the URL, and opens the browser.
`--no-browser`, `--port PORT`, and `--model MODEL` work as in task 3. The default
model alias is `sonnet`. No new dependencies were installed.

One process launch creates one fresh conversation and ledger. Refreshing the
browser preserves that live conversation. Restart to start over. **Close session**
or Ctrl+C stops it, allowing an active reply to finish within its 240-second
timeout. Closing the browser tab alone does not stop the Python process.

## Tools and instructions

| Tool | Capability and boundary |
| --- | --- |
| `add` | Add two finite Python numbers. |
| `fs_list` | List direct children of a NIMOI-relative directory; lexical order and pagination. |
| `fs_read` | Read UTF-8 text by line, with continuation; at most 2 MB per file and 30,000 characters per response. |
| `ledger_read` | List this chat's entries (optionally by tag), or read the current body, id, author and tags; large bodies are paginated JSON-text slices. |
| `ledger_write` | Create/revise a `pilot/` note with restricted tags, fixed author and an exact current `prev` id for updates. |

No built-in tools are loaded. Strict MCP config permits only this in-process
server. The PreToolUse hook and permission callback restrict names/argument shapes;
the Python implementations enforce file boundaries and ledger protection. Agent
tool identity is never accepted as an input field.

The system prompt identifies the agent as a NIMOI **test pilot**. It must read the
lexically latest `origins/onboarding_*.md` at the start of its first user-triggered
turn, follow the user's direction, not initiate tests, and report harness/tool
observations verbosely. It must not write files or execute code; its own ledger
is the sole writable surface. Launching the UI alone sends no model request.
The agent can raise issues and ask for guidance when work is malformed/impossible.

## Ledger layout and authority

The shared module is imported directly from
`nimoi/bootstrap-ledger/python-scribe/scribe.py`; importing does not write a cache
there. It is not copied, edited, or reimplemented. The adapter records with the
module's current standard (`wiki_ledger_v0.4` at implementation).

Each launch creates:

```text
ledgers/chat-<UTC>-<unique suffix>/<UTC>.ledger
```

This is a new ledger directory with one scribe session file, matching the scribe
format. The same scribe stays open across conversation turns. It writes a trailer
and releases its lease on clean shutdown. Task-local `.gitattributes` marks ledger
bytes `-text`, and `.gitignore` excludes `lease.json`. No git commands are used.

| Writer | Author | Names | Tags |
| --- | --- | --- | --- |
| Harness | `harness` | `harness/00000001`, etc., never reused | `harness`, `log.<event-kind>` |
| Agent | `agent.claude.test-pilot` | `pilot/<name>` | `pilot.note`, `pilot.observation`, `pilot.question` only |
| Session user text | `human.session-user` | `messages/user/<counter>` | `message.text`, `message.user` |
| Agent reply text | `agent.claude.test-pilot` | `messages/assistant/<counter>` | `message.text`, `message.assistant` |
| SDK error text | `harness` | `messages/runtime/<counter>` | `message.text`, `message.runtime` |

As of 2026-09-25, user submissions and each assistant text block are written first
as string bodies (with existing credential redaction). The following harness-authored
`log.message` record retains metadata and substitutes `[[messages/...]]` for each
text. Its `text_entries` lists the name, exact body ID, author and link. User
acceptance/send-attempt records also reference that same text. A repeated final
SDK result reuses the reply link when its text matches. Tool results remain
harness diagnostics; an SDK `UserMessage` is not assumed to be human-authored.
Text classification tags are written by the harness. Message names are outside
the agent's writable `pilot/` namespace and are never revised by this adapter.
Each text block remains separate, preserving order relative to tool calls.

These links sit in JSON string fields. W§14 leaves recursive interpretation of
links inside JSON bodies unresolved, and the current shared scribe does not expand
links. Consumers can use `text_entries` for explicit resolution. This storage
change does not promise automatic transclusion in the shared reader.

For example, messages carry `log.message`, tool requests `log.tool_request`,
Python results `log.python_tool_result`, and usage `log.usage`. Logs include
accepted messages, attempted/completed sends, SDK messages, tool requests/results,
usage/rate events and lifecycle/errors. Agent body strings are content, never
authority metadata. There is no author override, tag removal, deletion, or
cross-ledger write tool. Updating requires a prior read's exact id; stale ids are
refused. Namespace, author and tags each protect harness entries. History remains
append-only, including revisions of the agent's own notes.

Body and tag appends are individually durable but **not one atomic operation**.
A failure preserves partial records and stops further use of the adapter. No
JSONL fallback is created. If writing to the ledger is unavailable, a short UI/
stderr notification is the remaining failure channel. Do not remove a stale
lease automatically; the shared scribe's instructions require human review.

## Read boundary and UI

Filesystem paths are relative to NIMOI. Secret filenames, metadata/runtime
directories, absolute/UNC/device/alternate-stream paths, traversal, symlinks,
reparse points and multiply-linked files are rejected. Listing omits protected
entries. File results are redacted before reaching the agent. Known credential
fields/values and recognizable token strings are redacted from ledger and UI too;
this is not a universal detector for arbitrary credentials pasted into text.

The server has no per-user authentication; local programs can access it. It checks
Host/Origin, requires same-origin JSON for changes, and serves no external assets.
The file policy assumes other local processes do not maliciously replace paths
during a read. These are prototype controls, not a hardened security boundary.

The chat retains task 3's usage and activity panels. Input totals include uncached,
cache-read and cache-creation tokens; cost is cumulative SDK accounting, not a
measured subscription charge. Unknown quota utilization stays unknown. Markdown
is displayed as plain text. Each user submission allows at most 16 SDK turns and
240 seconds, with no hard monetary ceiling.

## Verification and extension points

```powershell
python -X utf8 -m unittest discover -v
```

Offline checks use the real scribe for append/history/tag/lease validation,
filesystem fixtures for read bounds, and HTTP requests for the local UI boundary.
No model calls occur in these tests. Detailed live evidence and failures are in
`../mem/task-4-record.md`.

Verified 2026-09-25: 16 offline tests pass, including text authorship, metadata/link
ordering, multiline and Unicode preservation, result-link reuse, credential
redaction, runtime/tool attribution and failure between text and metadata writes.

Verified 2026-09-24: 13 offline tests pass. A two-turn browser test exercised all
five tools, completed onboarding v1.12, created/read/revised `pilot/smoke`, and
attempted an overwrite of `harness/00000001`. The attempt was refused and the
protected entry remained unchanged. The closed ledger passes the shared scribe
validator with no findings and its lease is gone. Recheck the preserved evidence:

```powershell
python -X utf8 inspect_ledger.py chat-20260924t202304z-faad61817f --smoke
```

Onboarding order is prompted, not mechanically enforced: the pilot ran addition
before finishing onboarding in that test. It also misidentified the underlying
runtime, then corrected its claim in the second turn and revised its note. Both
original and corrected records survive. Final cumulative SDK cost was $0.20476.
Live tests used the core adapter/tools before the later `run/` directory deny,
credential-shaped name check and explicit `Bar:` prompt wording. The two new
denials passed offline tests; the handoff process loads the final code.

`app.py` owns local HTTP/lifecycle; `conversation.py` owns the SDK session;
`tools.py` registers and dispatches capabilities; `filesystem.py` enforces reads;
`ledger.py` adapts the shared scribe; `redaction.py` handles known-secret filtering.
The browser view stays in HTML/CSS/JS. Task-1 through task-3 files remain unchanged.
