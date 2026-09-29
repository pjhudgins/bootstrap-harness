"""Read-only UI fixture from a live-smoke snapshot; never starts agents."""
import argparse
import json
from pathlib import Path
from app import make_server

class Preview:
    def __init__(self, path, approval_demo=False):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.data.update(status="closed", id="preview-" + self.data["id"])
        if approval_demo:
            self.data["error"] = "Synthetic approval layout preview — decisions are disabled."
            self.data["requests"]["request-1"].update(status="needs_human",
                governor_note="Synthetic layout example: decide whether to approve this request within existing bounds.")

    def snapshot(self):
        return self.data

    def submit(self, *args):
        raise RuntimeError("Read-only preview; no messages are sent.")

    def human_decision(self, *args):
        raise RuntimeError("Read-only preview; no decision is recorded.")

    def stop(self):
        pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("--approval-demo", action="store_true")
    args = parser.parse_args()
    server = make_server(Preview(args.snapshot, args.approval_demo))
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()

