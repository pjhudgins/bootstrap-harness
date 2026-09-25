# Copied from ../task-2-tooling/policy.py on 2026-09-24 (policy v2). Changes made here
# belong to task 3.
"""Restrictions for a Codex agent whose only way to act is this harness's Python tools.

Layers, strongest first. None of this is a verified security boundary.
  0. Model choice. Codex builds the model's tool surface from the model catalogue. A
     model with tool_mode "code_mode_only" (gpt-6-astra, the gpt-5.6 family) is offered
     `exec`, which runs model-written JavaScript, and reaches dynamic tools such as `add`
     only from inside it; it also gets sub-agent tools (multi_agent_version v2). Neither
     can be switched off while `add` keeps working. MODEL has neither.
  1. Remove execution-capable tools: DISABLED_FEATURES (process-wide --disable), configured
     MCP servers disabled per thread, and no execution environment for the thread.
  2. Approval gate: anything not known-safe must ask; requests come to this client, which
     declines every one.
  3. Read-only sandbox with network off, for anything that still runs.
Evidence: the fake-model runs recorded in ../mem/task-2-record.md.
"""

# Catalogue tool_mode null: dynamic tools offered directly, no `exec`, no sub-agent tools.
MODEL = "gpt-5.5"
CODE_MODE_TOOL_MODES = {"code_mode_only"}

# Enabled-by-default features that let the agent run code, act on the machine, or pull in
# tools from elsewhere. Names and defaults from `codex features list`, codex-cli
# 0.155.0-alpha.9.2. Passed as --disable so they are off before anything starts.
DISABLED_FEATURES = {
    "shell_tool": "shell command tool",
    "unified_exec": "PTY command execution (the server still reports it on; see record)",
    "unified_exec_tty": "TTY for unified exec",
    "shell_snapshot": "runs the user's shell at session start to snapshot its environment",
    "code_mode": "model-written code run in the code-mode host (already off by default)",
    "code_mode_host": "the JavaScript host behind `exec`; not needed for dynamic tools when "
                      "MODEL is not code-mode-only (verified with the fake model)",
    "browser_use": "browser control, including page JavaScript",
    "browser_use_external": "control of an external browser",
    "browser_use_full_cdp_access": "raw Chrome DevTools Protocol access",
    "in_app_browser": "desktop app's browser",
    "computer_use": "desktop control",
    "in_app_local_automation": "local automations (effect unverified; off as a precaution)",
    "hooks": "runs configured hook commands",
    "multi_agent": "sub-agents, which carry their own tools",
    "apps": "ChatGPT connectors through the codex_apps MCP server",
    "plugins": "plugin-provided MCP servers and skills",
    "remote_plugin": "remote plugin catalogue",
    "skill_mcp_dependency_install": "installs MCP server dependencies for skills",
    "tool_suggest": "suggests installing more tools",
    "worktrees": "creates git worktrees",
}
# Deliberately left on: tools that cannot run code (see REVIEWED_MODEL_TOOLS).

THREAD_PARAMS = {
    "approvalPolicy": "untrusted",
    "approvalsReviewer": "user",  # never an auto-reviewer
    "sandbox": "read-only",
    "environments": [],  # empty = no environment access for this thread's turns
    "ephemeral": True,   # no session rollout file under CODEX_HOME
    # Stream the model's raw output items, so every tool call it makes is journaled,
    # including code-mode `exec`, which never appears as a thread item. Experimental.
    "experimentalRawEvents": True,
}

# Layer 2: every approval-type server request is refused.
APPROVAL_DECLINES = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
    "execCommandApproval": {"decision": "abort"},
    "applyPatchApproval": {"decision": "abort"},
}

# Completed thread-item types meaning the agent acted through something other than our
# dynamic tools. EXECUTION_ITEMS can run code or change files (an MCP tool can do anything).
TOOL_ITEMS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
              "collabAgentToolCall", "subAgentActivity", "webSearch", "imageView",
              "imageGeneration", "sleep"}
EXECUTION_ITEMS = {"commandExecution", "fileChange", "mcpToolCall",
                   "collabAgentToolCall", "subAgentActivity"}

# Tools Codex offers MODEL under this policy (observed with the fake model, 2026-09-24),
# each reviewed as unable to run code or change files. Any other tool offered to, or
# called by, the model fails the checks. Names as fake_model.tool_names() writes them.
REVIEWED_MODEL_TOOLS = {
    "create_goal": "thread goal bookkeeping",
    "get_goal": "thread goal bookkeeping",
    "update_goal": "thread goal bookkeeping",
    "request_user_input": "asks the user; this page shows the question and answers with none",
    "skills.list": "lists Codex skill instructions; read-only",
    "skills.read": "reads a Codex skill's instructions; read-only",
    "web_search": "OpenAI-hosted web search; runs nothing on this machine",
}


def disable_flags():
    return [arg for name in DISABLED_FEATURES for arg in ("--disable", name)]


def mcp_overrides(server_names):
    """Thread config that disables each configured MCP server by name."""
    return {f"mcp_servers.{name}.enabled": False for name in server_names}
