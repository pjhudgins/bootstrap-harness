"""Three-layer ownership and blocking requests. All mutation runs on one event loop."""
import asyncio
from dataclasses import dataclass
import math

from bounds import Bounds, Denied
from context import AgentContext
from ledger import HARNESS_AUTHOR, LedgerFailed
from models import select_model


@dataclass
class Job:
    context: AgentContext
    instructions_id: str
    result_name: str
    status: str = "running"
    result: dict | None = None
    error: str | None = None

    def public(self):
        c = self.context
        return {"agent_id": c.agent_id, "parent": c.parent_id, "author": c.author,
                "role": c.role, "provider": c.provider, "model": c.model,
                "bounds": c.bounds.data(), "instructions_id": self.instructions_id,
                "result_name": self.result_name, "status": self.status,
                "result": self.result, "error": self.error}


class Hierarchy:
    def __init__(self, log, task_dir, state, runner=None):
        self.log, self.task_dir, self.state, self.runner = log, task_dir, state, runner
        self.jobs, self.tasks, self.requests, self.answers = {}, {}, {}, {}
        self.closing = False

    def is_blocked(self, actor):
        return any(r["owner_id"] == actor and r["status"] in {"pending", "needs_human", "human_decided"}
                   for r in self.requests.values())

    def start(self, parent, model, instructions_id, bounds, result_name):
        c = parent.context
        role = {"governor": "owner", "owner": "worker"}.get(c.role)
        if not role or self.is_blocked(c.agent_id):
            raise Denied("Only an unblocked governor or owner may dispatch the next layer.")
        if self.closing or self.state.stopping or self.log.stop_event.is_set():
            raise Denied("Harness is closing.")
        model = select_model(role, model)
        child_bounds = Bounds.parse(bounds, parent.bounds.mounts)
        problems = child_bounds.problems_as_child_of(parent.bounds)
        if problems:
            raise Denied("\n".join(problems))
        entry = parent.access.resolve(instructions_id)
        if entry["author"] != c.author or not entry["name"].startswith("pilot/"):
            raise Denied("Instructions must be a note written by this parent.")
        if not any(line.startswith("Bar:") for line in entry["body"].splitlines()):
            raise ValueError("Instructions must include a Bar: line.")
        for key, value in (("ledger.read", entry["name"]), ("fs.read", "origins"),
                           ("fs.read", parent.onboarding_path), ("ledger.read", result_name)):
            child_bounds.require(key, value)
        siblings = [j for j in self.jobs.values() if j.context.parent_id == c.agent_id]
        if len(self.jobs) >= 8 or sum(j.status in {"running", "blocked", "waiting_workers"} for j in siblings) >= 2:
            raise Denied("At most two active direct children and eight total agents per launch.")
        parent.access.reserve_output(result_name)
        agent_id = f"owner-{len(siblings)+1}" if role == "owner" else f"{c.agent_id}.worker-{len(siblings)+1}"
        provider = "gpt" if model.startswith("gpt-") else "claude"
        context = AgentContext(agent_id, f"agent.{provider}.{agent_id}", model, child_bounds,
                               role, role == "owner", c.agent_id)
        job = Job(context, instructions_id, result_name)
        self.log.write("agent_started", **job.public())
        self.jobs[agent_id] = job
        self.tasks[agent_id] = asyncio.create_task(self._run(job, entry))
        self.publish(job)
        return job.public()

    def publish(self, job):
        self.state.child_update(job.public())
        self.state.activity(job.context.role + " " + job.status, summary=f"{job.context.agent_id} · {job.context.model}")

    def finish(self, job, status, text, error=None):
        try:
            if not self.log.failed:
                ref = self.log.finish_output(job.result_name, text,
                    author=job.context.author if status == "completed" else HARNESS_AUTHOR,
                    tag="agent.result" if status == "completed" else "agent.failure")
                self.log.write("agent_" + status, **{**job.public(), "status": status, "result": ref, "error": error})
                job.result = ref
        except LedgerFailed as exc:
            error = str(exc)
        if self.log.failed:
            status, error = "failed", "Shared ledger failed; launch stopping."
        job.status, job.error = status, error
        self.publish(job)
        if job.context.role == "owner" and not self.closing and not self.log.failed:
            self.state.notify_governor("owner_finished", job.public())

    async def _run(self, job, entry):
        try:
            if self.runner is None:
                from runtime import run_child
                runner = run_child
            else:
                runner = self.runner
            text = await runner(self.log, self.task_dir, self.state, job.context, entry, self)
            descendants = [self.tasks[key] for key, child in self.jobs.items()
                           if child.context.parent_id == job.context.agent_id and not self.tasks[key].done()]
            if descendants:
                job.status = "waiting_workers"
                self.publish(job)
                await asyncio.gather(*descendants)
        except asyncio.CancelledError:
            await self.cancel_children(job)
            self.finish(job, "cancelled", "Agent cancelled during shutdown.", "Harness shutdown")
        except Exception as exc:
            await self.cancel_children(job)
            error = self.log.clean(f"{type(exc).__name__}: {exc}")
            self.finish(job, "failed", error, error)
        else:
            self.finish(job, "completed", text)

    async def cancel_children(self, job):
        children = [child for key, child in self.jobs.items()
                    if child.context.parent_id == job.context.agent_id and not self.tasks[key].done()]
        for child in children:
            self.tasks[child.context.agent_id].cancel()
        if children:
            await asyncio.gather(*(self.tasks[c.context.agent_id] for c in children), return_exceptions=True)
        for child in children:
            if child.status == "running" and self.tasks[child.context.agent_id].cancelled():
                self.finish(child, "cancelled", "Cancelled before starting because owner stopped.", "Owner stopped")

    async def status(self, caller, agent_id, wait_seconds=0):
        job = self.jobs.get(agent_id)
        if not job or (caller.role != "governor" and job.context.parent_id != caller.agent_id):
            raise Denied("Agent is not owned by this caller.")
        if type(wait_seconds) not in (int, float) or not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 10:
            raise ValueError("wait_seconds must be between 0 and 10.")
        if not self.tasks[agent_id].done() and wait_seconds:
            await asyncio.wait({self.tasks[agent_id]}, timeout=wait_seconds)
        return job.public()

    def publish_request(self, request):
        self.state.request_update(request)

    async def request(self, service, request_id, response_name):
        c = service.context
        if c.role != "owner" or c.agent_id not in self.jobs or self.is_blocked(c.agent_id):
            raise Denied("Only an unblocked task owner may request the governor.")
        note = service.access.resolve(request_id)
        if note["author"] != c.author or not note["body"].strip():
            raise Denied("Request must pin your own written justification.")
        service.access.reserve_output(response_name)
        key = f"request-{len(self.requests)+1}"
        request = {"request_id": key, "owner_id": c.agent_id, "note_id": request_id,
                   "note_name": note["name"], "justification": note["body"],
                   "response_name": response_name, "status": "pending"}
        future = asyncio.get_running_loop().create_future()
        self.requests[key], self.answers[key] = request, future
        self.log.write("governor_request", **request)
        self.jobs[c.agent_id].status = "blocked"
        self.publish(self.jobs[c.agent_id])
        self.publish_request(request)
        self.state.notify_governor("governor_request", request)
        deadline = service.deadline
        remaining = max(0.01, deadline.when() - asyncio.get_running_loop().time()) if deadline and deadline.when() else None
        if remaining is not None:
            deadline.reschedule(None)
        try:
            return await future
        except asyncio.CancelledError:
            was_resolved = request["status"] == "resolved"
            if not was_resolved:
                request["status"] = "cancelled"
            if not self.log.failed and not was_resolved:
                self.log.finish_output(response_name, "Request cancelled during shutdown.", tag="governance.response")
                self.log.write("governor_request_cancelled", request_id=key)
            self.publish_request(request)
            raise
        finally:
            if remaining is not None:
                deadline.reschedule(asyncio.get_running_loop().time() + remaining)
            if self.jobs[c.agent_id].status == "blocked":
                self.jobs[c.agent_id].status = "running"
                self.publish(self.jobs[c.agent_id])

    def resolve(self, service, request_id, response_id, decision):
        if service.context.role != "governor":
            raise Denied("Only the governor may resolve requests.")
        request = self.requests.get(request_id)
        if not request or request["status"] not in {"pending", "human_decided"}:
            raise Denied("Request is not ready for a governor decision.")
        if decision not in {"approve", "deny", "needs_human"}:
            raise ValueError("Choose approve, deny, or needs_human.")
        response = service.access.resolve(response_id)
        if response["author"] != service.author:
            raise Denied("Response must be a governor-authored note.")
        owner = self.jobs[request["owner_id"]].context
        owner.bounds.require("ledger.read", response["name"])
        if decision == "needs_human":
            if request.get("human_decision"):
                raise Denied("Human decision is already recorded.")
            request.update(status="needs_human", governor_note=response["body"], governor_response_id=response_id)
            self.log.write("human_approval_requested", **request)
            self.publish_request(request)
            return {"request_id": request_id, "status": "needs_human"}
        if request.get("human_decision") == "deny" and decision != "deny":
            raise Denied("Human denied this request; governor cannot approve it.")
        ref = self.log.finish_output(request["response_name"], response["body"], author=service.author, tag="governance.response")
        answer = {"request_id": request_id, "decision": decision, "response": ref, "bounds_changed": False}
        self.log.write("governor_response", **answer, owner_id=owner.agent_id, response_id=response_id)
        request.update(status="resolved", decision=decision, response=ref)
        self.publish_request(request)
        self.answers[request_id].set_result(answer)
        return answer

    async def human_decision(self, request_id, decision, justification):
        request = self.requests.get(request_id)
        if not request or request["status"] != "needs_human" or decision not in {"approve", "deny"}:
            raise ValueError("Only a pending human approval can be decided.")
        if not isinstance(justification, str) or not justification.strip() or len(justification) > 4000:
            raise ValueError("Provide a human decision explanation (1–4000 characters).")
        ref = self.log.user_message(f"human-{request_id}", justification)
        self.log.write("human_decision", request_id=request_id, decision=decision, text=ref["link"], text_id=ref["id"])
        request.update(status="human_decided", human_decision=decision, human_text=ref["link"])
        self.publish_request(request)
        self.state.notify_governor("human_decision", request)
        return {"request_id": request_id, "status": "human_decided"}

    async def close(self):
        self.closing = True
        tasks = [t for t in self.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for agent_id, job in self.jobs.items():
            if job.status in {"running", "blocked", "waiting_workers"} and self.tasks[agent_id].cancelled():
                self.finish(job, "cancelled", "Agent cancelled before starting.", "Harness shutdown")
