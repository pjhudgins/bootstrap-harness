# Task 1: hello

Verified on this machine with the existing user-installed SDK 0.2.101. From
this directory in a normal PowerShell terminal:

```powershell
python -X utf8 hello.py
```

UTF-8 mode is necessary here because the greeting can contain emoji. Without
it the first live attempt failed with UnicodeEncodeError; with it the driver
printed `Hello, World! 👋` and exited 0.

For a separate environment (installation here has not succeeded inside the
restricted command environment):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 hello.py
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
