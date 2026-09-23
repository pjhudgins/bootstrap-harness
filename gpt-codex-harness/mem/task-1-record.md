# Task 1 record

2026-09-23, GPT session.

- Read bootstrap-harness/rules.md after the founder changed the swimlanes.
- Scope: Python -> Codex App Server -> one hello-world prompt -> print -> exit.
- Use standard-library subprocess/JSON support; no dependency installation.
- Inherit the configured model. Require saved ChatGPT authentication before
  starting a turn; no API-key fallback. No credential extraction or copying.
- Use an ephemeral thread and read-only sandbox. The no-tools instruction is
  task guidance, not a claimed execution-security boundary.
- No git commands issued. No modifications to the old gpt-openai swimlane.
- Local CLI: codex-cli 0.155.0-alpha.9.2. Initial home discovery failed despite
  USERPROFILE being present. Supplying the normal CODEX_HOME to the child process
  resolved that error; `codex login status` returned `Logged in using ChatGPT`.
  It also warned of access denied when cleaning/creating arg0 temp directories.
- Live hello-world result: blocked before initialization; no response obtained.
- First live attempt exited 1 before initialization: App Server could not
  initialize its SQLite runtime under the read-only user .codex directory.
  Use the documented sqlite_home override to place runtime state in the task's
  ignored `.runtime/` directory; leave credential storage in its existing place.
- Second attempt, with the SQLite override, exited 1 during startup with a
  generic `Access is denied. (os error 5)`. Precise remaining path unknown.
- Third attempt used the documented `codex app-server proxy` command through
  the script's `--proxy` option. It exited 1 because connection to the normal
  local control socket failed: `A socket operation encountered a dead network.
  (os error 10050)`. No protocol handshake or agent turn occurred.
- Python AST syntax check passed. This is not evidence of working protocol
  integration. No fake response or offline SDK mock was used as a substitute.
- Next step requires running outside the restricted command environment, or
  equivalent user-side execution. Paused for the rules' explicit privilege
  permission requirement. No privileges have been expanded.

## Founder authorization

2026-09-23: The founder authorized running all harness-driver scripts under
development in this swimlane outside the command sandbox, equivalent to normal
terminal execution. This permits the driver's normal user-profile runtime
writes. It does not change the sandbox configured for the agent the driver starts.
Proceeding with the same hello-world script under that authorization.

- First normal-permission run reached initialization and passed the ChatGPT
  account check. `thread/start` rejected `sandbox: readOnly`; this installed
  protocol expects `read-only`, `workspace-write`, or `danger-full-access`.
  Corrected the client to `read-only`. No model turn occurred in this attempt.

## Successful live run

2026-09-23, approximately 19:45 UTC: reran the same standalone driver with
normal terminal permissions after correcting the enum. Its account/read check
confirmed ChatGPT authentication; it received a completed agentMessage with
text `hello world`, observed successful turn completion, printed the response,
and exited with code 0. The command took approximately 5.8 seconds. Task 1 passed.

The run emitted diagnostics about plugin icon paths, unsupported PowerShell
shell snapshots, and the inherited codex_apps MCP transport failing to decode
HTTP response bodies. MCP startup retried and later reported cached-tool
fallback/shutdown cancellation. These did not prevent the greeting. They remain
unresolved and are not evidence that app tools work in this driver.

No usage collection, tool-restriction framework, or general orchestration was
implemented. No further model runs were needed after this success. The proxy
option has only been attempted inside the restricted environment, where it
failed; standalone mode is the verified route.
