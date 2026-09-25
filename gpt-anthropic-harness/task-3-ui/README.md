# Task 3: local conversation UI

From this directory, in a normal terminal:

```powershell
python -X utf8 app.py
```

The driver binds an available port on 127.0.0.1, prints its URL and opens the
default browser. `--no-browser` skips browser launch; `--port 8765` selects a
port; `--model MODEL` overrides the default `sonnet` alias. It uses the existing
user-installed Claude Agent SDK 0.2.101 and Claude authentication. No new packages
are required beyond that SDK. Normal terminal permissions are founder-authorized
for this lane; the restricted command environment cannot read the user SDK.

Every process launch creates one fresh SDK conversation and one journal. Browser
refresh, another tab at the same URL, and reconnecting to the running server all
show that same conversation. Restart the driver to start over. There is no
conversation picker or resume-from-disk path.

Send messages with the button or Enter; Shift+Enter adds a line. Responses appear
as SDK text blocks arrive. The UI shows Python tool results, the last prompt's
input/output usage, cumulative SDK cost, and reported rate status. Input includes
uncached, cache-read and cache-creation tokens, with the cache split shown. These figures
are SDK accounting, not measured subscription charges or inferred quota balances.
Use **Close session** or Ctrl+C in the terminal to exit. An active reply may finish
before shutdown; each reply has a 180-second timeout. Closing only the browser
tab does not terminate the Python server.

## Task-2 capabilities retained

- Outgoing accepted messages and attempted/completed sends, incoming SDK messages,
  tool hooks, Python results, usage, rate information and failures are journaled.
- No built-in tools are loaded. Only `mcp__calc__add` is exposed, with numeric
  validation, a PreToolUse hook and permission callback. Strict MCP config excludes
  account connectors; post-response checks require only the calculator server.
- A single long-lived SDK task serializes turns and retains conversation context.
  Errors that can leave context uncertain disable further sending until restart.
- SDK session persistence is off; the harness owns its record under `runs/`.

## Structure and extension points

`app.py` is the loopback HTTP server and launcher. `conversation.py` owns state,
the SDK lifecycle and tool registration. `policy.py` contains tool/argument rules;
`runlog.py` preserves records. `index.html`, `style.css` and `app.js` are the local
view. A new tool should be added to registration, policy, activity display and
verification together. Do not expand tool permissions merely by changing the UI.

The server uses Python's standard library; the browser uses no external scripts,
styles or fonts. Text is rendered literally, so model Markdown remains plain text.
The browser polls state every 700 ms; polling does not request model work.

## Records and limits

Each run journal is exclusively created, UTF-8 encoded, sequence-numbered and
flushed per event. Writes are serialized across HTTP/SDK threads. The journal
filename is visible in the UI. In-memory messages disappear when the process
exits; journals remain. Credentials should never be entered in chat. Known
credential fields/values and recognizable token forms are redacted, but this is
not a universal detector for arbitrary secrets. Raw server account/configuration
is not logged; account metadata is limited to plan/provider.

The server binds only 127.0.0.1, accepts the exact Host and requires same-origin
JSON for mutations. Other local programs can access it; this is a single-user
prototype, not an authentication or adversarial isolation boundary. Six SDK turns
per prompt and a 180-second timeout apply; there is no hard monetary cap. A
journal without a closing event may indicate an interrupted process, not success.

Offline checks (no model calls):

```powershell
python -X utf8 -m unittest discover -v
```

Verification results and any unresolved failures are in `../mem/task-3-record.md`.

Verified 2026-09-24: 11 offline checks passed; two live browser turns exercised
Python addition, memory across turns, execution refusal, usage display and refresh.
Evidence: `runs/20260924T155746Z-569a7db6.jsonl`. Close session shut down the worker
and server cleanly. Active-reply shutdown and a forced network timeout remain
unverified. The UI's input-total adjustment was checked against the same live
conversation after reload without another model request.
