# Task 1: hello

One OpenAI Agents SDK agent receives a greeting prompt; the script prints its
actual final output and exits. No tools or handoffs.

From `bootstrap-harness/gpt-openai-harness` in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --no-cache-dir -r task-1-hello/requirements.txt
.venv\Scripts\python.exe task-1-hello/hello.py
```

Before running, provide `OPENAI_API_KEY` through the environment or a private
`.env` file at this swimlane's root. Never put credentials in source files or chat.
An existing environment value takes precedence over `.env`.

The default model is `gpt-6-astra`, following the official quickstart consulted
on 2026-09-23. Set `OPENAI_MODEL` to use another OpenAI model available to your
API project. Account access to the default has not yet been verified.
SDK trace export is disabled for this minimal task. API failures remain visible
and produce a nonzero exit; no substitute greeting is printed.

Reference: [Official OpenAI Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart).
