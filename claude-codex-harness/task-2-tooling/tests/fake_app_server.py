"""Scripted stand-in for `codex app-server` over stdio, for offline tests only.

Usage: python fake_app_server.py SCENARIO
  clean    - the agent calls add once, then declines to run code.
  executes - as clean, but the second turn asks approval for a command and runs one.
  protocol - exercises the client: a server request reusing the client's request id,
             an approval request and an unknown request, all before the response.
Replies to unknown client methods with an empty result.
"""

import json
import sys

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "clean"
THREAD = "t-fake"


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def receive():
    line = sys.stdin.readline()
    if not line:
        sys.stderr.write("fake: stdin closed\n")
        sys.exit(0)
    return json.loads(line)


def notify(method, params):
    send({"method": method, "params": params})


def ask(request_id, method, params):
    """Send a server-to-client request and report the client's reply as a notification."""
    send({"id": request_id, "method": method, "params": params})
    reply = receive()
    notify("test/reply", {"to": method, "reply": reply})
    return reply


def agent_message(turn_id, text):
    notify("item/completed", {"threadId": THREAD, "turnId": turn_id, "item": {
        "type": "agentMessage", "id": f"m-{turn_id}", "text": text, "phase": "final_answer"}})


def usage(turn_id, total):
    breakdown = {"totalTokens": total, "inputTokens": total - 10, "cachedInputTokens": 0,
                 "cacheWriteInputTokens": 0, "outputTokens": 10, "reasoningOutputTokens": 0}
    notify("thread/tokenUsage/updated", {"threadId": THREAD, "turnId": turn_id, "tokenUsage": {
        "total": breakdown, "last": breakdown, "modelContextWindow": 1000}})


def raw(turn_id, item):
    notify("rawResponseItem/completed", {"threadId": THREAD, "turnId": turn_id, "item": item})


def completed(turn_id):
    notify("turn/completed", {"threadId": THREAD, "turn": {
        "id": turn_id, "items": [], "status": "completed", "error": None, "durationMs": 5}})


RESULTS = {
    "initialize": {"userAgent": "fake/0", "codexHome": "fake-home",
                   "platformFamily": "windows", "platformOs": "windows"},
    "account/read": {"account": {"type": "chatgpt", "email": "someone@example.com",
                                 "planType": "plus"}, "requiresOpenaiAuth": True},
    "account/rateLimits/read": {"rateLimits": {"primary": {"usedPercent": 1.5}}},
    "config/read": {"config": {"mcp_servers": {"demo": {
        "command": "demo", "env": {"GITHUB_TOKEN": "S3CRET-VALUE"}}}}},
    "thread/start": {"thread": {"id": THREAD}, "model": "fake-model"},
    "experimentalFeature/list": {"data": [], "nextCursor": None},
    "mcpServerStatus/list": {"data": [{"name": "demo", "runtimeStatus": "disabled",
                                       "tools": {}}], "nextCursor": None},
}

turns = 0
while True:
    request = receive()
    method, request_id = request.get("method"), request.get("id")
    if request_id is None:
        continue  # client notification
    sys.stderr.write(f"fake: {method}\n")
    if method == "account/usage/read":
        send({"id": request_id, "error": {"code": -32600, "message": "no estimate for this thread"}})
    elif method == "turn/start":
        turns += 1
        turn_id = f"u{turns}"
        notify("turn/started", {"threadId": THREAD, "turn": {"id": turn_id}})
        if SCENARIO == "protocol":
            ask(request_id, "item/tool/call", {"threadId": THREAD, "turnId": turn_id,
                "callId": "c1", "namespace": None, "tool": "add", "arguments": {"a": 2, "b": 3}})
            ask("srv-2", "item/commandExecution/requestApproval", {"command": "whoami"})
            ask(99, "unknown/thing", {})
        elif turns == 1:
            raw(turn_id, {"type": "function_call", "name": "add", "call_id": "c1",
                          "arguments": '{"a": 1234.5, "b": 8765.25}'})
            ask("srv-1", "item/tool/call", {"threadId": THREAD, "turnId": turn_id,
                "callId": "c1", "namespace": None, "tool": "add",
                "arguments": {"a": 1234.5, "b": 8765.25}})
        elif SCENARIO == "executes":
            raw(turn_id, {"type": "custom_tool_call", "name": "exec", "call_id": "c2",
                          "input": "text(6 * 7)"})
            ask("srv-2", "item/commandExecution/requestApproval", {"command": "python -c ..."})
            notify("item/completed", {"threadId": THREAD, "turnId": turn_id, "item": {
                "type": "commandExecution", "id": "x1", "command": "python -c ...",
                "status": "completed"}})
        send({"id": request_id, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
        raw(turn_id, {"type": "message", "role": "assistant", "content": []})
        usage(turn_id, 100 * turns)
        agent_message(turn_id, "9999.75" if turns == 1 else "I cannot run code here.")
        completed(turn_id)
    else:
        send({"id": request_id, "result": RESULTS.get(method, {})})
