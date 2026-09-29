"""An independent SDK session for each harness-owned actor."""
import asyncio
from importlib.metadata import version
from uuid import uuid4

from tools import SERVER, ToolService
from ledger import NIMOI
from messages import record_sdk_message
from prompts import build_prompt


class AgentSession:
    def __init__(self, log, task_dir, state, context, manager=None, *, client_factory=None, root=NIMOI):
        self.log, self.task_dir, self.state = log, task_dir, state
        self.context = context
        self.model, self.author = context.model, context.author
        self.client_factory = client_factory
        self.service = ToolService(log, context.bounds, context.author, root=root, manager=manager, state=state, context=context)
        self.turn = None
        self.unexpected = []

    async def __aenter__(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
        options = ClaudeAgentOptions(model=self.model, cwd=str(self.task_dir), tools=[], setting_sources=[],
            strict_mcp_config=True, mcp_servers={SERVER: self.service.build_server(lambda: self.turn)},
            can_use_tool=self.permission, max_turns=16, system_prompt=build_prompt(self.context, self.service),
            extra_args={"no-session-persistence": None},
            hooks={"PreToolUse": [HookMatcher(hooks=[self.pre_tool])],
                   "PostToolUse": [HookMatcher(hooks=[self.post_tool])],
                   "PostToolUseFailure": [HookMatcher(hooks=[self.post_tool])]},
            stderr=lambda line: self.log.write("cli_stderr", actor=self.author, line=line))
        self.log.write("configuration", actor=self.author, sdk_version=version("claude-agent-sdk"),
            model=self.model, bounds=self.service.bounds.data(), mounts=self.service.bounds.mounts, native_tools=[],
            custom_tools=sorted(self.service.full_names), max_turns=16, timeout_seconds=240,
            system_prompt=options.system_prompt)
        self.client = (self.client_factory or ClaudeSDKClient)(options=options)
        await self.client.__aenter__()
        try:
            account = (await self.client.get_server_info() or {}).get("account") or {}
            account = {k: account.get(k) for k in ("subscriptionType", "apiProvider")}
            self.log.write("account", actor=self.author, **account)
            if self.context.is_parent:
                self.state.update(account=account)
            await self.check_servers(False)
        except BaseException:
            await self.client.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def check_servers(self, required):
        servers = (await self.client.get_mcp_status()).get("mcpServers", [])
        self.log.write("mcp_status", actor=self.author, turn=self.turn,
            servers=[{k: s.get(k) for k in ("name", "status", "scope")} for s in servers], required=required)
        if not servers and not required:
            return
        if len(servers) != 1 or servers[0].get("name") != SERVER or servers[0].get("status") != "connected":
            raise RuntimeError("Tool server isolation check failed.")

    async def pre_tool(self, data, tool_id, context):
        permit = self.service.allowed(data["tool_name"], data.get("tool_input", {}))
        self.log.write("tool_request", actor=self.author, turn=self.turn, tool_id=tool_id,
            name=data["tool_name"], arguments=data.get("tool_input"), dispatch_valid=permit, stage="dispatch_validation")
        if data["tool_name"] not in self.service.full_names:
            self.unexpected.append(data["tool_name"])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "allow" if permit else "deny", "permissionDecisionReason": "Dispatch validation; capability checks run in handler"}}

    async def permission(self, name, args, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        permit = self.service.allowed(name, args)
        self.log.write("permission", actor=self.author, turn=self.turn, name=name, dispatch_valid=permit, stage="dispatch_validation")
        return PermissionResultAllow() if permit else PermissionResultDeny(message="Tool denied")

    async def post_tool(self, data, tool_id, context):
        self.log.write("tool_outcome", actor=self.author, turn=self.turn, tool_id=tool_id,
            event=data.get("hook_event_name"), name=data.get("tool_name"),
            response=data.get("tool_response"), error=data.get("error"))
        return {}

    async def query(self, text, turn=None, *, text_ref):
        from claude_agent_sdk import AssistantMessage, SystemMessage, UserMessage, TextBlock, ToolUseBlock, RateLimitEvent, ResultMessage
        self.turn = turn or uuid4().hex
        result, texts = None, []
        self.log.write("prompt_send_attempt", actor=self.author, turn=self.turn, **text_ref)
        async with asyncio.timeout(240):
            await self.client.query(text)
            self.log.write("prompt_sent", actor=self.author, turn=self.turn)
            async for message in self.client.receive_response():
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    names = message.data.get("tools", [])
                    self.log.write("session_init", actor=self.author, turn=self.turn, model=message.data.get("model"), tools=names)
                    if set(names) != self.service.full_names:
                        raise RuntimeError("Unexpected initialized tool set, including native delegation.")
                    if self.context.is_parent:
                        self.state.update(model=message.data.get("model") or self.model)
                else:
                    direction = "from_agent" if isinstance(message, AssistantMessage) else "to_agent" if isinstance(message, UserMessage) else "runtime"
                    record_sdk_message(self.log, self.turn, direction, message, agent_author=self.author)
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            texts.append(block.text)
                            if not message.error:
                                self.state.assistant_text(self.turn, block.text, actor=self.context.agent_id,
                                                          label="Claude" if self.context.is_parent else self.context.agent_id)
                        elif isinstance(block, ToolUseBlock) and block.name not in self.service.full_names:
                            self.unexpected.append(block.name)
                elif isinstance(message, RateLimitEvent):
                    info = message.rate_limit_info
                    self.log.write("rate_limit", actor=self.author, turn=self.turn, info=info)
                    if self.context.is_parent:
                        self.state.update(limits={"status": info.status, "type": info.rate_limit_type,
                                                  "resets_at": info.resets_at, "utilization": info.utilization})
                elif isinstance(message, ResultMessage):
                    result = message
                    usage = {"last_prompt": message.usage, "model_usage": message.model_usage,
                             "total_cost_usd": message.total_cost_usd, "num_turns": message.num_turns}
                    self.log.write("usage", actor=self.author, turn=self.turn, **usage)
                    self.state.record_usage(self.context, usage)
            await self.check_servers(True)
        if not result or result.is_error or not texts or self.unexpected or not self.service.onboarded:
            self.log.write("turn_failed", actor=self.author, turn=self.turn, subtype=getattr(result, "subtype", None),
                           unexpected_tools=self.unexpected, onboarded=self.service.onboarded)
            raise RuntimeError("Reply failed or onboarding incomplete; inspect the ledger and restart.")
        self.log.write("turn_complete", actor=self.author, turn=self.turn, session_id=result.session_id)
        return "\n\n".join(texts)


async def run_child(log, task_dir, state, context, entry):
    async with AgentSession(log, task_dir, state, context) as agent:
        return await agent.query(entry["body"], text_ref={"instructions_id": entry["id"],
                                 "text": f"[[{entry['name']}]]", "instructed_by": entry["author"]})
