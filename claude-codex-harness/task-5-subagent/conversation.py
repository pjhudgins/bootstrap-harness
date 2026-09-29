"""One conversation: a tree of agents under one ledger session, published as UI events.

The root test pilot takes turns from the page. Any agent below the depth cap can spawn
subagents: harness-owned agents (not Codex's own), each with its own app-server, that
run in parallel with their parent and are collected with subagent_wait. Stop on the page
interrupts every running agent. Everything goes to the ledger through one LedgerRecord;
the page sees small typed events, each naming its agent. A new kind of event = one
publish() here or in agent.py, plus one renderer in static/app.js.
"""

import json
import os
import queue
import subprocess
import threading
import time
import traceback
from pathlib import Path

import agent as agents
import bounds as bd
import codex_client as cc
import fake_model
import ledger_log
import policy
import prompt
from tools import ToolError

TASK_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = TASK_DIR / ".runtime"
WORKSPACE = TASK_DIR / "workspace"
SCRIPTS = TASK_DIR / "scripts"
ROOT_ID = "pilot"
AGENT_AREA = "agent/"      # where agents write in the ledger; the root's ledger.write
MAX_DEPTH = 2              # founder, 2026-09-25: "Nested, capped"
MAX_SUBAGENTS = 6          # per conversation
MAX_RUNNING = 3            # at once
ROOT_TURN_CAP = 1800       # seconds: a root turn may orchestrate several subagents
CHILD_TURN_CAP = 900
TURN_IDLE = 300            # a turn with no event for this long is interrupted
WAIT_MARGIN = 15           # subagent_wait returns this long before the waiter's own cap


class EventLog:
    """Append-only, in-memory list of UI events; readers wait for anything newer."""

    def __init__(self):
        self._events = []
        self._cond = threading.Condition()

    def publish(self, kind, **data):
        with self._cond:
            event = {"seq": len(self._events) + 1, "type": kind, "t": cc.utc_now(), **data}
            self._events.append(event)
            self._cond.notify_all()
            return event

    def after(self, seq, timeout):
        with self._cond:
            self._cond.wait_for(lambda: len(self._events) > seq, timeout)
            return self._events[seq:]


def read_catalog(codex, env):
    """Tool-relevant fields of every model in Codex's catalogue (`codex debug models`)."""
    try:
        out = subprocess.run([codex, "debug", "models"], env=env, cwd=TASK_DIR, timeout=60,
                             capture_output=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        models = json.loads(out.decode("utf-8-sig"))["models"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return {}
    fields = ("tool_mode", "multi_agent_version", "shell_type")
    return {m["slug"]: {f: m.get(f) for f in fields} for m in models if "slug" in m}


def latest_onboarding(fs_root):
    """origins/onboarding_<major>.<NN>.md with the highest version. The project defines
    the lexically highest name as the latest (nimoi/AGENTS.md: zero-padded versions)."""
    names = sorted(n for n in os.listdir(Path(fs_root) / "origins")
                   if n.startswith("onboarding_") and n.endswith(".md"))
    return f"origins/{names[-1]}"


def root_bounds(fs_root, workspace, scripts):
    rel = lambda p: Path(p).resolve().relative_to(Path(fs_root).resolve()).as_posix()
    bounds = bd.Bounds({"fs.read": ["*"], "fs.write": [rel(workspace) + "/"],
                        "fs.exec": [rel(scripts) + "/"],
                        "ledger.read": ["*"], "ledger.write": [AGENT_AREA]})
    problems = bounds.invariant_problems()
    if problems:
        raise bd.BoundsError("\n".join(problems))
    return bounds


def fake_reply(body):
    """Scripted stand-in model for --fake-model. Each line `tool: <name> <json args>` of the
    latest message to the agent is one tool call, made in order, one per response; after
    the last it reports the last result. A message with no such lines is echoed. So tests
    and demos decide every tool call, including a subagent's (its instructions are its
    message)."""
    inputs = body.get("input") or []
    last_user = max((i for i, item in enumerate(inputs) if item.get("role") == "user"),
                    default=None)
    if last_user is None:
        return [fake_model.message("(scripted fake model) nothing to answer")]
    text = " ".join(c.get("text", "") for c in (inputs[last_user].get("content") or [])
                    if isinstance(c, dict))
    steps = [line[len("tool:"):].strip() for line in text.splitlines() if line.startswith("tool:")]
    since = inputs[last_user + 1:]
    calls = sum(1 for item in since if item.get("type") == "function_call")
    if calls < len(steps):
        name, _, raw = steps[calls].partition(" ")
        try:
            arguments = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return [fake_model.message(f"(scripted fake model) Bad JSON arguments: {raw}")]
        return [fake_model.function_call(f"call_fake_{calls + 1}", name, arguments)]
    if steps:
        outputs = [i for i in since if i.get("type") == "function_call_output"]
        output = outputs[-1].get("output") if outputs else ""
        if isinstance(output, list):
            output = " ".join(c.get("text", "") for c in output if isinstance(c, dict))
        return [fake_model.message(f"(scripted fake model) Done. Last result: {str(output)[:600]}")]
    return [fake_model.message(f"(scripted fake model) You said: {text}")]


class Conversation:
    """start() once, send() per user message, interrupt() to stop every running turn,
    close() once. spawn() and wait() are called by agents' tools. `agent_factory` makes
    each agent (agent.Agent; tests pass a stand-in that runs no Codex)."""

    def __init__(self, codex=None, codex_home=None, model=None, fake=False,
                 ledger_root=ledger_log.LEDGER_ROOT, fs_root=ledger_log.NIMOI_ROOT,
                 runtime_dir=RUNTIME_DIR, workspace=WORKSPACE, scripts=SCRIPTS,
                 agent_factory=None):
        self.events = EventLog()
        # Opening the ledger takes its lease: a second harness on it is refused here.
        self.record = ledger_log.LedgerRecord(ledger_root)
        self.codex_arg, self.codex_home = codex, codex_home
        self.model = model or policy.MODEL
        self.fake = fake
        self.fs_root = Path(fs_root).resolve()
        self.runtime_dir, self.workspace, self.scripts = (Path(runtime_dir), Path(workspace),
                                                          Path(scripts))
        self.agent_factory = agent_factory or agents.Agent
        self.max_depth = MAX_DEPTH
        self.onboarding = latest_onboarding(self.fs_root)
        self.root_bounds = root_bounds(self.fs_root, self.workspace, self.scripts)
        self.agents, self.root, self.catalog = {}, None, {}
        self.state = "starting"
        self.fake_model, self.extra_codex_args = None, []
        self._inbox = queue.Queue()
        self._send_lock, self._spawn_lock = threading.Lock(), threading.Lock()
        self._closed = False
        self._inputs_seen = set()
        self.worker = None
        self.model_requests = 0

    # ---- setup -------------------------------------------------------------------
    def start(self):
        env = os.environ.copy()
        if self.codex_home:
            env["CODEX_HOME"] = str(Path(self.codex_home).resolve())
        else:  # in the gpt-codex swimlane codex could not find its home without this
            env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
        self.env = env
        self.codex = cc.find_codex(self.codex_arg)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.fake:
            self.fake_model = fake_model.FakeModel(fake_reply, on_request=self._on_model_request)
            self.extra_codex_args = self.fake_model.codex_args()
        self.record.write(
            "run", harness="task-5-subagent", record_format=ledger_log.RECORD_FORMAT,
            scribe=ledger_log.scribe_provenance(), model=self.model, fake_model=self.fake,
            codex_home=env["CODEX_HOME"], ledger=self.record.scribe.ledger,
            session=self.record.session, harness_author=self.record.author,
            root_bounds=self.root_bounds.as_dict(), onboarding=self.onboarding,
            workspace=str(self.workspace), scripts=str(self.scripts),
            limits={"max_depth": MAX_DEPTH, "max_subagents": MAX_SUBAGENTS,
                    "max_running": MAX_RUNNING, "root_turn_cap_s": ROOT_TURN_CAP,
                    "child_turn_cap_s": CHILD_TURN_CAP, "turn_idle_s": TURN_IDLE},
            unrecorded_notifications=list(agents.UNRECORDED),
            disabled_features=policy.DISABLED_FEATURES, thread_params=policy.THREAD_PARAMS)
        # Founder's condition for live runs: record every change under CODEX_HOME.
        self.home_before, self.started = cc.snapshot_tree(env["CODEX_HOME"]), time.time()
        self.catalog = read_catalog(self.codex, env)
        root = self.agent_factory(self, ROOT_ID, model=self.model, bounds=self.root_bounds,
                                  depth=0, role="test-pilot")
        self.agents[ROOT_ID], self.root = root, root
        thread, restrictions = root.start(prompt.root_instructions(
            bounds=self.root_bounds, author=root.author, onboarding=self.onboarding,
            agent_id=ROOT_ID, can_delegate=root.depth < self.max_depth))
        account = root.server.request("account/read", {"refreshToken": False}).get("account") or {}
        self._read_rate_limits("start")
        self.state = "idle"
        self.events.publish(
            "session", agent=ROOT_ID, model=thread.get("model"),
            effort=thread.get("reasoningEffort"), thread_id=root.thread_id,
            account={"type": account.get("type"), "plan": account.get("planType")},
            fake_model=self.fake, ledger_file=str(self.record.path),
            ledger={"name": self.record.scribe.ledger, "session": self.record.session,
                    "sessions": len(self.record.scribe.sessions),
                    "harness_author": self.record.author, "agent_author": root.author},
            bounds=self.root_bounds.as_dict(), onboarding=self.onboarding,
            limits={"max_depth": MAX_DEPTH, "max_subagents": MAX_SUBAGENTS,
                    "max_running": MAX_RUNNING},
            tools=root.toolbox.names, reviewed_tools=policy.REVIEWED_MODEL_TOOLS,
            restrictions=restrictions)
        self.events.publish("state", state="idle")
        self.worker = threading.Thread(target=self._work, name="root", daemon=True)
        self.worker.start()

    def _read_rate_limits(self, when):
        try:
            result = self.root.server.request("account/rateLimits/read")
        except cc.RpcError as error:
            self.record.write("rate_limits", agent=ROOT_ID, source=when, error=error.error)
            self.events.publish("notice", agent=ROOT_ID, text=f"Rate limits unavailable: "
                                                              f"{error.error.get('message')}")
            return
        self.record.write("rate_limits", agent=ROOT_ID, source=when, snapshot=result)
        self.events.publish("rate_limits", agent=ROOT_ID, source=when,
                            **agents.limits_view(result.get("rateLimits")))

    # ---- the root's conversation -----------------------------------------------------
    def send(self, text):
        text = (text or "").strip()
        if not text:
            raise ValueError("empty message")
        with self._send_lock:
            if self._closed or self.state != "idle":
                return False
            self.state = "running"
        self.events.publish("user_message", agent=ROOT_ID, text=text)
        self.events.publish("state", state="running")
        self._inbox.put(text)
        return True

    def interrupt(self):
        """Stop on the page: interrupt the pilot's turn if one is running, and every
        subagent that has not finished, including one still starting (live, 2026-09-25:
        a subagent spawned a moment before Stop ran its whole task). A subagent runs one
        turn only, so a stop that comes before its turn means the turn never starts."""
        reason = "stop requested on the page"
        stopped = []
        if self.root is not None and self.root.interrupt_if_running(reason):
            stopped.append(self.root.id)
        for agent in list(self.agents.values()):
            if agent is not self.root and not agent.finished.is_set():
                agent.interrupt(reason)
                stopped.append(agent.id)
        if stopped:
            try:
                self.record.write("stop", requested_by=ledger_log.HUMAN_AUTHOR, agents=stopped)
            except ledger_log.LedgerFailure:
                pass  # the agents are stopping anyway; the failure is reported elsewhere
        return stopped

    def _work(self):
        while True:
            text = self._inbox.get()
            if text is None:
                return
            try:
                self.root.run_turn(text, "user", ledger_log.HUMAN_AUTHOR,
                                   cap=ROOT_TURN_CAP, idle=TURN_IDLE)
            except ledger_log.LedgerFailure as error:
                self.events.publish("error", agent=ROOT_ID,
                                    message=f"Ledger failure, conversation stopped: {error}")
                self.state = "failed"
                self.events.publish("state", state="failed")
                return
            except Exception as error:
                self.events.publish("error", agent=ROOT_ID, message=repr(error))
                try:
                    self.record.write("error", agent=ROOT_ID, error=repr(error),
                                      traceback=traceback.format_exc())
                except ledger_log.LedgerFailure:
                    pass
            finally:
                if not self._closed and self.state != "failed":
                    self.state = "idle"
                    self.events.publish("state", state="idle")

    # ---- subagents (called from agents' tools) ----------------------------------------
    def spawn(self, parent, model, child_bounds, instructions, instructions_id):
        with self._spawn_lock:
            if self._closed:
                raise ToolError("refused: the conversation is ending")
            if parent.depth + 1 > self.max_depth:
                raise ToolError(f"refused: subagents may nest at most {self.max_depth} deep")
            if not policy.direct_tool_model(self.catalog.get(model)):
                allowed = sorted(m for m, e in self.catalog.items() if policy.direct_tool_model(e))
                raise ToolError(
                    f"refused model {model!r}: a subagent's model must use direct tools (a "
                    f"code-mode model always gets a JavaScript exec tool). Allowed: {allowed}")
            subagents = [a for a in self.agents.values() if a is not self.root]
            if len(subagents) >= MAX_SUBAGENTS:
                raise ToolError(f"refused: at most {MAX_SUBAGENTS} subagents per conversation")
            if sum(1 for a in subagents if not a.finished.is_set()) >= MAX_RUNNING:
                raise ToolError(f"refused: at most {MAX_RUNNING} subagents may run at once; "
                                "wait for one to finish")
            child_id = f"{parent.id}.{len(parent.children) + 1}"
            child = self.agent_factory(self, child_id, model=model, bounds=child_bounds,
                                       depth=parent.depth + 1, role="subagent", parent=parent,
                                       instructions_name=instructions,
                                       instructions_id=instructions_id)
            self.agents[child_id] = child
            parent.children.append(child_id)
        pinned = self.record.scribe.line(instructions_id) or {}
        self.record.write("subagent_spawn", agent=parent.id, subagent=child_id, model=model,
                          bounds=child_bounds.as_dict(), instructions=instructions,
                          instructions_id=instructions_id,
                          instructions_author=pinned.get("author"), author=child.author)
        self.events.publish("subagent_started", agent=child_id, parent=parent.id, model=model,
                            author=child.author, bounds=child_bounds.as_dict(),
                            instructions=instructions)
        threading.Thread(target=self._run_child, args=(child,), name=child_id, daemon=True).start()
        return {"subagent": child_id, "state": "starting", "author": child.author,
                "model": model, "bounds": child_bounds.as_dict(),
                "instructions": instructions, "instructions_id": instructions_id}

    def _run_child(self, child):
        child.error = None
        try:
            # The version the parent pinned, whoever wrote it: linked, never copied.
            pinned = self.record.scribe.line(child.instructions_id)
            child.start(prompt.subagent_instructions(
                bounds=child.bounds, author=child.author, onboarding=self.onboarding,
                agent_id=child.id, parent=child.parent.author,
                instructions=child.instructions_name, instructions_id=child.instructions_id,
                can_delegate=child.depth < self.max_depth))
            turn = child.run_turn(pinned["body"], "task", pinned["author"],
                                  cap=CHILD_TURN_CAP, idle=TURN_IDLE,
                                  linked={"name": child.instructions_name,
                                          "id": child.instructions_id,
                                          "by": child.parent.author})
            child.state = {"completed": "done"}.get(turn["status"], turn["status"])
            child.error = (turn.get("error") or {}).get("message")
        except Exception as error:
            child.state, child.error = "failed", repr(error)
            self.events.publish("error", agent=child.id, message=repr(error))
            try:
                self.record.write("error", agent=child.id, error=repr(error),
                                  traceback=traceback.format_exc())
            except ledger_log.LedgerFailure:
                pass
        finally:
            try:
                child.close()
            finally:
                report = self.report(child)
                try:
                    self.record.write("subagent_finished", **report)
                except ledger_log.LedgerFailure:
                    pass
                self.events.publish("subagent_finished", **report)
                child.finished.set()

    def report(self, child):
        text, name, text_id = child.last_message or (None, None, None)
        return {"agent": child.id, "subagent": child.id, "state": child.state,
                "author": child.author, "model": child.model, "error": getattr(child, "error", None),
                "report": text[:4000] if text else None, "report_entry": name,
                "report_id": text_id, "turns": child.stats["turns"],
                "tool_calls": child.stats["tool_calls"], "model_calls": child.stats["model_calls"],
                "unreviewed_calls": child.stats["unreviewed_calls"],
                "token_usage_total": (child.stats["usage"] or {}).get("total")}

    def wait(self, parent, subagent, seconds):
        """Wait for one of the caller's own subagents. The wait ends early if the caller's
        turn is being stopped, or WAIT_MARGIN seconds before the caller's turn cap."""
        child = self.agents.get(subagent)
        if child is None or child.parent is not parent:
            raise ToolError(f"not your subagent: {subagent!r}; yours are {parent.children}")
        end = time.monotonic() + seconds
        if parent.turn_cap_deadline is not None:
            end = min(end, parent.turn_cap_deadline - WAIT_MARGIN)
        while not child.finished.wait(max(0.0, min(0.2, end - time.monotonic()))):
            if parent.stop_requested():
                return {"subagent": child.id, "state": child.state, "author": child.author,
                        "note": "not waiting: your own turn is being stopped"}
            if time.monotonic() >= end:
                break
        if child.finished.is_set():
            return self.report(child)
        return {"subagent": child.id, "state": child.state, "author": child.author,
                "note": "still running; call subagent_wait again"}

    def _on_model_request(self, entry):
        """Fake-model runs only: the tools Codex offered, and each input item not seen
        before in this conversation, summarised (what Codex adds, not only what we sent)."""
        body = entry["body"] if isinstance(entry["body"], dict) else {}
        self.model_requests += 1
        new_inputs = []
        for item in fake_model.input_summary(body):
            if item["sha256"] not in self._inputs_seen:
                self._inputs_seen.add(item["sha256"])
                new_inputs.append(item)
        self.record.write("model_request", path=entry["path"], model=body.get("model"),
                          tool_names=fake_model.tool_names(body), new_inputs=new_inputs)

    # ---- shutdown ---------------------------------------------------------------------
    def close(self):
        with self._send_lock:
            if self._closed:
                return
            self._closed = True
            self.state = "ended"
        try:
            self._close()
        finally:
            self.record.close()

    def _close(self):
        if self.root is not None:
            self.root.interrupt("the conversation is ending")
        if self.worker is not None:
            self._inbox.put(None)
            self.worker.join(timeout=30)
        children = [a for a in self.agents.values() if a is not self.root]
        for child in children:
            child.interrupt("the conversation is ending")
        deadline = time.monotonic() + 60
        for child in children:
            child.finished.wait(max(0.0, deadline - time.monotonic()))
        for child in children:  # any still running is stopped hard; its thread then ends
            if not child.finished.is_set():
                child.close()
                child.finished.wait(10)
        code = None
        if self.root is not None and self.root.server is not None:
            if self.worker is None or not self.worker.is_alive():
                try:
                    self._read_rate_limits("end")
                except cc.ProtocolError:
                    pass
            code = self.root.close()
        if hasattr(self, "home_before"):
            changes = cc.diff_snapshots(self.home_before, cc.snapshot_tree(self.env["CODEX_HOME"]),
                                        self.started, time.time())
            self.record.write("codex_home_changes", codex_home=self.env["CODEX_HOME"],
                              files_before=len(self.home_before), changes=changes,
                              note="while_running = mtime within the conversation's lifetime; "
                                   "other Codex processes (e.g. the desktop app) may also write")
        if self.fake_model is not None:
            self.fake_model.close()
        summary = {"agents": {a.id: {"state": a.state, "model": a.model, "parent":
                                     a.parent.id if a.parent else None,
                                     "turns": a.stats["turns"], "tool_calls": a.stats["tool_calls"],
                                     "unreviewed_calls": a.stats["unreviewed_calls"],
                                     "token_usage_total": (a.stats["usage"] or {}).get("total")}
                              for a in self.agents.values()},
                   "root_app_server_exit": code}
        try:
            self.record.write("summary", **summary)
        finally:
            self.events.publish("ended", agent=ROOT_ID, ledger_file=str(self.record.path),
                                summary=summary)
