"""Capture real App Server tool offers against a local scripted model; no model service call."""

import argparse
from datetime import datetime, timezone
import json
import threading
import subprocess
from uuid import uuid4

from agent_tools import registry
from bounds import root_bounds
from fake_model import FakeModel, function_call, message, tool_names
from ledger_store import AGENT_AUTHOR, HARNESS_AUTHOR, AgentJournal, LedgerJournal, NIMOI
from policy import permitted_call
from protocol import TASK
from codex_backend import AgentRuntime, RuntimeOptions, start_client
from functools import partial


def capture(model, real_config=False):
    run_id = 'surface-' + datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz') + '-' + uuid4().hex[:8]
    journal = LedgerJournal(TASK / 'ledgers', run_id, stream_mode='compact')
    workspace = TASK / 'workspace' / run_id
    workspace.mkdir(parents=True)
    home = TASK / '.runtime' / 'surface-home'
    home.mkdir(parents=True, exist_ok=True)
    bounds = root_bounds(NIMOI, workspace, TASK / 'scripts')
    stop = threading.Event()
    tools = registry(journal, bounds=bounds, author=AGENT_AUTHOR, stop_event=stop)
    offered, step = set(), 0
    onboarding = 'nimoi:/' + tools.gate.path

    def record(request):
        if isinstance(request['body'], dict):
            names = tool_names(request['body'])
            offered.update(names)
            journal.write('offered_tools', {'model': model, 'tools': names, 'source': 'local fake provider'})

    def script(body):
        nonlocal step
        step += 1
        if step == 1:
            name, arguments = 'fs_read', {'path': onboarding, 'limit': 24000}
        elif not tools.gate.complete:
            name, arguments = 'fs_read', {'path': onboarding, 'offset': tools.gate.offset, 'limit': 24000}
        elif not any(c['tool'] == 'add' for c in runtime.client.calls):
            name, arguments = 'add', {'a': 19.25, 'b': 22.75}
        else:
            return [message('Offline scripted response: add was requested through the offered tool path.')]
        if step > 12:
            return [message('Offline scripted response stopped after repeated tool refusals.')]
        if 'functions.exec' in tool_names(body):
            return [{'type': 'custom_tool_call', 'name': 'exec', 'namespace': 'functions',
                     'call_id': f'call_{step}', 'input': f'text(await tools.{name}({json.dumps(arguments)}));'}]
        return [function_call(f'call_{step}', name, arguments)]

    fake = FakeModel(script, record)
    options = RuntimeOptions(fake_provider=fake, offline_home=None if real_config else home)
    prompt = 'Bar: offline protocol verification. Follow the scripted onboarding and addition calls.'
    ref = journal._text(prompt, 'instruction', author=HARNESS_AUTHOR)
    instruction = {'name': ref['text'][2:-2], 'id': ref['text_id'], 'author': HARNESS_AUTHOR, 'body': prompt}
    runtime = AgentRuntime(AgentJournal(journal, 'main', AGENT_AUTHOR, instruction), stop, tools, bounds,
                           AGENT_AUTHOR, model=model, runner=partial(start_client, options=options))
    try:
        journal.write('surface_audit_start', {'model': model, 'auth': 'none', 'model_endpoint': 'loopback fake provider'})
        runtime.start()
        runtime.run_turn(prompt)
        names = sorted(offered)
        native = [n for n in names if n.startswith('collaboration.')]
        unreviewed = [n for n in names if n not in native and not permitted_call(n.split('>')[-1], tools)]
        added = any(c['tool'] == 'add' and c['success'] and c['output'] == {'sum': 42.0}
                    for c in runtime.client.calls)
        version = subprocess.run(['codex', '--version'], capture_output=True, text=True,
                                 timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
        result = {'model': model, 'codex_version': version, 'config': 'saved user config' if real_config else 'isolated offline home',
                  'offered_tools': names, 'native_delegation_still_offered': native,
                  'unreviewed_offered': unreviewed, 'onboarding_complete': tools.gate.complete,
                  'add_callback_42': added, 'ledger': run_id}
        journal.write('surface_audit_result', result)
        return result
    except Exception as error:
        journal.write('surface_audit_failed', {'type': type(error).__name__, 'error': str(error)})
        raise
    finally:
        runtime.close()
        fake.close()
        journal.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--real-config', action='store_true', help='Use saved Codex configuration; model service remains the local fake provider.')
    args = parser.parse_args()
    result = capture(args.model, args.real_config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['add_callback_42'] and not result['unreviewed_offered'] else 1)
