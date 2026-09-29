"""Agents: identity, bounds, tools, checks and recording, independent of the model backend.

    HarnessEnv   what every agent in one launch shares: ledger, bus, paths, budget, spawner,
                 and the backend factory (tests substitute fakes)
    BudgetPool   one spending budget for the launch; every Claude agent's running cost counts
    AgentCore    one agent (governor, task owner or subagent): its bounds, tools, checks,
                 onboarding gate and records
    TurnResult   what a backend reports when a turn ends

Task 6 splits the model backend out of task 5's AgentCore. A Claude agent runs through
backend_claude.ClaudeBackend (the Claude Agent SDK); a GPT agent runs through
backend_codex.CodexBackend (the Codex App Server). Both call back into the core:
  - check(tool, input): the same checks for every agent and backend
      1. fail closed: once the ledger cannot record, no tool runs;
      2. the allowlist: exactly the tools built for this agent's role and bounds;
      3. the onboarding gate (subagents, task owners): until the latest onboarding has
         been read in full, line 1 to the last line, only reading that file is allowed.
    Targets (paths, entries, scripts) are checked inside each tool on the resolved target.
  - call_tool(...): check, record, run the handler, record. It is used by backends that
    have no hooks of their own (Codex); the Claude backend reaches the same records
    through SDK hooks.
  - log_text / record_prompt / record_usage: text entries by their writers, message
    records linking to them, usage, all with the agent's id and turn.

Limits (task 6): each agent has its own max_turns, the model rounds allowed per message, which
both backends enforce themselves, so the governor can raise it while a turn runs
(governance.py, more_budget). Bounds can change while an agent runs (expand_bounds): the checks
read core.bounds on every call. An agent's tools can therefore be built from wider
`tool_bounds` (a task owner's: the governor's ceiling) than the bounds it starts with; a tool
whose scope is empty refuses every target.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import calc_tool
import exec_tool
import files
import ledger_tools
from bounds import Bounds, fs_path
from files import Decision
from ledgerlog import to_jsonable, wikilink
from tools import ToolDef

ACCOUNT_KEYS_LOGGED = ("subscriptionType", "apiProvider")  # founder decision: no email/organization
SPAWN_SERVER, SPAWN_TOOL = "agents", "mcp__agents__spawn"
GOV_SERVER = "gov"  # governance.py's role tools (the governor's, and owners' `request`)
DIGEST_OVER_CHARS = 2000


def mcp_name(server: str, tool_name: str) -> str:
    return f"mcp__{server}__{tool_name}"


def lineage(model: str) -> str:
    """Which backend runs a model: "claude" or "gpt"."""
    if model.startswith("claude-"):
        return "claude"
    if model.startswith("gpt-"):
        return "gpt"
    raise ValueError(f"unknown model lineage: {model!r}")


def agent_author(model: str, agent_id: str = "pilot", session: str | None = None) -> str:
    """An agent's ledger author: its id, the ledger session it lives in, its model and the harness.
    Never taken from the model. Task 6 adds the session, so a new session's T1 is not an older one's."""
    at = f"{agent_id}@{session}" if session else agent_id
    return f"{at}:{model}@claude-anthropic-harness"


def planned_tools(bounds: Bounds, can_spawn: bool, extra: list[str] = ()) -> list[str]:
    # `bounds` here are the agent's tool bounds (AgentCore(tool_bounds=...)), which may be wider
    # than the bounds it starts with.
    """The tools an agent with these bounds gets (its prompt lists them; its allowlist is them).
    `extra` names role tools (governance.py) added on top of the bounds-driven set."""
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
    return names + list(extra)


def compact(value: Any, limit: int = DIGEST_OVER_CHARS) -> Any:
    """`value`, or a digest of it if its JSON is longer than `limit` (the full value is recorded elsewhere)."""
    text = json.dumps(to_jsonable(value), ensure_ascii=False)
    if len(text) <= limit:
        return value
    return {"digest": {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars": len(text),
                       "preview": text[:500]}}


def default_backend_factory(core: "AgentCore") -> Any:
    """Pick the backend by model lineage (imported lazily: each backend pulls in its own SDK)."""
    if lineage(core.model) == "claude":
        from backend_claude import ClaudeBackend
        return ClaudeBackend(core)
    from backend_codex import CodexBackend
    return CodexBackend(core)


def default_client_factory(options: Any, core: "AgentCore") -> Any:
    from claude_agent_sdk import ClaudeSDKClient
    return ClaudeSDKClient(options=options)


@dataclass
class TurnResult:
    is_error: bool
    subtype: str  # "success", an SDK error subtype, "interrupted", "stopped", "failed", ...
    final_text: str | None = None


class BudgetPool:
    """One budget for the launch. Each Claude agent's cost is its SDK-reported running total (a
    running total, not per turn: task 3 finding), so spent = the sum of the latest totals. GPT agents
    on the ChatGPT account report tokens, not dollars; they are limited by turns and time instead."""

    def __init__(self, total_usd: float):
        self.total = total_usd
        self._costs: dict[str, float] = {}
        self._tokens: dict[str, int] = {}

    def record(self, agent_id: str, cumulative_usd: float) -> None:
        self._costs[agent_id] = max(self._costs.get(agent_id, 0.0), cumulative_usd)

    def record_tokens(self, agent_id: str, cumulative_tokens: int) -> None:
        self._tokens[agent_id] = max(self._tokens.get(agent_id, 0), cumulative_tokens)

    def raise_total(self, extra_usd: float) -> None:
        self.total += extra_usd

    @property
    def spent(self) -> float:
        return sum(self._costs.values())

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)

    def by_agent(self) -> dict[str, float]:
        return dict(self._costs)

    def tokens_by_agent(self) -> dict[str, int]:
        return dict(self._tokens)


@dataclass
class HarnessEnv:
    scribe: Any  # the scribe module
    ledger: Any  # the open scribe.Scribe
    bus: Any  # events.EventBus
    root: Path  # nimoi
    workspace: Path  # task 7: the project folder (scripts' working directory, where drafts are promoted from)
    never_writable: list[str]  # bounds targets no agent may write (the scripts dir)
    budget: BudgetPool
    max_turns: int  # per user message / per subagent run
    harness_author: str
    ledger_facts: dict[str, Any] = field(default_factory=dict)
    spawner: Any = None  # subagents.Spawner, attached after construction
    institution: Any = None  # governance.Institution, attached after construction (task 6)
    backend_factory: Callable[["AgentCore"], Any] = default_backend_factory
    client_factory: Callable[[Any, "AgentCore"], Any] = default_client_factory  # Claude SDK client
    codex: Any = None  # backend_codex.CodexSettings (binary, CODEX_HOME, ...); None = Codex not configured
    never_accessible: list[str] = field(default_factory=list)  # task 7: bounds targets no file tool reaches (ledger dir)
    onboarding: str | None = None  # task 7: the project's fixed onboarding file, as a bounds target
    write_root: str | None = None  # task 7: the project folder, as a bounds target; no write lands outside it

    @property
    def ledger_failed(self) -> str | None:
        return self.bus.log.failed

    @property
    def session(self) -> str:
        return self.ledger.session


class AgentCore:
    def __init__(self, env: HarnessEnv, *, agent_id: str, model: str, bounds: Bounds, system_prompt: str,
                 role: str = "pilot", depth: int = 0, parent_id: str | None = None,
                 onboarding_required: str | None = None, budget_usd: float | None = None,
                 extra_tools: list[ToolDef] = (), tool_bounds: Bounds | None = None,
                 max_turns: int | None = None):
        self.env = env
        self.agent_id = agent_id
        self.model = model
        self.lineage = lineage(model)
        self.role = role  # "governor", "owner", "subagent" (task 6); "pilot" for a lone top-level agent
        self.tool_bounds = tool_bounds or bounds  # which tools are built; `bounds` decides what they allow
        self.bounds = self.tool_bounds
        self.system_prompt = system_prompt
        self.depth = depth
        self.parent_id = parent_id
        self.author = agent_author(model, agent_id, env.session)
        self.budget_usd = env.budget.total if budget_usd is None else budget_usd  # SDK tripwire (Claude)
        self.max_turns = env.max_turns if max_turns is None else max_turns  # model rounds per message
        self.can_spawn = env.spawner is not None and env.spawner.can_spawn(self)

        self.guard = ledger_tools.LedgerGuard(env.scribe, env.ledger, self.author, self.tool_bounds, agent_id,
                                              role_label=role)
        self.files = files.FsAccess(env.root, self.tool_bounds, env.never_writable, env.never_accessible,
                                    env.write_root)
        self.file_tools = files.FileTools(access=self.files, guard=self.guard, bus=env.bus, agent_id=agent_id,
                                          on_read=self._note_read)
        self.exec_tool = exec_tool.ExecTool(access=self.files, bus=env.bus, workspace=env.workspace,
                                            agent_id=agent_id)
        self.tool_defs: list[ToolDef] = self._build_tools(list(extra_tools))
        self.tool_handlers = {t.full_name: t.handler for t in self.tool_defs}
        self.allowed_tools = frozenset(self.tool_handlers)
        self.set_bounds(bounds)  # tools are built; from here on, `bounds` decides every target

        # Task owners and subagents must read onboarding in full before anything else (rules.md 5e;
        # task 6 applies it to owners too). The governor is told to.
        self.onboarding_required = onboarding_required
        self.onboarding_read = onboarding_required is None
        self._onboarding_next_line = 1

        self.turn = 0
        self.cost_usd = 0.0  # this agent's running total (Claude)
        self.tokens = 0  # this agent's running total (GPT)
        self.num_turns = 0
        self.children_running: set[AgentCore] = set()
        self.last_text: str | None = None
        self.last_text_link: str | None = None
        self._turn_links: dict[str, str] = {}
        self.backend = env.backend_factory(self)
        self.active = False  # True while the backend session is open

    # ---- tools ----------------------------------------------------------------------------

    def set_bounds(self, bounds: Bounds) -> None:
        """The bounds every check reads from now on (at start, and on an approved expand_bounds)."""
        self.bounds = bounds
        self.guard.bounds = bounds
        self.files.bounds = bounds

    def _build_tools(self, extra: list[ToolDef]) -> list[ToolDef]:
        defs: list[ToolDef] = []
        for server, sdk_tools in ((calc_tool.SERVER_NAME, calc_tool.tools()),
                                  (files.SERVER_NAME, self.file_tools.tools()),
                                  (exec_tool.SERVER_NAME, self.exec_tool.tools()),
                                  (ledger_tools.SERVER_NAME, ledger_tools.tools_for(self.guard, self.env.bus)),
                                  (SPAWN_SERVER, self.env.spawner.tools_for(self) if self.can_spawn else []),
                                  (GOV_SERVER, self.env.institution.tools_for(self) if self.env.institution else [])):
            defs += [ToolDef.from_sdk(server, t) for t in sdk_tools]
        return defs + extra

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

    async def call_tool(self, name: str, tool_input: dict[str, Any], tool_use_id: str) -> dict[str, Any]:
        """Check, record, run and record one tool call, for backends without hooks (Codex)."""
        decision = self.check(name, tool_input)
        self.pub("tool_call", phase="pre", tool_use_id=tool_use_id, tool_name=name, tool_input=tool_input,
                 policy_allow=decision.allow, policy_reason=decision.reason)
        if not decision.allow:
            return {"content": [{"type": "text", "text": f"error: denied: {decision.reason}"}], "is_error": True}
        try:
            out = await self.tool_handlers[name](tool_input)
        except Exception as e:  # handlers return errors as results; this is a harness bug
            self.pub("tool_call", phase="failure", tool_use_id=tool_use_id, tool_name=name, error=repr(e))
            return {"content": [{"type": "text", "text": f"error: {e!r}"}], "is_error": True}
        self.pub("tool_call", phase="post", tool_use_id=tool_use_id, tool_name=name,
                 tool_response=compact(out.get("content")))
        return out

    # ---- recording --------------------------------------------------------------------

    def pub(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """Publish a record about this agent. agent and turn are defaults; fields may override them."""
        return self.env.bus.publish(kind, **{"agent": self.agent_id, "turn": self.turn, **fields})

    def announce_start(self, **extra: Any) -> None:
        self.pub("session_start" if self.parent_id is None else f"{self.role}_session",  # owner_/subagent_session
                 role=self.role, model=self.model, lineage=self.lineage, backend=self.backend.kind,
                 parent=self.parent_id, depth=self.depth, cwd=str(self.env.root), bounds=self.bounds.render(),
                 tools=sorted(self.allowed_tools), agent_author=self.author, can_spawn=self.can_spawn,
                 onboarding_required=self.onboarding_required, budget_usd=self.budget_usd,
                 max_turns=self.env.max_turns, **self.env.ledger_facts, **extra, system_prompt=self.system_prompt)

    def log_text(self, text: str, *, author: str, direction: str) -> str:
        """Write text as its own ledger entry first; return the wikilink that replaces it
        (or the text itself if the ledger write failed, so it is never lost)."""
        record = self.env.bus.publish_text(text, author=author, direction=direction,
                                           agent=self.agent_id, turn=self.turn)
        link = wikilink(record) if record else None
        if link:
            self._turn_links[text] = link
        return link or text

    def record_prompt(self, text: str, *, author: str, link: str | None = None, link_id: str | None = None) -> None:
        """The message sent to the agent. When it already is a ledger entry (a pinned brief), link it."""
        self._turn_links = {}
        if link:
            self._turn_links[text] = link  # so echoes of the brief (Codex) are recorded as the link too
            self.pub("message", direction="to_agent", message={"_type": "prompt", "text": link},
                     text_id=link_id, text_author=author)
        else:
            sent = self.log_text(text, author=author, direction="to_agent")
            self.pub("message", direction="to_agent", message={"_type": "prompt", "text": sent})

    def record_agent_text(self, text: str) -> str:
        """Text the agent produced: its own entry by this agent; returns the link that replaces it."""
        link = self.log_text(text, author=self.author, direction="from_agent")
        self.last_text, self.last_text_link = text, link if link != text else None
        return link

    def link_for(self, text: str | None) -> str | None:
        return self._turn_links.get(text) if text else None

    def record_usage(self, *, cumulative_usd: float | None = None, cumulative_tokens: int | None = None,
                     num_turns: int | None = None, is_error: bool, subtype: str, **raw: Any) -> None:
        turn_cost = None
        if cumulative_usd is not None:
            turn_cost = cumulative_usd - self.cost_usd
            if turn_cost < 0:  # would break the running-total assumption; keep the higher figure
                self.pub("cost_anomaly", reported=cumulative_usd, previous=self.cost_usd)
            self.cost_usd = max(self.cost_usd, cumulative_usd)
            self.env.budget.record(self.agent_id, self.cost_usd)
        if cumulative_tokens is not None:
            self.tokens = max(self.tokens, cumulative_tokens)
            self.env.budget.record_tokens(self.agent_id, self.tokens)
        self.num_turns += num_turns or 0
        self.pub("usage", lineage=self.lineage, turn_cost_usd=turn_cost, agent_cost_usd=self.cost_usd,
                 agent_tokens=self.tokens, session_cost_usd=self.env.budget.spent, budget_usd=self.env.budget.total,
                 num_turns=num_turns, is_error=is_error, subtype=subtype, **raw)

    # ---- running ----------------------------------------------------------------------

    async def run_turn(self, text: str, *, author: str, link: str | None = None,
                       link_id: str | None = None) -> TurnResult:
        """Send one message through the backend and record everything until the turn ends."""
        return await self.backend.run_turn(text, author=author, link=link, link_id=link_id)

    async def interrupt(self) -> None:
        """Interrupt this agent and, first, any subagents it is waiting on."""
        for child in list(self.children_running):
            await child.interrupt()
        if self.active:
            self.pub("interrupt_requested")
            await self.backend.interrupt()
