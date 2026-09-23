# claude-openai-harness — notebook

Swimlane: Claude lineage, OpenAI Agents SDK (`openai-agents`), OpenAI models only.
Rules: `../rules.md` (authoritative; no git; write only inside this swimlane).

## Tasks
### task-1-hello — drafted, blocked on API key (2026-09-23)
`task-1-hello/hello.py` sends one prompt ("Say hello world."), prints the reply to stdout, prints provenance to stderr, and exits 0 or 1.
Environment: Python 3.13.3. openai-agents 0.22.3 is installed user-wide (founder decision). The SDK's default model is `gpt-5.6-luna`.
Checked so far: the imports and the no-key failure path (exits 1 cleanly, no network call). It has never reached a model.
Blocked on: the API key, which the founder is handling with the Codex agent. After that come the model pin and the tracing decision.
Details: `mem/task-1-decisions.md`.

Cross-reference: `../claude-anthropic-harness/` finished the same task with the Claude Agent SDK. This draft follows its isolation choices.

## Pointers
- `mem/task-1-decisions.md`: environment, assumptions, open questions for task 1.
