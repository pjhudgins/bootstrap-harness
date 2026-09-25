# Task 3: local conversation UI

A Python standard-library web server with one Codex App Server conversation per
launch. Carries task 2 into a small browser UI: messages, Python `add`, tool-call
records, token usage, account limits, and the same execution restrictions.

## Launch

Use Python 3.10+ and a `codex` executable on PATH already signed in with ChatGPT
(`codex login`). From the NIMOI root, in a normal terminal:

```powershell
python bootstrap-harness/gpt-codex-harness/task-3-ui/serve.py
```

The driver chooses a free port on **127.0.0.1**, prints the URL and journal path,
and opens the default browser. `--no-browser` prints the URL without opening it;
`--port 8765` requests a fixed port. No package installation is required.

Wait for **Ready**, then send a message. The suggestion fills an addition prompt;
press **Send** to run it. Enter sends; Shift+Enter adds a line. One turn runs at a
time. Follow-up messages use the same conversation, as do all tabs and refreshes
of that launch. Replies stream as plain text, including any Markdown syntax.

**End session** or Ctrl+C closes the App Server and listener. Closing the browser
alone leaves the driver running. Relaunch creates a new ephemeral thread and a
new journal, without loading old conversation history. Download the journal
before ending, or open the saved file afterward. A failed turn leaves an error
on the page and a retained journal; relaunch to start again.

Normal-terminal execution of harness drivers was authorized for this swimlane.
The parent command sandbox could not start the earlier prototypes because of
Codex runtime writes; see `../mem/task-1-record.md`. This permission does not
change the conversation's own App Server sandbox settings.

## Authentication, restrictions, and records

- Requires App Server `account/read` to report ChatGPT sign-in. The child does
  not inherit `OPENAI_API_KEY` or `CODEX_API_KEY`; there is no API-key fallback.
  Existing Codex authentication is used without extracting credentials.
- Inherits the configured model. Tested with `gpt-6-astra` and installed Codex
  `0.155.0-alpha.9.2`; experimental protocol/config fields may change.
- Same task-2 controls: disable shell/code mode/JS, browser/computer, plugins,
  apps, hooks, subagents, MCP servers and web search for the conversation;
  request read-only sandbox, no approvals, and an empty environment list.
  Startup checks the required feature states and rejects unexpected requests.
- **Known limitation:** this runtime reports `unified_exec=true` despite disable
  overrides. Shell disabled plus empty environments restricted the observed
  runs, but these prototype settings are not a proven security boundary. The
  UI exposes this caveat in Restriction details. `code_mode_host` remains enabled
  because disabling it prevented Python tool dispatch in task 2; code mode
  itself is disabled. No persistent Codex configuration is changed.
- `runs/<UTC timestamp>-<unique suffix>.jsonl` retains incoming/outgoing protocol
  events, user/agent messages, Python arguments/results, usage/limit snapshots,
  errors, and child exit status. Both successful and failed attempts remain.
  Full config and account/read identity payloads are omitted; credential-shaped
  text is redacted before logging. This is not a universal secret detector;
  keep credentials out of prompts. The journal contains conversation content
  and account-limit metadata. Runtime database files stay in ignored `.runtime/`.
- Conversation token totals are cumulative snapshots, replaced rather than
  summed. Cached input is already included in input. Missing values stay unknown.
  Account windows are shared with other Codex activity; their percentages are
  not a per-conversation cost estimate. Full reported metadata remains in the log.

The listener is local, validates Host and mutation Origin, and serves only an
explicit route list. It is intended for one local user, with no authentication
between processes on that user's machine. Message text is rendered with
`textContent`, not evaluated as HTML. No external frontend assets are loaded.

## Extension points

- `protocol.py`: JSON-RPC transport, feature overrides, journal redaction, and
  `TOOLS` registry. Register a schema plus a validating Python handler returning
  a JSON-serializable result. Handlers run in the driver and should be bounded;
  only `add` is currently exposed.
- `session.py`: one protocol worker, serialized user turns, and an observable
  state projection. Idle polling keeps late notifications without expiring the
  conversation merely because the user pauses between messages.
- `serve.py`: local routes: GET `/api/state`, GET `/api/journal`, POST
  `/api/messages` and POST `/api/stop`.
- `static/`: plain HTML, CSS and JavaScript. State polls every 450 ms; message
  deltas update existing nodes. Tool cards support generic JSON results as well
  as the formatted `add` result. Update the static tool label/suggestion when
  changing the registry.

This is a task-3 prototype: one conversation, no history browser, resume,
attachments, model selector, or general-purpose code executor. Active App Server
requests have a 180-second deadline; failures are surfaced rather than retried.

## Verification

```powershell
python -m unittest discover -s bootstrap-harness/gpt-codex-harness/task-3-ui -p test_ui.py -v
```

Eleven offline checks cover turn admission, streaming completion, usage
accounting, independent launches, Python dispatch, quota/transport failures,
local HTTP boundaries, and journal download. They do not call a model.

Live browser verification on 2026-09-23: `19.25 + 22.75` returned 42 via Python;
the follow-up "add 8 to that result" returned 50 on the same thread. Refresh
retained the conversation. A Python execution probe reported no execution tool;
no command/file/MCP/subagent execution item was observed. End session shut down
the App Server with exit 0. Relaunch produced a different thread and empty UI.
Details and exact journals: `../mem/task-3-record.md`.

App Server reference: <https://learn.chatgpt.com/docs/app-server>.
