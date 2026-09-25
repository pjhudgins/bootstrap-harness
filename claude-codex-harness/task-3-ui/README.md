# task-3-ui (claude-codex-harness)

A local web page for one conversation with the restricted Codex agent from task 2.
Every launch starts a new conversation. Python 3.10+ standard library only; it uses
the saved Codex login in `~/.codex`.

```powershell
python ui.py                 # new conversation; opens your default browser
python ui.py --fake-model    # scripted local stand-in model: no tokens, for trying the page
python ui.py --no-browser --port 8765
python -m unittest discover -s tests -t .   # 7 tests, including real Codex + fake model
```

Finish with the page's **End conversation** button (or Ctrl+C in the terminal). That
closes the app-server and completes the journal. Closing the tab alone leaves the
conversation running. Other options: `--model`, `--codex`, `--codex-home`.

## What the page shows
- The conversation: your messages, the agent's replies streamed as they are written,
  Python tool calls (`add` with its arguments and result), each tool call the model
  makes (unreviewed ones in red), declined approvals, turn status. **Stop**
  interrupts a running turn.
- Side panels: session (model, account type, thread, journal path); restrictions
  (the model's tool mode, MCP servers, features, approvals and sandbox); tools
  (ours, the reviewed list, and with `--fake-model` the exact tools Codex offered);
  token usage; rate limits.

## Task-2 capabilities, all kept
Everything below is copied from `../task-2-tooling` (rules.md: a task's code lives
in its folder): `codex_client.py`, `tools.py`, `policy.py` (policy v2: `gpt-5.5`,
20 features off, MCP servers off, no environment, decline-all approvals, read-only
sandbox, raw events), and `fake_model.py`. Each copied file notes its origin.
Every launch writes one append-only journal, `runs/<UTC stamp>-ui[-fake]-<random>.jsonl`,
with the same record kinds as task 2. That includes every protocol message,
`tool_call`, `model_call`, `usage`, `rate_limits`, `restrictions`, and
`codex_home_changes` (the founder's condition for live runs).

## Design
- `conversation.py`: the UI-independent core. It owns the app-server and the thread,
  and runs turns on one worker thread (the only reader of the app-server after
  setup). It journals everything and publishes typed events on an in-memory
  `EventLog`.
- `ui.py`: `http.server` on 127.0.0.1. It serves `static/` and streams the event log
  to the page over Server-Sent Events, replayed from the start, so a reload or
  reconnect loses nothing. It takes POSTs for send, interrupt and end.
- `static/app.js`: one renderer per event type; unknown types are shown as a generic
  line. **To extend:** publish a new event type in `conversation.py` and add a
  renderer. Model text is only ever set as text, never as HTML.

## Local safety
Bound to 127.0.0.1. Requests with any other `Host` are refused (DNS rebinding). The
API needs a per-launch token that is embedded in the page, which other websites
cannot read (CSRF). A strict Content-Security-Policy is set: no inline script, and
nothing loaded from elsewhere. The token is never printed or journaled. This is not
an authentication system: anything running as you on this machine can still read
the page.

Results and findings: `../mem/task-3-record.md`.
