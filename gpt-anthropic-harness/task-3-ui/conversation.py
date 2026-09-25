"""One launch's state and one long-lived SDK task; HTTP never touches the SDK."""

import asyncio
import copy
from importlib.metadata import version
import threading
from uuid import uuid4

from policy import TOOL_NAME, add_numbers, allowed


class Conversation:
    def __init__(self, log, model):
        self.log = log
        self.lock = threading.RLock()
        self.loop = None
        self.queue = None
        self.stopping = False
        self.data = {"id": uuid4().hex, "status": "starting", "model": model,
                     "messages": [], "activity": [], "usage": None, "limits": None,
                     "account": {}, "error": None, "revision": 0, "journal": log.path.name}

    def update(self, **fields):
        with self.lock:
            self.data.update(self.log.clean(fields))
            self.data["revision"] += 1

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def activity(self, label, **fields):
        with self.lock:
            self.data["activity"].append(self.log.clean({"label": label, **fields}))
            self.data["revision"] += 1

    def submit(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError("Enter a message of 1–16,000 characters.")
        with self.lock:
            if self.stopping or self.data["status"] != "ready":
                raise RuntimeError("The conversation is not ready for another message.")
            turn = uuid4().hex
            self.log.write("user_message_accepted", turn=turn, text=text)
            self.data["messages"].append({"role": "user", "text": self.log.clean(text), "turn": turn})
            self.update(status="thinking")
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (turn, text))
            return turn

    def assistant_text(self, turn, text):
        with self.lock:
            messages = self.data["messages"]
            if messages and messages[-1]["role"] == "assistant" and messages[-1]["turn"] == turn:
                messages[-1]["text"] += "\n\n" + self.log.clean(text)
            else:
                messages.append({"role": "assistant", "text": self.log.clean(text), "turn": turn})
            self.data["revision"] += 1

    def stop(self):
        with self.lock:
            if not self.stopping:
                self.stopping = True
                self.update(status="stopping")
                if self.loop and self.queue and not self.loop.is_closed():
                    try:
                        self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
                    except RuntimeError:
                        pass  # A failed worker may close its loop during shutdown.


async def serve_conversation(state, task_dir):
    # Lazy import keeps HTTP/state tests independent of SDK and credentials.
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
        PermissionResultAllow, PermissionResultDeny, RateLimitEvent, ResultMessage,
        SystemMessage, TextBlock, ToolUseBlock, UserMessage, create_sdk_mcp_server, tool,
    )
    log = state.log
    turn = None
    unexpected = []

    async def pre_tool(data, tool_id, context):
        permit = allowed(data["tool_name"], data.get("tool_input", {}))
        log.write("tool_request", turn=turn, tool_id=tool_id, name=data["tool_name"],
                  arguments=data.get("tool_input"), allowed=permit)
        if data["tool_name"] != TOOL_NAME:
            unexpected.append(data["tool_name"])
        if not permit:
            state.activity("Tool denied", name=data["tool_name"])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "permissionDecision": "allow" if permit else "deny",
                "permissionDecisionReason": "Only finite-number Python addition is permitted"}}

    async def permission(name, args, context):
        permit = allowed(name, args)
        log.write("permission", turn=turn, tool_id=context.tool_use_id, name=name, allowed=permit)
        return PermissionResultAllow() if permit else PermissionResultDeny(message="Tool denied")

    async def post_tool(data, tool_id, context):
        log.write("tool_outcome", turn=turn, tool_id=tool_id, event=data.get("hook_event_name"),
                  name=data.get("tool_name"), response=data.get("tool_response"), error=data.get("error"))
        return {}

    @tool("add", "Add two finite numbers using Python.", {
        "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"], "additionalProperties": False,
    })
    async def add(args):
        log.write("python_tool_start", turn=turn, arguments=args)
        try:
            total = add_numbers(args)
        except ValueError as exc:
            log.write("python_tool_error", turn=turn, error=str(exc))
            state.activity("Addition rejected", reason=str(exc))
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        log.write("python_tool_result", turn=turn, arguments=args, result=total)
        state.activity("Python add", a=args["a"], b=args["b"], result=total)
        return {"content": [{"type": "text", "text": str(total)}]}

    async def check_servers(client, required):
        status = await client.get_mcp_status()
        servers = status.get("mcpServers", [])
        log.write("mcp_status", turn=turn, servers=[{k: s.get(k) for k in ("name", "status", "scope")}
                                                   for s in servers], required=required)
        if not servers and not required:
            return
        if len(servers) != 1 or servers[0].get("name") != "calc" or servers[0].get("status") != "connected":
            raise RuntimeError("Calculator connection or tool isolation check failed.")

    options = ClaudeAgentOptions(
        model=state.data["model"], cwd=str(task_dir), tools=[], setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={"calc": create_sdk_mcp_server(name="calc", version="0.1.0", tools=[add])},
        can_use_tool=permission, max_turns=6,
        system_prompt="You are an assistant in a local NIMOI prototype conversation. "
                      "You may discuss any topic with the user. Your only tool is Python addition. "
                      "Be honest about tool use and limitations. If a request is impossible, "
                      "ambiguous, or malformed, raise the issue and ask the user for guidance.",
        extra_args={"no-session-persistence": None},
        hooks={"PreToolUse": [HookMatcher(hooks=[pre_tool])],
               "PostToolUse": [HookMatcher(hooks=[post_tool])],
               "PostToolUseFailure": [HookMatcher(hooks=[post_tool])]},
        stderr=lambda line: log.write("cli_stderr", line=line),
    )
    state.loop = asyncio.get_running_loop()
    state.queue = asyncio.Queue()
    log.write("configuration", conversation=state.data["id"], sdk_version=version("claude-agent-sdk"),
              model=options.model, builtin_tools=[], custom_tools=[TOOL_NAME], max_turns=6,
              timeout_seconds=180, strict_mcp_config=True, setting_sources=[],
              session_persistence=False, system_prompt=options.system_prompt)
    async with ClaudeSDKClient(options=options) as client:
        account = (await client.get_server_info() or {}).get("account") or {}
        account = {k: account.get(k) for k in ("subscriptionType", "apiProvider")}
        log.write("account", **account)
        await check_servers(client, False)
        state.update(account=account, status="stopping" if state.stopping else "ready")
        if state.stopping:
            return
        while True:
            job = await state.queue.get()
            if job is None:
                break
            turn, text = job
            result = None
            text_seen = False
            log.write("prompt_send_attempt", turn=turn, text=text)
            async with asyncio.timeout(180):
                await client.query(text)
                log.write("prompt_sent", turn=turn)
                async for message in client.receive_response():
                    if isinstance(message, SystemMessage) and message.subtype == "init":
                        names = message.data.get("tools", [])
                        log.write("session_init", turn=turn, model=message.data.get("model"), tools=names)
                        state.update(model=message.data.get("model") or options.model)
                        if set(names) != {TOOL_NAME}:
                            raise RuntimeError("Unexpected initialized tool set.")
                    else:
                        direction = ("from_agent" if isinstance(message, AssistantMessage) else
                                     "to_agent" if isinstance(message, UserMessage) else "runtime")
                        log.write("message", turn=turn, direction=direction, message=message)
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                state.assistant_text(turn, block.text)
                                text_seen = True
                            elif isinstance(block, ToolUseBlock) and block.name != TOOL_NAME:
                                unexpected.append(block.name)
                    elif isinstance(message, RateLimitEvent):
                        log.write("rate_limit", turn=turn, info=message.rate_limit_info)
                        info = message.rate_limit_info
                        state.update(limits={"status": info.status, "type": info.rate_limit_type,
                                             "resets_at": info.resets_at, "utilization": info.utilization})
                    elif isinstance(message, ResultMessage):
                        result = message
                        usage = {"last_prompt": message.usage, "model_usage": message.model_usage,
                                 "total_cost_usd": message.total_cost_usd, "num_turns": message.num_turns}
                        log.write("usage", turn=turn, **usage)
                        state.update(usage=usage)
                await check_servers(client, True)
            if not result or result.is_error or not text_seen or unexpected:
                log.write("turn_failed", turn=turn, subtype=getattr(result, "subtype", None),
                          unexpected_tools=unexpected)
                raise RuntimeError("The reply did not complete successfully. Restart for a fresh conversation.")
            log.write("turn_complete", turn=turn, session_id=result.session_id)
            state.update(status="stopping" if state.stopping else "ready")


def worker(state, task_dir):
    try:
        asyncio.run(serve_conversation(state, task_dir))
    except Exception as exc:
        state.log.write("conversation_error", error_type=type(exc).__name__, error=str(exc))
        state.update(status="error", error=f"{type(exc).__name__}: {exc}")
        state.log.write("conversation_closed", success=False)
    else:
        state.update(status="closed")
        state.log.write("conversation_closed", success=True)
