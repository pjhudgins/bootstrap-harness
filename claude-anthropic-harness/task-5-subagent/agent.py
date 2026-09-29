"""Agents: identity, bounds, tools, checks, and how each agent's stream is recorded.

    HarnessEnv   what every agent in one launch shares: ledger, bus, paths, budget, spawner,
                 and the client factory (tests substitute a fake client)
    BudgetPool   one spending budget for the launch; every agent's running cost counts
    AgentCore    one agent, top-level or subagent: its bounds, tools, checks and recording

(Split out of session.py on 2026-09-25. AgentSession, the top-level conversation worker,
stays there.)

Every tool is harness-owned; the CLI's built-in tools are not loaded (files.py says why).
So AgentCore.check is only three things:
  - fail closed: once the ledger cannot record, no tool runs;
  - the allowlist: exactly the tools built for this agent's bounds;
  - the onboarding gate for subagents: until the latest onboarding has been read in full,
    line 1 to the last line, only reading that file is allowed.
Targets (paths, entries, scripts) are checked inside each tool on the resolved target
(files.FsAccess, ledger_tools.LedgerGuard). The PreToolUse hook and can_use_tool both
call check.

Recording (ledger first, then the page; each record names its agent and turn):
  - message text is its own entry by its writer, then a message record links to it;
  - tool calls: pre (with the decision), post and failure. A large response is stored as a
    digest, because the full response is already in the message stream;
  - usage, rate limits, MCP status and a compact context-usage summary;
  - tool_inventory_mismatch if the session's tool list is not exactly what was built;
  - unexpected_tool_use if the model calls a tool it was not given.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

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
    create_sdk_mcp_server,
)

import calc_tool
import exec_tool
import files
import ledger_tools
from bounds import Bounds, fs_path
from files import Decision
from ledgerlog import to_jsonable, wikilink

ACCOUNT_KEYS_LOGGED = ("subscriptionType", "apiProvider")  # founder decision: no email/organization
SPAWN_SERVER, SPAWN_TOOL = "agents", "mcp__agents__spawn"
DIGEST_OVER_CHARS = 2000


def mcp_name(server: str, tool_name: str) -> str:
    return f"mcp__{server}__{tool_name}"


def agent_author(model: str, agent_id: str = "pilot") -> str:
    """An agent's ledger author: its id (pilot, pilot.1, ...), model and harness. Never from the model."""
    return f"{agent_id}:{model}@claude-anthropic-harness"


def direction(message: Any) -> str:
    if isinstance(message, AssistantMessage):
        return "from_agent"
    if isinstance(message, UserMessage):
        return "to_agent"  # e.g. tool results fed back to the model
    return "harness"


def planned_tools(bounds: Bounds, can_spawn: bool) -> list[str]:
    """The tools an agent with these bounds gets: its prompt lists them, and its allowlist is them."""
    names = [mcp_name(calc_tool.SERVER_NAME, "add")]
    if bounds.scope("fs.read").allow:
        names += [mcp_name(files.SERVER_NAME, t) for t in ("list", "read", "search")]
    if bounds.scope("fs.write").allow:
        names.append(mcp_name(files.SERVER_NAME, "write"))
    if bounds.scope("fs.exec").allow:
        names.append(mcp_name(exec_tool.SERVER_NAME, "python"))
    if bounds.scope("ledger.read").allow:
        names += [mcp_name(ledger_tools.SERVER_NAME, t) for t in ("read", "list")]
    if bounds.scope("ledger.write").allow:
        names.append(mcp_name(ledger_tools.SERVER_NAME, "write"))
    if can_spawn:
        names.append(SPAWN_TOOL)
    return names


def compact(value: Any, limit: int = DIGEST_OVER_CHARS) -> Any:
    """`value`, or a digest of it if its JSON is longer than `limit` (the full value is recorded elsewhere)."""
    text = json.dumps(to_jsonable(value), ensure_ascii=False)
    if len(text) <= limit:
        return value
    return {"digest": {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars": len(text),
                       "preview": text[:500]}}


def default_client_factory(options: ClaudeAgentOptions, core: "AgentCore") -> Any:
    return ClaudeSDKClient(options=options)


class BudgetPool:
    """One budget for the launch. Each agent's cost is its SDK-reported running total (a running
    total, not per turn: task 3 finding), so spent = the sum of the latest totals."""

    def __init__(self, total_usd: float):
        self.total = total_usd
        self._costs: dict[str, float] = {}

    def record(self, agent_id: str, cumulative_usd: float) -> None:
        self._costs[agent_id] = max(self._costs.get(agent_id, 0.0), cumulative_usd)

    @property
    def spent(self) -> float:
        return sum(self._costs.values())

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)

    def by_agent(self) -> dict[str, float]:
        return dict(self._costs)


@dataclass
class HarnessEnv:
    scribe: Any  # the scribe module
    ledger: Any  # the open scribe.Scribe
    bus: Any  # events.EventBus
    root: Path  # nimoi
    workspace: Path
    never_writable: list[str]  # bounds targets no agent may write (the scripts dir)
    budget: BudgetPool
    max_turns: int  # per user message / per subagent run
    harness_author: str
    ledger_facts: dict[str, Any] = field(default_factory=dict)
    spawner: Any = None  # subagents.Spawner, attached after construction
    client_factory: Callable[[ClaudeAgentOptions, "AgentCore"], Any] = default_client_factory

    @property
    def ledger_failed(self) -> str | None:
        return self.bus.log.failed


class AgentCore:
    def __init__(self, env: HarnessEnv, *, agent_id: str, model: str, bounds: Bounds, system_prompt: str,
                 depth: int = 0, parent_id: str | None = None, onboarding_required: str | None = None,
                 budget_usd: float | None = None):
        self.env = env
        self.agent_id = agent_id
        self.model = model
        self.bounds = bounds
        self.system_prompt = system_prompt
        self.depth = depth
        self.parent_id = parent_id
        self.author = agent_author(model, agent_id)
        self.budget_usd = env.budget.total if budget_usd is None else budget_usd  # SDK tripwire
        self.can_spawn = env.spawner is not None and env.spawner.can_spawn(depth)

        self.guard = ledger_tools.LedgerGuard(env.scribe, env.ledger, self.author, bounds, agent_id)
        self.files = files.FsAccess(env.root, bounds, env.never_writable)
        self.file_tools = files.FileTools(access=self.files, guard=self.guard, bus=env.bus, agent_id=agent_id,
                                          on_read=self._note_read)
        self.exec_tool = exec_tool.ExecTool(access=self.files, bus=env.bus, workspace=env.workspace,
                                            agent_id=agent_id)
        self.tool_defs = self._build_tools()  # {server: [SdkMcpTool]}
        self.tool_handlers = {mcp_name(s, t.name): t.handler for s, ts in self.tool_defs.items() for t in ts}
        self.allowed_tools = frozenset(self.tool_handlers)
        self.mcp_servers = {s: create_sdk_mcp_server(name=s, version="0.1.0", tools=ts)
                            for s, ts in self.tool_defs.items()}

        # Subagents must read onboarding, in full, before anything else (rules.md 5e; peer review:
        # a partial read must not count). Top-level agents are told to.
        self.onboarding_required = onboarding_required
        self.onboarding_read = onboarding_required is None
        self._onboarding_next_line = 1

        self.turn = 0
        self.cost_usd = 0.0  # this agent's running total
        self.num_turns = 0
        self.client: Any = None
        self.children_running: set[AgentCore] = set()
        self.last_text: str | None = None
        self.last_text_link: str | None = None
        self._turn_links: dict[str, str] = {}

    # ---- tools ----------------------------------------------------------------------------

    def _build_tools(self) -> dict[str, list[Any]]:
        defs = {calc_tool.SERVER_NAME: calc_tool.tools(), files.SERVER_NAME: self.file_tools.tools(),
                exec_tool.SERVER_NAME: self.exec_tool.tools(),
                ledger_tools.SERVER_NAME: ledger_tools.tools_for(self.guard, self.env.bus),
                SPAWN_SERVER: self.env.spawner.tools_for(self) if self.can_spawn else []}
        return {server: tools for server, tools in defs.items() if tools}

    def _note_read(self, target: str, start: int, end: int, total: int) -> None:
        """Onboarding counts as read once pages have covered line 1 to the last line, contiguously."""
        if self.onboarding_read or target.casefold() != self.onboarding_required.casefold():
            return
        if start <= self._onboarding_next_line <= end + 1:
            self._onboarding_next_line = max(self._onboarding_next_line, end + 1)
        if self._onboarding_next_line > total:
            self.onboarding_read = True
            self.pub("onboarding_read", path=target, lines=total)

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        if self.env.ledger_failed:
            return Decision(False, "the ledger can no longer record, so the harness has stopped acting (fail closed)")
        if tool_name not in self.allowed_tools:
            return Decision(False, f"{tool_name} is not available to this agent; its tools are "
                                   f"{sorted(self.allowed_tools)}")
        if not self.onboarding_read:
            if tool_name == mcp_name(files.SERVER_NAME, "read"):
                d = self.files.check("fs.read", tool_input.get("path"))
                if d.allow and d.target.casefold() == self.onboarding_required.casefold():
                    return Decision(True, "reading onboarding")
            full = fs_path(self.onboarding_required, self.env.root)
            return Decision(False, f"read the latest onboarding in full first: mcp__fs__read with path {full}, "
                                   "then the following pages until next_offset is null. The harness allows "
                                   "no other tool until you have.")
        return Decision(True, "available to this agent; the tool checks its own target")

    # ---- recording --------------------------------------------------------------------

    def pub(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """Publish a record about this agent. agent and turn are defaults; fields may override them."""
        return self.env.bus.publish(kind, **{"agent": self.agent_id, "turn": self.turn, **fields})

    def announce_start(self, **extra: Any) -> None:
        self.pub("session_start" if self.parent_id is None else "subagent_session",
                 model=self.model, parent=self.parent_id, depth=self.depth, cwd=str(self.env.root),
                 bounds=self.bounds.render(), tools=sorted(self.allowed_tools),
                 mcp_servers=sorted(self.mcp_servers), agent_author=self.author, can_spawn=self.can_spawn,
                 onboarding_required=self.onboarding_required, budget_usd=self.budget_usd,
                 max_turns=self.env.max_turns, **self.env.ledger_facts, **extra,
                 system_prompt=self.system_prompt)

    def log_text(self, text: str, *, author: str, direction: str) -> str:
        """Write text as its own ledger entry first; return the wikilink that replaces it
        (or the text itself if the ledger write failed, so it is never lost)."""
        record = self.env.bus.publish_text(text, author=author, direction=direction,
                                           agent=self.agent_id, turn=self.turn)
        link = wikilink(record) if record else None
        if link:
            self._turn_links[text] = link
        return link or text

    def message_body(self, message: Any) -> Any:
        """The stream message as JSON, with agent text replaced by wikilinks to its own entries."""
        body = to_jsonable(message)
        if isinstance(message, AssistantMessage):
            for block in body.get("content") or []:
                if block.get("_type") == "ToolUseBlock" and block.get("name") not in self.allowed_tools:
                    self.pub("unexpected_tool_use", tool_name=block.get("name"), tool_use_id=block.get("id"))
                if block.get("_type") == "TextBlock" and block.get("text"):
                    text = block["text"]
                    block["text"] = self.log_text(text, author=self.author, direction="from_agent")
                    self.last_text, self.last_text_link = text, block["text"] if block["text"] != text else None
        elif isinstance(message, ResultMessage) and message.result in self._turn_links:
            body["result"] = self._turn_links[message.result]
        return body

    async def run_turn(self, client: Any, text: str, *, author: str, link: str | None = None,
                       link_id: str | None = None) -> ResultMessage | None:
        """Send one message and record everything until the result. Exceptions propagate.

        When the text already is a ledger entry (a subagent's pinned instructions), pass its
        `link` and `link_id`: the message record links to it rather than copying it."""
        self._turn_links = {}
        if link:
            self.pub("message", direction="to_agent", message={"_type": "prompt", "text": link},
                     text_id=link_id, text_author=author)
        else:
            sent = self.log_text(text, author=author, direction="to_agent")
            self.pub("message", direction="to_agent", message={"_type": "prompt", "text": sent})
        result = None
        await client.query(text)
        async for message in client.receive_response():
            self.pub("message", direction=direction(message), message=self.message_body(message))
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self._check_inventory(message.data)
            elif isinstance(message, RateLimitEvent):
                self.pub("rate_limit", info=message.rate_limit_info)
            elif isinstance(message, ResultMessage):
                result = message
                self._record_usage(message)
        await self._record_session_facts(client)
        return result

    def _check_inventory(self, data: dict[str, Any]) -> None:
        reported = set(data.get("tools") or [])
        self.pub("session_tools", tools=sorted(reported), mcp_servers=data.get("mcp_servers"))
        if reported != set(self.allowed_tools):
            self.pub("tool_inventory_mismatch", expected=sorted(self.allowed_tools), reported=sorted(reported),
                     missing=sorted(set(self.allowed_tools) - reported), extra=sorted(reported - set(self.allowed_tools)))

    def _record_usage(self, m: ResultMessage) -> None:
        turn_cost = None
        if m.total_cost_usd is not None:
            turn_cost = m.total_cost_usd - self.cost_usd
            if turn_cost < 0:  # would break the running-total assumption; keep the higher figure
                self.pub("cost_anomaly", reported=m.total_cost_usd, previous=self.cost_usd)
            self.cost_usd = max(self.cost_usd, m.total_cost_usd)
            self.env.budget.record(self.agent_id, self.cost_usd)
        self.num_turns += m.num_turns or 0
        self.pub("usage", reported_total_cost_usd=m.total_cost_usd, turn_cost_usd=turn_cost,
                 agent_cost_usd=self.cost_usd, session_cost_usd=self.env.budget.spent,
                 budget_usd=self.env.budget.total, usage=m.usage, model_usage=m.model_usage,
                 num_turns=m.num_turns, duration_ms=m.duration_ms, duration_api_ms=m.duration_api_ms,
                 is_error=m.is_error, subtype=m.subtype, permission_denials=m.permission_denials,
                 # subtype can say "success" on an API failure (task 4 finding), so keep these too.
                 api_error_status=m.api_error_status, stop_reason=m.stop_reason, errors=m.errors,
                 result=self._turn_links.get(m.result, m.result) if m.is_error else None)

    async def _record_session_facts(self, client: Any) -> None:
        # The init message under-reports MCP servers (connectors attach later); ask directly.
        status = await client.get_mcp_status()
        servers = [(s.get("name"), s.get("scope"), s.get("status")) for s in status.get("mcpServers", [])]
        self.pub("mcp_status", servers=servers,
                 unexpected=[srv for srv, _, _ in servers if srv not in self.mcp_servers])
        try:
            u = await client.get_context_usage()
            self.pub("context_usage", usage={  # a summary: the full report is ~10 KB per turn
                "totalTokens": u.get("totalTokens"), "maxTokens": u.get("maxTokens"),
                "categories": [{"name": c.get("name"), "tokens": c.get("tokens")} for c in u.get("categories", [])],
                "memoryFiles": u.get("memoryFiles"),  # how the auto-memory leak was found (task 4)
                "mcpTools": sorted({t.get("name") for t in u.get("mcpTools") or []})})
        except Exception as e:  # informative, not essential
            self.pub("context_usage", error=repr(e))

    async def interrupt(self) -> None:
        """Interrupt this agent and, first, any subagents it is waiting on."""
        for child in list(self.children_running):
            await child.interrupt()
        if self.client is not None:
            self.pub("interrupt_requested")
            await self.client.interrupt()

    # ---- SDK options ------------------------------------------------------------------

    def options(self) -> ClaudeAgentOptions:
        async def pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            decision = self.check(input_data["tool_name"], input_data.get("tool_input", {}))
            self.pub("tool_call", phase="pre", tool_use_id=tool_use_id, tool_name=input_data["tool_name"],
                     tool_input=input_data.get("tool_input"), policy_allow=decision.allow,
                     policy_reason=decision.reason)
            if decision.allow:
                return {}  # continue into normal permission flow
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": decision.reason}}

        async def post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            self.pub("tool_call", phase="post", tool_use_id=tool_use_id, tool_name=input_data["tool_name"],
                     tool_response=compact(input_data.get("tool_response")))
            return {}

        async def post_tool_use_failure(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            self.pub("tool_call", phase="failure", tool_use_id=tool_use_id, tool_name=input_data["tool_name"],
                     error=input_data.get("error"), is_interrupt=input_data.get("is_interrupt"))
            return {}

        # Extension point: a UI approval step would await a user decision here.
        async def can_use_tool(tool_name: str, tool_input: dict, ctx: ToolPermissionContext):
            decision = self.check(tool_name, tool_input)
            self.pub("permission", tool_use_id=ctx.tool_use_id, tool_name=tool_name, allow=decision.allow,
                     reason=decision.reason, cli_decision_reason=ctx.decision_reason,
                     blocked_path=ctx.blocked_path)
            if decision.allow:
                return PermissionResultAllow()
            return PermissionResultDeny(message=decision.reason)

        return ClaudeAgentOptions(
            model=self.model,
            cwd=str(self.env.root),
            system_prompt=self.system_prompt,
            tools=[],  # no built-in tools at all: every tool is harness-owned (files.py says why)
            mcp_servers=self.mcp_servers,
            strict_mcp_config=True,  # keep account claude.ai connectors and nimoi/.mcp.json out
            can_use_tool=can_use_tool,
            hooks={
                "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
                "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
                "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_use_failure])],
            },
            setting_sources=[],  # no CLAUDE.md, hooks or plugins from nimoi or the user
            # Auto memory loads regardless of setting_sources (task 4 finding); documented off switch.
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            max_turns=self.env.max_turns,
            max_budget_usd=self.budget_usd,  # SDK tripwire; the harness's own checks are the real stop
            extra_args={"no-session-persistence": None},
            stderr=lambda line: self.pub("cli_stderr", line=line),
        )
