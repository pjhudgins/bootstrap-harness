"""Read-only browser fixture from a closed smoke ledger; never starts agents."""
import argparse
from pathlib import Path
from app import make_server
from ledger import scribe, AGENT_AUTHOR, HUMAN_AUTHOR


class Preview:
    def __init__(self, name):
        view = scribe.load(Path(__file__).parent / "ledgers", name)
        if view.findings or not view.head_closed:
            raise ValueError("Preview requires a valid closed ledger.")
        self.data = {"id": "preview-" + name, "status": "closed", "model": "Read-only smoke preview",
                     "account": {}, "messages": [], "activity": [], "children": {}, "agent_usage": {},
                     "usage": None, "limits": None, "error": None, "revision": 1, "journal": name}
        seen = set()
        for name in view.names():
            if not name.startswith("harness/"):
                continue
            event = view.current(name).body
            kind = event["kind"]
            if kind == "message":
                for ref in event["text_entries"]:
                    if ref["id"] in seen:
                        continue
                    seen.add(ref["id"])
                    text = view.line(ref["id"])
                    author = text["author"]
                    if author == "harness":
                        continue
                    actor = "parent" if author == AGENT_AUTHOR else author.removeprefix("agent.claude.")
                    self.data["messages"].append({"role": "user" if author == HUMAN_AUTHOR else "assistant",
                        "text": text["body"], "turn": event.get("turn"), "actor": actor, "label": actor})
            elif kind in {"subagent_completed", "subagent_failed", "subagent_cancelled"}:
                self.data["children"][event["agent_id"]] = event
            elif kind == "usage":
                actor = "parent" if event["actor"] == AGENT_AUTHOR else event["actor"].removeprefix("agent.claude.")
                self.data["agent_usage"][actor] = event
                if actor == "parent":
                    self.data["usage"] = event
            elif kind in {"python_tool_result", "tool_denied"}:
                self.data["activity"].append({"label": event["tool"], "summary": event["actor"]})
        costs = [v["total_cost_usd"] for v in self.data["agent_usage"].values() if v.get("total_cost_usd") is not None]
        self.data["family_cost_usd"] = sum(costs) if costs else None

    def snapshot(self):
        return self.data

    def submit(self, text):
        raise RuntimeError("Read-only preview; no messages are sent.")

    def stop(self):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger")
    args = parser.parse_args()
    server = make_server(Preview(args.ledger))
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
