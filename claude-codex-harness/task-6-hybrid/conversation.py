# From ../task-5-subagent/conversation.py. Task 6: three layers on two engines, the
# governor's inbox, task owners that live until closed, and requests with approvals.
"""One conversation: the governor, its task owners and their subagents, under one ledger
session, published as UI events (rules.md task 6).

The human talks only to the governor (Claude Opus). The governor writes tickets and
dispatches task owners on them; each works turn by turn (its ticket, then the governor's
messages) until the governor closes it, and each turn's report goes to the governor.
Task owners delegate to one-turn subagents (task 5), and ask the governor, blocking, for
what they may not do: the governor grants up to its own bounds, refuses or answers, or
asks the human on the page, and the harness carries out what the human approves
(founder, 2026-09-28). News for the governor (reports, requests, the human's decisions)
reaches it as a harness message when it is idle. Stop interrupts every running turn.
Everything goes to the ledger through one LedgerRecord; the page sees small typed
events, each naming its agent.
"""

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

import bounds as bd
import codex_client as cc
import fake_claude
import fake_model
import ledger_log
import paths
import policy
import prompt
from agent import TurnTimeout
from tools import ToolError

TASK_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = TASK_DIR / ".runtime"
OFFLINE_CLAUDE_HOME = TASK_DIR.parent / ".runtime" / "offline-claude-home"
OFFLINE_CODEX_HOME = TASK_DIR.parent / ".runtime" / "offline-codex-home"
WORKSPACE = TASK_DIR / "workspace"
SCRIPTS = TASK_DIR / "scripts"
GOVERNOR_ID = "gov"
# As lists: a record must survive the scribe's JSON round trip, which tuples do not.
LAYER_MODELS = {layer: list(models) for layer, models in policy.LAYER_MODELS.items()}
MAX_DEPTH = 2              # governor 0, task owners 1, subagents 2 (the leaf layer)
MAX_OWNERS = 4             # task owners per conversation
MAX_SUBAGENTS = 6          # per conversation
MAX_RUNNING = 3            # subagents at once
GOVERNOR_TURN_CAP = 1800   # seconds
OWNER_TURN_CAP = 1800
CHILD_TURN_CAP = 900
TURN_IDLE = 300            # a turn with no event for this long is interrupted
WAIT_MARGIN = 15           # waits return this long before the waiter's own cap


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


def governor_bounds(fs_root, workspace, scripts):
    """The governor's bounds: what it may delegate, not what it does itself (it has no
    file-writing or script tools)."""
    rel = lambda p: Path(p).resolve().relative_to(Path(fs_root).resolve()).as_posix()
    bounds = bd.Bounds({"fs.read": ["*"], "fs.write": [rel(workspace) + "/"],
                        "fs.exec": [rel(scripts) + "/"], "ledger.read": ["*"],
                        "ledger.write": ["agent/", "tickets/"]})
    problems = bounds.invariant_problems()
    if problems:
        raise bd.BoundsError("\n".join(problems))
    return bounds


def union(a, b):
    """Bounds covering everything either covers (a grant added to what is held)."""
    return bd.Bounds({key: list(a[key]) + list(b[key]) for key in bd.KEYS})


def fake_codex_reply(body):
    """Scripted stand-in model for Codex agents under --fake-model: see fake_claude.reply
    for the script language, the same for both engines."""
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


class Request:
    """A task owner's request to the governor."""

    def __init__(self, rid, owner, kind, justification, details, text_name, text_id):
        self.id, self.owner, self.kind = rid, owner, kind
        self.justification, self.details = justification, details
        self.text_name, self.text_id = text_name, text_id
        self.state = "pending"        # pending -> asked_human -> decided
        self.decision = None          # {"by", "decision", "message", "granted"}
        self.governor_note = None
        self.done = threading.Event()

    def view(self):
        return {"request": self.id, "owner": self.owner.id, "request_kind": self.kind,
                "state": self.state, "justification": self.justification[:2000],
                "details": self.details, "entry": self.text_name, "decision": self.decision}


class Conversation:
    """start() once, send() per human message, interrupt() to stop every running turn,
    decide() for the human's approvals, close() once. The tools call spawn(), wait(),
    start_owner(), message_owner(), owner_status(), close_owner(), make_request(),
    wait_request(), list_requests() and answer_request(). `engines` maps "codex" and
    "claude" to agent classes (tests pass stand-ins that run no model)."""

    def __init__(self, codex=None, codex_home=None, fake=False,
                 ledger_root=ledger_log.LEDGER_ROOT, fs_root=ledger_log.NIMOI_ROOT,
                 runtime_dir=RUNTIME_DIR, workspace=WORKSPACE, scripts=SCRIPTS, engines=None):
        self.events = EventLog()
        # Opening the ledger takes its lease: a second harness on it is refused here.
        self.record = ledger_log.LedgerRecord(ledger_root)
        self.codex_arg, self.codex_home, self.fake = codex, codex_home, fake
        self.fs_root = Path(fs_root).resolve()
        self.runtime_dir, self.workspace, self.scripts = (Path(runtime_dir), Path(workspace),
                                                          Path(scripts))
        self.engines = engines or self._real_engines()
        self.max_depth = MAX_DEPTH
        self.onboarding = latest_onboarding(self.fs_root)
        self.governor_bounds = governor_bounds(self.fs_root, self.workspace, self.scripts)
        self.agents, self.governor, self.catalog = {}, None, {}
        self.requests = {}
        self.state = "starting"
        self.codex = None
        self.codex_env, self.claude_env, self.extra_codex_args = dict(os.environ), {}, []
        self.fake_codex = self.fake_claude = self.claude_loop = None
        self._inbox = queue.Queue()            # the governor's turns: ("human", text) or ("harness", None)
        self._pending = []                     # news for the governor, not yet delivered
        self._state_lock, self._spawn_lock = threading.Lock(), threading.Lock()
        self._closed = False
        self._inputs_seen = set()
        self.worker = None
        self.homes = []                        # (name, path, snapshot) recorded at the end

    @staticmethod
    def _real_engines():
        import claude_agent
        import codex_agent
        return {"claude": claude_agent.ClaudeAgent, "codex": codex_agent.CodexAgent}

    # ---- setup -------------------------------------------------------------------
    def start(self):
        stripped = policy.strip_host_agent_environment(os.environ)
        self.codex_env = dict(os.environ)
        if self.codex_home:
            self.codex_env["CODEX_HOME"] = str(Path(self.codex_home).resolve())
        elif self.fake:  # a fake-model run touches neither ~/.codex nor ~/.claude
            self.codex_env["CODEX_HOME"] = str(OFFLINE_CODEX_HOME)
        else:
            self.codex_env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
        try:
            self.codex = cc.find_codex(self.codex_arg)
        except FileNotFoundError:
            self.codex = None  # GPT agents then cannot start; the record says so
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        if self.fake:
            self.fake_codex = fake_model.FakeModel(fake_codex_reply, on_request=self._on_codex_request)
            self.extra_codex_args = self.fake_codex.codex_args()
            self.fake_claude = fake_claude.FakeClaude(on_request=self._on_claude_request)
            OFFLINE_CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
            self.claude_env = self.fake_claude.env(OFFLINE_CLAUDE_HOME)
        claude_home = Path(self.claude_env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        self.record.write(
            "run", harness="task-6-hybrid", record_format=ledger_log.RECORD_FORMAT,
            scribe=ledger_log.scribe_provenance(), fake_models=self.fake,
            codex=self.codex, codex_home=self.codex_env["CODEX_HOME"], claude_home=str(claude_home),
            host_agent_variables_removed=stripped, ledger=self.record.scribe.ledger,
            session=self.record.session, harness_author=self.record.author,
            governor_bounds=self.governor_bounds.as_dict(), onboarding=self.onboarding,
            workspace=str(self.workspace), scripts=str(self.scripts),
            layer_models=LAYER_MODELS,
            limits={"max_depth": MAX_DEPTH, "max_owners": MAX_OWNERS,
                    "max_subagents": MAX_SUBAGENTS, "max_running": MAX_RUNNING,
                    "turn_caps_s": {"governor": GOVERNOR_TURN_CAP, "task-owner": OWNER_TURN_CAP,
                                    "subagent": CHILD_TURN_CAP}, "turn_idle_s": TURN_IDLE},
            disabled_features=policy.DISABLED_FEATURES, thread_params=policy.THREAD_PARAMS)
        # Founder's conditions for live runs: record every change under each home.
        self.started = time.time()
        for name, home in (("codex_home_changes", self.codex_env["CODEX_HOME"]),
                           ("claude_home_changes", str(claude_home))):
            self.homes.append((name, home, cc.snapshot_tree(home)))
        if self.codex:
            self.catalog = read_catalog(self.codex, self.codex_env)
        import claude_agent  # the SDK is imported only when a conversation starts
        self.claude_loop = claude_agent.ClaudeLoop()
        governor = self._make("claude", GOVERNOR_ID, model=policy.GOVERNOR_MODEL,
                              bounds=self.governor_bounds, layer="governor")
        self.agents[GOVERNOR_ID], self.governor = governor, governor
        model, restrictions = governor.start(prompt.governor_instructions(
            bounds=self.governor_bounds, author=governor.author, onboarding=self.onboarding))
        self.state = "idle"
        self.events.publish(
            "session", agent=GOVERNOR_ID, model=model, engine=governor.engine,
            fake_model=self.fake, ledger_file=str(self.record.path),
            ledger={"name": self.record.scribe.ledger, "session": self.record.session,
                    "sessions": len(self.record.scribe.sessions),
                    "harness_author": self.record.author, "agent_author": governor.author},
            bounds=self.governor_bounds.as_dict(), onboarding=self.onboarding,
            layer_models=LAYER_MODELS,
            limits={"max_owners": MAX_OWNERS, "max_subagents": MAX_SUBAGENTS,
                    "max_running": MAX_RUNNING},
            tools=governor.toolbox.names, restrictions=restrictions,
            host_agent_variables_removed=len(stripped))
        self.events.publish("state", state="idle")
        self.worker = threading.Thread(target=self._work, name="governor", daemon=True)
        self.worker.start()

    def _make(self, engine, agent_id, **kwargs):
        cls = self.engines[engine]
        return cls(self, agent_id, **kwargs)

    # ---- the governor's conversation --------------------------------------------------
    def send(self, text):
        """A message from the human to the governor."""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty message")
        with self._state_lock:
            if self._closed or self.state != "idle":
                return False
            self.state = "running"
        self.events.publish("user_message", agent=GOVERNOR_ID, text=text)
        self.events.publish("state", state="running")
        self._inbox.put(("human", text))
        return True

    def notify_governor(self, line):
        """News for the governor: delivered now if it is idle, else after its turn."""
        with self._state_lock:
            if self._closed:
                return
            self._pending.append(line)
            if self.state != "idle":
                return
            self.state = "running"
        self.events.publish("state", state="running")
        self._inbox.put(("harness", None))

    def _harness_message(self):
        with self._state_lock:
            lines, self._pending = self._pending, []
        if not lines:
            return None
        return ("[harness] News since your last turn:\n" + "\n".join(f"- {line}" for line in lines))

    def _work(self):
        while True:
            item = self._inbox.get()
            if item is None:
                return
            source, text = item
            if source == "harness":
                text = self._harness_message()
            try:
                if text is not None:
                    if source == "harness":
                        self.events.publish("inbox", agent=GOVERNOR_ID, text=text)
                        author, role = ledger_log.HARNESS_AUTHOR, "harness"
                    else:
                        author, role = ledger_log.HUMAN_AUTHOR, "user"
                    self.governor.run_turn(text, role, author, cap=GOVERNOR_TURN_CAP,
                                           idle=TURN_IDLE)
            except ledger_log.LedgerFailure as error:
                self.events.publish("error", agent=GOVERNOR_ID,
                                    message=f"Ledger failure, conversation stopped: {error}")
                with self._state_lock:
                    self.state = "failed"
                self.events.publish("state", state="failed")
                return
            except Exception as error:
                self.events.publish("error", agent=GOVERNOR_ID, message=repr(error))
                try:
                    self.record.write("error", agent=GOVERNOR_ID, error=repr(error),
                                      traceback=traceback.format_exc())
                except ledger_log.LedgerFailure:
                    pass
            finally:
                with self._state_lock:
                    more = bool(self._pending) and not self._closed
                    if not self._closed and self.state != "failed":
                        self.state = "running" if more else "idle"
                if more:
                    self._inbox.put(("harness", None))
                elif not self._closed and self.state == "idle":
                    self.events.publish("state", state="idle")

    # ---- task owners (the governor's tools) ------------------------------------------
    def _owners(self):
        return [a for a in self.agents.values() if a.layer == "task-owner"]

    def _own_owner(self, governor, owner_id):
        owner = self.agents.get(owner_id)
        if owner is None or owner.layer != "task-owner" or owner.parent is not governor:
            raise ToolError(f"no task owner {owner_id!r}; yours are "
                            f"{[o.id for o in self._owners()]}")
        return owner

    def start_owner(self, governor, model, owner_bounds, ticket, ticket_id):
        problem = policy.model_problem("task-owner", model)
        if problem:
            raise ToolError(f"refused: {problem}")
        engine = policy.engine_for(model)
        if engine == "codex" and not self.codex:
            raise ToolError("refused: Codex is not available here, so no GPT task owner")
        with self._spawn_lock:
            if self._closed:
                raise ToolError("refused: the conversation is ending")
            if len(self._owners()) >= MAX_OWNERS:
                raise ToolError(f"refused: at most {MAX_OWNERS} task owners per conversation")
            owner_id = f"{governor.id}.{len(governor.children) + 1}"
            owner = self._make(engine, owner_id, model=model, bounds=owner_bounds,
                               layer="task-owner", parent=governor, instructions_name=ticket,
                               instructions_id=ticket_id)
            owner.inbox, owner.idle, owner.closing = queue.Queue(), threading.Event(), False
            self.agents[owner_id] = owner
            governor.children.append(owner_id)
        pinned = self.record.scribe.line(ticket_id) or {}
        self.record.write("owner_start", agent=governor.id, owner=owner_id, model=model,
                          engine=engine, bounds=owner_bounds.as_dict(), ticket=ticket,
                          ticket_id=ticket_id, ticket_author=pinned.get("author"),
                          author=owner.author)
        self.events.publish("agent_started", agent=owner_id, parent=governor.id, layer="task-owner",
                            model=model, engine=engine, author=owner.author,
                            bounds=owner_bounds.as_dict(), instructions=ticket)
        threading.Thread(target=self._run_owner, args=(owner, pinned), name=owner_id,
                         daemon=True).start()
        return {"owner": owner_id, "state": "starting", "model": model, "engine": engine,
                "author": owner.author, "bounds": owner_bounds.as_dict(), "ticket": ticket,
                "ticket_id": ticket_id}

    def _run_owner(self, owner, pinned):
        owner.error = None
        try:
            owner.start(prompt.owner_instructions(
                bounds=owner.bounds, author=owner.author, onboarding=self.onboarding,
                agent_id=owner.id, governor=owner.parent.author, ticket=owner.instructions_name,
                ticket_id=owner.instructions_id))
            text, role, author = pinned["body"], "ticket", pinned["author"]
            linked = {"name": owner.instructions_name, "id": owner.instructions_id,
                      "by": owner.parent.author}
            while text is not None:
                owner.idle.clear()
                turn = owner.run_turn(text, role, author, cap=OWNER_TURN_CAP, idle=TURN_IDLE,
                                      linked=linked)
                owner.idle.set()
                report = owner.last_message[0] if owner.last_message else None
                self.record.write("owner_report", agent=owner.id, turn=owner.stats["turns"],
                                  status=turn["status"],
                                  report_entry=owner.last_message[1] if owner.last_message else None)
                self.notify_governor(
                    f"Task owner {owner.id} ended turn {owner.stats['turns']} ({turn['status']})"
                    + (f"; its report: {report[:1500]}" if report else "; no report")
                    + (f" [[{owner.last_message[1]}]]" if owner.last_message else ""))
                text = owner.inbox.get()
                role, author, linked = "governor", owner.parent.author, None
        except Exception as error:
            owner.state, owner.error = "failed", repr(error)
            self.events.publish("error", agent=owner.id, message=repr(error))
            try:
                self.record.write("error", agent=owner.id, error=repr(error),
                                  traceback=traceback.format_exc())
            except ledger_log.LedgerFailure:
                pass
            self.notify_governor(f"Task owner {owner.id} failed: {error!r}")
        finally:
            owner.idle.set()
            try:
                owner.close()
            finally:
                if owner.state != "failed":
                    owner.state = "closed"
                try:
                    self.record.write("owner_closed", agent=owner.id, owner=owner.id,
                                      state=owner.state, turns=owner.stats["turns"],
                                      error=owner.error)
                except ledger_log.LedgerFailure:
                    pass
                self.events.publish("agent_finished", agent=owner.id, state=owner.state,
                                    model=owner.model, error=owner.error)
                owner.finished.set()

    def message_owner(self, governor, owner_id, text):
        owner = self._own_owner(governor, owner_id)
        if owner.closing or owner.finished.is_set():
            raise ToolError(f"{owner_id} is closed")
        owner.inbox.put(text)
        return {"owner": owner_id, "queued": True, "state": owner.state,
                "note": "it gets the message as its next turn"}

    def owner_status(self, governor, owner_id, seconds):
        if owner_id is None:
            return {"owners": [self._owner_view(o) for o in self._owners()]}
        owner = self._own_owner(governor, owner_id)
        end = time.monotonic() + seconds
        if governor.turn_cap_deadline is not None:
            end = min(end, governor.turn_cap_deadline - WAIT_MARGIN)
        while owner.state in ("starting", "running") and time.monotonic() < end:
            if governor.stop_requested():
                break
            owner.idle.wait(max(0.0, min(0.2, end - time.monotonic())))
            if owner.idle.is_set() and owner.state != "starting":
                break
        return self._owner_view(owner)

    def _owner_view(self, owner):
        text, name, _ = owner.last_message or (None, None, None)
        pending = [r.id for r in self.requests.values() if r.owner is owner and r.state != "decided"]
        return {"owner": owner.id, "state": owner.state, "model": owner.model,
                "turns": owner.stats["turns"], "bounds": owner.bounds.as_dict(),
                "last_report": text[:4000] if text else None, "report_entry": name,
                "open_requests": pending, "error": getattr(owner, "error", None)}

    def close_owner(self, governor, owner_id, reason):
        owner = self._own_owner(governor, owner_id)
        if owner.closing:
            return self._owner_view(owner)
        owner.closing = True
        self.record.write("owner_close", agent=governor.id, owner=owner_id, reason=reason)
        owner.interrupt_if_running(f"the governor closed it: {reason}")
        owner.inbox.put(None)
        return {**self._owner_view(owner), "closing": True}

    # ---- subagents (a task owner's tools) -------------------------------------------------
    def spawn(self, parent, model, child_bounds, instructions, instructions_id):
        problem = policy.model_problem("subagent", model)
        if problem:
            raise ToolError(f"refused: {problem}")
        engine = policy.engine_for(model)
        if engine == "codex" and not self.codex:
            raise ToolError("refused: Codex is not available here, so no GPT subagent")
        with self._spawn_lock:
            if self._closed:
                raise ToolError("refused: the conversation is ending")
            if parent.depth + 1 > self.max_depth:
                raise ToolError(f"refused: subagents may nest at most {self.max_depth} deep")
            subagents = [a for a in self.agents.values() if a.layer == "subagent"]
            if len(subagents) >= MAX_SUBAGENTS:
                raise ToolError(f"refused: at most {MAX_SUBAGENTS} subagents per conversation")
            if sum(1 for a in subagents if not a.finished.is_set()) >= MAX_RUNNING:
                raise ToolError(f"refused: at most {MAX_RUNNING} subagents may run at once; "
                                "wait for one to finish")
            child_id = f"{parent.id}.{len(parent.children) + 1}"
            child = self._make(engine, child_id, model=model, bounds=child_bounds,
                               layer="subagent", parent=parent, instructions_name=instructions,
                               instructions_id=instructions_id)
            self.agents[child_id] = child
            parent.children.append(child_id)
        pinned = self.record.scribe.line(instructions_id) or {}
        self.record.write("subagent_spawn", agent=parent.id, subagent=child_id, model=model,
                          engine=engine, bounds=child_bounds.as_dict(), instructions=instructions,
                          instructions_id=instructions_id,
                          instructions_author=pinned.get("author"), author=child.author)
        self.events.publish("agent_started", agent=child_id, parent=parent.id, layer="subagent",
                            model=model, engine=engine, author=child.author,
                            bounds=child_bounds.as_dict(), instructions=instructions)
        threading.Thread(target=self._run_child, args=(child, pinned), name=child_id,
                         daemon=True).start()
        return {"subagent": child_id, "state": "starting", "author": child.author,
                "model": model, "engine": engine, "bounds": child_bounds.as_dict(),
                "instructions": instructions, "instructions_id": instructions_id}

    def _run_child(self, child, pinned):
        child.error = None
        try:
            child.start(prompt.subagent_instructions(
                bounds=child.bounds, author=child.author, onboarding=self.onboarding,
                agent_id=child.id, parent=child.parent.author,
                instructions=child.instructions_name, instructions_id=child.instructions_id))
            turn = child.run_turn(pinned["body"], "task", pinned["author"],
                                  cap=CHILD_TURN_CAP, idle=TURN_IDLE,
                                  linked={"name": child.instructions_name,
                                          "id": child.instructions_id,
                                          "by": child.parent.author})
            child.state = {"completed": "done"}.get(turn["status"], turn["status"])
            child.error = (turn.get("error") or {}).get("message") \
                if isinstance(turn.get("error"), dict) else turn.get("error")
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
                self.events.publish("agent_finished", **report)
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
        if child is None or child.parent is not parent or child.layer != "subagent":
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

    # ---- requests ----------------------------------------------------------------------------
    def make_request(self, owner, kind, justification, details, seconds):
        with self._spawn_lock:
            rid = f"r{len(self.requests) + 1}"
        recorded = self.record.request(owner.id, justification, owner.author, request=rid,
                                       request_kind=kind, details=details)
        name, text_id = (recorded[0], recorded[1]) if recorded else (None, None)
        request = Request(rid, owner, kind, justification, details, name, text_id)
        self.requests[rid] = request
        self.events.publish("request", agent=owner.id, **request.view())
        detail = f" {json.dumps(details)}" if details else ""
        self.notify_governor(f"Request {rid} from task owner {owner.id} ({kind}){detail}: "
                             f"{justification[:1500]} [[{name}]] — decide it with request_answer.")
        return self.wait_request(owner, rid, seconds)

    def wait_request(self, owner, rid, seconds):
        request = self.requests.get(rid)
        if request is None or request.owner is not owner:
            raise ToolError(f"no request {rid!r} of yours")
        end = time.monotonic() + seconds
        if owner.turn_cap_deadline is not None:
            end = min(end, owner.turn_cap_deadline - WAIT_MARGIN)
        while not request.done.wait(max(0.0, min(0.2, end - time.monotonic()))):
            if owner.stop_requested():
                return {**request.view(), "note": "not waiting: your own turn is being stopped"}
            if time.monotonic() >= end:
                break
        view = request.view()
        if request.state != "decided":
            view["note"] = ("pending: the human is deciding" if request.state == "asked_human"
                            else "pending: the governor has not decided") + \
                "; call governor_wait to keep waiting"
        return view

    def list_requests(self, state):
        return {"requests": [r.view() for r in self.requests.values()
                             if state is None or r.state == state]}

    def answer_request(self, governor, rid, decision, message, granted):
        request = self.requests.get(rid)
        if request is None or request.owner.parent is not governor:
            raise ToolError(f"no request {rid!r} for you")
        if request.state != "pending":
            raise ToolError(f"{rid} is {request.state}: "
                            + ("the human decides it" if request.state == "asked_human"
                               else "already decided"))
        if decision == "grant":
            if request.kind != "bounds":
                raise ToolError("only a bounds request is granted by you: "
                                + ("a script promotion needs the human (ask_human)"
                                   if request.kind == "promote_script" else "use answer"))
            if granted is None:
                raise ToolError("a grant needs `bounds`: the part you grant")
            grant = bd.Bounds(granted)
            beyond = grant.problems_as_child_of(governor.bounds)
            if beyond:
                raise ToolError("refused: you may grant only inside your own bounds; ask the "
                                "human for the rest (ask_human):\n" + "\n".join(beyond))
            self._grant(request, grant, by=governor.author)
        elif decision == "ask_human":
            request.state, request.governor_note = "asked_human", message
            self.record.write("approval_asked", agent=governor.id, request=rid,
                              owner=request.owner.id, request_kind=request.kind,
                              details=request.details,
                              governor_note=message)
            self.events.publish("approval", agent=governor.id, **request.view(),
                                governor_note=message)
            return {**request.view(), "note": "the human decides it on the page"}
        self._resolve(request, by=governor.author, decision=decision, message=message)
        return request.view()

    def decide(self, rid, approve, note):
        """The human's decision on a request the governor asked about (from the page)."""
        request = self.requests.get(rid)
        if request is None or request.state != "asked_human":
            raise ValueError(f"no request {rid!r} is waiting for you")
        note = (note or "").strip()
        decision = "approved" if approve else "denied"
        if approve:
            try:
                if request.kind == "bounds":
                    self._grant(request, bd.Bounds(request.details["bounds"]),
                                by=ledger_log.HUMAN_AUTHOR)
                elif request.kind == "promote_script":
                    self._promote(request)
            except (bd.BoundsError, paths.PathRefused, ToolError, OSError) as error:
                decision, note = "failed", f"approved, but the harness could not do it: {error}"
        self._resolve(request, by=ledger_log.HUMAN_AUTHOR, decision=decision, message=note)
        self.notify_governor(f"The human {decision} request {rid} ({request.kind}) from "
                             f"{request.owner.id}" + (f": {note}" if note else "."))
        return request.view()

    def _grant(self, request, grant, *, by):
        owner = request.owner
        widened = union(owner.bounds, grant)
        problems = widened.invariant_problems()
        if problems:
            raise ToolError("refused: the grant would let it write where scripts run:\n"
                            + "\n".join(problems))
        before, owner.bounds = owner.bounds, widened
        self.record.write("bounds_granted", agent=owner.id, request=request.id, by=by,
                          granted=grant.as_dict(), before=before.as_dict(),
                          after=widened.as_dict())
        self.events.publish("bounds_granted", agent=owner.id, request=request.id, by=by,
                            bounds=widened.as_dict())
        request.decision_granted = grant.as_dict()

    def _promote(self, request):
        """Copy a workspace draft into the scripts folder, byte for byte (human approval)."""
        policy_ = paths.PathPolicy(self.fs_root)
        source, source_rel = policy_.resolve(request.details["from_path"])
        target, target_rel = policy_.resolve(request.details["to_path"], must_exist=False)
        workspace, scripts = self.workspace.resolve(), self.scripts.resolve()
        if workspace not in source.parents or source.suffix.lower() != ".py" or not source.is_file():
            raise ToolError("from_path must be an existing .py file in the workspace")
        if target.parent != scripts or target.suffix.lower() != ".py":
            raise ToolError("to_path must be a .py file directly in the scripts folder")
        if target.exists():
            raise ToolError(f"{target_rel} exists already; the harness does not replace scripts")
        data = source.read_bytes()
        shutil.copyfile(source, target)
        self.record.write("script_promoted", agent=request.owner.id, request=request.id,
                          by=ledger_log.HUMAN_AUTHOR, source=source_rel, target=target_rel,
                          bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def _resolve(self, request, *, by, decision, message):
        request.state = "decided"
        request.decision = {"by": by, "decision": decision, "message": message,
                            "granted": getattr(request, "decision_granted", None)}
        self.record.write("request_decided", agent=request.owner.id, request=request.id,
                          request_kind=request.kind, **request.decision)
        self.events.publish("request_decided", agent=request.owner.id, **request.view())
        request.done.set()

    # ---- fake-model runs only ------------------------------------------------------------------
    def _new_inputs(self, summary):
        fresh = [item for item in summary if item["sha256"] not in self._inputs_seen]
        self._inputs_seen.update(item["sha256"] for item in fresh)
        return fresh

    def _on_codex_request(self, entry):
        body = entry["body"] if isinstance(entry["body"], dict) else {}
        self.record.write("model_request", engine="codex", path=entry["path"],
                          model=body.get("model"), tool_names=fake_model.tool_names(body),
                          new_inputs=self._new_inputs(fake_model.input_summary(body)))

    def _on_claude_request(self, entry):
        body = entry["body"] if isinstance(entry["body"], dict) else {}
        self.record.write("model_request", engine="claude", path=entry["path"],
                          model=body.get("model"), tool_names=fake_claude.tool_names(body),
                          new_inputs=self._new_inputs(fake_claude.input_summary(body)))

    # ---- stop and shutdown ---------------------------------------------------------------------
    def interrupt(self):
        """Stop on the page: interrupt every running turn (the governor's and the task
        owners'), and every subagent that has not finished, including one still starting.
        Task owners are not closed: the governor decides that."""
        reason = "stop requested on the page"
        stopped = []
        for agent in list(self.agents.values()):
            if agent.layer == "subagent" and not agent.finished.is_set():
                agent.interrupt(reason)
                stopped.append(agent.id)
            elif agent.layer != "subagent" and agent.interrupt_if_running(reason):
                stopped.append(agent.id)
        if stopped:
            try:
                self.record.write("stop", requested_by=ledger_log.HUMAN_AUTHOR, agents=stopped)
            except ledger_log.LedgerFailure:
                pass
        return stopped

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self.state = "ended"
        try:
            self._close()
        finally:
            self.record.close()

    def _close(self):
        reason = "the conversation is ending"
        for request in self.requests.values():  # unblock anyone waiting on a decision
            if request.state != "decided":
                self._resolve(request, by=ledger_log.HARNESS_AUTHOR, decision="cancelled",
                              message=reason)
        for agent in list(self.agents.values()):
            agent.interrupt(reason)
            if agent.layer == "task-owner":
                agent.closing = True
                agent.inbox.put(None)
        if self.worker is not None:
            self._inbox.put(None)
            self.worker.join(timeout=30)
        others = [a for a in self.agents.values() if a is not self.governor]
        deadline = time.monotonic() + 60
        for agent in others:
            agent.finished.wait(max(0.0, deadline - time.monotonic()))
        for agent in others:  # any still running is stopped hard; its thread then ends
            if not agent.finished.is_set():
                agent.close()
                agent.finished.wait(10)
        if self.governor is not None:
            self.governor.close()
        if self.claude_loop is not None:
            self.claude_loop.close()
        for name, home, before in self.homes:
            changes = cc.diff_snapshots(before, cc.snapshot_tree(home), self.started, time.time())
            self.record.write(name, home=home, files_before=len(before), changes=changes,
                              note="while_running = mtime within the conversation's lifetime; "
                                   "other processes (desktop apps, other sessions) may also write")
        for fake in (self.fake_codex, self.fake_claude):
            if fake is not None:
                fake.close()
        summary = {"agents": {a.id: {"layer": a.layer, "engine": a.engine, "state": a.state,
                                     "model": a.model, "parent": a.parent.id if a.parent else None,
                                     "turns": a.stats["turns"], "tool_calls": a.stats["tool_calls"],
                                     "unreviewed_calls": a.stats["unreviewed_calls"],
                                     "token_usage_total": (a.stats["usage"] or {}).get("total")}
                              for a in self.agents.values()},
                   "requests": {r.id: r.view() for r in self.requests.values()}}
        try:
            self.record.write("summary", **summary)
        finally:
            self.events.publish("ended", agent=GOVERNOR_ID, ledger_file=str(self.record.path),
                                summary=summary)
