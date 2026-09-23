"""Print one real agent response through the locally authenticated Codex App Server."""

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", action="store_true",
                        help="Connect to the existing local App Server instead.")
    args = parser.parse_args()
    child_env = os.environ.copy()
    # The desktop command environment may omit CODEX_HOME; Rust home discovery
    # failed here even though Python correctly resolved the Windows user profile.
    child_env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
    # This experiment must use the saved ChatGPT login, never an API-key override.
    child_env.pop("CODEX_API_KEY", None)
    child_env.pop("OPENAI_API_KEY", None)
    command = ["codex", "app-server"]
    if args.proxy:
        command.append("proxy")
    else:
        runtime_dir = Path(__file__).resolve().parent / ".runtime"
        runtime_dir.mkdir(exist_ok=True)
        command.extend(["--listen", "stdio://",
                        "-c", f"sqlite_home={json.dumps(str(runtime_dir))}"])
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        env=child_env,
        cwd=Path(__file__).resolve().parent,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    messages = queue.Queue()
    deadline = time.monotonic() + 120

    def read_stdout() -> None:
        for line in process.stdout:
            messages.put(line)
        messages.put(None)

    threading.Thread(target=read_stdout, daemon=True).start()

    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def receive() -> dict:
        try:
            line = messages.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise RuntimeError("App Server did not finish within 120 seconds.") from None
        if line is None:
            raise RuntimeError("App Server closed stdout before completion; see stderr.")
        message = json.loads(line)
        if "method" in message and "id" in message:
            send({"id": message["id"], "error": {
                "code": -32601, "message": "Hello client does not handle server requests."
            }})
            raise RuntimeError(f"Unexpected server request: {message['method']}")
        return message

    def request(request_id: int, method: str, params: dict) -> dict:
        send({"id": request_id, "method": method, "params": params})
        while True:
            message = receive()
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']['message']}")
                return message["result"]

    try:
        request(1, "initialize", {"clientInfo": {
            "name": "nimoi_hello", "version": "0.1.0"
        }})
        send({"method": "initialized", "params": {}})
        account = request(2, "account/read", {"refreshToken": False}).get("account")
        if not account or account.get("type") != "chatgpt":
            raise RuntimeError("A saved ChatGPT login is required. Run codex login first.")
        thread = request(3, "thread/start", {
            "cwd": str(Path(__file__).resolve().parent),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
        })
        # Send directly so even events arriving before the response are consumed.
        send({"id": 4, "method": "turn/start", "params": {
            "threadId": thread["thread"]["id"],
            "input": [{"type": "text", "text":
                "Say hello world. Reply with only: hello world. Do not use tools."}],
        }})
        replies = []
        while True:
            message = receive()
            if message.get("id") == 4 and "error" in message:
                raise RuntimeError(message["error"]["message"])
            params = message.get("params", {})
            if message.get("method") == "item/completed":
                item = params["item"]
                if item["type"] == "agentMessage":
                    replies.append(item["text"])
            if message.get("method") == "turn/completed":
                turn = params["turn"]
                if turn["status"] != "completed":
                    raise RuntimeError(f"Turn {turn['status']}: {turn.get('error')}")
                if not replies:
                    raise RuntimeError("Turn completed without an agent message.")
                print("\n".join(replies))
                break
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"hello: {error}", file=sys.stderr)
        raise SystemExit(1)
