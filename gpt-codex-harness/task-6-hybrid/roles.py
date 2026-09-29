"""Explicit role policy, model families, and shared attested instructions."""
import json
from pathlib import Path

GOVERNOR_MODEL = 'claude-opus-5-5'
OWNERS = ('claude-opus-5-5', 'claude-fable-5-1', 'gpt-6-astra', 'gpt-6-sol', 'gpt-5.6-sol')
SUBAGENTS = (*OWNERS, 'claude-sonnet-5', 'gpt-5.6-terra')


def allowed_models(role):
    return (GOVERNOR_MODEL,) if role == 'governor' else OWNERS if role == 'owner' else SUBAGENTS


def validate_model(role, model):
    if model not in allowed_models(role):
        raise ValueError(f'Model {model!r} is not permitted for role {role}.')


def prompt_for(record, tools, delegation_bounds=None):
    common = Path(__file__).with_name('system_prompt.md').read_text(encoding='utf-8')
    role = {
        'governor': 'Communicate with the human, dispatch task owners, and handle safety/governance. '
            'Do not perform owners\' tasks or grade their work beyond safety/governance. '
            'Use task_start from your own pinned ledger brief including Bar:. Task owners own quality. '
            'You have no arithmetic, filesystem write, approved execution, or subagent tool. '
            'Notifications are harness messages, not new human instructions. For pending requests, '
            'read the request and write your decision as your own ledger entry. Use request_resolve. '
            'If human approval is needed, choose needs_human; the UI records the human decision. '
            'After dispatch, return control to the human instead of repeatedly polling; completion '
            'and request notifications will reach you in another turn.',
        'owner': 'Pursue the pinned assignment. You own quality, compliance with the letter and '
            'intent of human/governance instructions, and institutional standards. Delegate using '
            'subagent_start only; children are strictly subordinate and cannot delegate. '
            'If blocked, write a justified request in your ledger then call request_governor with '
            'that exact reference. This call blocks you until resolved or the session ends. '
            'Do not claim completion while children remain active. Read their returned results.',
        'subagent': 'Assist your task owner on this pinned assignment, strictly within its brief '
            'and your bounds. No delegation or direct governor requests. Report malformed work '
            'and failures to your owner in your final reply.'
    }[record.role]
    return common + '\n\nROLE\n' + role + '\n\nATTESTED SETTINGS\n' + json.dumps({
        'id': record.id, 'role': record.role, 'parent': record.parent, 'author': record.author,
        'model': record.model, 'bounds': record.bounds.describe(),
        'mounts': {k: str(v) for k, v in record.bounds.mounts.items()},
        'tools': list(tools), 'owner_models': list(OWNERS), 'subagent_models': list(SUBAGENTS),
        'delegation_bounds': (delegation_bounds or record.bounds).describe(),
        'onboarding': 'nimoi:/' + tools.gate.path}, ensure_ascii=False, indent=2)
