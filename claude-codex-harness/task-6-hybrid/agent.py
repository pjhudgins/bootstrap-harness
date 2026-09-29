# From ../task-5-subagent/agent.py: the engine-independent half. The Codex half is now
# codex_agent.py; the Claude Agent SDK engine is claude_agent.py.
"""One agent in the harness's tree: the governor, a task owner or a subagent, running
on Codex App Server (codex_agent.py) or the Claude Agent SDK (claude_agent.py).

This base holds what does not depend on the engine: identity and layer, bounds and
tools, the onboarding gate, turns (the incoming message recorded; a stop before a turn
means it never starts; the idle and cap limits), message streams, tool calls, and the
check of every tool call a model makes: one that is neither ours nor reviewed stops the
turn. An engine implements start(instructions), close() and _engine_turn(text, cap,
idle), and calls _message_done, _stream_fragment, model_call and tool_call.

Streamed message fragments are shown on the page but not recorded one by one: each
message's fragments are summarised in one `message_stream` record (founder, 2026-09-25).
"""

import hashlib
import json
import threading
import time

import codex_client as cc
import ledger_log
import policy
import tools

TOOL_RECORD_CHARS = 2000
INTERRUPT_GRACE = 30      # seconds a turn may take to end after an interrupt


class TurnTimeout(RuntimeError):
    """A turn did not end within its grace after being interrupted."""


class Stream:
    """The fragments of one agent message, as they arrive."""

    def __init__(self, turn_started):
        self.fragments, self.chars, self.parts = 0, 0, []
        self.turn_started = turn_started
        self.first = self.last = None
        self.first_at = self.last_at = None

    def add(self, delta):
        now, now_at = time.monotonic(), cc.utc_now()
        if self.first is None:
            self.first, self.first_at = now, now_at
        self.last, self.last_at = now, now_at
        self.fragments += 1
        self.chars += len(delta)
        self.parts.append(delta)

    def text(self):
        return "".join(self.parts)

    def summary(self, final_text):
        """The metadata recorded for the message: never the fragments themselves."""
        if not self.fragments:
            return {"fragments": 0}
        return {"fragments": self.fragments, "chars": self.chars,
                "first_fragment_at": self.first_at, "last_fragment_at": self.last_at,
                "first_fragment_after_ms": round((self.first - self.turn_started) * 1000),
                "streamed_ms": round((self.last - self.first) * 1000),
                "matches_text": None if final_text is None else self.text() == final_text}


class Agent:
    engine = None  # "codex" or "claude"

    def __init__(self, conv, agent_id, *, model, bounds, layer, parent=None,
                 instructions_name=None, instructions_id=None):
        self.conv, self.id, self.model, self.bounds, self.layer = conv, agent_id, model, bounds, layer
        self.parent = parent
        self.depth = parent.depth + 1 if parent else 0
        self.instructions_name, self.instructions_id = instructions_name, instructions_id
        self.author = ledger_log.agent_author(layer, model, agent_id)
        self.record, self.scribe = conv.record, conv.record.scribe
        self.onboarding = conv.onboarding
        self.produced = set()        # ledger names its own tool calls created: always readable
        self.toolbox = tools.Toolbox(self, fs_root=conv.fs_root, workspace=conv.workspace,
                                     scripts=conv.scripts)
        self.state = "starting"
        self.children = []
        self.finished = threading.Event()
        self.last_message = None     # (text, transcript name, text id)
        self.stats = {"turns": 0, "tool_calls": 0, "model_calls": [], "unreviewed_calls": [],
                      "declined": [], "usage": None}
        self.turn_cap_deadline = None
        self._turn_lock = threading.Lock()  # state, and the stop flag, around turn ends
        self._stop_turn = threading.Event()
        self._stop_reason = None
        self._activity = time.monotonic()
        self._tools_running = 0      # our tool calls in progress (Claude runs them in parallel)
        self._turn_started = None
        self._streams = {}           # message key -> Stream, for the turn running now
        self._onboarding_lock = threading.Lock()
        self._onboarding_upto, self._onboarding_total = 0, None

    # ---- what the Toolbox asks of its agent ---------------------------------------------
    def onboarding_done(self):
        return self._onboarding_total is not None and self._onboarding_upto >= self._onboarding_total

    def note_onboarding_lines(self, start, end, total):
        with self._onboarding_lock:
            was = self.onboarding_done()
            if start <= self._onboarding_upto + 1:
                self._onboarding_upto = max(self._onboarding_upto, end)
            self._onboarding_total = total
            now = self.onboarding_done()
        if now and not was:
            self.record.write("onboarding_read", agent=self.id, path=self.onboarding, lines=total)
            self.publish("notice", text=f"{self.id} has read the onboarding; its tools are open.")

    def publish(self, kind, **data):
        self.conv.events.publish(kind, agent=self.id, **data)

    # ---- stopping ----------------------------------------------------------------------
    def interrupt(self, reason="stop requested"):
        """Stop the running turn, or the next one if none is running yet."""
        with self._turn_lock:
            if not self._stop_turn.is_set():
                self._stop_reason = reason
            self._stop_turn.set()

    def interrupt_if_running(self, reason):
        """Stop the turn running now, if one is; True if one was."""
        with self._turn_lock:
            if self.state != "running":
                return False
            if not self._stop_turn.is_set():
                self._stop_reason = reason
            self._stop_turn.set()
            return True

    def stop_requested(self):
        return self._stop_turn.is_set()

    def touch(self):
        """Something happened: the idle limit starts again."""
        self._activity = time.monotonic()

    def deadline(self, cap, idle):
        """When the turn is interrupted: at its cap, or `idle` seconds without events. A
        tool of ours that is running (a wait may block for minutes) is not idleness."""
        if self._tools_running:
            return self.turn_cap_deadline
        return min(self.turn_cap_deadline, self._activity + idle)

    def limit_reason(self, cap, idle):
        return (f"the turn reached its {cap} s cap" if time.monotonic() >= self.turn_cap_deadline
                else f"nothing happened for {idle} s")

    # ---- one turn ------------------------------------------------------------------------
    def run_turn(self, text, role, author, *, cap, idle, linked=None):
        """Record the incoming message, run one turn, return {"id", "status", "error"}.

        The message is recorded under its writer; `linked` ({"name", "id", "by"}) names an
        entry version (a ticket, a subagent's task) that is linked, not copied. A stop
        asked for before the turn starts means it never starts: nothing is sent."""
        self.stats["turns"] += 1
        with self._turn_lock:
            self.state = "running"
        try:
            if self._stop_turn.is_set():
                self.record.write("interrupt", agent=self.id, turn_id=None,
                                  reason=self._stop_reason, before_start=True)
                self.publish("notice", text=f"{self.id} was stopped before its turn began: "
                                            f"{self._stop_reason}.")
                return {"id": None, "status": "interrupted", "error": None}
            turn = self.stats["turns"]
            if linked:
                self.record.link_message(self.id, role, linked["name"], linked["id"], author,
                                         turn=turn, instructed_by=linked["by"])
            else:
                self.record.message(self.id, role, text, author, turn=turn)
            self._turn_started = self._activity = time.monotonic()
            self.turn_cap_deadline = self._turn_started + cap
            result = self._engine_turn(text, cap, idle)
            self._flush_streams()
            self.record.write("turn", agent=self.id, turn_id=result.get("id"),
                              status=result["status"], error=result.get("error"),
                              duration_ms=result.get("duration_ms"),
                              interrupted_because=result.get("interrupted_because"),
                              **({"engine": result["engine"]} if result.get("engine") else {}))
            self.publish("turn_completed", turn_id=result.get("id"), status=result["status"],
                         error=(result.get("error") or {}).get("message")
                         if isinstance(result.get("error"), dict) else result.get("error"),
                         duration_ms=result.get("duration_ms"))
            return result
        finally:
            with self._turn_lock:  # a stop ends one turn, not the agent's next one
                self.turn_cap_deadline = None
                if self.state == "running":
                    self.state = "idle"
                self._stop_turn.clear()
                self._stop_reason = None

    def _engine_turn(self, text, cap, idle):
        raise NotImplementedError

    def start(self, instructions):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    # ---- messages and their streams ------------------------------------------------------
    def _stream_fragment(self, key, delta):
        self._streams.setdefault(key, Stream(self._turn_started)).add(delta)
        self.publish("agent_delta", item_id=key, delta=delta)

    def _message_done(self, key, text, phase=None):
        """A completed agent message: its text by the agent, then its stream's summary."""
        recorded = self.record.message(self.id, "agent", text, self.author,
                                       turn=self.stats["turns"], item_id=key, phase=phase)
        if recorded:
            self.last_message = (text, recorded[0], recorded[1])
        self._record_stream(key, recorded, text, complete=True)
        self.publish("agent_message", item_id=key, text=text, phase=phase)

    def _record_stream(self, key, recorded, final_text, *, complete):
        stream = self._streams.pop(key, None) or Stream(self._turn_started)
        fields = {"agent": self.id, "turn": self.stats["turns"], "item_id": key,
                  "complete": complete, **stream.summary(final_text)}
        if recorded:
            fields.update(text=f"[[{recorded[0]}]]", text_id=recorded[1])
        self.record.write("message_stream", **fields)

    def _flush_streams(self):
        """Messages still streaming when the turn ended (interrupted or failed): their
        partial text is recorded as the agent's, marked incomplete."""
        for key in list(self._streams):
            partial = self._streams[key].text()
            recorded = self.record.message(self.id, "agent", partial, self.author,
                                           turn=self.stats["turns"], item_id=key,
                                           complete=False)
            self._record_stream(key, recorded, None, complete=False)

    # ---- tool calls ------------------------------------------------------------------------
    def model_call(self, name, *, kind=None, call_id=None, payload=None):
        """A tool call the model made, as the engine reports it. One that is neither ours
        nor reviewed stops the turn."""
        reviewed = policy.reviewed_call(name, self.toolbox.names)
        self.stats["model_calls"].append(name)
        self.record.write("model_call", agent=self.id, name=name, type=kind, call_id=call_id,
                          payload=payload, reviewed=reviewed)
        self.publish("model_call", name=name, reviewed=reviewed,
                     payload=json.dumps(payload, default=str)[:2000])
        if not reviewed:
            self.stats["unreviewed_calls"].append(name)
            self.interrupt(f"it called {name}, a tool that was never reviewed")

    def tool_call(self, tool, arguments, *, namespace=None, call_id=None, turn_id=None):
        """Run one of our tools for the engine; returns (success, output text)."""
        self.touch()
        with self._turn_lock:
            self._tools_running += 1
        try:
            success, output = self.toolbox.call(tool, namespace, arguments)
        finally:
            with self._turn_lock:
                self._tools_running -= 1
            self.touch()
        recorded = output if len(output) <= TOOL_RECORD_CHARS else {
            "first_chars": output[:TOOL_RECORD_CHARS], "chars": len(output),
            "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "whole_output": "as returned to the model (the engine's record of it)"}
        self.stats["tool_calls"] += 1
        self.record.write("tool_call", agent=self.id, engine=self.engine, call_id=call_id,
                          turn_id=turn_id, tool=tool, namespace=namespace, arguments=arguments,
                          success=success, output=recorded)
        self.publish("tool_call", tool=tool, arguments=arguments,
                     output=output[:TOOL_RECORD_CHARS] + ("…" if len(output) > TOOL_RECORD_CHARS
                                                          else ""), success=success)
        return success, output
