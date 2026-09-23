# Task 1: hello through Codex App Server

Requires Python 3.10+ and `codex` on PATH, already signed in with ChatGPT.
No Python packages or Platform API key are needed.

From this directory:

```powershell
python hello.py
```

To connect through an already-running local App Server instead:

```powershell
python hello.py --proxy
```

Standalone mode keeps SQLite runtime state in ignored `.runtime/` beside the
script. Proxy mode closes only its proxy process, not the shared App Server.

The script starts a hidden App Server subprocess over stdio, checks for ChatGPT
authentication, creates an ephemeral thread, sends one greeting prompt, prints
the agent's actual completed message, and closes the subprocess. It inherits
the configured model. Diagnostics go to stderr; failures exit nonzero. The
request deadline is 120 seconds, followed by up to 10 seconds for cleanup.

It uses the existing Codex credential store without extracting or copying
credentials. If CODEX_HOME is absent, it passes Python's resolved user-home
`.codex` directory to the subprocess. API-key environment overrides are removed
from that subprocess. The script does not change persistent Codex configuration.

The read-only sandbox and no-tools prompt are sufficient scope for this greeting;
they are not a verified prohibition of all code execution. Usage collection and
general orchestration are outside this task.

Verified 2026-09-23 with codex-cli 0.155.0-alpha.9.2: standalone mode, launched
with normal terminal permissions, used ChatGPT authentication, printed
`hello world`, and exited 0. This desktop command sandbox had blocked startup;
the founder authorized normal-terminal execution for drivers in this swimlane.
Plugin/shell/MCP startup warnings did not prevent the response. The optional
proxy path remains unverified outside the sandbox. See the swimlane record for
all attempts.

Reference: [Official App Server documentation](https://learn.chatgpt.com/docs/app-server).
