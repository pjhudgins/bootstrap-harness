"""The top-level conversation: one worker task owns the top-level agent's backend.

(Task 5: HarnessEnv, BudgetPool and AgentCore moved to agent.py on 2026-09-25. Task 6: the agent
runs through its backend, Claude or Codex, not a raw SDK client; and the top-level agent may be
the governor, which also receives harness events.)

The worker enters and exits the backend's context itself, because the Claude SDK's anyio task
group must be entered and exited by the same task. Only interrupt() crosses tasks.

Two kinds of message reach the agent, one turn at a time, never interrupting a turn:
  - the human's, from the web layer (send). They may queue while a turn runs (up to MAX_QUEUED),
    and they always go first;
  - harness events, from the institution (deliver): owners' turns ending, requests, the
    human's decisions. Events that arrive while a turn runs are delivered together, as one
    harness-authored message, after the human's queued messages.

States: starting -> idle <-> busy -> (over_budget | ledger_failed | stopped | failed).
ledger_failed is fail-closed: once the ledger cannot record, no new message is accepted.
"""

from __future__ import annotations

import asyncio
import collections
from typing import Any

from agent import ACCOUNT_KEYS_LOGGED, AgentCore
from ledger_tools import Denied
from ledgerlog import HUMAN_AUTHOR

MAX_QUEUED = 3  # human messages waiting while a turn runs
TERMINAL = ("stopped", "failed", "ledger_failed")


class AgentSession:
    def __init__(self, core: AgentCore, institution: Any = None):
        self.core = core
        self.env = core.env
        self.institution = institution
        self.state = "starting"
        self._human: collections.deque[tuple[str, str]] = collections.deque()  # (text, author)
        self._events: list[str] = []
        self._wake = asyncio.Event()
        self._closing = False
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        if institution is not None:
            institution.deliver = self.deliver

    # ---- public API used by the web layer -------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        snap = {"state": self.state, "turn": self.core.turn, "model": self.core.model, "role": self.core.role,
                "session_cost_usd": self.env.budget.spent, "budget_usd": self.env.budget.total,
                "cost_by_agent": self.env.budget.by_agent(), "tokens_by_agent": self.env.budget.tokens_by_agent(),
                "ledger_failed": self.env.ledger_failed, "queued": len(self._human),
                "events_waiting": len(self._events)}
        if self.institution is not None:
            snap["institution"] = self.institution.status()
        return snap

    async def start(self, timeout: float = 120) -> None:
        self._task = asyncio.create_task(self._run(), name="agent-session")
        await asyncio.wait_for(self._ready.wait(), timeout)
        if self.state == "failed":
            raise RuntimeError("agent session failed to start; see the ledger")

    def send(self, text: str, author: str = HUMAN_AUTHOR) -> str | None:
        """Queue a human message. Returns a refusal reason, or None if accepted. `author` is the human user
        (the web UI), or, for scripted runs, whoever really wrote the text (harness.DRIVER_AUTHOR)."""
        if self.env.ledger_failed:
            self._set_state("ledger_failed")
            return "the ledger can no longer record; the harness has stopped (fail closed)"
        if self.state in ("starting", *TERMINAL) or self._closing:
            return f"agent is {self.state}"
        if self.core.lineage == "claude" and self.env.budget.spent >= self.env.budget.total:
            self._set_state("over_budget")
            return f"budget ${self.env.budget.total:.2f} reached"
        if len(self._human) >= MAX_QUEUED:
            return f"{MAX_QUEUED} messages are already waiting"
        self._human.append((text, author))
        if self.state == "idle":
            self._set_state("busy")  # at once, so a caller polling the state sees the message taken
        self._wake.set()
        return None

    def deliver(self, text: str) -> None:
        """A harness event for the agent (governance.Institution). Delivered between turns."""
        if self.state in TERMINAL or self._closing:
            return
        self._events.append(text)
        self._wake.set()

    def decide(self, approval: str, approve: bool, note: str = "", decided_by: str = HUMAN_AUTHOR) -> str | None:
        """The human's decision on an approval card. Returns a refusal reason, or None if applied."""
        if self.institution is None:
            return "this launch has no task owners, so nothing awaits approval"
        try:
            self.institution.human_decide(approval, approve, note, decided_by=decided_by)
        except Denied as e:
            return str(e)
        return None

    async def interrupt(self) -> bool:
        if self.state != "busy" or not self.core.active:
            return False
        await self.core.interrupt()
        return True

    async def stop(self, timeout: float = 30) -> None:
        if self._task is None or self._task.done():
            if self.institution is not None:
                await self.institution.close_all()
            return
        self._closing = True
        if self.state == "busy":
            try:
                await asyncio.wait_for(self.interrupt(), 5)
            except Exception as e:
                self.core.pub("interrupt_error", error=repr(e))
        if self.institution is not None:  # owners end before the governor's session closes
            await self.institution.close_all()
        self._wake.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except asyncio.TimeoutError:
            self.core.pub("session_error", error="worker did not stop in time; cancelled")
            self._task.cancel()

    # ---- worker ---------------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        self.state = state
        self.core.pub("status", **{k: v for k, v in self.snapshot().items() if k != "institution"})

    def _next_message(self) -> tuple[str, str] | None:
        if self._human:
            return self._human.popleft()
        if self._events:
            events, self._events = self._events, []
            return "\n\n".join(events), self.env.harness_author
        return None

    async def _run(self) -> None:
        try:
            async with self.core.backend as backend:
                info = await backend.server_info()
                account = info.get("account") or {}
                self.core.announce_start(account={k: account.get(k) for k in ACCOUNT_KEYS_LOGGED})
                self._set_state("idle")
                self._ready.set()
                while not self._closing:
                    await self._wake.wait()
                    self._wake.clear()
                    while not self._closing and self.state not in TERMINAL:
                        if self.env.ledger_failed:
                            self._set_state("ledger_failed")
                            break
                        if self.core.lineage == "claude" and self.env.budget.spent >= self.env.budget.total:
                            self._set_state("over_budget")
                            break
                        message = self._next_message()
                        if message is None:
                            break
                        await self._turn(*message)
                    if self.state in TERMINAL:
                        break
                    if self.state == "busy":
                        self._set_state("idle")
        except Exception as e:
            self.core.pub("session_error", error=repr(e))
            self._set_state("failed")
        finally:
            if self.state != "failed":
                self._set_state("stopped")
            self._ready.set()

    async def _turn(self, text: str, author: str) -> None:
        self.core.turn += 1
        if self.state != "busy":
            self._set_state("busy")
        try:
            result = await self.core.run_turn(text, author=author)
            if result.subtype == "stopped":  # the backend stopped the agent (e.g. a native Codex call)
                self._set_state("stopped")
        except Exception as e:
            self.core.pub("turn_error", error=repr(e))
