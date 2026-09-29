"""The hierarchy chooses roles; this factory chooses a transport."""
from functools import partial
from codex_backend import AgentRuntime, RuntimeOptions, start_client
from roles import prompt_for


def runtime_for(record, journal, tools, delegation_bounds):
    prompt = prompt_for(record, tools, delegation_bounds)
    if record.model.startswith('claude-'):
        from claude_backend import ClaudeRuntime
        return ClaudeRuntime(record, journal, tools, prompt)
    return AgentRuntime(journal, record.stop, tools, record.bounds, record.author,
        record.id, record.model, partial(start_client, options=RuntimeOptions(
            stream_mode=journal.owner.stream_mode, prompt=prompt)))
