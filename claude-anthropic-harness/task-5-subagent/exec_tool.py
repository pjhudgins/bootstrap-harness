"""mcp__exec__python: run an approved Python script (rules.md task 5d).

Core safety: an agent never executes where it can write. That is enforced in three places:
  - bounds.py refuses any bounds where fs.write and fs.exec overlap;
  - files.py never lets any agent write the harness scripts directory;
  - scripts reach that directory only when a human copies them there ("promotion").
So an agent can draft a script (ledger entry -> workspace file), but it cannot run it.

A run, in order:
  1. The script must be a .py file within the agent's fs.exec, resolved like every path
     (files.FsAccess).
  2. exec_started is recorded, with the script's sha256 and the args. If the ledger cannot
     record it, the script does not start.
  3. The script runs as `python -I -B -X utf8 <script> <args>`:
     - a stripped environment (no keys, no PATH), no stdin and no console window;
     - the workspace as its working directory.
  4. Output is capped per stream while it streams (OUTPUT_CAP_BYTES). An overflow, the
     timeout, or a pipe still held open after exit kills the whole process tree
     (taskkill /T on Windows).
  5. exec is recorded: exit code, flags, duration, stdout and stderr. That entry is the
     captured output, and its link is returned together with the output.

Open trade-off, left for the founder: the working directory is the workspace and args are
allowed. An approved script can therefore read agent-written drafts as data. That is more
useful, but less contained than the peers' choice: cwd = scripts, no args.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from files import FsAccess
from ledger_tools import Denied, denied_reply
from ledgerlog import wikilink

SERVER_NAME = "exec"
EXEC_TIMEOUT_S = 60
PIPE_GRACE_S = 5  # after the script exits, how long its pipes may stay open (held by descendants)
OUTPUT_CAP_BYTES = 128_000  # per stream, in the ledger
REPLY_CAP_CHARS = 20_000  # per stream, in the tool's reply
MAX_ARGS = 20
ENV_KEEP = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")  # what Python itself needs on Windows
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


async def kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the script and everything it started."""
    if os.name == "nt":
        await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                capture_output=True, timeout=15)
    try:
        proc.kill()
    except ProcessLookupError:
        pass


class ExecTool:
    def __init__(self, *, access: FsAccess, bus: Any, workspace: Path, agent_id: str):
        self.access = access
        self.bus = bus
        self.workspace = Path(workspace).resolve()
        self.agent_id = agent_id

    async def run(self, script: Any, args: Any = None) -> dict[str, Any]:
        d = self.access.require("fs.exec", script)
        if d.path.suffix.lower() != ".py" or not d.path.is_file():
            raise Denied(f"{d.target} is not a .py file")
        args = list(args or [])
        if len(args) > MAX_ARGS or not all(isinstance(a, str) for a in args):
            raise Denied(f"args must be a list of at most {MAX_ARGS} strings")
        script_sha = hashlib.sha256(d.path.read_bytes()).hexdigest()
        started = self.bus.publish("exec_started", agent=self.agent_id, script=d.target,
                                   script_sha256=script_sha, args=args)
        if not started or not started.get("name"):
            raise Denied("the ledger could not record this run, so the script was not started")

        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        self.workspace.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", "-X", "utf8", str(d.path), *args, cwd=str(self.workspace), env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW)
        overflow = asyncio.Event()

        async def drain(stream: asyncio.StreamReader) -> bytes:
            buf = bytearray()
            while chunk := await stream.read(65536):
                room = OUTPUT_CAP_BYTES - len(buf)
                buf += chunk[:max(room, 0)]
                if len(chunk) > room:
                    overflow.set()
            return bytes(buf)

        readers = [asyncio.create_task(drain(proc.stdout)), asyncio.create_task(drain(proc.stderr))]
        exited, overflowed_wait = asyncio.create_task(proc.wait()), asyncio.create_task(overflow.wait())
        done, _ = await asyncio.wait({exited, overflowed_wait}, timeout=EXEC_TIMEOUT_S,
                                     return_when=asyncio.FIRST_COMPLETED)
        timed_out = not done
        if exited not in done:  # timeout or overflow
            await kill_tree(proc)
            await proc.wait()
        overflowed_wait.cancel()
        pipe_held = False
        try:
            out, err = await asyncio.wait_for(asyncio.gather(*readers), PIPE_GRACE_S)
        except asyncio.TimeoutError:  # a descendant still holds the pipes open
            pipe_held = True
            await kill_tree(proc)
            for r in readers:
                r.cancel()
            out, err = b"", b""
        duration_ms = int((time.monotonic() - t0) * 1000)
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        record = self.bus.publish("exec", agent=self.agent_id, script=d.target, script_sha256=script_sha,
                                  args=args, exit_code=proc.returncode, timed_out=timed_out,
                                  overflowed=overflow.is_set(), pipe_held_open=pipe_held,
                                  duration_ms=duration_ms, stdout=stdout, stderr=stderr, started=started.get("id"))
        return {"output_entry": wikilink(record) if record else None, "script": d.target,
                "exit_code": proc.returncode, "timed_out": timed_out, "overflowed": overflow.is_set(),
                "duration_ms": duration_ms, "stdout": stdout[:REPLY_CAP_CHARS], "stderr": stderr[:REPLY_CAP_CHARS],
                "reply_truncated": len(stdout) > REPLY_CAP_CHARS or len(stderr) > REPLY_CAP_CHARS}

    def tools(self) -> list[Any]:
        if not self.access.bounds.scope("fs.exec").allow:
            return []

        @tool("python", "Run a Python script within your fs.exec (the harness scripts directory). You cannot write "
              "there; scripts are promoted by a human. The run is recorded as a ledger entry whose body holds the "
              "exit code, stdout and stderr; its [[link]] is returned along with the output. Timeout "
              f"{EXEC_TIMEOUT_S}s; no stdin; the working directory is the workspace.",
              {"type": "object", "properties": {"script": {"type": "string"},
                                                "args": {"type": "array", "items": {"type": "string"}}},
               "required": ["script"]})
        async def python_(args: dict[str, Any]) -> dict[str, Any]:
            try:
                out = await self.run(args.get("script"), args.get("args"))
                return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]}
            except Denied as e:
                return denied_reply(self.bus, self.agent_id, f"mcp__{SERVER_NAME}__python", args, e)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"error: {e!r}"}], "is_error": True}

        return [python_]
