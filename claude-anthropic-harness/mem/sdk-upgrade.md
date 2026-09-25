# SDK upgrade 0.2.101 → 0.2.159 (2026-09-25)

**Founder direction:** "update to the latest sdk version. This may break work in prior tasks - that is okay, this is a rapid prototyping effort and better to get current early". This authorized writing outside the swimlane, to user site-packages.

## What changed on disk
- **Command:** `python -m pip install --user --upgrade claude-agent-sdk` updated `claude-agent-sdk` from 0.2.101 to 0.2.159 (the latest on PyPI).
- **Dependencies unchanged:** anyio 4.13.0, mcp 1.29.0, starlette 1.3.1, uvicorn 0.49.0, sse-starlette 3.4.4, httpx 0.28.1, pydantic 2.13.4. `pip check` reports no broken requirements.
- **Bundled CLI:** `_bundled/claude.exe` moved from 2.1.177 to 2.1.281. The PATH `claude` (2.1.251) was never what the SDK used.

## Compatibility check
- **API surface:** every `ClaudeAgentOptions` field, `ResultMessage` field, client method and exported name the harness code uses is still present.
- **New options:** `forward_subagent_text`, `resume_drops_turn`, `resume_session_at`, `verbatim_prompts`.
- **`SystemPromptPreset`** now has `exclude_dynamic_sections` and `snapshot`.
- **Offline tests:** task 2 (11), task 3 (12) and task 4 (22) all pass. They don't start the CLI.
- **Live smoke test:** task 2 `driver.py add outside`, `runs/run-20260925T131055Z.jsonl.log`.
  - Both scenarios passed: 1912.75, and the outside read was refused by the hook.
  - No unexpected MCP servers, and `memoryFiles` was empty (cwd is the task folder).

## Observations
- **The first run on the new version cost more: $0.031 against $0.0068 for the same `add` scenario.** The cause is the prompt cache. The old run read 6.7k cached tokens; the new run had to write 7.6k, because the new CLI's prompt differs, so nothing cached matched. The prompt size is about the same.
- **The context breakdown is shaped differently.** The "System prompt" category (205 tokens) is gone, "Messages" went from 217 to 780, and there is a new "Autocompact buffer" of 33,000 (reserved headroom, not tokens used). The system prompt is probably now counted inside Messages; not verified.
- **Task 4 checked live on the new version:** session `20260925T132206Z`, turn, usage, MCP status and clean shutdown all worked. `CLAUDE_CODE_DISABLE_AUTO_MEMORY` is honoured by CLI 2.1.281 (`memoryFiles: []`). The "System prompt" category reappears there (1,137 tokens), since task 4 passes a custom system prompt.
- **Not yet checked live on the new version:** task 3's UI.
