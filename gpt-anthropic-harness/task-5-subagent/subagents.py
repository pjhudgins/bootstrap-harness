"""Harness-owned parallel agents; public job state excludes runtime handles."""
import asyncio
from dataclasses import dataclass
import math

from bounds import Bounds, Denied
from context import AgentContext
from ledger import HARNESS_AUTHOR, LedgerFailed


@dataclass
class ChildJob:
    context: AgentContext
    parent: str
    instructions_id: str
    result_name: str
    status: str = "running"
    result: dict | None = None
    error: str | None = None

    def public(self):
        return {"agent_id": self.context.agent_id, "parent": self.parent,
                "author": self.context.author, "model": self.context.model,
                "bounds": self.context.bounds.data(), "instructions_id": self.instructions_id,
                "result_name": self.result_name, "status": self.status,
                "result": self.result, "error": self.error}


class Subagents:
    def __init__(self, log, task_dir, state, runner=None):
        self.log, self.task_dir, self.state = log, task_dir, state
        self.runner = runner
        self.jobs, self.tasks = {}, {}
        self.closing = False

    def start(self, parent, model, instructions_id, bounds, result_name):
        if self.closing or self.state.stopping or self.log.stop_event.is_set():
            raise Denied("Harness is closing; no new children.")
        if model not in {"sonnet", "haiku", "opus"}:
            raise ValueError("Choose a Claude model alias: sonnet, haiku or opus.")
        child_bounds = Bounds.parse(bounds, parent.bounds.mounts)
        problems = child_bounds.problems_as_child_of(parent.bounds)
        if problems:
            raise Denied("\n".join(problems))
        entry = parent.access.resolve(instructions_id)
        if entry["author"] != parent.author or not entry["name"].startswith("pilot/"):
            raise Denied("Instructions must be a note written by this parent.")
        if not any(line.startswith("Bar:") for line in entry["body"].splitlines()):
            raise ValueError("Instructions must include a Bar: line.")
        child_bounds.require("ledger.read", entry["name"])
        child_bounds.require("fs.read", "origins")
        child_bounds.require("fs.read", parent.onboarding_path)
        child_bounds.require("ledger.read", result_name)
        if len(self.jobs) >= 4 or sum(j.status == "running" for j in self.jobs.values()) >= 2:
            raise Denied("Child budget: at most 2 running, 4 total per launch.")
        parent.access.reserve_output(result_name)
        agent_id = f"child-{len(self.jobs) + 1}"
        context = AgentContext(agent_id, "agent.claude." + agent_id, model, child_bounds)
        job = ChildJob(context, parent.author, instructions_id, result_name)
        self.log.write("subagent_started", **job.public())
        self.jobs[agent_id] = job
        self.tasks[agent_id] = asyncio.create_task(self._run(job, entry))
        self.publish(job)
        return job.public()

    def publish(self, job):
        self.state.child_update(job.public())
        self.state.activity("Subagent " + job.status, summary=f"{job.context.agent_id} · {job.context.model}")

    def finish(self, job, status, text, error=None):
        # The runner has already exited its SDK context and awaited tool cleanup.
        # Preserve an explicit terminal output even for cancellation/failure.
        try:
            if not self.log.failed:
                ref = self.log.finish_output(job.result_name, text,
                    author=job.context.author if status == "completed" else HARNESS_AUTHOR,
                    tag="agent.result" if status == "completed" else "agent.failure")
                self.log.write("subagent_" + status, **{**job.public(), "status": status, "result": ref, "error": error})
                job.result = ref
        except LedgerFailed as exc:
            error = str(exc)
        if self.log.failed:
            status, error = "failed", "Shared ledger failed; launch stopping."
        job.status, job.error = status, error
        self.publish(job)

    async def _run(self, job, entry):
        try:
            if self.runner is None:
                from runtime import run_child
                runner = run_child
            else:
                runner = self.runner
            text = await runner(self.log, self.task_dir, self.state, job.context, entry)
        except asyncio.CancelledError:
            self.finish(job, "cancelled", "Child cancelled during harness shutdown.", "Harness shutdown")
        except Exception as exc:
            error = self.log.clean(f"{type(exc).__name__}: {exc}")
            self.finish(job, "failed", error, error)
        else:
            self.finish(job, "completed", text)

    async def status(self, author, agent_id, wait_seconds=10):
        job = self.jobs.get(agent_id)
        if not job or job.parent != author:
            raise Denied("Unknown child for this parent.")
        if type(wait_seconds) not in (int, float) or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 10:
            raise ValueError("wait_seconds must be between 0 and 10.")
        if job.status == "running" and wait_seconds:
            await asyncio.wait({self.tasks[agent_id]}, timeout=wait_seconds)
        return job.public()

    async def close(self):
        self.closing = True
        tasks = [t for t in self.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for agent_id, job in self.jobs.items():
            if job.status == "running" and self.tasks[agent_id].cancelled():
                self.finish(job, "cancelled", "Child cancelled before starting.", "Harness shutdown before child started")
