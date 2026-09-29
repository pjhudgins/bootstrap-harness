"""The top-level conversation: one worker task owns the top-level agent's client.

(Task 5: HarnessEnv, BudgetPool and AgentCore moved to agent.py on 2026-09-25.)

The worker enters and exits the client's context itself, because the SDK's anyio task
group must be entered and exited by the same task. Web requests hand it messages through
a queue; only interrupt() crosses tasks. The client comes from env.client_factory, so
tests can run the whole flow with a fake client.

States: starting -> idle <-> busy -> (over_budget | ledger_failed | stopped | failed).
ledger_failed is fail-closed: once the ledger cannot record, no new message is accepted.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent import ACCOUNT_KEYS_LOGGED, AgentCore
from ledgerlog import HUMAN_AUTHOR


class AgentSession:
    def __init__(self, core: AgentCore):
        self.core = core
        self.env = core.env
        self.state = "starting"
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    # ---- public API used by the web layer -------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "turn": self.core.turn, "model": self.core.model,
                "session_cost_usd": self.env.budget.spent, "budget_usd": self.env.budget.total,
                "cost_by_agent": self.env.budget.by_agent(), "ledger_failed": self.env.ledger_failed}

    async def start(self, timeout: float = 90) -> None:
        self._task = asyncio.create_task(self._run(), name="agent-session")
        await asyncio.wait_for(self._ready.wait(), timeout)
        if self.state == "failed":
            raise RuntimeError("agent session failed to start; see the ledger")

    def send(self, text: str) -> str | None:
        """Queue a user message. Returns a refusal reason, or None if accepted."""
        if self.env.ledger_failed:
            self._set_state("ledger_failed")
            return "the ledger can no longer record; the harness has stopped (fail closed)"
        if self.state != "idle":
            return f"agent is {self.state}"
        if self.env.budget.spent >= self.env.budget.total:
            self._set_state("over_budget")
            return f"budget ${self.env.budget.total:.2f} reached"
        self.core.turn += 1  # safe here: the state gate admits one message at a time
        self._set_state("busy")
        self._inbox.put_nowait(text)
        return None

    async def interrupt(self) -> bool:
        if self.state != "busy" or self.core.client is None:
            return False
        await self.core.interrupt()
        return True

    async def stop(self, timeout: float = 20) -> None:
        if self._task is None or self._task.done():
            return
        if self.state == "busy":
            try:
                await asyncio.wait_for(self.interrupt(), 5)
            except Exception as e:
                self.core.pub("interrupt_error", error=repr(e))
        self._inbox.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except asyncio.TimeoutError:
            self.core.pub("session_error", error="worker did not stop in time; cancelled")
            self._task.cancel()

    # ---- worker ---------------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        self.state = state
        self.core.pub("status", **self.snapshot())

    async def _run(self) -> None:
        try:
            async with self.env.client_factory(self.core.options(), self.core) as client:
                self.core.client = client
                info = await client.get_server_info() or {}
                account = info.get("account") or {}
                self.core.announce_start(account={k: account.get(k) for k in ACCOUNT_KEYS_LOGGED})
                self._set_state("idle")
                self._ready.set()
                while (text := await self._inbox.get()) is not None:
                    await self._turn(client, text)
        except Exception as e:
            self.core.pub("session_error", error=repr(e))
            self._set_state("failed")
        finally:
            self.core.client = None
            if self.state != "failed":
                self._set_state("stopped")
            self._ready.set()

    async def _turn(self, client: Any, text: str) -> None:
        try:
            await self.core.run_turn(client, text, author=HUMAN_AUTHOR)
        except Exception as e:
            self.core.pub("turn_error", error=repr(e))
        finally:
            if self.env.ledger_failed:
                self._set_state("ledger_failed")
            elif self.env.budget.spent >= self.env.budget.total:
                self._set_state("over_budget")
            else:
                self._set_state("idle")
