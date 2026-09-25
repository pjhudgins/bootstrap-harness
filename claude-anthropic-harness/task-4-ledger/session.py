"""One agent conversation, owned by one long-lived asyncio task (from task-3-ui/session.py).

Task 4 changes:
  - every record goes to the wiki ledger via the bus (ledgerlog.py), not a JSONL file;
  - ledger tools (ledger_tools.py) join calc; both MCP servers are expected;
  - cwd and the read policy root are the nimoi directory, minus candidate_repos/;
  - a system prompt (system_prompt.md) is filled in and recorded in full at session start.

The worker task enters and exits the ClaudeSDKClient context itself, because the
SDK's anyio task group must be entered and exited by the same task. Web requests
drop text into the inbox; only interrupt() crosses tasks.

States: starting -> idle <-> busy -> (over_budget | stopped | failed)
"""

import asyncio
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    ToolPermissionContext,
    UserMessage,
)

import calc_tool
import ledger_tools
from events import EventBus
from policy import ALLOWED_BUILTINS, EXCLUDED_DIRS, ToolPolicy

ACCOUNT_KEYS_LOGGED = ("subscriptionType", "apiProvider")  # founder decision: no email/organization
EXPECTED_MCP_SERVERS = {calc_tool.SERVER_NAME, ledger_tools.SERVER_NAME}


def direction(message: Any) -> str:
    if isinstance(message, AssistantMessage):
        return "from_agent"
    if isinstance(message, UserMessage):
        return "to_agent"  # e.g. tool results fed back to the model
    return "harness"


class AgentSession:
    def __init__(self, bus: EventBus, guard: ledger_tools.LedgerGuard, *, model: str, root: Path,
                 system_prompt: str, budget_usd: float, max_turns: int, ledger_facts: dict[str, Any]):
        self.bus = bus
        self.guard = guard
        self.model = model
        self.root = Path(root).resolve()
        self.system_prompt = system_prompt
        self.budget_usd = budget_usd
        self.max_turns = max_turns
        self.ledger_facts = ledger_facts  # recorded at session start
        self.policy = ToolPolicy(self.root)

        self.state = "starting"
        self.turn = 0
        # ResultMessage.total_cost_usd and model_usage are running session totals, not
        # per-turn (verified live 2026-09-23, task 3). Only ResultMessage.usage is per-turn.
        self.session_cost_usd = 0.0

        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._client: ClaudeSDKClient | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    # ---- public API used by the web layer -------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "turn": self.turn, "model": self.model,
                "session_cost_usd": self.session_cost_usd, "budget_usd": self.budget_usd}

    async def start(self, timeout: float = 90) -> None:
        self._task = asyncio.create_task(self._run(), name="agent-session")
        await asyncio.wait_for(self._ready.wait(), timeout)
        if self.state == "failed":
            raise RuntimeError("agent session failed to start; see the ledger")

    def send(self, text: str) -> str | None:
        """Queue a user message. Returns a refusal reason, or None if accepted."""
        if self.state != "idle":
            return f"agent is {self.state}"
        if self.session_cost_usd >= self.budget_usd:
            self._set_state("over_budget")
            return f"budget ${self.budget_usd:.2f} reached"
        self.turn += 1  # safe here: the state gate admits one message at a time
        self._set_state("busy")
        self._inbox.put_nowait(text)
        return None

    async def interrupt(self) -> bool:
        if self.state != "busy" or self._client is None:
            return False
        self.bus.publish("interrupt_requested")
        await self._client.interrupt()
        return True

    async def stop(self, timeout: float = 20) -> None:
        if self._task is None or self._task.done():
            return
        if self.state == "busy":
            try:
                await asyncio.wait_for(self.interrupt(), 5)
            except Exception as e:
                self.bus.publish("interrupt_error", error=repr(e))
        self._inbox.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except asyncio.TimeoutError:
            self.bus.publish("session_error", error="worker did not stop in time; cancelled")
            self._task.cancel()

    # ---- worker ---------------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        self.state = state
        self.bus.publish("status", **self.snapshot())

    async def _run(self) -> None:
        try:
            async with ClaudeSDKClient(options=self._options()) as client:
                self._client = client
                info = await client.get_server_info() or {}
                account = info.get("account") or {}
                self.bus.publish(
                    "session_start", model=self.model, cwd=str(self.root),
                    loaded_builtins=ALLOWED_BUILTINS, excluded_dirs=list(EXCLUDED_DIRS),
                    mcp_servers=sorted(EXPECTED_MCP_SERVERS), agent_author=self.guard.agent_author,
                    max_turns_per_message=self.max_turns, budget_usd=self.budget_usd,
                    account={k: account.get(k) for k in ACCOUNT_KEYS_LOGGED},
                    **self.ledger_facts, system_prompt=self.system_prompt,
                )
                self._set_state("idle")
                self._ready.set()
                while (text := await self._inbox.get()) is not None:
                    await self._turn(client, text)
        except Exception as e:
            self.bus.publish("session_error", error=repr(e))
            self._set_state("failed")
        finally:
            self._client = None
            if self.state != "failed":
                self._set_state("stopped")
            self._ready.set()

    async def _turn(self, client: ClaudeSDKClient, text: str) -> None:
        self.bus.log.context = {"turn": self.turn}
        self.bus.publish("message", direction="to_agent", message={"_type": "prompt", "text": text})
        try:
            await client.query(text)
            async for message in client.receive_response():
                self.bus.publish("message", direction=direction(message), message=message)
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    self.bus.publish("session_tools", tools=message.data.get("tools"),
                                     mcp_servers=message.data.get("mcp_servers"))
                elif isinstance(message, RateLimitEvent):
                    self.bus.publish("rate_limit", info=message.rate_limit_info)
                elif isinstance(message, ResultMessage):
                    self._record_usage(message)
            await self._record_session_facts(client)
        except Exception as e:
            self.bus.publish("turn_error", error=repr(e))
        finally:
            over = self.session_cost_usd >= self.budget_usd
            self._set_state("over_budget" if over else "idle")

    def _record_usage(self, m: ResultMessage) -> None:
        turn_cost = None
        if m.total_cost_usd is not None:
            turn_cost = m.total_cost_usd - self.session_cost_usd
            if turn_cost < 0:  # would break the running-total assumption; keep the higher figure
                self.bus.publish("cost_anomaly", reported=m.total_cost_usd, previous=self.session_cost_usd)
            self.session_cost_usd = max(self.session_cost_usd, m.total_cost_usd)
        self.bus.publish(
            "usage", reported_total_cost_usd=m.total_cost_usd, turn_cost_usd=turn_cost,
            session_cost_usd=self.session_cost_usd,
            budget_usd=self.budget_usd, usage=m.usage, model_usage=m.model_usage,
            num_turns=m.num_turns, duration_ms=m.duration_ms, duration_api_ms=m.duration_api_ms,
            is_error=m.is_error, subtype=m.subtype, permission_denials=m.permission_denials,
        )

    async def _record_session_facts(self, client: ClaudeSDKClient) -> None:
        # The init message under-reports MCP servers (connectors attach later); ask directly.
        status = await client.get_mcp_status()
        servers = [(s.get("name"), s.get("scope"), s.get("status")) for s in status.get("mcpServers", [])]
        self.bus.publish("mcp_status", servers=servers,
                         unexpected=[srv for srv, _, _ in servers if srv not in EXPECTED_MCP_SERVERS])
        try:
            usage = await client.get_context_usage()
            usage.pop("gridRows", None)  # UI rendering data, ~50KB of noise per record
            self.bus.publish("context_usage", usage=usage)
        except Exception as e:  # informative, not essential
            self.bus.publish("context_usage", error=repr(e))

    # ---- options --------------------------------------------------------------------------

    def _options(self) -> ClaudeAgentOptions:
        bus, policy = self.bus, self.policy

        async def pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            decision = policy.check(input_data["tool_name"], input_data.get("tool_input", {}))
            bus.publish("tool_call", phase="pre", tool_use_id=tool_use_id,
                        tool_name=input_data["tool_name"], tool_input=input_data.get("tool_input"),
                        policy_allow=decision.allow, policy_reason=decision.reason)
            if decision.allow:
                return {}  # continue into normal permission flow
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }}

        async def post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            bus.publish("tool_call", phase="post", tool_use_id=tool_use_id,
                        tool_name=input_data["tool_name"], tool_response=input_data.get("tool_response"))
            return {}

        async def post_tool_use_failure(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            bus.publish("tool_call", phase="failure", tool_use_id=tool_use_id,
                        tool_name=input_data["tool_name"], error=input_data.get("error"),
                        is_interrupt=input_data.get("is_interrupt"))
            return {}

        # Extension point: a UI approval step would await a user decision here.
        async def can_use_tool(tool_name: str, tool_input: dict, ctx: ToolPermissionContext):
            decision = policy.check(tool_name, tool_input)
            bus.publish("permission", tool_use_id=ctx.tool_use_id, tool_name=tool_name,
                        allow=decision.allow, reason=decision.reason,
                        cli_decision_reason=ctx.decision_reason, blocked_path=ctx.blocked_path)
            if decision.allow:
                return PermissionResultAllow()
            return PermissionResultDeny(message=decision.reason)

        return ClaudeAgentOptions(
            model=self.model,
            cwd=str(self.root),
            system_prompt=self.system_prompt,
            tools=list(ALLOWED_BUILTINS),  # execution/write tools are never loaded
            mcp_servers={
                calc_tool.SERVER_NAME: calc_tool.build_server(),
                ledger_tools.SERVER_NAME: ledger_tools.build_server(self.guard),
            },
            strict_mcp_config=True,  # keep account claude.ai connectors and nimoi/.mcp.json out
            can_use_tool=can_use_tool,
            hooks={
                "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
                "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
                "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_use_failure])],
            },
            setting_sources=[],  # no CLAUDE.md, memory, hooks or plugins from nimoi or the user
            max_turns=self.max_turns,  # per user message
            max_budget_usd=self.budget_usd,  # SDK tripwire; the harness gate in send() is the real stop
            extra_args={"no-session-persistence": None},
            stderr=lambda line: bus.publish("cli_stderr", line=line),
        )
