# Adapted from gpt-codex-harness/task-5-subagent/policy.py, 2026-09-28.
"""Requested restrictions and observed calls. Flags are not an isolation proof."""

from functools import lru_cache
import json
import os
import subprocess


DISABLED = ('shell_tool', 'unified_exec', 'unified_exec_tty', 'shell_snapshot',
            'code_mode', 'js_repl', 'multi_agent', 'apps', 'hooks', 'plugins',
            'remote_plugin', 'skill_mcp_dependency_install', 'tool_suggest',
            'browser_use', 'browser_use_external', 'browser_use_full_cdp_access',
            'in_app_browser', 'computer_use', 'in_app_local_automation', 'worktrees')
RESTRICTIONS = {**{f'features.{name}': False for name in DISABLED},
                'agents.enabled': False, 'web_search': 'disabled'}
# Permitted runtime helpers. Calls outside this set and the registered harness
# tools stop the actor. Raw events are observations, not a pre-execution hook.
RUNTIME_TOOLS = {'exec', 'functions.exec', 'wait', 'functions.wait', 'clock.sleep',
                 'clock__curr_time', 'create_goal', 'get_goal', 'update_goal',
                 'request_user_input', 'request_user_input_async',
                 'functions.request_user_input', 'functions.request_user_input_async',
                 'skills.list', 'skills.read', 'skills__list', 'skills__read'}
EXECUTION_ITEMS = {'commandExecution', 'fileChange', 'mcpToolCall', 'collabToolCall',
                   'collabAgentToolCall', 'subAgentActivity'}
APPROVAL_DECLINES = {
    'item/permissions/requestApproval': {'permissions': {}, 'scope': 'turn'},
    'item/commandExecution/requestApproval': {'decision': 'decline'},
    'item/fileChange/requestApproval': {'decision': 'decline'},
    'mcpServer/elicitation/request': {'action': 'decline', 'content': None, '_meta': None},
    'execCommandApproval': {'decision': 'abort'},
    'applyPatchApproval': {'decision': 'abort'},
}


@lru_cache(maxsize=4)
def model_catalog(codex_home):
    """Read only tool-relevant metadata. Never log the model instruction payloads."""
    env = os.environ.copy()
    env['CODEX_HOME'] = codex_home
    for name in ('CODEX_API_KEY', 'OPENAI_API_KEY'):
        env.pop(name, None)
    try:
        result = subprocess.run(['codex', 'debug', 'models'], env=env, capture_output=True,
                                timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        models = json.loads(result.stdout.decode('utf-8-sig'))['models']
        return {m['slug']: {k: m.get(k) for k in ('tool_mode', 'multi_agent_version', 'shell_type')}
                for m in models}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return {}


def needs_exec(entry):
    # Unknown catalogue metadata keeps the required dispatcher available, with
    # an explicit caveat. Direct-tool models can disable the host entirely.
    return entry is None or entry.get('tool_mode') == 'code_mode_only'


def call_identity(item):
    kind = item.get('type', '')
    if kind in ('function_call', 'custom_tool_call'):
        name = item.get('name', '')
        return f"{item['namespace']}.{name}" if item.get('namespace') else name
    if kind.endswith('_call'):
        return kind[:-5]
    return None


def permitted_call(name, tool_names):
    return name in RUNTIME_TOOLS or name in tool_names
