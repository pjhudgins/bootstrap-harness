# Task 4 version of ../task-3-ui/ui.py: the record is the ledger; startup explains a
# held ledger lease.
"""Task 4: a local web page for one conversation with the test-pilot Codex agent.

    python ui.py                 # new conversation, opens the default browser
    python ui.py --fake-model    # scripted stand-in model: no tokens, for trying the page
    python ui.py --no-browser --port 8765

Every launch is a new conversation (a new ephemeral Codex thread) and a new session file
in the harness's ledger, ledgers/claude-codex-pilot/. End it with the page's End button
or Ctrl+C, which closes the session with a trailer and releases the ledger's lease.
Python standard library plus bootstrap-ledger's scribe module.

HTTP surface (127.0.0.1 only):
  GET  /                    the page (carries this launch's API token)
  GET  /static/<file>       app.js, style.css
  GET  /api/events?token=   Server-Sent Events: every UI event, replayed from the start
  POST /api/send            {"text": ...}  one user message (409 while a turn runs)
  POST /api/interrupt       stop the running turn
  POST /api/end             end the conversation and stop the server
POSTs need the header X-NIMOI-Token. Requests with any other Host are refused, so
other websites open in the same browser cannot drive the agent (CSRF, DNS rebinding).
"""

import argparse
import hmac
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from conversation import Conversation
from ledger_log import LEDGER_NAME, LEDGER_ROOT, scribe

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {"index.html": "text/html; charset=utf-8",
                 "app.js": "text/javascript; charset=utf-8",
                 "style.css": "text/css; charset=utf-8"}
MAX_BODY = 64 * 1024
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
           "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"}


def make_server(conversation, port=0):
    token = secrets.token_urlsafe(24)
    hosts = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for name, value in HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self, token_given):
            if self.headers.get("Host") not in hosts:
                self._send(403, {"error": "unexpected Host"})
                return False
            if token_given is not None and not hmac.compare_digest(token_given, token):
                self._send(403, {"error": "bad or missing token"})
                return False
            return True

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/api/events":
                if self._allowed((query.get("token") or [""])[0]):
                    self._stream(query)
                return
            if not self._allowed(None):
                return
            name = "index.html" if url.path == "/" else url.path.removeprefix("/static/")
            if name not in CONTENT_TYPES or (url.path != "/" and not url.path.startswith("/static/")):
                self._send(404, {"error": "not found"})
                return
            body = (STATIC / name).read_bytes()
            if name == "index.html":
                body = body.replace(b"__NIMOI_TOKEN__", token.encode("ascii"))
            self._send(200, body, CONTENT_TYPES[name])

        def _stream(self, query):
            after = int(self.headers.get("Last-Event-ID") or (query.get("after") or ["0"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            for name, value in HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            try:
                while True:
                    batch = conversation.events.after(after, timeout=15)
                    for event in batch:
                        self.wfile.write(f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"
                                         .encode("utf-8"))
                        after = event["seq"]
                    if not batch:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    if any(event["type"] == "ended" for event in batch):
                        return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413, {"error": "message too large"})
                return
            # Read before refusing: answering with the body unread resets the connection
            # on Windows, and the client would see a reset instead of the 403.
            raw = self.rfile.read(length)
            if not self._allowed(self.headers.get("X-NIMOI-Token") or ""):
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": "body is not JSON"})
                return
            path = urlparse(self.path).path
            if path == "/api/send":
                try:
                    accepted = conversation.send(body.get("text"))
                except ValueError as error:
                    self._send(400, {"error": str(error)})
                    return
                self._send(202 if accepted else 409, {"accepted": accepted,
                                                      "state": conversation.state})
            elif path == "/api/interrupt":
                conversation.interrupt()
                self._send(202, {"accepted": True})
            elif path == "/api/end":
                self._send(202, {"accepted": True})
                threading.Thread(target=lambda: (conversation.close(), server.shutdown()),
                                 daemon=True).start()
            else:
                self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    bound = server.server_address[1]
    hosts.update({f"127.0.0.1:{bound}", f"localhost:{bound}"})
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--fake-model", action="store_true",
                        help="scripted local stand-in for the model: no tokens")
    parser.add_argument("--model", help="model (default: policy.MODEL; see task-2 record)")
    parser.add_argument("--codex", help="path to the codex executable")
    parser.add_argument("--codex-home", help="Codex home (default: $CODEX_HOME, else ~/.codex)")
    parser.add_argument("--ledger-root", default=str(LEDGER_ROOT),
                        help="folder of ledgers (default: ledgers/ here). Use a scratch folder "
                             "for fake-model trials, so they stay out of the pilot's ledger.")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    try:
        conversation = Conversation(codex=args.codex, codex_home=args.codex_home,
                                    model=args.model, fake=args.fake_model,
                                    ledger_root=args.ledger_root)
    except scribe.OpenRefused as error:
        print(f"ui: the ledger could not be opened ({error.code}): {error}", file=sys.stderr)
        if error.code == "lease_held":
            print("ui: another harness process holds the ledger, or one died holding it. "
                  "If none is running: check the pid and host in lease.json are gone, run "
                  "`python <nimoi>/bootstrap-ledger/python-scribe/scribe.py check "
                  f"{args.ledger_root} {LEDGER_NAME}`, then delete lease.json (a human's call; "
                  "see bootstrap-ledger/python-scribe/interfaces.md).", file=sys.stderr)
        for finding in error.findings or []:
            print(f"ui:   {finding}", file=sys.stderr)
        sys.exit(1)
    print(f"ui: ledger session {conversation.journal.path}", file=sys.stderr, flush=True)
    try:
        conversation.start()
    except Exception as error:
        print(f"ui: FAILED to start the conversation: {error!r}", file=sys.stderr, flush=True)
        conversation.close()
        sys.exit(1)
    server = make_server(conversation, args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"ui: conversation ready at {url}  (End button or Ctrl+C to finish)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        conversation.close()
        server.server_close()
        print(f"ui: conversation ended; ledger session {conversation.journal.path}", flush=True)


if __name__ == "__main__":
    main()
