"""Subagents owned by the harness (rules.md task 5e, 5f): the `mcp__agents__spawn` tool.

Not the SDK's native subagents. Each child is its own Claude session, with its own bounds,
tools, checks and system prompt, recorded in the same ledger session.

A spawn, in order (every refusal names its reason and is recorded as tool_denied):
  1. the model must be on the allowlist, and the parent below the depth limit;
  2. the child's bounds, in the same notation as the parent's, must be a subset of the
     parent's (exclusions inherited) and keep fs.write and fs.exec apart (bounds.py);
  3. the harness's own records stay private: the child's ledger.read gets `!log`, unless
     the parent grants a log/ entry explicitly. Those records copy other agents' tool inputs
     and outputs, so ledger.read over log/ would show everything any agent has read
     (peer review, 2026-09-25);
  4. the child's fs.read must cover the latest onboarding, which the child must read in
     full before any other tool (agent.AgentCore.check);
  5. the instructions:
     - must be a revision the PARENT wrote;
     - are pinned to an exact id;
     - must be readable by the parent and within the child's ledger.read;
  6. a Claude child's budget is capped by what is left of the launch budget and, when the parent
     is a task owner, of that owner's allowance, which covers its whole subtree (task 6 live run,
     2026-09-29: the cap had been the launch budget alone). It must be at least
     MIN_CHILD_BUDGET_USD. A GPT child has no dollar budget: it reports tokens.
Then `subagent_start` is recorded (no record, no child). The child's first message is the
pinned instructions, word for word, recorded as a link to the parent's entry rather than
as a copy. The framing (onboarding first, report back) is in the child's system prompt.

The parent's call blocks until the child finishes (founder decision, 2026-09-25), and
returns the child's final reply. There is one spawn at a time per parent; parallel calls
queue. Interrupting the parent interrupts the child first.

Identity: ids form a tree (pilot -> pilot.1 -> pilot.1.1). Authors are
"<id>:<model>@claude-anthropic-harness", and records carry agent=<id> and the label
agent.<id>. Nesting is allowed to MAX_DEPTH below the top-level agent.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from claude_agent_sdk import tool

from agent import SPAWN_TOOL, AgentCore, HarnessEnv, lineage
from bounds import Bounds, BoundsError, fs_path
from ledger_tools import Denied, denied_reply
from ledgerlog import LOG_PREFIX
from prompts import latest_onboarding

# Task 6, subagent layer: "claude sonnet or higher, gpt terra or higher". A subagent's lineage may
# differ from its owner's; GPT subagents need Codex configured (env.codex).
MODELS = ("claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
# Who may spawn: task owners (and task 5's lone "pilot"). Subagents are the last layer, and the
# governor dispatches task owners instead (governance.py).
SPAWNING_ROLES = ("owner", "pilot")
MAX_DEPTH = 2  # governor/pilot (0) -> task owner (1) -> subagent (2); shown in prompts
MIN_CHILD_BUDGET_USD = 0.05
REPLY_CAP = 20000
LOG_NAME = LOG_PREFIX.rstrip("/")


def private_log(bounds: Bounds) -> Bounds:
    """Exclude log/ from ledger.read unless an allow entry names it explicitly (step 3 above)."""
    if any(a == LOG_NAME or a.startswith(LOG_PREFIX) for a in bounds.scope("ledger.read").allow):
        return bounds
    return bounds.with_exclusion("ledger.read", LOG_NAME)


def instruction_text(body: Any) -> str:
    return body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False)


class Spawner:
    def __init__(self, env: HarnessEnv, *, make_prompt: Callable[..., str], max_depth: int = MAX_DEPTH,
                 models: tuple[str, ...] = MODELS):
        self.env = env
        self.make_prompt = make_prompt  # (core fields) -> filled subagent system prompt
        self.max_depth = max_depth
        self.models = models
        self._counters: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def can_spawn(self, core: AgentCore) -> bool:
        return core.role in SPAWNING_ROLES

    def check(self, parent: AgentCore, *, model: Any, bounds_text: Any, instructions: Any,
              instructions_id: Any = None, budget_usd: Any = None) -> tuple[Bounds, str, dict[str, Any], float]:
        """Validate a spawn. Returns (child bounds, onboarding target, pinned instructions, budget)."""
        if model not in self.models:
            raise Denied(f"model {model!r} is not available; choose one of {', '.join(self.models)}")
        if lineage(model) == "gpt" and self.env.codex is None:
            raise Denied(f"{model} runs through Codex, which is not configured for this launch")
        if not self.can_spawn(parent):
            raise Denied(f"{parent.agent_id} ({parent.role}) cannot spawn subagents; only task owners can")
        if not isinstance(bounds_text, str):
            raise Denied("bounds must be text in the bounds notation")
        try:
            bounds = private_log(Bounds.parse(bounds_text).within(parent.bounds))
        except BoundsError as e:
            raise Denied(f"bounds refused: {e}") from e
        onboarding = latest_onboarding(self.env.root)
        if not bounds.permits("fs.read", onboarding):
            raise Denied(f"the child's fs.read must cover {onboarding}: it must read onboarding first")
        pinned_id = instructions_id or parent.guard.read_entry(instructions).id  # Denied if unreadable
        brief = parent.guard.read_line(pinned_id)  # exact revision, readable by the parent
        if brief["name"] != instructions:
            raise Denied(f"{pinned_id} is a revision of {brief['name']!r}, not of {instructions!r}")
        if brief["author"] != parent.author:
            raise Denied(f"instructions must be written by the parent ({parent.author}); {pinned_id} was "
                         f"written by {brief['author']}")
        if not bounds.permits("ledger.read", instructions):
            raise Denied(f"instructions {instructions!r} are outside the child's ledger.read "
                         f"({bounds.scope('ledger.read').render() or 'nothing'})")
        if lineage(model) != "claude":
            return bounds, onboarding, brief, None  # GPT on the ChatGPT plan: tokens, not dollars
        cap, what = self.env.budget.remaining, "left in the launch"
        institution = self.env.institution
        if institution is not None and parent.agent_id in institution.owners:
            left = institution.allowance_left(parent.agent_id)
            if left < cap:
                cap, what = left, f"left of {parent.agent_id}'s allowance (for it and its subagents)"
        budget = cap if budget_usd is None else min(float(budget_usd), cap)
        if budget < MIN_CHILD_BUDGET_USD:
            raise Denied(f"budget: ${cap:.2f} {what}, ${budget:.2f} for this child; a Claude subagent needs at "
                         f"least ${MIN_CHILD_BUDGET_USD:.2f}. A task owner can ask the governor for more_budget.")
        return bounds, onboarding, brief, budget

    async def spawn(self, parent: AgentCore, *, model: Any, bounds_text: Any, instructions: Any,
                    instructions_id: Any = None, budget_usd: Any = None) -> dict[str, Any]:
        lock = self._locks.setdefault(parent.agent_id, asyncio.Lock())
        async with lock:
            bounds, onboarding, brief, budget = self.check(
                parent, model=model, bounds_text=bounds_text, instructions=instructions,
                instructions_id=instructions_id, budget_usd=budget_usd)
            n = self._counters.get(parent.agent_id, 0) + 1
            child_id, depth = f"{parent.agent_id}.{n}", parent.depth + 1
            started = parent.pub("subagent_start", child=child_id, model=model, requested_bounds=bounds_text,
                                 bounds=bounds.render(), instructions=instructions, instructions_id=brief["id"],
                                 depth=depth, budget_usd=budget)
            if not started or not started.get("name"):
                raise Denied("the ledger could not record this spawn, so no subagent was started")
            self._counters[parent.agent_id] = n
            # The built-in-free file tools accept full paths; the prompt gives the full one.
            prompt = self.make_prompt(agent_id=child_id, parent_id=parent.agent_id, model=model, bounds=bounds,
                                      depth=depth, can_spawn=False, role="subagent",
                                      onboarding=str(fs_path(onboarding, self.env.root)),
                                      instructions=instructions, instructions_id=brief["id"])
            child = AgentCore(self.env, agent_id=child_id, model=model, bounds=bounds, system_prompt=prompt,
                              role="subagent", depth=depth, parent_id=parent.agent_id,
                              onboarding_required=onboarding, budget_usd=budget)  # None (GPT): no dollar tripwire applies
            child.announce_start(instructions_id=brief["id"])
            parent.children_running.add(child)
            status = "failed"
            try:
                async with child.backend:
                    child.turn = 1
                    result = await child.run_turn(instruction_text(brief["body"]), author=brief["author"],
                                                  link=f"[[{instructions}]]", link_id=brief["id"])
                status = "success" if not result.is_error else f"error: {result.subtype}"
            except Exception as e:
                child.pub("subagent_error", error=repr(e))
                status = f"failed: {e!r}"
            finally:
                parent.children_running.discard(child)
            tokens = child.tokens if child.lineage == "gpt" else None  # Claude children: dollars, tokens not tracked
            parent.pub("subagent_end", child=child_id, status=status, onboarding_read=child.onboarding_read,
                       final_reply=child.last_text_link, cost_usd=child.cost_usd, tokens=tokens,
                       num_turns=child.num_turns, session_cost_usd=self.env.budget.spent)
            return {"agent": child_id, "author": child.author, "model": model, "status": status,
                    "onboarding_read": child.onboarding_read, "final_reply_entry": child.last_text_link,
                    "final_reply": (child.last_text or "")[:REPLY_CAP], "cost_usd": child.cost_usd,
                    "tokens": tokens, "num_turns": child.num_turns, "session_cost_usd": self.env.budget.spent,
                    "bounds": bounds.render(), "instructions_id": brief["id"]}

    def tools_for(self, parent: AgentCore) -> list[Any]:
        """The spawn tool for `parent` (only built when the parent may spawn)."""

        @tool("spawn", "Start a subagent owned by the harness and wait for it to finish. First write its "
              "instructions as a ledger entry (they must be yours). `bounds` uses exactly the notation of your "
              "own bounds and must be within them: your exclusions are inherited, fs.write and fs.exec may not "
              "overlap, fs.read must cover the latest onboarding, and ledger.read excludes log/ unless you name "
              "a log/ entry. `instructions_id` pins a revision (default: the current one). `model` is one of "
              f"{', '.join(self.models)}. Returns the child's id, status, final reply (and its ledger entry), "
              "onboarding flag and cost.",
              {"type": "object", "properties": {
                  "model": {"type": "string", "enum": list(self.models)},
                  "bounds": {"type": "string", "description": "the child's bounds, one scope per line"},
                  "instructions": {"type": "string", "description": "ledger name of the instruction entry"},
                  "instructions_id": {"type": "string", "description": "optional: the exact revision id"},
                  "budget_usd": {"type": "number"}},
               "required": ["model", "bounds", "instructions"]})
        async def spawn_(args: dict[str, Any]) -> dict[str, Any]:
            try:
                out = await self.spawn(parent, model=args.get("model"), bounds_text=args.get("bounds"),
                                       instructions=args.get("instructions"),
                                       instructions_id=args.get("instructions_id"), budget_usd=args.get("budget_usd"))
                return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]}
            except Denied as e:
                return denied_reply(self.env.bus, parent.agent_id, SPAWN_TOOL, args, e)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"error: {e!r}"}], "is_error": True}

        return [spawn_]
