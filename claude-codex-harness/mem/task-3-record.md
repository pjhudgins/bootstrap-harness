# Task 3 record: local web UI over the task-2 harness

2026-09-24, Claude session (Opus 5.5), claude-codex-harness. rules.md, task 3:
"Build a harness that implements the capabilities from task 2, but it should launch
a simple but extensible local web ui for the user to run a single conversation with
the agent. New conversation on each launch." Founder: "When you feel comfortable
with task 2, proceed to task 3."

**Bar:** a person can launch it, hold one conversation with the restricted agent in
a browser, and see tool calls, usage and rate limits. Every launch leaves a journal
as complete as task 2's. "Extensible" means a new kind of event needs one
server-side publish and one client-side renderer. Prototype: local, single user,
not production.

## Interpretation and assumptions
- Standard library only: `http.server` plus plain HTML, CSS and JS. No packages, no
  CDN, and nothing loaded from the network.
- One launch = one app-server process = one new ephemeral thread = one journal.
  Closing the tab does not end the conversation; Ctrl+C in the terminal does.
- Task-2 capabilities, all kept: (a) the journal; (b) policy v2 (gpt-5.5, 20
  features off, MCP servers off, no environment, decline-all approvals, read-only
  sandbox, raw events); (c) `add`; (d) tool calls journaled **and shown**; (e)
  usage and rate limits journaled **and shown**.
- The task-2 modules are copied, not imported (rules.md: all code for a task in
  its folder), each marked with its source.
- Local-only safety: bind 127.0.0.1; reject any Host other than 127.0.0.1 or
  localhost; the API needs a per-launch token that is embedded in the page, so
  other websites open in the same browser cannot drive the agent. The token is never
  journaled.
- The default browser opens on launch; `--no-browser` suppresses it (for tests).

## Pause points
1. Build, offline tests with the fake model (no tokens).
2. Browser check in the Claude browser pane with the fake model, then one short
   live conversation.
3. Pause and report.

## Built
`conversation.py` (core: app-server, thread, worker, journal, EventLog), `ui.py`
(HTTP, SSE, local safety), `static/` (index.html, app.js, style.css), copies of the
four task-2 modules. Task-3 changes to the copies:
- `codex_client.Timeout`, so the turn loop can poll for Stop.
- The fake model gives each message a unique id (see finding 3).
- `request_user_input` is answered with no answers rather than an error, and the
  question is shown on the page.

## Verification, 2026-09-24
- **Tests: 7 pass**, three runs in a row, with `-W error::ResourceWarning`: the event
  log; the server refuses a foreign Host, a missing or wrong token (POST and
  SSE), and non-static paths; and real Codex + fake model through
  `Conversation`: add 2 + 3 → 5, one turn at a time, offered tools all reviewed,
  a two-turn reply-id regression, and the journal record kinds.
- **Browser, fake model** (`runs/20260924T174837Z-ui-fake-86a1a6.jsonl`): page
  loads; add 19.5 + 22.5 → 42.0 shown as a tool card; End stops the server
  (exit 0) and the page shows the summary.
- **Browser, live** (`runs/20260924T175025Z-ui-bc0cd8.jsonl`, 304 records,
  gpt-5.5 on the ChatGPT login):
  1. "Use the add tool to add 2.5 and 4.25" → `add({"a":2.5,"b":4.25})` → 6.75;
     reply "2.5 + 4.25 = **6.75**"; 4.5 s.
  2. Any-language execution probe → "I don't have a code-execution tool available
     in this session."; 2.6 s.
  3. "Write a 600-word story…", then Stop → the stream was cut mid-sentence and the
     turn ended `interrupted` (5.9 s).
  End → summary: 3 turns, 1 Python tool call, model calls `["add"]`, 0
  unreviewed, 0 declined, 30,114 tokens; app-server exit 0. Rate limits shown:
  primary 21 % of a 10,080-minute window. `~/.codex`: `models_cache.json` and
  `logs_2.sqlite-wal`, both while running. The WAL also changed in the task-2
  astra replay; it may be this app-server's or the desktop app's, and is
  unattributed. No email, bearer token or API token in the journal.
- Phone width (375 px): the layout stacks; no console errors.

## Findings
1. **The Claude browser pane reads only the project-root
   `nimoi/.claude/launch.json`.** My first `preview_start("task3-ui-fake")`
   instead started that file's `detection-report` configuration: `python -m
   http.server 8791 --bind 127.0.0.1 --directory
   candidate_analysis/detection_reports`, read-only, for 11 s. It served its index
   to the pane and was stopped as soon as I saw the name. Nothing was written. Adding
   my configuration there would mean writing outside the swimlane, so the UI
   servers were started with a background shell instead, and the pane was pointed
   at their URL. The swimlane `.claude/launch.json` I had created was removed.
2. The pane's coordinate clicks missed while the window was not drawn; clicking
   by element reference worked. The `confirm()` on End was replaced by an
   auto-accept in the page for these tests only.
3. Bug found in the browser: the fake model reused one message id, and the page
   merged turn 2's reply into turn 1's bubble. Fixed on both sides: unique ids, and
   bubbles are reset at each turn start. A regression test was added.
4. Bug found by the tests: refusing a POST before reading its body reset the
   connection on Windows (the client saw a reset, not a 403). The body is now read
   first.
5. Replies are markdown, shown as plain text. Rendering it safely would be an
   extension.

## Discrepancies (added 2026-09-24, onboarding v1.12 convention)
DISCREPANCY: Claude browser pane `preview_start` by name | expected: reads the
session's `.claude/launch.json` (the swimlane's, then the working directory) | found:
reads `nimoi/.claude/launch.json` only, and started its `detection-report`
http.server for 11 s (preview_start result "name": "detection-report") | 2026-09-24
DISCREPANCY: task-3-ui fake model | expected: unique message ids | found: every
message had id `msg_fake`, merging two turns' replies in the page (browser check;
fixed) | 2026-09-24
