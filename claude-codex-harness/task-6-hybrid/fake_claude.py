"""A stand-in for the Anthropic Messages API on 127.0.0.1, for offline Claude runs.

The Agent SDK's bundled Claude CLI is pointed at it with ANTHROPIC_BASE_URL, a dummy key
and a Claude config folder inside this lane, so an offline run touches neither ~/.claude
nor the founder's login (checked 2026-09-28). It records every request, so the tools and
system prompt the CLI sends can be inspected, and answers from a script, streaming text
in fragments, as the Codex stand-in (fake_model.py) does.

The script (reply()): each line `tool: <name> <json args>` of the latest message to the
agent is one tool call, made in order, one per response; after the last it reports the
last result; a message with no such lines is echoed. The CLI adds system-role reminder
messages to the conversation; they are skipped.
"""

import hashlib
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import policy

FRAGMENT_CHARS = 16
DUMMY_KEY = "nimoi-offline-dummy"  # not a key: the stand-in accepts anything
_ids = itertools.count(1)


def _text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or []
                     if isinstance(c, dict) and c.get("type") == "text")


def _results_of(content):
    return [c for c in content or [] if isinstance(c, dict) and c.get("type") == "tool_result"] \
        if isinstance(content, list) else []


def reply(body):
    """The scripted answer to one request: a list of content blocks."""
    messages = [m for m in body.get("messages") or [] if m.get("role") in ("user", "assistant")]
    # The latest message to the agent: a user message that is not only tool results.
    last = max((i for i, m in enumerate(messages) if m["role"] == "user"
                and _text_of(m.get("content")).strip() and not _results_of(m.get("content"))),
               default=None)
    if last is None:
        return [{"type": "text", "text": "(scripted fake Claude) nothing to answer"}]
    text = _text_of(messages[last]["content"])
    steps = [line[len("tool:"):].strip() for line in text.splitlines() if line.startswith("tool:")]
    since = messages[last + 1:]
    calls = sum(1 for m in since if m["role"] == "assistant"
                for c in m.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use")
    if calls < len(steps):
        name, _, raw = steps[calls].partition(" ")
        try:
            arguments = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return [{"type": "text", "text": f"(scripted fake Claude) Bad JSON arguments: {raw}"}]
        return [{"type": "tool_use", "id": f"toolu_fake_{next(_ids)}",
                 "name": policy.claude_tool_name(name), "input": arguments}]
    if steps:
        results = [r for m in since for r in _results_of(m.get("content"))]
        output = results[-1].get("content") if results else ""
        if isinstance(output, list):
            output = " ".join(c.get("text", "") for c in output if isinstance(c, dict))
        return [{"type": "text", "text": f"(scripted fake Claude) Done. Last result: {str(output)[:600]}"}]
    return [{"type": "text", "text": f"(scripted fake Claude) You said: {text}"}]


def tool_names(body):
    return [t.get("name") for t in body.get("tools") or []]


def input_summary(body):
    """One line per system block and message: role, size, a hash and the first line of
    its text (at most 100 characters). Shows what the CLI adds, without recording it all."""
    items = [("system", block.get("text", "")) for block in body.get("system") or []
             if isinstance(block, dict)]
    for m in body.get("messages") or []:
        content = m.get("content")
        text = _text_of(content) or json.dumps(content, default=str)
        items.append((m.get("role"), text))
    return [{"role": role, "chars": len(text),
             "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
             "head": text.strip().split("\n", 1)[0][:100]} for role, text in items]


def _events(message, blocks, stop):
    events = [("message_start", {"type": "message_start", "message": message})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {"type": "text", "text": ""}}))
            for start in range(0, len(block["text"]), FRAGMENT_CHARS):
                events.append(("content_block_delta", {
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "text_delta", "text": block["text"][start:start + FRAGMENT_CHARS]}}))
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {**block, "input": {}}}))
            events.append(("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(("message_delta", {"type": "message_delta",
                                     "delta": {"stop_reason": stop, "stop_sequence": None},
                                     "usage": {"output_tokens": 10}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return events


class FakeClaude:
    """script(body) -> list of content blocks for one Messages request.
    on_request(entry), if given, sees every request as it is recorded."""

    def __init__(self, script=reply, on_request=None):
        self.script, self.on_request, self.requests = script, on_request, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body):
                entry = {"method": self.command, "path": self.path, "body": body}
                fake.requests.append(entry)
                if fake.on_request:
                    fake.on_request(entry)

            def do_HEAD(self):  # the CLI's connectivity check
                self._record(None)
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                self._record(None)
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"undecodable_bytes": len(raw)}
                self._record(body)
                if not self.path.split("?")[0].endswith("/v1/messages"):
                    self.send_response(404)
                    self.end_headers()
                    return
                blocks = fake.script(body)
                stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
                message = {"id": f"msg_fake_{next(_ids)}", "type": "message", "role": "assistant",
                           "model": body.get("model", "fake"), "content": [], "stop_reason": None,
                           "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}
                if not body.get("stream"):
                    data = json.dumps({**message, "content": blocks, "stop_reason": stop}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for name, data in _events(message, blocks, stop):
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def env(self, config_dir):
        """Environment for a Claude CLI that talks only to this stand-in."""
        return {"ANTHROPIC_BASE_URL": self.url, "ANTHROPIC_API_KEY": DUMMY_KEY,
                "CLAUDE_CONFIG_DIR": str(config_dir)}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
