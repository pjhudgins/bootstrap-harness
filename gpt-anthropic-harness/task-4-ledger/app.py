"""Launch a fresh ledger-backed NIMOI test-pilot conversation."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import webbrowser

from conversation import Conversation, worker
from ledger import LedgerLog, LedgerFailed

TASK_DIR = Path(__file__).resolve().parent
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}


def make_server(state, port=0):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Never put arbitrary HTTP paths/headers in terminal logs.

        def reply(self, status, body, mime="application/json"):
            if mime == "application/json":
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {"error": "Invalid host"})
            if self.path == "/api/state":
                return self.reply(200, state.snapshot())
            if self.path in ASSETS:
                name, mime = ASSETS[self.path]
                return self.reply(200, (TASK_DIR / name).read_bytes(), mime)
            self.reply(404, {"error": "Not found"})

        def do_POST(self):
            # Drain a bounded body before rejecting it. On Windows, closing with
            # unread bytes can reset the connection before the 403 reaches the UI.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"error": "Invalid content length"})
            if not 0 < length <= 65536:
                return self.reply(413, {"error": "Request body must be 1–65536 bytes"})
            if self.headers.get("Transfer-Encoding"):
                return self.reply(400, {"error": "Transfer encoding unsupported"})
            try:
                raw_body = self.rfile.read(length)
            except OSError:
                return self.reply(408, {"error": "Incomplete request"})
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.valid_host() or self.headers.get("Origin") != origin:
                return self.reply(403, {"error": "Same-origin requests required"})
            if self.headers.get_content_type() != "application/json":
                return self.reply(415, {"error": "JSON required"})
            try:
                body = json.loads(raw_body.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if self.path == "/api/message":
                    turn = state.submit(body.get("text"))
                    return self.reply(202, {"turn": turn})
                if self.path == "/api/shutdown":
                    state.stop()
                    self.reply(200, {"status": "stopping"})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.reply(404, {"error": "Not found"})
            except (ValueError, UnicodeError):
                self.reply(400, {"error": "Enter a valid JSON message of 1–16,000 characters"})
            except LedgerFailed:
                state.update(status="error", error="Ledger write failed. Preserve ledger/lease for human review.")
                self.reply(500, {"error": "Ledger unavailable; message not accepted"})
            except RuntimeError as exc:
                self.reply(409, {"error": str(exc)})
            except OSError:
                state.update(status="error", error="Unable to preserve the conversation ledger. Human review required.")
                self.reply(500, {"error": "Ledger write failed; message not accepted"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses an available port")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    with LedgerLog(TASK_DIR / "ledgers") as log:
        state = Conversation(log, args.model)
        try:
            server = make_server(state, args.port)
        except OSError as exc:
            log.write("server_error", error_type=type(exc).__name__, error=str(exc))
            print("Could not start local server; see", log.path, file=sys.stderr)
            return 1
        url = f"http://127.0.0.1:{server.server_port}"
        log.write("server_started", url=url, conversation=state.data["id"])
        thread = threading.Thread(target=worker, args=(state, TASK_DIR), daemon=True)
        thread.start()
        print(f"Open {url}", flush=True)
        print(f"Ledger: {log.path}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            state.stop()
            deadline = time.monotonic() + 250
            while thread.is_alive() and time.monotonic() < deadline:
                thread.join(timeout=1)
            server.server_close()
            stopped = not thread.is_alive()
            if not log.failed:
                log.write("server_stopped", worker_stopped=stopped)
        return 0 if stopped and not state.data["error"] else 1


if __name__ == "__main__":
    sys.exit(main())
