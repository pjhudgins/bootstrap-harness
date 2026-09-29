"""One launch's state and one long-lived SDK task; HTTP never touches the SDK."""

import asyncio
import copy
import threading
import sys
import math
from pathlib import Path
from uuid import uuid4

from bounds import Mounts, parent_bounds
from context import AgentContext
from runtime import AgentSession
from subagents import Subagents
from ledger import AGENT_AUTHOR, LedgerFailed, NIMOI


class Conversation:
    def __init__(self, log, model):
        self.log = log
        self.lock = threading.RLock()
        self.loop = None
        self.queue = None
        self.stopping = False
        self.data = {"id": uuid4().hex, "status": "starting", "model": model,
                     "messages": [], "activity": [], "usage": None, "limits": None,
                     "account": {}, "error": None, "revision": 0,
                     "children": {}, "agent_usage": {}, "family_cost_usd": None,
                     "journal": log.path.relative_to(log.root).as_posix()}

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
            self.log.check()
            turn = uuid4().hex
            text_ref = self.log.user_message(turn, text)
            self.log.write("user_message_accepted", turn=turn, text=text_ref["link"], text_id=text_ref["id"])
            self.data["messages"].append({"role": "user", "text": self.log.clean(text), "turn": turn})
            self.update(status="thinking")
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (turn, text, text_ref))
            return turn

    def assistant_text(self, turn, text, actor="parent", label="Claude"):
        with self.lock:
            messages = self.data["messages"]
            if (messages and messages[-1]["role"] == "assistant" and messages[-1]["turn"] == turn
                    and messages[-1].get("actor") == actor):
                messages[-1]["text"] += "\n\n" + self.log.clean(text)
            else:
                messages.append({"role": "assistant", "text": self.log.clean(text), "turn": turn,
                                 "actor": actor, "label": label})
            self.data["revision"] += 1

    def child_update(self, job):
        with self.lock:
            self.data["children"][job["agent_id"]] = self.log.clean(job)
            self.data["revision"] += 1

    def record_usage(self, context, usage):
        with self.lock:
            previous = self.data["agent_usage"].get(context.agent_id, {})
            cost = usage.get("total_cost_usd")
            if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                cost = previous.get("total_cost_usd")
            elif previous.get("total_cost_usd") is not None:
                cost = max(cost, previous["total_cost_usd"])
            record = {**usage, "total_cost_usd": cost, "model": context.model, "author": context.author}
            self.data["agent_usage"][context.agent_id] = self.log.clean(record)
            costs = [v["total_cost_usd"] for v in self.data["agent_usage"].values() if v["total_cost_usd"] is not None]
            self.data["family_cost_usd"] = sum(costs) if costs else None
            if context.is_parent:
                self.data["usage"] = self.log.clean(record)
            self.data["revision"] += 1

    def stop(self):
        with self.lock:
            if not self.stopping:
                self.stopping = True
                self.log.stop_event.set()
                self.update(status="stopping")
                if self.loop and self.queue and not self.loop.is_closed():
                    try:
                        self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
                    except RuntimeError:
                        pass  # A failed worker may close its loop during shutdown.


async def serve_conversation(state, task_dir):
    state.loop = asyncio.get_running_loop()
    state.queue = asyncio.Queue()
    manager = Subagents(state.log, task_dir, state)
    task_dir = Path(task_dir)
    workspace = task_dir / "workspace" / state.log.name
    workspace.mkdir(parents=True, exist_ok=False)
    mounts = Mounts(workspace.relative_to(NIMOI).as_posix(), (task_dir / "scripts").relative_to(NIMOI).as_posix())
    context = AgentContext("parent", AGENT_AUTHOR, state.data["model"], parent_bounds(mounts), "parent", True)
    state.update(workspace=mounts.workspace, bounds=context.bounds.data())
    owner = asyncio.current_task()
    closing = False

    async def watch_stop():
        while not state.log.stop_event.is_set():
            await asyncio.sleep(0.05)
        if not closing:
            owner.cancel()

    watcher = asyncio.create_task(watch_stop())
    try:
        async with AgentSession(state.log, task_dir, state, context, manager) as agent:
            try:
                state.update(status="stopping" if state.stopping else "ready")
                if state.stopping:
                    return
                while True:
                    job = await state.queue.get()
                    if job is None:
                        break
                    turn, text, ref = job
                    await agent.query(text, turn, text_ref={"text": ref["link"], "text_id": ref["id"]})
                    state.update(status="stopping" if state.stopping else "ready")
            finally:
                # Do not interrupt SDK teardown after the queue/turn has stopped.
                closing = True
    except asyncio.CancelledError:
        if state.log.failed:
            raise LedgerFailed("Shared ledger failed; launch stopped.") from None
        if not state.stopping:
            raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await manager.close()


def worker(state, task_dir):
    try:
        asyncio.run(serve_conversation(state, task_dir))
    except Exception as exc:
        state.update(status="error", error=f"{type(exc).__name__}: {exc}")
        if not state.log.failed:
            try:
                state.log.write("conversation_error", error_type=type(exc).__name__, error=str(exc))
                state.log.write("conversation_closed", success=False)
            except LedgerFailed:
                state.update(status="error", error="Ledger failure; preserve ledger/lease for human review.")
        if state.log.failed:
            print("Ledger failure: no further writes attempted. Preserve ledger and lease for human review.", file=sys.stderr)
    else:
        state.update(status="closed")
        state.log.write("conversation_closed", success=True)
