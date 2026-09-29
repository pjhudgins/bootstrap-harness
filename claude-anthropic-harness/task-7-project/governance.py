"""Governance (task 6): the governor, task owners, and the requests between them.

    Institution   one per launch: the owners, requests and human approvals, the role tools,
                  and the events delivered to the governor
    OwnerRun      one task owner: its agent, its background task and inbox, its state
    Request       one owner request to the governor; the owner's tool call waits on it
    Approval      a decision only the human can make (a card in the UI)

Roles (rules.md 6c):
  - Governor (claude-opus-5-5 only):
      - talks with the human; dispatches and supervises task owners; resolves their requests;
      - does not do owners' work, and does not grade their quality beyond what safety and
        governance need;
      - its tools make this structural: fs.read, its own ledger area and the role tools below;
        no fs write, no exec, no subagents (founder decision, 2026-09-28).
  - Task owner (OWNER_MODELS):
      - pursues one assignment, with the task-5 toolset plus `request`;
      - owns the quality of its work, and sets its own bar;
      - the governor passes the task and the rules under which success can be achieved, never
        the success bar (founder, 2026-09-29). The harness refuses assignments, and follow-up
        messages, that carry a `Bar:` line.
  - Subagent: task 5's spawn (subagents.py), from owners only.

Dispatch (the governor's `dispatch`), in order; every refusal names its reason:
  1. the model is on the owner list, and fewer than MAX_OWNERS owners are open;
  2. the bounds are within the CEILING, a human-edited file, with the same rules as spawn:
     exclusions inherited, fs.write and fs.exec apart, `!log` unless a log/ entry is named,
     fs.read covering the latest onboarding;
  3. the assignment:
       - is a revision the governor wrote, pinned to an exact id;
       - is within the owner's ledger.read;
       - has no `Bar:` line;
  4. `owner_start` is recorded (no record, no owner). The owner then starts in the background,
     and dispatch returns its id at once.
The owner's first message is the pinned assignment, word for word, recorded as a link to the
governor's entry. The owner must read onboarding in full before any other tool (the gate).
When an owner's turn ends, the governor gets an event with its reply. An idle owner keeps its
session until it is stopped; `message_owner` gives it a new instruction.

Requests (the owner's `request`, self-blocking). The four kinds are the founder's decision
(2026-09-28):
  clarify         a question; the governor answers, asking the human in chat if it must
  expand_bounds   the owner's full bounds as it wants them, within the ceiling; the governor
                  approves (optionally narrower) or denies; approved bounds apply at once
  promote_script  copy a draft from the project folder into the scripts directory. It always needs the human:
                  the governor denies it, or forwards it with its assessment, and the human
                  decides in the UI. The harness snapshots the draft's bytes when the request
                  is made, and on approval writes exactly those bytes, recording their sha256;
                  so what was reviewed is what is promoted
  more_budget     more dollars (a Claude owner's allowance, within the launch budget) and/or
                  more model rounds per message (the live limit for both lineages)
Each request is checked and recorded first. The owner's tool call then waits until the request
is resolved, and the governor gets an event.

Budget, stated plainly:
  - the launch budget is the hard cap for every Claude agent (it is also the SDK tripwire);
  - each owner has a dollar allowance that covers its subtree: its own cost (Claude owners) and
    its subagents' (live run, 2026-09-29: a subagent's budget came from the whole launch budget,
    not its owner's allowance). A GPT owner's allowance covers the Claude subagents it spawns;
  - the allowance is checked when each of the owner's turns starts, and it caps each Claude
    subagent's budget at spawn (subagents.Spawner). It does not cap an owner's own turn;
  - within a turn, model rounds (max_turns) are the limit that can be raised live, for both
    lineages;
  - GPT agents report tokens, not dollars.

Events reach the governor between its turns, never during one (session.AgentSession.deliver).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from agent import AgentCore, HarnessEnv, TurnResult, lineage
from bounds import Bounds, BoundsError, fs_path, fs_target
from ledger_tools import Denied, denied_reply
from ledgerlog import HUMAN_AUTHOR, wikilink
from prompts import latest_onboarding
from subagents import instruction_text, private_log

GOV_SERVER = "gov"
GOVERNOR_MODELS = ("claude-opus-5-5",)  # rules.md 6c b1: claude opus only
OWNER_MODELS = ("claude-opus-5-5", "claude-fable-5-1", "gpt-6-astra", "gpt-6-sol", "gpt-5.6-sol")  # 6c b2
MAX_OWNERS = 3
OWNER_MAX_TURNS = 40  # model rounds per message, unless the governor sets another at dispatch
TURNS_CAP = 200
DEFAULT_ALLOWANCE_USD = 1.50  # a Claude owner's allowance, unless the governor sets one
MIN_ALLOWANCE_USD = 0.10
REQUEST_KINDS = ("clarify", "expand_bounds", "promote_script", "more_budget")
OPEN_STATES = ("starting", "working", "waiting", "idle")
EVENT_REPLY_CHARS = 4000
SCRIPT_MAX_BYTES = 200_000
SCRIPT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}\.py")
BAR_LINE = re.compile(r"(?im)^[\W_]*bar\s*:")  # "Bar:", "**Bar:**", "- bar :" at the start of a line
NO_BAR = ("the governor passes the task and the rules under which success can be achieved, never the success "
          "bar (founder, 2026-09-29): remove the Bar: line; the owner sets its own")

GOVERNOR_TOOLS = ("dispatch", "status", "message_owner", "stop_owner", "resolve")
OWNER_TOOLS = ("request",)


def role_tool_names(role: str) -> list[str]:
    names = GOVERNOR_TOOLS if role == "governor" else OWNER_TOOLS if role == "owner" else ()
    return [f"mcp__{GOV_SERVER}__{n}" for n in names]


@dataclass
class OwnerRun:
    core: AgentCore
    assignment: str
    assignment_id: str
    allowance_usd: float  # dollars for the owner's subtree: its own cost (Claude) and its subagents'
    state: str = "starting"
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    turns: int = 0
    last_status: str | None = None
    stop_reason: str | None = None  # the governor stopped it (stop_owner), or the backend did
    close_reason: str | None = None  # the launch closed it (close_all)


@dataclass
class Request:
    id: str
    owner: str
    kind: str
    justification: str
    details: dict[str, Any]
    future: asyncio.Future
    state: str = "pending"  # pending, awaiting_human, approved, denied, answered, cancelled
    resolution: dict[str, Any] | None = None
    snapshot: bytes | None = None  # promote_script: the draft's bytes when the request was made


@dataclass
class Approval:
    id: str
    request: str
    owner: str
    kind: str
    assessment: str  # the governor's
    details: dict[str, Any]
    state: str = "pending"  # pending, approved, denied


class Institution:
    def __init__(self, env: HarnessEnv, *, ceiling: Bounds, scripts_dir: Path,
                 make_owner_prompt: Callable[..., str], owner_models: tuple[str, ...] = OWNER_MODELS,
                 max_owners: int = MAX_OWNERS, owner_max_turns: int = OWNER_MAX_TURNS):
        problems = ceiling.invariant_problems()
        if problems:
            raise BoundsError("ceiling: " + "; ".join(problems))
        self.env = env
        self.ceiling = ceiling
        self.scripts_dir = scripts_dir
        self.make_owner_prompt = make_owner_prompt
        self.owner_models = owner_models
        self.max_owners = max_owners
        self.owner_max_turns = owner_max_turns
        self.governor: AgentCore | None = None
        self.owners: dict[str, OwnerRun] = {}
        self.requests: dict[str, Request] = {}
        self.approvals: dict[str, Approval] = {}
        self.events: list[str] = []  # every event, delivered or not
        self.deliver: Callable[[str], None] | None = None  # the governor's session sets this
        self._n = {"owner": 0, "request": 0, "approval": 0}

    # ---- helpers ------------------------------------------------------------------------------

    def _event(self, text: str) -> None:
        self.events.append(text)
        if self.deliver is not None:
            self.deliver(text)

    def _pub(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """A governance record, about the governor (who dispatches and resolves)."""
        return self.governor.pub(kind, **fields) if self.governor else self.env.bus.publish(kind, agent=None, **fields)

    def subtree_cost(self, owner_id: str) -> float:
        """Dollars spent by an owner and its subagents (owner.1, owner.1.1, ...)."""
        return sum(v for k, v in self.env.budget.by_agent().items() if k == owner_id or k.startswith(owner_id + "."))

    def allowance_left(self, owner_id: str) -> float:
        run = self.owners[owner_id]
        return max(0.0, run.allowance_usd - self.subtree_cost(owner_id))

    def _state(self, run: OwnerRun, state: str) -> None:
        run.state = state
        core = run.core
        self._pub("owner_state", owner=core.agent_id, state=state, model=core.model, turns=run.turns,
                  cost_usd=core.cost_usd, subtree_cost_usd=self.subtree_cost(core.agent_id), tokens=core.tokens,
                  max_turns=core.max_turns, allowance_usd=run.allowance_usd, bounds=core.bounds.render())

    def _run(self, owner: Any) -> OwnerRun:
        run = self.owners.get(owner) if isinstance(owner, str) else None
        if run is None:
            raise Denied(f"no owner {owner!r}; owners: {sorted(self.owners) or 'none'}")
        return run

    def _turn_refusal(self, run: OwnerRun) -> str | None:
        if self.env.ledger_failed:
            return "the ledger can no longer record (fail closed)"
        if run.core.lineage == "claude" and self.env.budget.spent >= self.env.budget.total:
            return f"the launch budget (${self.env.budget.total:.2f}) is spent"
        spent = self.subtree_cost(run.core.agent_id)
        if run.core.lineage == "claude" and spent >= run.allowance_usd:
            return (f"its allowance (${run.allowance_usd:.2f}) is spent (${spent:.2f} by it and its subagents); "
                    "grant more_budget or stop it")
        return None

    # ---- dispatch -----------------------------------------------------------------------------

    def check_dispatch(self, governor: AgentCore, *, model: Any, bounds_text: Any, assignment: Any,
                       assignment_id: Any = None, budget_usd: Any = None,
                       max_turns: Any = None) -> tuple[Bounds, str, dict[str, Any], str, float | None, int]:
        if governor.role != "governor":
            raise Denied("only the governor dispatches task owners")
        if model not in self.owner_models:
            raise Denied(f"model {model!r} is not a task-owner model; choose one of {', '.join(self.owner_models)}")
        if lineage(model) == "gpt" and self.env.codex is None:
            raise Denied(f"{model} runs through Codex, which is not configured for this launch")
        open_owners = [o for o, r in self.owners.items() if r.state in OPEN_STATES]
        if len(open_owners) >= self.max_owners:
            raise Denied(f"{self.max_owners} owners are open ({', '.join(open_owners)}); stop one first")
        if not isinstance(bounds_text, str):
            raise Denied("bounds must be text in the bounds notation")
        try:
            bounds = private_log(Bounds.parse(bounds_text).within(self.ceiling))
        except BoundsError as e:
            raise Denied(f"bounds refused (the ceiling is the limit): {e}") from e
        onboarding = self.env.onboarding or latest_onboarding(self.env.root)  # task 7: the project's fixed file
        if not bounds.permits("fs.read", onboarding):
            raise Denied(f"the owner's fs.read must cover {onboarding}: it must read onboarding first")
        pinned_id = assignment_id or governor.guard.read_entry(assignment).id
        brief = governor.guard.read_line(pinned_id)
        if brief["name"] != assignment:
            raise Denied(f"{pinned_id} is a revision of {brief['name']!r}, not of {assignment!r}")
        if brief["author"] != governor.author:
            raise Denied(f"the assignment must be written by the governor ({governor.author}); {pinned_id} was "
                         f"written by {brief['author']}")
        if BAR_LINE.search(instruction_text(brief["body"])):
            raise Denied(NO_BAR)
        if not bounds.permits("ledger.read", assignment):
            raise Denied(f"assignment {assignment!r} is outside the owner's ledger.read "
                         f"({bounds.scope('ledger.read').render() or 'nothing'})")
        remaining = self.env.budget.remaining
        allowance = min(float(budget_usd) if budget_usd is not None else DEFAULT_ALLOWANCE_USD, remaining)
        if lineage(model) == "claude" and allowance < MIN_ALLOWANCE_USD:
            raise Denied(f"budget: ${remaining:.2f} left in the launch; a Claude owner needs at least "
                         f"${MIN_ALLOWANCE_USD:.2f}")
        turns = self.owner_max_turns if max_turns is None else int(max_turns)
        if not 1 <= turns <= TURNS_CAP:
            raise Denied(f"max_turns must be 1 to {TURNS_CAP}")
        return bounds, onboarding, brief, pinned_id, allowance, turns

    async def dispatch(self, governor: AgentCore, **kw: Any) -> dict[str, Any]:
        bounds, onboarding, brief, pinned_id, allowance, turns = self.check_dispatch(governor, **kw)
        owner_id = f"owner.{self._n['owner'] + 1}"
        model, assignment = kw["model"], kw["assignment"]
        started = governor.pub("owner_start", owner=owner_id, model=model, requested_bounds=kw["bounds_text"],
                               bounds=bounds.render(), ceiling=self.ceiling.render(), assignment=assignment,
                               assignment_id=pinned_id, allowance_usd=allowance, max_turns=turns)
        if not started or not started.get("name"):
            raise Denied("the ledger could not record this dispatch, so no owner was started")
        self._n["owner"] += 1
        prompt = self.make_owner_prompt(agent_id=owner_id, parent_id=governor.agent_id, model=model, bounds=bounds,
                                        tool_bounds=self.ceiling, onboarding=str(fs_path(onboarding, self.env.root)),
                                        assignment=assignment, assignment_id=pinned_id)
        core = AgentCore(self.env, agent_id=owner_id, model=model, bounds=bounds, tool_bounds=self.ceiling,
                         system_prompt=prompt, role="owner", depth=1, parent_id=governor.agent_id,
                         onboarding_required=onboarding, max_turns=turns)  # SDK tripwire: the launch budget
        run = OwnerRun(core, assignment, pinned_id, allowance)
        self.owners[owner_id] = run
        run.inbox.put_nowait((instruction_text(brief["body"]), brief["author"], f"[[{assignment}]]", pinned_id))
        run.task = asyncio.create_task(self._run_owner(run), name=f"owner-{owner_id}")
        return {"owner": owner_id, "author": core.author, "model": model, "bounds": bounds.render(),
                "assignment_id": pinned_id, "allowance_usd": allowance, "max_turns": turns,
                "note": "The owner runs in the background. You get a harness message when its turn ends, or when "
                        "it asks you something."}

    async def _run_owner(self, run: OwnerRun) -> None:
        core = run.core
        status = "closed"
        try:
            core.announce_start(assignment=run.assignment, assignment_id=run.assignment_id,
                                allowance_usd=run.allowance_usd)
            async with core.backend:
                while (item := await run.inbox.get()) is not None:
                    text, author, link, link_id = item
                    refusal = self._turn_refusal(run)
                    if refusal:
                        core.pub("turn_refused", reason=refusal)
                        self._state(run, "idle")
                        self._event(f"[harness] Task owner {core.agent_id}'s turn was not started: {refusal}.")
                        continue
                    core.turn += 1
                    run.turns += 1
                    self._state(run, "working")
                    try:
                        result = await core.run_turn(text, author=author, link=link, link_id=link_id)
                    except Exception as e:
                        core.pub("turn_error", error=repr(e))
                        result = TurnResult(True, f"failed: {e!r}", core.last_text)
                    run.last_status = result.subtype
                    if result.subtype == "stopped":  # the backend stopped it (e.g. a native Codex call)
                        status = "stopped"
                        run.stop_reason = run.stop_reason or "stopped by the harness (see native_call_stop)"
                        break
                    self._state(run, "idle")
                    self._event(self._turn_report(run, result))
        except Exception as e:
            core.pub("owner_error", error=repr(e))
            status = f"failed: {e!r}"
        finally:
            for req in self.requests.values():
                if req.owner == core.agent_id and req.state in ("pending", "awaiting_human"):
                    self._finish(req, "cancelled", "the owner has ended")
            if run.stop_reason and status == "closed":
                status = "stopped"
            self._state(run, "stopped" if status == "stopped" else "failed" if status.startswith("failed") else "closed")
            self._pub("owner_end", owner=core.agent_id, status=status, reason=run.stop_reason or run.close_reason,
                      turns=run.turns,
                      cost_usd=core.cost_usd, tokens=core.tokens, onboarding_read=core.onboarding_read,
                      final_reply=core.last_text_link, session_cost_usd=self.env.budget.spent)
            if status != "closed" or run.close_reason is None:
                self._event(f"[harness] Task owner {core.agent_id} has ended: {status}"
                            + (f" ({run.stop_reason})" if run.stop_reason else "") + ".")

    def _turn_report(self, run: OwnerRun, result: TurnResult) -> str:
        core = run.core
        reply = core.last_text or "(no reply text)"
        clipped = reply if len(reply) <= EVENT_REPLY_CHARS else reply[:EVENT_REPLY_CHARS] + " [...clipped; read the link]"
        dollars = f"${self.subtree_cost(core.agent_id):.2f} with its subagents"
        usage = dollars if core.lineage == "claude" else f"{core.tokens} tokens ({dollars})"
        return (f"[harness] Task owner {core.agent_id} ({core.model}) finished turn {run.turns}: {result.subtype}; "
                f"{usage} so far. It is idle: give it a new instruction with message_owner, or stop it with "
                f"stop_owner.\nIts reply ({core.last_text_link or 'not recorded'}):\n{clipped}")

    # ---- supervision ----------------------------------------------------------------------------

    async def message_owner(self, governor: AgentCore, owner: Any, text: Any) -> dict[str, Any]:
        run = self._run(owner)
        if run.state != "idle":
            raise Denied(f"{owner} is {run.state}; message it when it is idle")
        if not isinstance(text, str) or not text.strip():
            raise Denied("text must be a non-empty string")
        if BAR_LINE.search(text):
            raise Denied(NO_BAR)
        refusal = self._turn_refusal(run)
        if refusal:
            raise Denied(refusal)
        record = self.env.bus.publish_text(text, author=governor.author, direction="to_agent",
                                           agent=run.core.agent_id, turn=run.core.turn + 1)
        if not record or not record.get("name"):
            raise Denied("the ledger could not record the message, so it was not sent")
        self._state(run, "working")  # claimed now, so a second message is refused
        run.inbox.put_nowait((text, governor.author, wikilink(record), record["id"]))
        return {"owner": owner, "state": "working", "message": wikilink(record)}

    async def stop_owner(self, governor: AgentCore, owner: Any, reason: Any) -> dict[str, Any]:
        run = self._run(owner)
        if run.state not in OPEN_STATES:
            raise Denied(f"{owner} has already ended ({run.state})")
        if not isinstance(reason, str) or not reason.strip():
            raise Denied("give a reason")
        governor.pub("owner_stop_requested", owner=owner, reason=reason)
        run.stop_reason = reason
        await self._stop(run)
        return {"owner": owner, "state": "stopping", "note": "Its session closes; you get a harness message."}

    async def _stop(self, run: OwnerRun) -> None:
        for req in self.requests.values():
            if req.owner == run.core.agent_id and req.state in ("pending", "awaiting_human"):
                self._finish(req, "cancelled", f"the owner is ending: {run.stop_reason or run.close_reason}")
        run.inbox.put_nowait(None)
        if run.state in ("working", "waiting"):
            await run.core.interrupt()

    async def close_all(self, timeout: float = 30) -> None:
        """At the end of a launch: stop every open owner and wait for its session to close."""
        for run in self.owners.values():
            if run.state in OPEN_STATES:
                run.close_reason = "the launch is ending"
                await self._stop(run)
        tasks = [r.task for r in self.owners.values() if r.task and not r.task.done()]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def status(self) -> dict[str, Any]:
        owners = [{"owner": o, "model": r.core.model, "state": r.state, "turns": r.turns, "last_status": r.last_status,
                   "assignment": r.assignment, "bounds": r.core.bounds.render(), "cost_usd": r.core.cost_usd,
                   "tokens": r.core.tokens, "subtree_cost_usd": self.subtree_cost(o),
                   "allowance_usd": r.allowance_usd, "allowance_left_usd": self.allowance_left(o),
                   "max_turns": r.core.max_turns,
                   "onboarding_read": r.core.onboarding_read, "last_reply": r.core.last_text_link}
                  for o, r in self.owners.items()]
        requests = [{"request": q.id, "owner": q.owner, "kind": q.kind, "state": q.state,
                     "justification": q.justification, "details": q.details}
                    for q in self.requests.values() if q.state in ("pending", "awaiting_human")]
        approvals = [{"approval": a.id, "request": a.request, "owner": a.owner, "kind": a.kind, "state": a.state,
                      "assessment": a.assessment, "details": a.details}
                     for a in self.approvals.values() if a.state == "pending"]
        return {"owners": owners, "open_requests": requests, "approvals_waiting_for_human": approvals,
                "budget": {"spent_usd": self.env.budget.spent, "total_usd": self.env.budget.total,
                           "tokens_by_agent": self.env.budget.tokens_by_agent()},
                "ceiling": self.ceiling.render(), "max_owners": self.max_owners}

    # ---- requests -------------------------------------------------------------------------------

    def check_request(self, owner: AgentCore, kind: Any, justification: Any,
                      details: Any) -> tuple[dict[str, Any], bytes | None]:
        if owner.role != "owner" or owner.agent_id not in self.owners:
            raise Denied("only task owners make requests to the governor")
        if kind not in REQUEST_KINDS:
            raise Denied(f"kind must be one of {', '.join(REQUEST_KINDS)}")
        if not isinstance(justification, str) or not justification.strip():
            raise Denied("give a justification")
        details = details if isinstance(details, dict) else {} if details is None else None
        if details is None:
            raise Denied("details must be an object")
        snapshot = None
        if kind == "expand_bounds":
            if not isinstance(details.get("bounds"), str):
                raise Denied("expand_bounds needs details.bounds: your full bounds as they should be, in the notation")
            try:
                Bounds.parse(details["bounds"]).within(self.ceiling)
            except BoundsError as e:
                raise Denied(f"beyond what the governor can grant (the ceiling; only the human can raise it): {e}") from e
        elif kind == "promote_script":
            snapshot = self._script_snapshot(owner, details)
        elif kind == "more_budget":
            usd, turns = details.get("budget_usd", 0), details.get("turns", 0)
            if not (isinstance(usd, (int, float)) and isinstance(turns, int)) or usd < 0 or turns < 0 or not (usd or turns):
                raise Denied("more_budget needs details.budget_usd (dollars, Claude owners) and/or details.turns "
                             "(extra model rounds per message), not negative")
        return details, snapshot

    def _script_snapshot(self, owner: AgentCore, details: dict[str, Any]) -> bytes:
        name, source = details.get("name"), details.get("source")
        if not isinstance(name, str) or not SCRIPT_NAME.fullmatch(name):
            raise Denied("promote_script needs details.name: a file name ending .py, no directories")
        d = owner.files.check("fs.read", source)
        if not d.allow:
            raise Denied(d.reason)
        folder = fs_target(self.env.workspace, self.env.root).rstrip("/")
        if not (folder == "" or d.target.casefold().startswith(folder.casefold() + "/")):
            raise Denied(f"drafts are promoted from the project folder ({folder}/); {d.target} is not in it")
        scripts = fs_target(self.scripts_dir, self.env.root) or ""
        if d.target.casefold() == scripts.casefold() or d.target.casefold().startswith(scripts.casefold() + "/"):
            raise Denied("that file is already in the scripts directory")
        path = fs_path(d.target, self.env.root)
        if not path.is_file():
            raise Denied(f"{d.target} is not a file")
        data = path.read_bytes()
        if len(data) > SCRIPT_MAX_BYTES:
            raise Denied(f"{d.target} is over {SCRIPT_MAX_BYTES} bytes")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise Denied(f"{d.target} is not UTF-8 text") from e
        if (self.scripts_dir / name).exists():
            raise Denied(f"{name} already exists in the scripts directory; promotion never replaces a script")
        details["source"] = d.target
        return data

    async def request(self, owner: AgentCore, kind: Any, justification: Any, details: Any) -> dict[str, Any]:
        details, snapshot = self.check_request(owner, kind, justification, details)
        req_id = f"req.{self._n['request'] + 1}"
        shown = dict(details)
        if snapshot is not None:
            shown.update(sha256=hashlib.sha256(snapshot).hexdigest(), bytes=len(snapshot),
                         text=snapshot.decode("utf-8"))
        opened = owner.pub("request_open", request=req_id, request_kind=kind, justification=justification, details=shown)
        if not opened or not opened.get("name"):
            raise Denied("the ledger could not record this request, so it was not made")
        self._n["request"] += 1
        req = Request(req_id, owner.agent_id, kind, justification, shown, asyncio.get_running_loop().create_future(),
                      snapshot=snapshot)
        self.requests[req_id] = req
        run = self.owners[owner.agent_id]
        self._state(run, "waiting")
        body = json.dumps({k: v for k, v in shown.items() if k != "text"}, ensure_ascii=False)
        self._event(f"[harness] Task owner {owner.agent_id} ({owner.model}) asks you ({req_id}, {kind}). Its work "
                    f"waits until you resolve it with resolve (request={req_id}).\nJustification: {justification}\n"
                    f"Details: {body}" + (f"\nThe draft ({shown['bytes']} bytes) is recorded in {opened['name']}; "
                                          f"its source is {shown['source']}." if snapshot is not None else ""))
        try:
            return await req.future
        finally:
            if run.state == "waiting":
                self._state(run, "working")

    def _finish(self, req: Request, state: str, text: str, applied: dict[str, Any] | None = None) -> dict[str, Any]:
        req.state = state
        req.resolution = {"request": req.id, "kind": req.kind, "decision": state, "text": text, "applied": applied}
        self._pub("request_resolved", request=req.id, owner=req.owner, request_kind=req.kind, decision=state, text=text,
                  applied=applied)
        if not req.future.done():
            req.future.set_result(req.resolution)
        return {"request": req.id, "state": state, "applied": applied}

    async def resolve(self, governor: AgentCore, request: Any, decision: Any, text: Any, bounds: Any = None,
                      budget_usd: Any = None, max_turns: Any = None) -> dict[str, Any]:
        req = self.requests.get(request) if isinstance(request, str) else None
        if req is None:
            raise Denied(f"no request {request!r}")
        if req.state != "pending":
            raise Denied(f"{request} is {req.state}")
        if decision not in ("approve", "deny", "answer"):
            raise Denied("decision must be approve, deny or answer")
        if not isinstance(text, str) or not text.strip():
            raise Denied("give your answer or your reason in text")
        run = self.owners[req.owner]
        if decision == "deny":
            return self._finish(req, "denied", text)
        if req.kind == "clarify":
            return self._finish(req, "answered", text)
        if decision == "answer":
            raise Denied(f"answer is for clarify requests; approve or deny this {req.kind} request")
        if req.kind == "expand_bounds":
            try:
                new = private_log(Bounds.parse(bounds if isinstance(bounds, str) else req.details["bounds"])
                                  .within(self.ceiling))
            except BoundsError as e:
                raise Denied(f"bounds refused (the ceiling is the limit): {e}") from e
            old = run.core.bounds.render()
            run.core.set_bounds(new)
            governor.pub("bounds_changed", owner=req.owner, request=req.id, old=old, new=new.render())
            self._state(run, run.state)
            return self._finish(req, "approved", text, applied={"bounds": new.render()})
        if req.kind == "more_budget":
            add_usd = req.details.get("budget_usd", 0) if budget_usd is None else budget_usd
            add_turns = req.details.get("turns", 0) if max_turns is None else max_turns
            if not (isinstance(add_usd, (int, float)) and isinstance(add_turns, int)) or add_usd < 0 or add_turns < 0:
                raise Denied("budget_usd and max_turns must be non-negative (additional dollars and rounds)")
            applied: dict[str, Any] = {}
            if add_usd:  # a GPT owner's dollars cover the Claude subagents it spawns
                granted = min(float(add_usd), self.env.budget.remaining)
                run.allowance_usd += granted
                applied["allowance_usd"] = run.allowance_usd
            if add_turns:
                run.core.max_turns = min(run.core.max_turns + add_turns, TURNS_CAP)
                applied["max_turns"] = run.core.max_turns
            governor.pub("budget_changed", owner=req.owner, request=req.id, **applied)
            self._state(run, run.state)
            return self._finish(req, "approved", text, applied=applied)
        # promote_script: the human decides
        approval_id = f"approval.{self._n['approval'] + 1}"
        details = {k: req.details[k] for k in ("name", "source", "sha256", "bytes")}
        opened = governor.pub("approval_open", approval=approval_id, request=req.id, owner=req.owner, request_kind=req.kind,
                              assessment=text, script=details["name"], source=details["source"],
                              sha256=details["sha256"], bytes=details["bytes"])  # not "name": records carry their own
        if not opened or not opened.get("name"):
            raise Denied("the ledger could not record the approval, so nothing was forwarded")
        self._n["approval"] += 1
        self.approvals[approval_id] = Approval(approval_id, req.id, req.owner, req.kind, text,
                                               {**details, "text": req.details.get("text")})
        req.state = "awaiting_human"
        return {"request": req.id, "state": "awaiting_human", "approval": approval_id,
                "note": "The human decides in the UI; you get a harness message with the decision."}

    def human_decide(self, approval_id: str, approve: bool, note: str = "",
                     decided_by: str = HUMAN_AUTHOR) -> dict[str, Any]:
        """The human's decision on an approval card (web.py). Returns what was applied, or raises Denied.
        `decided_by` names who decided: the human, or a scripted run's driver (harness.DRIVER_AUTHOR)."""
        approval = self.approvals.get(approval_id)
        if approval is None or approval.state != "pending":
            raise Denied(f"no pending approval {approval_id!r}")
        req = self.requests[approval.request]
        self._pub("human_decision", approval=approval_id, request=req.id, owner=req.owner, request_kind=req.kind,
                  decision="approved" if approve else "denied", note=note, decided_by=decided_by)
        if approve:
            try:
                applied = self._promote(req, approval)
            except Denied as e:
                approval.state = "denied"
                self._finish(req, "denied", f"The human approved, but the promotion failed: {e}")
                self._event(f"[harness] The human approved {approval_id} ({req.kind} for {req.owner}), but the "
                            f"promotion failed: {e}.")
                raise
            approval.state = "approved"
            out = self._finish(req, "approved", f"The human approved it. {note}".strip(), applied=applied)
        else:
            approval.state = "denied"
            out = self._finish(req, "denied", f"The human denied it. {note}".strip())
        self._event(f"[harness] The human {approval.state} {approval_id} ({req.kind} {approval.details['name']} "
                    f"for {req.owner}, {req.id})" + (f": {note}" if note else "") + ".")
        return out

    def _promote(self, req: Request, approval: Approval) -> dict[str, Any]:
        """Write exactly the bytes the request snapshotted (what the governor and the human reviewed)."""
        target = self.scripts_dir / approval.details["name"]
        data = req.snapshot or b""
        if hashlib.sha256(data).hexdigest() != approval.details["sha256"]:
            raise Denied("the snapshot does not match its recorded sha256")
        try:
            self.scripts_dir.mkdir(parents=True, exist_ok=True)  # task 7: each project's own scripts/
            with open(target, "xb") as f:
                f.write(data)
        except FileExistsError as e:
            raise Denied(f"{target.name} already exists in the scripts directory") from e
        except OSError as e:
            raise Denied(f"could not write {target.name}: {e}") from e
        target_name = fs_target(target, self.env.root)
        self._pub("script_promoted", request=req.id, approval=approval.id, owner=req.owner, target=target_name,
                  source=approval.details["source"], sha256=approval.details["sha256"], bytes=len(data))
        return {"script": target_name, "sha256": approval.details["sha256"]}

    # ---- role tools ---------------------------------------------------------------------------------

    def tools_for(self, core: AgentCore) -> list[Any]:
        """The governor's or an owner's role tools (server "gov"); none for other roles."""
        bus = self.env.bus

        def reply(data: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}

        async def run(name: str, args: dict[str, Any], coro: Any) -> dict[str, Any]:
            try:
                return reply(await coro)
            except Denied as e:
                return denied_reply(bus, core.agent_id, f"mcp__{GOV_SERVER}__{name}", args, e)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"error: {e!r}"}], "is_error": True}

        async def done(value: Any) -> Any:
            return value

        if core.role == "owner":
            @tool("request", "Ask the governor for a decision. Your work waits until it is resolved, and the result "
                  "comes back as this tool's output. Kinds: clarify (a question: put it in justification); "
                  "expand_bounds (details.bounds: your full bounds as they should be, in the notation); "
                  "promote_script (details.source: your draft in the project folder; details.name: its file name in the "
                  "scripts directory; the human decides); more_budget (details.budget_usd and/or details.turns: "
                  "additional dollars and model rounds). Give the justification the governor needs to decide.",
                  {"type": "object", "properties": {
                      "kind": {"type": "string", "enum": list(REQUEST_KINDS)},
                      "justification": {"type": "string"},
                      "details": {"type": "object"}},
                   "required": ["kind", "justification"]})
            async def request_(args: dict[str, Any]) -> dict[str, Any]:
                return await run("request", args, self.request(core, args.get("kind"), args.get("justification"),
                                                               args.get("details")))
            return [request_]

        if core.role != "governor":
            return []

        @tool("dispatch", "Start a task owner in the background and return its id at once. First write its "
              "assignment as your own ledger entry under gov/ (the task, or where it is defined, and the rules; "
              "never a Bar: line). `bounds` uses the notation of your bounds and must be within the ceiling. "
              f"`model` is one of {', '.join(self.owner_models)}. Optional: assignment_id (a pinned revision), "
              "budget_usd (a Claude owner's allowance), max_turns (model rounds per message).",
              {"type": "object", "properties": {
                  "model": {"type": "string", "enum": list(self.owner_models)},
                  "bounds": {"type": "string", "description": "the owner's bounds, one scope per line"},
                  "assignment": {"type": "string", "description": "ledger name of your assignment entry"},
                  "assignment_id": {"type": "string"}, "budget_usd": {"type": "number"},
                  "max_turns": {"type": "integer"}},
               "required": ["model", "bounds", "assignment"]})
        async def dispatch_(args: dict[str, Any]) -> dict[str, Any]:
            return await run("dispatch", args, self.dispatch(
                core, model=args.get("model"), bounds_text=args.get("bounds"), assignment=args.get("assignment"),
                assignment_id=args.get("assignment_id"), budget_usd=args.get("budget_usd"),
                max_turns=args.get("max_turns")))

        @tool("status", "The task owners (state, model, bounds, usage, last reply), open requests, approvals "
              "waiting for the human, the budget and the ceiling.", {"type": "object", "properties": {}})
        async def status_(args: dict[str, Any]) -> dict[str, Any]:
            return await run("status", args, done(self.status()))

        @tool("message_owner", "Give an idle task owner a new instruction (a new turn in its session). It returns "
              "at once; you get a harness message when the turn ends. No Bar: lines.",
              {"type": "object", "properties": {"owner": {"type": "string"}, "text": {"type": "string"}},
               "required": ["owner", "text"]})
        async def message_owner_(args: dict[str, Any]) -> dict[str, Any]:
            return await run("message_owner", args, self.message_owner(core, args.get("owner"), args.get("text")))

        @tool("stop_owner", "Stop a task owner: interrupt its turn if it is working, cancel its open requests and "
              "close its session. Give the reason.",
              {"type": "object", "properties": {"owner": {"type": "string"}, "reason": {"type": "string"}},
               "required": ["owner", "reason"]})
        async def stop_owner_(args: dict[str, Any]) -> dict[str, Any]:
            return await run("stop_owner", args, self.stop_owner(core, args.get("owner"), args.get("reason")))

        @tool("resolve", "Resolve an owner's request. decision: answer (clarify), approve or deny; text: your answer, "
              "reason or assessment. expand_bounds: approve applies the requested bounds, or your narrower `bounds`. "
              "more_budget: approve grants what was asked, or your own budget_usd / max_turns (additional). "
              "promote_script: approve forwards it to the human with your assessment; the human decides.",
              {"type": "object", "properties": {
                  "request": {"type": "string"}, "decision": {"type": "string", "enum": ["answer", "approve", "deny"]},
                  "text": {"type": "string"}, "bounds": {"type": "string"}, "budget_usd": {"type": "number"},
                  "max_turns": {"type": "integer"}},
               "required": ["request", "decision", "text"]})
        async def resolve_(args: dict[str, Any]) -> dict[str, Any]:
            return await run("resolve", args, self.resolve(
                core, args.get("request"), args.get("decision"), args.get("text"), bounds=args.get("bounds"),
                budget_usd=args.get("budget_usd"), max_turns=args.get("max_turns")))

        return [dispatch_, status_, message_owner_, stop_owner_, resolve_]
