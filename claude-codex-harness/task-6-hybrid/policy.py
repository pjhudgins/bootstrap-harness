# From ../task-5-subagent/policy.py (policy v2). Task 6: models by layer (founder,
# 2026-09-28), two engines, and rule 5g: Codex's code-mode `exec` is allowed.
"""Who may run as what, and how each engine is restricted. None of this is a verified
security boundary; mem/task-6-plan.md and the README say what each layer does.

Models by layer (founder, 2026-09-28, "As proposed"): LAYER_MODELS. A model id starting
`claude-` runs on the Claude Agent SDK, `gpt-` on Codex App Server.

Codex (from task 2's findings, re-checked 2026-09-28 with the fake model):
  0. Every GPT model allowed here is code-mode (catalogue `tool_mode: code_mode_only`):
     it reaches our tools only from inside `exec`, which runs model-written JavaScript
     in a V8 isolate (no file system, network or console, by its own description;
     unverified). Rule 5g allows it: `code_mode_host` stays on for these models.
  1. Everything else that can run code or act on the machine is off: DISABLED_FEATURES
     (process-wide --disable), configured MCP servers off per thread, no environment.
     With these off, exec's nested tools are ours plus Codex's goals and clock.
  2. Codex's own sub-agent tools (`collaboration.*`, multi-agent v2) cannot be switched
     off for these models. They are not reviewed: a call to one stops the turn (5f).
  3. Approvals: anything not known-safe must ask, and this client declines every ask.
  4. Read-only sandbox with network off, for anything that still runs.
Claude (claude-anthropic-harness's isolation recipe): no setting sources, strict MCP
config, auto memory off, no built-in tools, only our in-process MCP tools, no session
persistence; a tool use outside our tools stops the turn.
"""

LAYER_MODELS = {
    "governor": ("claude-opus-5-5",),
    "task-owner": ("claude-opus-5-5", "claude-fable-5-1", "gpt-6-astra", "gpt-6-sol"),
    "subagent": ("claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1",
                 "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-sol", "gpt-6-astra"),
}
GOVERNOR_MODEL = LAYER_MODELS["governor"][0]


def engine_for(model):
    """"claude" or "codex" for a model id; ValueError for anything else."""
    if isinstance(model, str) and model.startswith("claude-"):
        return "claude"
    if isinstance(model, str) and model.startswith("gpt-"):
        return "codex"
    raise ValueError(f"no engine runs {model!r}")


def model_problem(layer, model):
    """Why `model` may not run in `layer`, or None."""
    allowed = LAYER_MODELS[layer]
    if model not in allowed:
        return f"a {layer} may run only {', '.join(allowed)}; not {model!r}"
    return None


# ---- Codex -------------------------------------------------------------------------------
CODE_MODE_TOOL_MODES = {"code_mode_only"}
# Enabled-by-default features that let the agent run code, act on the machine, or pull in
# tools from elsewhere. Names from `codex features list`, codex-cli 0.155.0-alpha.9.2.
DISABLED_FEATURES = {
    "shell_tool": "shell command tool",
    "unified_exec": "PTY command execution (the server still reports it on; task-2 record)",
    "unified_exec_tty": "TTY for unified exec",
    "shell_snapshot": "runs the user's shell at session start to snapshot its environment",
    "code_mode": "model-written code run in the code-mode host (already off by default)",
    "code_mode_host": "the JavaScript host behind `exec`: off for direct-tool models; ON for "
                      "code-mode models, whose only way to our tools is exec (rule 5g)",
    "browser_use": "browser control, including page JavaScript",
    "browser_use_external": "control of an external browser",
    "browser_use_full_cdp_access": "raw Chrome DevTools Protocol access",
    "in_app_browser": "desktop app's browser",
    "computer_use": "desktop control",
    "in_app_local_automation": "local automations (effect unverified; off as a precaution)",
    "hooks": "runs configured hook commands",
    "multi_agent": "sub-agents (v2 models keep collaboration.* anyway: see REVIEWED)",
    "apps": "ChatGPT connectors through the codex_apps MCP server",
    "plugins": "plugin-provided MCP servers and skills",
    "remote_plugin": "remote plugin catalogue",
    "skill_mcp_dependency_install": "installs MCP server dependencies for skills",
    "tool_suggest": "suggests installing more tools",
    "worktrees": "creates git worktrees",
}
CODE_MODE_KEEPS = ("code_mode_host",)

THREAD_PARAMS = {
    "approvalPolicy": "untrusted",
    "approvalsReviewer": "user",  # never an auto-reviewer
    "sandbox": "read-only",
    "environments": [],  # empty = no environment access for this thread's turns
    "ephemeral": True,   # no session rollout file under CODEX_HOME
    # Stream the model's raw output items, so every tool call it makes is recorded,
    # including exec's JavaScript, which never appears as a thread item.
    "experimentalRawEvents": True,
}

APPROVAL_DECLINES = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
    "execCommandApproval": {"decision": "abort"},
    "applyPatchApproval": {"decision": "abort"},
}

TOOL_ITEMS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
              "collabAgentToolCall", "subAgentActivity", "webSearch", "imageView",
              "imageGeneration", "sleep"}
EXECUTION_ITEMS = {"commandExecution", "fileChange", "mcpToolCall",
                   "collabAgentToolCall", "subAgentActivity"}

# Model tool calls reviewed as unable to change this machine beyond our own tools. Names
# as the raw events give them, with and without their namespace. Anything else a model
# calls, above all collaboration.* (Codex's own sub-agents), stops its turn.
REVIEWED_MODEL_TOOLS = {
    "create_goal": "thread goal bookkeeping",
    "get_goal": "thread goal bookkeeping",
    "update_goal": "thread goal bookkeeping",
    "request_user_input": "asks the user; this harness answers with nothing",
    "request_user_input_async": "asks the user; this harness answers with nothing",
    "skills.list": "lists Codex skill instructions; read-only",
    "skills.read": "reads a Codex skill's instructions; read-only",
    "web_search": "OpenAI-hosted web search; runs nothing on this machine",
    # Rule 5g, code-mode models only:
    "exec": "model-written JavaScript in Codex's V8 isolate; it reaches only our tools "
            "(bounded here) and Codex's goals and clock; its code is recorded",
    "wait": "waits for a running exec cell",
    "clock.sleep": "waits",
}
REVIEWED_NAMESPACES = ("functions.",)  # "functions.exec" is "exec"


def reviewed_call(name, own_tools):
    """Is a model's tool call one of ours or reviewed?"""
    bare = next((name[len(p):] for p in REVIEWED_NAMESPACES if name.startswith(p)), name)
    return bare in own_tools or bare in REVIEWED_MODEL_TOOLS or name in REVIEWED_MODEL_TOOLS


def code_mode_model(catalog_entry):
    return bool(catalog_entry) and catalog_entry.get("tool_mode") in CODE_MODE_TOOL_MODES


def disable_flags(code_mode):
    """--disable flags for a Codex agent; a code-mode model keeps its exec host (5g)."""
    return [arg for name in DISABLED_FEATURES
            if not (code_mode and name in CODE_MODE_KEEPS) for arg in ("--disable", name)]


def mcp_overrides(server_names):
    """Thread config that disables each configured MCP server by name."""
    return {f"mcp_servers.{name}.enabled": False for name in server_names}


# ---- Claude ------------------------------------------------------------------------------
MCP_SERVER = "nimoi"          # our in-process MCP server; tools are mcp__nimoi__<name>
CLAUDE_ENV = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",   # auto memory loads regardless of settings
    "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
}
# The SDK starts its CLI with this process's whole environment. Started from inside a
# Claude Code session, that environment names the session's API endpoint, its messaging
# socket and token, and marks the CLI as the session's child (seen 2026-09-28). A
# harness agent must be no one's child: these are removed from this process at start.
HOST_AGENT_PREFIXES = ("CLAUDE", "ANTHROPIC_", "MCP_")


def strip_host_agent_environment(environ):
    """Remove host-agent variables from `environ` (in place). Returns their names only."""
    names = sorted(n for n in environ if n.upper().startswith(HOST_AGENT_PREFIXES))
    for name in names:
        del environ[name]
    return names


def claude_tool_name(name):
    return f"mcp__{MCP_SERVER}__{name}"
