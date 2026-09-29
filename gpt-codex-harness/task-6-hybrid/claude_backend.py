"""Claude SDK adapter using the peers' strict MCP and hook allowlist recipe.

One async lifetime owns the SDK context. Synchronous harness workers communicate
with it through a queue; connect/disconnect never cross asyncio task scopes.
"""
import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import asdict, is_dataclass
from importlib.metadata import version
import json
from pathlib import Path
import queue
import threading
import time
import uuid

from privacy import redact
from protocol import SessionStopped

SERVER = 'nimoi'


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def base_model(model):
    return model.removesuffix('[1m]') if isinstance(model, str) else model


class ClaudeRuntime:
    active_timeout = 600

    def __init__(self, record, journal, tools, prompt, *, client_factory=None):
        self.record, self.journal, self.tools, self.prompt = record, journal, tools, prompt
        self.stop = record.stop
        self.client_factory = client_factory
        self.commands = queue.Queue()
        self.ready = Future()
        self.thread = threading.Thread(target=self._thread, name=f'claude-{record.id}', daemon=True)
        self.names = {f'mcp__{SERVER}__{name}' for name in tools}
        self.turn_id = None
        self.info = {}
        self.fatal = None

    def _wait(self, future):
        while True:
            try:
                return future.result(timeout=.2)
            except FutureTimeout:
                if not self.thread.is_alive():
                    raise RuntimeError(self.fatal or 'Claude runtime stopped before replying.')

    def start(self):
        self.thread.start()
        return self._wait(self.ready)

    def run_turn(self, text, message_id=None):
        future = Future()
        self.commands.put((text, message_id, future))
        return self._wait(future)

    def idle(self):
        if self.stop.wait(.2):
            raise SessionStopped()
        self.journal.flush_pending()
        if not self.thread.is_alive():
            raise RuntimeError(self.fatal or 'Claude connection ended.')

    def close(self):
        self.commands.put(None)
        self.thread.join(20)
        if self.thread.is_alive():
            self.stop.set()
            self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError('Claude runtime did not stop; ledger must remain open.')

    def _thread(self):
        try:
            asyncio.run(self._serve())
        except BaseException as error:
            self.fatal = redact(str(error))
            if not self.ready.done():
                self.ready.set_exception(RuntimeError(self.fatal))

    async def permission(self, name, arguments, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        permitted = name in self.names and not self.stop.is_set()
        self.journal.write('sdk_permission', {'tool': name, 'permitted': permitted})
        return PermissionResultAllow() if permitted else PermissionResultDeny(message='Only registered harness tools are allowed.')

    async def pre_tool(self, data, tool_id, context):
        permitted = data['tool_name'] in self.names and not self.stop.is_set()
        self.journal.write('sdk_tool_request', {'tool': data['tool_name'], 'call_id': tool_id,
            'arguments': data.get('tool_input'), 'permitted': permitted})
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'allow' if permitted else 'deny',
            'permissionDecisionReason': 'Harness tool allowlist; handlers enforce role, onboarding and bounds.'}}

    async def invoke(self, name, arguments):
        self.journal.write('python_tool_started', {'tool': name, 'arguments': arguments})
        work = asyncio.create_task(asyncio.to_thread(self.tools[name][1], arguments))
        try:
            result = await asyncio.shield(work)
            success = True
        except asyncio.CancelledError:
            self.stop.set()
            try:
                await asyncio.shield(work)
            except (SessionStopped, ValueError):
                pass
            raise
        except (ValueError, OverflowError) as error:
            result, success = {'error': str(error)}, False
        except BaseException:
            self.stop.set()
            raise
        result = redact(result)
        self.journal.write('python_tool', {'tool': name, 'turnId': self.turn_id,
            'arguments': arguments, 'output': result, 'success': success})
        return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}], 'isError': not success}

    async def check_servers(self, client, required=False):
        servers = (await client.get_mcp_status()).get('mcpServers', [])
        self.journal.write('sdk_mcp_status', {'servers': [
            {k: s.get(k) for k in ('name', 'status', 'scope')} for s in servers]})
        if not servers and not required:
            return
        if len(servers) != 1 or servers[0].get('name') != SERVER or servers[0].get('status') != 'connected':
            raise RuntimeError('Claude MCP server isolation check failed.')

    async def _serve(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, create_sdk_mcp_server, tool
        registered = []
        for name, (spec, _) in self.tools.items():
            async def call(arguments, name=name):
                return await self.invoke(name, arguments)
            registered.append(tool(name, spec['description'], spec['inputSchema'])(call))
        options = ClaudeAgentOptions(model=self.record.model, cwd=str(Path(__file__).parent),
            tools=[], setting_sources=[], strict_mcp_config=True,
            skills=[], plugins=[], agents={}, system_prompt=self.prompt, max_turns=32,
            include_partial_messages=True, can_use_tool=self.permission,
            mcp_servers={SERVER: create_sdk_mcp_server(name=SERVER, version='0.6.0', tools=registered)},
            hooks={'PreToolUse': [HookMatcher(hooks=[self.pre_tool])]},
            env={'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1', 'ENABLE_TOOL_SEARCH': 'false',
                 'ANTHROPIC_API_KEY': '', 'ANTHROPIC_AUTH_TOKEN': '', 'MCP_TOOL_TIMEOUT': '86400000'},
            extra_args={'no-session-persistence': None},
            stderr=lambda line: self.journal.write('stderr', line.rstrip()))
        self.journal.write('agent_start', {'backend': 'claude-agent-sdk', 'sdk_version': version('claude-agent-sdk'),
            'requested_model': self.record.model, 'author': self.record.author, 'role': self.record.role, 'tools': sorted(self.names),
            'bounds': self.record.bounds.describe(), 'settings_sources': [], 'strict_mcp_config': True,
            'native_tools': [], 'auto_memory': False, 'session_persistence': False,
            'active_timeout_seconds': self.active_timeout, 'max_turns': 32})
        async with (self.client_factory or ClaudeSDKClient)(options=options) as client:
            info = await client.get_server_info() or {}
            account = {k: (info.get('account') or {}).get(k) for k in ('subscriptionType', 'apiProvider')}
            self.journal.write('account', account)
            if account['apiProvider'] != 'firstParty' or not account['subscriptionType']:
                raise RuntimeError('Claude subscription sign-in is required; no API-key fallback.')
            models = {base_model(m.get('resolvedModel') or m.get('value')) for m in info.get('models', [])}
            if self.record.model not in models:
                raise RuntimeError('Requested Claude model is not in the runtime catalog.')
            await self.check_servers(client)
            self.info = {'model': self.record.model, 'backend': 'claude-agent-sdk', 'account': account,
                         'models': sorted(models), 'exec_host_enabled': False}
            self.journal.write('restriction_caveat', {'note': 'Claude native tools, saved settings, auto-memory '
                'and external MCP are disabled; init inventory and MCP status are checked. Harness hooks deny '
                'unknown tools before dispatch. Approved Python still runs with normal process rights.'})
            self.ready.set_result(self.info)
            while not self.stop.is_set():
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.1)
                    continue
                if command is None:
                    break
                text, message_id, future = command
                try:
                    result = await self._query(client, text, message_id)
                    future.set_result(result)
                except BaseException as error:
                    future.set_exception(error)
                    break
        if not self.journal.failed:
            self.journal.flush_pending(force=True)
            self.journal.write('server_exit', {'backend': 'claude-agent-sdk', 'disconnected': True})

    async def _query(self, client, text, message_id):
        self.turn_id = uuid.uuid4().hex
        ref = self.journal.instruction
        if ref:
            if ref['body'] != text:
                raise ValueError('Input differs from its pinned instruction.')
            self.journal.write('input_sent', {'turn': self.turn_id,
                'text': f"[[{ref['name']}]]", 'text_id': ref['id'], 'author': ref['author']})
        else:
            self.journal.write('input_sent', {'turn': self.turn_id, 'human_message_id': message_id})
        await client.query(text)

        async def watchdog():
            elapsed, previous = 0, time.monotonic()
            while True:
                await asyncio.sleep(.2)
                now = time.monotonic()
                if self.record.status != 'blocked':
                    elapsed += now - previous
                previous = now
                if self.stop.is_set():
                    raise SessionStopped()
                if elapsed > self.active_timeout:
                    raise RuntimeError('Claude active turn deadline exceeded; governance wait excluded.')

        receive = asyncio.create_task(self._receive(client))
        watch = asyncio.create_task(watchdog())
        try:
            done, _ = await asyncio.wait((receive, watch), return_when=asyncio.FIRST_COMPLETED)
            if watch in done:
                self.stop.set()
                await client.interrupt()
                return await watch
            return await receive
        finally:
            for pending in (receive, watch):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(receive, watch, return_exceptions=True)

    async def _receive(self, client):
        from claude_agent_sdk import AssistantMessage, SystemMessage, StreamEvent, ResultMessage, RateLimitEvent, TextBlock, ToolUseBlock
        result, texts, stream_id = None, [], None
        text_positions = {}
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == 'init':
                names = set(message.data.get('tools', []))
                model = base_model(message.data.get('model'))
                self.journal.write('sdk_init', {'tools': sorted(names), 'model': model})
                if names != self.names or model != self.record.model:
                    raise RuntimeError('Claude initialized with unexpected tools or model; stopping.')
            elif isinstance(message, StreamEvent):
                event = message.event
                if event.get('type') == 'message_start':
                    stream_id = (event.get('message') or {}).get('id') or message.uuid
                if event.get('type') == 'content_block_delta' and event.get('delta', {}).get('type') == 'text_delta':
                    position = event.get('index', 0)
                    positions = text_positions.setdefault(stream_id, [])
                    if position not in positions:
                        positions.append(position)
                    self.journal.write('agent_delta', {'id': f'{stream_id}:{position}',
                        'turn_id': self.turn_id, 'text': event['delta']['text']})
            elif isinstance(message, AssistantMessage):
                if message.error:
                    self.journal.write('sdk_error', {'error': message.error})
                    raise RuntimeError(f'Claude runtime reported {message.error}.')
                if base_model(message.model) != self.record.model:
                    raise RuntimeError('Claude assistant model differs from requested model.')
                mid = message.message_id or stream_id or message.uuid or uuid.uuid4().hex
                text_ordinal = 0
                for index, block in enumerate(message.content):
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                        # SDK may omit thinking blocks from AssistantMessage, so
                        # its list index can differ from the streaming block index.
                        positions = text_positions.get(mid, [])
                        position = positions[text_ordinal] if text_ordinal < len(positions) else index
                        text_ordinal += 1
                        self.journal.write('agent_text', {'id': f'{mid}:{position}', 'text': block.text, 'complete': True})
                    elif isinstance(block, ToolUseBlock):
                        self.journal.write('model_call', {'name': block.name, 'call_id': block.id,
                            'permitted': block.name in self.names, 'payload': block.input})
                        if block.name not in self.names:
                            raise RuntimeError('Unexpected native Claude tool call observed.')
            elif isinstance(message, RateLimitEvent):
                self.journal.write('sdk_limits', plain(message.rate_limit_info))
            elif isinstance(message, ResultMessage):
                result = message
                self.journal.write('usage', {'backend': 'claude', 'last_prompt': plain(message.usage),
                    'model_usage': plain(message.model_usage), 'total_cost_usd': message.total_cost_usd,
                    'num_turns': message.num_turns})
                self.journal.write('sdk_result', {'subtype': message.subtype, 'is_error': message.is_error,
                    'errors': message.errors, 'session_id': message.session_id, 'stop_reason': message.stop_reason})
            elif isinstance(message, SystemMessage):
                # Init can contain email/settings; log selected fields above only.
                self.journal.write('sdk_system', {'subtype': message.subtype})
        await self.check_servers(client, required=True)
        if result is None or result.is_error or not texts or not self.tools.gate.complete:
            raise RuntimeError('Claude turn failed, produced no text, or onboarding is incomplete.')
        self.journal.write('agent_turn_complete', {'turn_id': self.turn_id})
        return self.turn_id, texts
