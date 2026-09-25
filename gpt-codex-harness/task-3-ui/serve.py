"""Launch a local web UI and a fresh Codex conversation. Python stdlib only."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import webbrowser

from session import Conversation


STATIC = Path(__file__).resolve().parent / "static"


def handler_for(conversation):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Do not create a second, unstructured conversation/access log.

        def reply(self, status, data, content_type="application/json; charset=utf-8", attachment=False):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{conversation.log_path.name}"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def valid_host(self):
            return self.headers.get("Host") == self.server.authority

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {"error": "Unrecognized local host."})
            if self.path == "/api/state":
                return self.reply(200, conversation.snapshot())
            if self.path == "/api/journal":
                with conversation.journal.lock:
                    journal_bytes = conversation.log_path.read_bytes()
                return self.reply(200, journal_bytes,
                                  "application/x-ndjson; charset=utf-8", attachment=True)
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if self.path not in assets:
                return self.reply(404, {"error": "Not found."})
            name, mime = assets[self.path]
            self.reply(200, (STATIC / name).read_bytes(), mime + "; charset=utf-8")

        def do_POST(self):
            if (not self.valid_host() or self.headers.get("Origin") != self.server.origin
                    or self.headers.get("X-Nimoi-UI") != "1"):
                return self.reply(403, {"error": "Use the local conversation page."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.reply(415, {"error": "JSON required."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    return self.reply(413, {"error": "Request is too large or empty."})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object.")
                if self.path == "/api/messages":
                    conversation.submit(body.get("text"))
                    return self.reply(202, {"accepted": True})
                if self.path == "/api/stop":
                    conversation.stop()
                    self.reply(200, {"stopping": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self.reply(404, {"error": "Not found."})
            except (ValueError, UnicodeDecodeError) as error:
                self.reply(400, {"error": str(error)})
            except RuntimeError as error:
                self.reply(409, {"error": str(error)})

    return Handler


def make_server(conversation, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(conversation))
    server.daemon_threads = True
    server.authority = f"127.0.0.1:{server.server_port}"
    server.origin = "http://" + server.authority
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Local port; default chooses a free port.")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser.")
    args = parser.parse_args()
    conversation = Conversation()
    try:
        server = make_server(conversation, args.port)
    except Exception:
        conversation.journal.write("launch_failed", {"error": "Unable to bind local port."})
        conversation.journal.close()
        raise
    conversation.start()
    print(f"NIMOI: {server.origin}", flush=True)
    print(f"Journal: {conversation.log_path}", flush=True)
    print("Ctrl+C or End session in the page stops this launch.", flush=True)
    if not args.no_browser:
        webbrowser.open(server.origin)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        conversation.stop()
        server.server_close()
        conversation.wait()
        print("Session ended.", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
