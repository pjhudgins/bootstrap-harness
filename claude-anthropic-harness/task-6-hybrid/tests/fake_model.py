# Copied from claude-codex-harness/task-5-subagent/fake_model.py (2026-09-28; the peer's file is
# unchanged), which came from its task-4-ledger. Used only by this lane's offline Codex tests.
# From ../task-4-ledger/fake_model.py (2026-09-25). Task 5: messages are streamed as text
# fragments, as a real model's are, and input_summary() shows what Codex sent.
"""A stand-in for the OpenAI Responses API on 127.0.0.1, for offline Codex runs.

Codex is pointed at it as a custom model provider. It records every request (so the
exact tool list and input Codex sends the model can be inspected) and answers each one
from a script, streaming message text in fragments. No credentials: the provider is
configured with requires_openai_auth = false, and request headers other than a harmless
few are never recorded.
"""

import hashlib
import itertools
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SAFE_HEADERS = ("content-type", "content-encoding", "user-agent", "originator", "accept")
PROVIDER_ID = "nimoi_fake"
FRAGMENT_CHARS = 16  # message text is streamed in fragments of this many characters


_message_ids = itertools.count(1)


def message(text):
    # Unique ids (task 3): the page keys streamed text by item id, and a repeated id
    # merged two turns' replies into one bubble.
    return {"type": "message", "role": "assistant", "id": f"msg_fake_{next(_message_ids)}",
            "content": [{"type": "output_text", "text": text}]}


def function_call(call_id, name, arguments):
    return {"type": "function_call", "call_id": call_id, "name": name,
            "arguments": json.dumps(arguments)}


def tool_names(body):
    """Every tool a request offers the model, as dotted names.

    Codex 0.155 sends tools as an `additional_tools` input item, not in `tools`; both are
    read. Namespaces are flattened (`clock.sleep`). Tools reachable only from inside
    code-mode `exec` JavaScript are listed as `...exec>name`, parsed from the headings
    of exec's description, which is the only place they are declared.
    """
    found = []

    def walk(tools, prefix):
        for tool in tools or []:
            name = tool.get("name") or tool.get("type")
            if tool.get("type") == "namespace":
                walk(tool.get("tools"), f"{prefix}{name}.")
                continue
            found.append(prefix + name)
            if tool.get("type") == "custom" and name == "exec":
                for nested in re.findall(r"^### `([^`]+)`", tool.get("description") or "", re.M):
                    found.append(f"{prefix}exec>{nested}")

    walk(body.get("tools"), "")
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            walk(item.get("tools"), "")
    return found


def input_summary(body):
    """One line per input item Codex sent: type, role, size, a hash and the first line
    of its text (at most 100 characters). Shows what Codex adds to the conversation,
    such as environment context or instruction files, without recording it whole."""
    summary = []
    for item in body.get("input") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, list):
            text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        else:
            text = item.get("output") or item.get("arguments") or ""
            text = text if isinstance(text, str) else json.dumps(text)
        summary.append({"type": item.get("type"), "role": item.get("role"),
                        "chars": len(text),
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "head": text.strip().split("\n", 1)[0][:100]})
    return summary


def _events(items):
    """The Responses stream for one reply: each message is announced, streamed as text
    fragments and then completed; other items are completed at once."""
    events = [{"type": "response.created", "response": {"id": "resp_fake"}}]
    for index, item in enumerate(items):
        if item.get("type") == "message":
            text = "".join(c.get("text", "") for c in item.get("content") or [])
            events.append({"type": "response.output_item.added", "output_index": index,
                           "item": {**item, "status": "in_progress", "content": []}})
            for start in range(0, len(text), FRAGMENT_CHARS):
                events.append({"type": "response.output_text.delta", "item_id": item["id"],
                               "output_index": index, "content_index": 0,
                               "delta": text[start:start + FRAGMENT_CHARS]})
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append({"type": "response.completed", "response": {
        "id": "resp_fake", "usage": {
            "input_tokens": 100, "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 110}}})
    return events


class FakeModel:
    """script(body) -> list of output items for one Responses request.
    on_request(entry), if given, sees every request as it is recorded."""

    def __init__(self, script, on_request=None):
        self.script = script
        self.on_request = on_request
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self, body):
                entry = {"method": self.command, "path": self.path, "body": body,
                         "headers": {k: v for k, v in self.headers.items()
                                     if k.lower() in SAFE_HEADERS}}
                fake.requests.append(entry)
                if fake.on_request:
                    fake.on_request(entry)
                return entry

            def do_GET(self):
                self._record(None)
                self.send_error(404, "fake model: not implemented")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Content-Encoding"):
                    self._record({"undecoded_bytes": len(raw)})
                    self.send_error(415, "fake model: compressed bodies are not supported")
                    return
                body = json.loads(raw or b"{}")
                self._record(body)
                if not self.path.rstrip("/").endswith("/responses"):
                    self.send_error(404, "fake model: not implemented")
                    return
                items = fake.script(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in _events(items):
                    self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                                     .encode("utf-8"))
                self.wfile.flush()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def codex_args(self):
        """app-server arguments that make Codex use this server as its model provider."""
        provider = (f'{{ name = "NIMOI fake model", base_url = "{self.base_url}", '
                    'wire_api = "responses", requires_openai_auth = false, '
                    'request_max_retries = 0, stream_max_retries = 0 }')
        return ["-c", f'model_provider="{PROVIDER_ID}"',
                "-c", f"model_providers.{PROVIDER_ID}={provider}",
                "--disable", "enable_request_compression"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
