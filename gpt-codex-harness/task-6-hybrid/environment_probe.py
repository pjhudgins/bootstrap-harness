"""Read installed runtime metadata without credentials or user configuration."""
import importlib.metadata
import inspect
import json
import asyncio
import sys


async def models(sdk):
    options = sdk.ClaudeAgentOptions(tools=[], setting_sources=[], strict_mcp_config=True,
        env={'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1', 'ANTHROPIC_API_KEY': ''},
        extra_args={'no-session-persistence': None})
    async with sdk.ClaudeSDKClient(options=options) as client:
        info = await client.get_server_info() or {}
        print(json.dumps({'models': info.get('models'),
            'account': {k: (info.get('account') or {}).get(k)
                        for k in ('subscriptionType', 'apiProvider')}}, indent=2))

if __name__ == '__main__':
    import claude_agent_sdk as sdk
    if '--types' in sys.argv:
        print({name: getattr(sdk, name).__annotations__ for name in
               ('AssistantMessage', 'UserMessage', 'StreamEvent', 'ResultMessage', 'RateLimitEvent')})
        raise SystemExit()
    if '--models' in sys.argv:
        asyncio.run(models(sdk))
        raise SystemExit()
    print(json.dumps({'sdk_version': importlib.metadata.version('claude-agent-sdk'),
                      'module': sdk.__file__,
                      'options': str(inspect.signature(sdk.ClaudeAgentOptions)),
                      'client_methods': [n for n in dir(sdk.ClaudeSDKClient) if not n.startswith('_')]}, indent=2))
