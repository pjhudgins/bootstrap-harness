# Task 1 record — 2026-09-23

## Authorization and assumptions

User: "in bootstrap-harness, read rules.md and begin work on
gpt-anthropic-harness/task-1-hello".
Read onboarding_1.11.md, bootstrap-harness/AGENTS.md and rules.md first.
The swimlane did not exist and was created. Interpret task 1 as one Claude agent;
the legacy pydantic wording does not apply to this architecture.
Default to the SDK's `sonnet` alias, configurable with `--model`; another lane's
founder-selected model is not assumed binding on this lane.
Use existing SDK authentication for a single hello request, without inspecting
credentials. Install dependencies only in a task-local virtual environment.

## References and cross-pollination

Read claude-anthropic-harness/notebook.md and its task-1-hello/hello.py.
Its notebook warns that task 1 is obsolete because account connectors attached
without strict_mcp_config. Adopt tools=[], setting_sources=[],
strict_mcp_config=True and no-session-persistence for this fresh implementation.
Official SDK README consulted:
https://github.com/anthropics/claude-agent-sdk-python/blob/main/README.md

## Observations and failures

- Python resolves to C:/Python313/python.exe; `pip show claude-agent-sdk` found
  no installation visible to this interpreter, despite the other lane's record.
- Created task-1-hello/.venv. Initial install selected SDK 0.2.159 but failed
  with a Connection broken / InvalidChunkLength transport error during dependency
  resolution. A no-cache binary-only retry failed the same way.
- A third attempt uses the public PyPI index and pip's legacy resolver to avoid
  the failing metadata resolution path. It downloaded the SDK wheel (104.4 MB),
  anyio and jsonschema wheels, but then failed with the same InvalidChunkLength
  error. The failing dependency URL was not identified in pip's output.
- Python AST parsing passed. SDK remains uninstalled in the task-local venv;
  import/runtime behavior and actual Claude response are unverified. No live
  request was made. Pause here for the human checkpoint rather than expanding
  this hello-world step into broader environment repair.
