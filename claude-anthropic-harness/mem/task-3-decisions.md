# task-3-ui — decisions, findings, runs (2026-09-23)

Bar: a simple local UI that shows task 2's capabilities live and is easy to extend. Not a product, not hardened.

## Founder decisions (2026-09-23)
- "capabilities from task 3" was a typo for task 2; `rules.md` has been corrected.
- UI: chat plus a live event side panel.
- Approvals: the policy decides and the UI shows each decision. `can_use_tool` in `session.py` is marked as the extension point for click-to-approve.
- Budget: $5.00 per launch (`--budget`).

## Design
- **`app.py`**: Starlette + uvicorn, all already installed; nothing new was installed. Binds 127.0.0.1 only. Protections:
  - `TrustedHostMiddleware` rejects foreign Host headers (DNS rebinding).
  - POSTs require JSON and a local Origin (cross-site form posts).
  - Routes: `/`, `/api/state`, `/api/events` (SSE), `/api/send`, `/api/interrupt`.
- **`session.py`**: one long-lived worker task owns `ClaudeSDKClient`. The SDK's anyio task group must be entered and exited by the same task. Requests put text into an inbox; only `interrupt()` crosses tasks. Options are task 2's, including `strict_mcp_config=True`.
- **`events.py`**: publish writes to the log first, then fans out to SSE subscribers. What the UI shows is always what the log holds. History is replayed on reload or reconnect via `Last-Event-ID`.
- **`static/app.js`**: `renderers[kind]`. Kinds without a renderer still appear in the side panel as JSON. Every side entry expands to the full record.
- The agent's cwd, and the policy root, is `workspace/`. It cannot read the harness code or the conversation logs.
- `runlog.py`, `policy.py` and `calc_tool.py` were copied unchanged from task 2; the rules keep each task's code in its own folder.

## Findings
1. **`ResultMessage.total_cost_usd` and `model_usage` are running session totals** in a multi-turn `ClaudeSDKClient` session. Only `ResultMessage.usage` is per-turn. Evidence: `conv-20260923T210118Z`, where turn 2's model_usage input tokens are 205 = 141 + 64. The first version summed the totals and over-counted (showed $0.0685 against a true $0.0397). The fix takes the reported total, logs `turn_cost_usd` as the difference, and raises `cost_anomaly` if the total ever falls.
2. **A stale tab across a relaunch would lose records.** EventSource reconnects with the old `Last-Event-ID`, so the new conversation's early records were skipped. The fix: each stream opens with `event: hello {conversation}`, and the page reloads when that id changes. Verified live.
3. **Interrupt works.** The turn ends `error_during_execution` at $0 and the session goes back to idle. The conversation keeps its memory afterwards: the agent recalled the earlier 2.5 + 4.
4. The UI shows no partial text; replies appear whole. `include_partial_messages=True` would stream them, which is a possible extension.

## Verification
- Offline: 12 tests (`python -m unittest discover -s task-3-ui/tests -t task-3-ui`). They cover the policy, add, the bus, SSE replay and close, cross-thread publish, routes, and Host/Origin/content-type rejections, using a fake session.
- Real session start and graceful stop without a prompt: states starting → idle → stopped.
- Live in the browser pane:
  - `conv-20260923T210118Z` (pre-fix): read + add, then a refused `../app.py` read.
  - `conv-20260923T210305Z`: add, interrupt, and a memory check. Session total $0.0125.
  - Two further launches with no prompts, for the reload test.
- Phone-width layout checked.
- **Not tested:** Ctrl+C shutdown of the full server. The server was stopped by killing the process each time. The log is flushed per record, so nothing was lost, but the lifespan shutdown path has only run with the fake session and in the direct `start`/`stop` probe.
- Launching needed no edit to `nimoi/.claude/launch.json`, which is outside this directory. The server ran as a background process and the browser pane was pointed at its URL.
