# task-1-hello — decisions and assumptions (2026-09-23)

Bar: a minimal harness whose behaviour and provenance are recorded clearly. Not a production client.

## Environment observed
- Python 3.13.3. `python-dotenv` 1.2.2 is installed. **`openai-agents` and `openai` are not installed.**
- `OPENAI_API_KEY` is not set, and there is no `.env` in this swimlane.

## Assumptions in the draft (consequential ones flagged)
1. **Tracing off (consequential).** By default the Agents SDK sends a trace of each run (prompt and output) to OpenAI's tracing backend. The draft calls `set_tracing_disabled(True)`, so nothing leaves the machine except the model call itself. Tradeoff: that also means no trace in the OpenAI dashboard.
2. **No tools or handoffs, `max_turns=1`.** Same reasoning as the claude-anthropic lane: the agent can't touch anything.
3. **Auth: env var first, then `<swimlane>/.env`.** Loaded with python-dotenv (`override=False`). Rules allow either location. The script never prints the key.
4. **Model: not pinned.** It falls back to the SDK default until the founder picks one. Provenance on stderr says `sdk-default` in that case.
5. **Provenance on stderr:** model, response ids, token counts. Cost isn't reported because the SDK doesn't return it.
6. **Fixed instructions** ("You are a helpful assistant.") so the SDK doesn't add an unstated system prompt.

## Founder answers (2026-09-23)
- Install: user-wide. `pip install --user openai-agents` installed openai-agents 0.22.3 and openai 3.19.0. **Side effect:** it upgraded the shared `urllib3` from 2.5.0 to 2.8.0 in user site-packages, and added griffelib 2.3.0 and websockets 16.1.1. `pip check` reported no broken requirements afterwards.
- Key, model and tracing: on hold. The founder is sorting out the API key with the Codex agent.

## Verified without a key
- Every SDK call the draft uses exists in 0.22.3: `Runner.run(max_turns=)`, `ModelResponse.usage/response_id`, and `set_tracing_disabled`.
- The SDK's default model is `gpt-5.6-luna`. When the model isn't pinned, the provenance line now names it.
- Running with no key prints `[error=OpenAIError: Missing credentials...]` and exits 1. The failure happens before any network call.

## Open questions for the founder
- Key: will you put `OPENAI_API_KEY` in `claude-openai-harness/.env` or set it in the environment?
- Model to pin?
- Keep tracing off?
