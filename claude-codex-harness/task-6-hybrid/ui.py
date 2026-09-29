# From ../task-5-subagent/ui.py (2026-09-25). Task 6: the page is a chat with the
# governor, the other agents' activity beside it, and the human's approvals.
"""Task 6: a local web page for one conversation with the governor, and a view of every
task owner and subagent it leads to (rules.md 6d).

    python ui.py                 # new conversation, opens the default browser
    python ui.py --fake-model --ledger-root .runtime/fake-ledgers   # scripted models, no tokens
    python ui.py --no-browser --port 8765

Every launch is a new conversation (a new session for every agent) and a new session
file in the harness's ledger, ledgers/claude-codex-hybrid/. End it with the page's End
button, or Ctrl+C: both stop every agent, close the session with a trailer and release
the ledger's lease. Killing the process leaves the lease for a human to clear. The port
is bound first, so a port already in use opens no ledger session. Python standard
library, the Claude Agent SDK, and bootstrap-ledger's scribe module.

HTTP surface (127.0.0.1 only):
  GET  /                    the page (carries this launch's API token)
  GET  /static/<file>       app.js, style.css
  GET  /api/events?token=   Server-Sent Events: every UI event, replayed from the start
  POST /api/send            {"text": ...}  one user message (409 while a turn runs)
  POST /api/interrupt       stop every running turn
  POST /api/decide          {"request": id, "approve": bool, "note": ...}  the human's decision
  POST /api/end             end the conversation and stop the server
POSTs need the header X-NIMOI-Token. Requests with any other Host are refused, so
other websites open in the same browser cannot drive the agent (CSRF, DNS rebinding).
"""

import argparse
import hmac
import json
import secrets
import socket
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


class PageServer(ThreadingHTTPServer):
    """A server that owns its port. HTTPServer sets SO_REUSEADDR, and on Windows that lets
    a second server bind a port another HTTPServer is already serving, silently: requests
    then reach either one (observed 2026-09-25). This one binds exclusively, so a port in
    use is an error."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(port=0):
    """Bind the page's server. It serves `server.conversation`, set before serve_forever."""
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
            events = self.server.conversation.events
            after = int(self.headers.get("Last-Event-ID") or (query.get("after") or ["0"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            for name, value in HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            try:
                while True:
                    batch = events.after(after, timeout=15)
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
            conversation = self.server.conversation
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
                self._send(202, {"accepted": True, "stopping": conversation.interrupt()})
            elif path == "/api/decide":
                try:
                    decided = conversation.decide(str(body.get("request")),
                                                  bool(body.get("approve")), body.get("note"))
                except ValueError as error:
                    self._send(409, {"error": str(error)})
                    return
                self._send(200, decided)
            elif path == "/api/end":
                self._send(202, {"accepted": True})
                threading.Thread(target=lambda: (conversation.close(), self.server.shutdown()),
                                 daemon=True).start()
            else:
                self._send(404, {"error": "not found"})

    server = PageServer(("127.0.0.1", port), Handler)
    server.conversation = None
    bound = server.server_address[1]
    hosts.update({f"127.0.0.1:{bound}", f"localhost:{bound}"})
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--fake-model", action="store_true",
                        help="scripted local stand-ins for Claude and GPT: no tokens, and the "
                             "in-swimlane Codex and Claude homes")
    parser.add_argument("--codex", help="path to the codex executable")
    parser.add_argument("--codex-home", help="Codex home (default: $CODEX_HOME, else ~/.codex)")
    parser.add_argument("--ledger-root", default=str(LEDGER_ROOT),
                        help="folder of ledgers (default: ledgers/ here). Use a scratch folder "
                             "for fake-model trials, so they stay out of the harness's ledger.")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    try:  # first: a port in use must not leave an opened ledger session behind
        server = make_server(args.port)
    except OSError as error:
        print(f"ui: cannot listen on 127.0.0.1:{args.port}: {error.strerror or error}; "
              "no ledger session was opened", file=sys.stderr)
        sys.exit(1)
    try:
        conversation = Conversation(codex=args.codex, codex_home=args.codex_home,
                                    fake=args.fake_model, ledger_root=args.ledger_root)
    except scribe.OpenRefused as error:
        server.server_close()
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
    print(f"ui: ledger session {conversation.record.path}", file=sys.stderr, flush=True)
    try:
        conversation.start()
    except Exception as error:
        print(f"ui: FAILED to start the conversation: {error!r}", file=sys.stderr, flush=True)
        conversation.close()
        server.server_close()
        sys.exit(1)
    server.conversation = conversation
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
        print(f"ui: conversation ended; ledger session {conversation.record.path}", flush=True)


if __name__ == "__main__":
    main()
