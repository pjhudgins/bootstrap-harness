# Message text bodies — 2026-09-25

Bar: readable message records with truthful authorship, preserving diagnostic
evidence and existing session files. User requested separate text bodies and
harness message records with wikilinks to them.

Implemented in `task-4-ledger/ledger.py` and `conversation.py`:

- User submissions first create string bodies under `messages/user/`, authored
  `human.session-user`. The harness then writes a `log.message` record whose text
  field is `[[messages/user/...]]`. Acceptance and send-attempt events reuse it.
- Each assistant text block creates a string body under `messages/assistant/`,
  authored `agent.claude.test-pilot`. Its SDK message follows with text fields
  replaced by wikilinks, leaving model, tool-use and usage metadata intact.
- `text_entries` records exact IDs as well as names/links/authors. Text entries
  are create-only in the adapter and outside the agent tool's writable namespace.
- Known SDK error text uses `messages/runtime/`, authored `harness`. Tool-result
  SDK UserMessages are not mistaken for human submissions. Known echoed human
  text and matching final SDK results reuse the original text reference.
- Tags (`message.text` and the role-specific tag) are harness-authored metadata.
  Existing redaction is retained; the displayed text and raw text sent to the SDK
  are not replaced by links. Paragraphs and Unicode survive in text bodies.
- A failure during any text/tag/metadata write stops further writes, preserving
  partial evidence and the lease. No existing ledger is migrated or rewritten.

Consequential interpretations: retain one text entry per SDK TextBlock, so tool
ordering and intermediate assistant prose survive; agent identity remains this
session's test pilot. Unmatched runtime result fields remain diagnostic data.
The human author is an anonymous session role, not an identified person.

[checked: `python -B -X utf8 -m unittest discover -v` in task-4-ledger]
16 tests pass. New checks use the real scribe to reload and resolve references,
verify body authors/order/Unicode, preserve tool and usage metadata, reuse final
result links, reject message rewriting, check redaction/runtime attribution, and
simulate failure between text and metadata. HTTP test checks submission records
and confirms the queue still receives actual prompt text.

The shared reader renders JSON bodies as escaped JSON, whereas string bodies
receive clickable wiki links [checked: bootstrap-ledger/reader/server.py body_html].
Thus these stored JSON-field wikilinks are valid references but not clickable in
that reader yet. W§14 leaves links inside JSON bodies unresolved. Explicit
`text_entries` receipts let a consumer resolve them without depending on that
future rule. No changes made to the shared reader or standard.

DISCREPANCY: task-4-ledger logging before this change — expected direct human/agent-authored text bodies per user request, found text embedded in harness-authored event JSON; corrected for future sessions without rewriting historical files; 2026-09-25.

[checked: inspect_ledger.py] Previous session `chat-20260925t130116z-ce16642659`
closed cleanly with no findings or lease. [checked: launcher and browser] Updated
UI runs at http://127.0.0.1:57738, PID 16300, conversation 83151704, fresh ledger
`chat-20260925t134206z-9afbffcf4d`. Browser Ready; no model prompt sent during this
change's verification. Actual model traffic with the new format remains for the
user's next turn; offline tests cover the logging transformation.

State: completed — requested logging implemented, tested and loaded by the UI.
