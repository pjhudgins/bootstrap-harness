"""Installed SDK types with a fake transport; no model requests or credentials."""
import asyncio
import copy
import unittest

import test_hybrid as fixtures
from claude_backend import ClaudeRuntime
from ledger_store import AgentJournal
from roles import prompt_for

try:
    import claude_agent_sdk as sdk
except ImportError:
    sdk = None


@unittest.skipIf(sdk is None, 'Installed SDK is visible in the normal-terminal driver environment.')
class ClaudeBackendTests(fixtures.HybridTests):
    # Avoid inheriting the base fixture's test methods a second time.
    def make_runtime(self, tools_override=None, model_override=None):
        test = self
        tools = self.family.make_tools(self.gov)
        self.onboard(tools)
        class Client:
            def __init__(self, options):
                test.options = options
            async def __aenter__(self):
                self.task = asyncio.current_task()
                return self
            async def __aexit__(self, *args):
                test.assertIs(self.task, asyncio.current_task())
            async def get_server_info(self):
                return {'account': {'subscriptionType': 'Claude Max', 'apiProvider': 'firstParty'},
                        'models': [{'resolvedModel': test.gov.model}]}
            async def get_mcp_status(self):
                return {'mcpServers': [{'name': 'nimoi', 'status': 'connected'}]}
            async def query(self, text):
                pass
            async def receive_response(self):
                yield sdk.SystemMessage(subtype='init', data={
                    'tools': tools_override if tools_override is not None else sorted(runtime.names),
                    'model': model_override or test.gov.model})
                yield sdk.StreamEvent(uuid='stream-1', session_id='fake', event={
                    'type': 'message_start', 'message': {'id': 'message-1'}})
                yield sdk.StreamEvent(uuid='stream-2', session_id='fake', event={
                    'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'Hello governor.'}})
                yield sdk.AssistantMessage(content=[sdk.TextBlock(text='Hello governor.')], model=test.gov.model, message_id='message-1')
                yield sdk.ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                    num_turns=1, session_id='fake', usage={'input_tokens': 3, 'output_tokens': 2}, total_cost_usd=.001)
            async def interrupt(self):
                pass
        runtime = ClaudeRuntime(self.gov, AgentJournal(self.log, 'main', self.gov.author), tools,
            prompt_for(self.gov, tools, self.bounds), client_factory=Client)
        return runtime

    def test_sdk_lifetime_usage_and_authored_text(self):
        runtime = self.make_runtime()
        try:
            info = runtime.start()
            turn, messages = runtime.run_turn('hello', 'human-message')
            self.assertEqual(messages, ['Hello governor.'])
            self.assertEqual(info['account']['apiProvider'], 'firstParty')
            self.assertEqual(self.options.tools, [])
            self.assertEqual(self.options.setting_sources, [])
            self.assertTrue(self.options.strict_mcp_config)
            refs = self.log.results_for('main')
            self.assertEqual(refs[0]['author'], self.gov.author)
            events = [self.log.scribe.current(n).body for n in self.log.scribe.names() if n.startswith('harness/events/')]
            ids = {e['data']['id'] for e in events if e['kind'] in ('agent_delta', 'agent_text')}
            self.assertEqual(ids, {'message-1:1'})
        finally:
            runtime.close()

    def test_unexpected_sdk_inventory_stops_turn(self):
        runtime = self.make_runtime(tools_override=['Bash', 'Task'])
        try:
            runtime.start()
            with self.assertRaisesRegex(RuntimeError, 'unexpected tools'):
                runtime.run_turn('hello', 'human-message')
        finally:
            runtime.close()

    def test_substituted_model_stops_turn(self):
        runtime = self.make_runtime(model_override='claude-sonnet-5')
        try:
            runtime.start()
            with self.assertRaisesRegex(RuntimeError, 'unexpected tools or model'):
                runtime.run_turn('hello', 'human-message')
        finally:
            runtime.close()

    def test_pretool_hook_denies_native_and_allows_harness(self):
        runtime = self.make_runtime()
        denied = asyncio.run(runtime.pre_tool({'tool_name': 'Bash'}, 'id', {}))
        allowed = asyncio.run(runtime.pre_tool({'tool_name': 'mcp__nimoi__fs_read'}, 'id', {}))
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(allowed['hookSpecificOutput']['permissionDecision'], 'allow')


# Reuse setup helpers without collecting inherited test cases.
for name in vars(fixtures.HybridTests):
    if name.startswith('test_') and name not in ClaudeBackendTests.__dict__:
        setattr(ClaudeBackendTests, name, None)

if __name__ == '__main__':
    unittest.main()
