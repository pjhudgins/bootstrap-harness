# Task 1: hello

From this directory, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
.\.venv\Scripts\python.exe hello.py
```

Uses the SDK's bundled Claude Code CLI and its existing authentication, or an
`ANTHROPIC_API_KEY` environment variable. Never put credentials in this file.
The driver does not load `.env` files. The default Claude model alias is `sonnet`;
pass `--model MODEL` to select an explicit Claude model.

One prompt, one turn, text to stdout, exit 0 on a successful nonempty response.
Failures exit 1 with a short diagnostic on stderr. No built-in tools or MCP
servers are enabled, filesystem settings are not loaded, and session persistence
is disabled. These are task settings, not a general security sandbox.

SDK reference: https://github.com/anthropics/claude-agent-sdk-python/blob/main/README.md
