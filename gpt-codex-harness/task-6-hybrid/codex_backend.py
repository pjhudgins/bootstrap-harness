"""Independent App Server connections with identical permission injection."""

import json
import os
from pathlib import Path
from dataclasses import dataclass

from protocol import Client, RESTRICTIONS, TASK
from protocol import RpcError
from policy import model_catalog, needs_exec


@dataclass(frozen=True)
class RuntimeOptions:
    stream_mode: str = 'compact'
    prompt: str | None = None
    # A local fake provider is used only by the explicit offline capture driver.
    fake_provider: object = None
    offline_home: object = None


def start_client(journal, stop, tools, bounds, author, actor='main', model=None, *, options=None):
    options = options or RuntimeOptions()
    owner = getattr(journal, 'owner', journal)
    runtime = TASK / '.runtime' / owner.directory.name / actor
    runtime.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault('CODEX_HOME', str(Path.home() / '.codex'))
    env.pop('CODEX_API_KEY', None)
    env.pop('OPENAI_API_KEY', None)
    if options.offline_home:
        env['CODEX_HOME'] = str(options.offline_home)
    catalog = model_catalog(env['CODEX_HOME'])
    command = ['codex', 'app-server', '--listen', 'stdio://']
    for key, value in {**RESTRICTIONS, 'sqlite_home': str(runtime)}.items():
        command.extend(['-c', f'{key}={json.dumps(value)}'])
    if options.fake_provider:
        command.extend(options.fake_provider.codex_args())
    journal.write('agent_start', {'actor': actor, 'author': author, 'requested_model': model,
                                  'bounds': bounds.describe(), 'command': command})
    client = Client(journal, command, env, stop, tools)
    try:
        capabilities = {'experimentalApi': True}
        if options.stream_mode == 'compact':
            capabilities['optOutNotificationMethods'] = ['item/reasoning/summaryTextDelta',
                'item/reasoning/summaryPartAdded', 'item/reasoning/textDelta', 'item/plan/delta']
        client.request('initialize', {'clientInfo': {'name': 'nimoi_subagent_ui', 'version': '0.2.0'},
                                      'capabilities': capabilities})
        client.send({'method': 'initialized', 'params': {}})
        if not options.fake_provider:
            account = client.request('account/read', {'refreshToken': False}).get('account')
            if not account or account.get('type') != 'chatgpt':
                raise RuntimeError('ChatGPT sign-in is required; no API-key fallback.')
        config = client.request('config/read', {'includeLayers': False, 'cwd': str(TASK)})['config']
        features, cursor = {}, None
        while True:
            page = client.request('experimentalFeature/list', {'limit': 100, 'cursor': cursor})
            features.update({item['name']: item['enabled'] for item in page['data']})
            cursor = page.get('nextCursor')
            if not cursor:
                break
        journal.write('preflight', {'features': features})
        for name in ('shell_tool', 'js_repl', 'code_mode', 'multi_agent', 'apps', 'hooks', 'plugins', 'browser_use', 'computer_use'):
            if features.get(name) is not False:
                raise RuntimeError(f'Required restriction not confirmed disabled: {name}')
        models, default_model, cursor = [], None, None
        while True:
            page = client.request('model/list', {'limit': 100, 'cursor': cursor, 'includeHidden': False})
            models.extend(item['model'] for item in page['data'])
            default_model = next((item['model'] for item in page['data'] if item.get('isDefault')), default_model)
            cursor = page.get('nextCursor')
            if not cursor:
                break
        if model is not None and model not in models:
            raise ValueError('Requested child model is not in the available model catalog.')
        selected_model = model or config.get('model') or default_model
        metadata = catalog.get(selected_model)
        exec_enabled = needs_exec(metadata)
        overrides = dict(RESTRICTIONS)
        overrides['features.code_mode_host'] = exec_enabled
        for key in ('mcp_servers', 'plugins'):
            for name in config.get(key) or {}:
                overrides[f'{key}.{name}.enabled'] = False
        prompt = options.prompt or (TASK / 'system_prompt.md').read_text(encoding='utf-8')
        prompt += '\n\nCODEX RUNTIME SETTINGS\n' + json.dumps({
            'actor': actor, 'author': author,
            'mounts': {k: str(v) for k, v in bounds.mounts.items()}, 'bounds': bounds.describe(),
            'available_models': models, 'tools': list(tools), 'exec_host_enabled': exec_enabled}, ensure_ascii=False, indent=2)
        params = {'cwd': str(TASK), 'sandbox': 'read-only', 'approvalPolicy': 'untrusted',
                  'approvalsReviewer': 'user', 'ephemeral': True, 'experimentalRawEvents': True,
                  'environments': [], 'config': overrides, 'baseInstructions': prompt,
                  'developerInstructions': 'Follow the NIMOI test-pilot role and attested bounds. Only supplied harness tools grant writes, approved execution and delegation.',
                  'dynamicTools': [spec for spec, handler in tools.values()]}
        if selected_model is not None:
            params['model'] = selected_model
        thread = client.request('thread/start', params)
        client.thread_id = thread['thread']['id']
        if selected_model is not None and thread.get('model') != selected_model:
            raise RuntimeError('Runtime selected a different model than requested; child stopped.')
        effective, cursor = {}, None
        while True:
            page = client.request('experimentalFeature/list', {'threadId': client.thread_id, 'limit': 100, 'cursor': cursor})
            effective.update({item['name']: item['enabled'] for item in page['data']})
            cursor = page.get('nextCursor')
            if not cursor:
                break
        if not exec_enabled and effective.get('code_mode_host') is not False:
            raise RuntimeError('Direct-tool model exec host restriction was not confirmed.')
        journal.write('restriction_evidence', {'model': thread.get('model'), 'catalog': metadata,
            'exec_host_requested': exec_enabled, 'unified_exec_reported': features.get('unified_exec'),
            'exec_host_reported': effective.get('code_mode_host'),
            'raw_calls_monitored': True, 'approval_requests': 'declined',
            'note': 'Requested settings and catalogue metadata; offered tools require a provider capture.'})
        caveat = ('JavaScript exec remains enabled for this model to dispatch harness tools. '
                  'Native delegation may remain offered despite disabled flags; it is forbidden by instructions and monitored after output. '
                  if exec_enabled else 'JavaScript exec host is disabled for this direct-tool model. ')
        journal.write('restriction_caveat', {'note': caveat +
            'Shell, patch, external MCP/apps and browser tools are requested off. All approval requests are declined. '
            'Raw-call monitoring is detection, not a pre-execution boundary; trusted scripts run with normal process privileges.'})
        return client, {'model': thread.get('model'), 'models': models, 'thread_id': client.thread_id,
                        'exec_host_enabled': exec_enabled}
    except BaseException:
        client.close()
        raise


class AgentRuntime:
    """One connection and identical turn/usage/cleanup behavior for any agent."""
    def __init__(self, journal, stop, tools, bounds, author, actor='main', model=None, runner=start_client):
        self.journal, self.stop, self.tools, self.bounds = journal, stop, tools, bounds
        self.author, self.actor, self.model, self.runner = author, actor, model, runner
        self.client = None
        self.info = {}

    def start(self):
        self.client, self.info = self.runner(self.journal, self.stop, self.tools, self.bounds,
                                              self.author, self.actor, self.model)
        self.read_limits()
        return self.info

    def read_limits(self):
        try:
            self.journal.write('rate_limits', self.client.request('account/rateLimits/read', {}))
        except RpcError as error:
            self.journal.write('rate_limits_unavailable', {'error': str(error)})

    def run_turn(self, text, message_id=None):
        result = self.client.run_turn(text, message_id) if message_id is not None else self.client.run_turn(text)
        self.journal.write('agent_turn_complete', {'actor': self.actor})
        self.read_limits()
        return result

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            finally:
                self.client = None

    def idle(self):
        self.client.receive(idle=True)
