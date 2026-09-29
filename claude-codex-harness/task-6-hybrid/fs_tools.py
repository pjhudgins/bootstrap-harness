"""The filesystem tools, one per fs.* bound: fs_list and fs_read (fs.read), fs_write
(fs.write) and python_exec (fs.exec).

Paths are relative to the nimoi folder. Each passes the fixed rules first (paths.py),
then the agent's bounds, then the tool's own rules. The two tools that change something
record what they are about to do before they do it, so a ledger that cannot record
means nothing happens; they record the result after.
"""

import hashlib
import os
import sys

import ledger_log
import paths
import runner
from toolkit import WORKERS, ToolError, string, tool

MAX_LIST = 500
MAX_FILE_BYTES = 5_000_000
MAX_FS_CHARS = 30000
EXEC_TIMEOUT = 30
EXEC_OUTPUT_BYTES = 64_000
MAX_ARGS = 10


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


@tool("fs_list",
      "List a folder within your fs.read. Paths are relative to the nimoi folder; '.' is "
      "the nimoi folder.", {"path": string()})
def fs_list(box, args):
    resolved, rel = box.paths.resolve(args.get("path") or ".")
    box.need("fs.read", rel)
    if not resolved.is_dir():
        raise ToolError(f"not a folder: {rel}")
    entries = []
    for child in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        item = {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        if item["type"] == "file":
            try:
                item["size"] = child.stat().st_size
            except OSError:
                item["size"] = None
        if paths.secret_name(child.name) or paths.hidden_name(child.name) \
                or child.name.lower() == paths.GIT:
            item["readable"] = False
        entries.append(item)
    return {"path": rel or ".", "entries": entries[:MAX_LIST], "total": len(entries),
            "truncated": len(entries) > MAX_LIST}


@tool("fs_read",
      "Read a text file within your fs.read: up to max_lines lines from start_line "
      "(1-based).",
      {"path": string(), "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}},
      ("path",))
def fs_read(box, args):
    resolved, rel = box.paths.resolve(args["path"])
    if rel != box.agent.onboarding:  # the onboarding file is always readable
        box.need("fs.read", rel)
    if resolved.is_dir():
        raise ToolError(f"is a folder (use fs_list): {rel}")
    paths.check_not_secret(resolved)
    start, count = args.get("start_line", 1), args.get("max_lines", 400)
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in (start, count)):
        raise ToolError("start_line and max_lines must be positive integers")
    size = resolved.stat().st_size
    with open(resolved, "rb") as handle:
        data = handle.read(MAX_FILE_BYTES)
    if b"\x00" in data[:8192]:
        raise ToolError("refused: binary file")
    lines = data.decode("utf-8", errors="replace").splitlines()
    end = min(start - 1 + count, len(lines))
    # Key-like strings are redacted in what agents read, as in the ledger.
    chunk, redactions = ledger_log.redact_keys("\n".join(lines[start - 1:end]))
    if rel == box.agent.onboarding:
        box.agent.note_onboarding_lines(start, end, len(lines))
    return {"path": rel, "size": size, "lines": f"{start}-{end} of {len(lines)}",
            "text": chunk[:MAX_FS_CHARS], "chars_truncated": len(chunk) > MAX_FS_CHARS,
            "file_truncated_at_bytes": MAX_FILE_BYTES if size > MAX_FILE_BYTES else None,
            "redactions": redactions}


@tool("fs_write",
      "Write the body of one exact ledger entry version to a file within your fs.write, "
      "byte for byte. Draft the text with ledger_write first, then pass the id it returned "
      "(or an id from ledger_read). A new file is created; an existing file is replaced "
      "only if you pass its current sha256 as replace_sha256 (a refusal tells you it).",
      {"id": string("the ledger entry version to write, e.g. 20260925T192816Z:42"),
       "path": string("the target file, relative to the nimoi folder"),
       "replace_sha256": string("the sha256 of the file being replaced; omit for a new file")},
      ("id", "path"), layers=WORKERS, needs="fs.write")
def fs_write(box, args):
    entry_id, path, expected = args["id"], args["path"], args.get("replace_sha256")
    if not isinstance(entry_id, str) or not (expected is None or isinstance(expected, str)):
        raise ToolError("id and replace_sha256 must be strings")
    line = box.agent.scribe.line(entry_id)
    if not isinstance(line, dict) or "name" not in line or "body" not in line:
        raise ToolError(f"{entry_id!r} is not the id of a ledger entry version")
    name = line["name"]
    box.need_entry(name)
    if not isinstance(line["body"], str):
        raise ToolError(f"{entry_id} ({name}) has no text body to write")
    resolved, rel = box.paths.resolve(path, must_exist=False)
    box.need("fs.write", rel)
    if box.where_scripts_run(rel):
        raise ToolError("refused: never write where scripts run")
    paths.check_not_secret(resolved)
    if resolved.is_dir():
        raise ToolError(f"is a folder: {rel}")
    current = _sha256(resolved.read_bytes()) if resolved.exists() else None
    if current is not None and expected is None:
        raise ToolError(f"{rel} exists (sha256 {current}); to replace it, pass "
                        f"replace_sha256 = that sha256")
    if expected is not None and expected != current:
        raise ToolError(f"{rel} {'has sha256 ' + current if current else 'does not exist'}, "
                        f"not {expected}: nothing written")
    if not resolved.parent.exists():
        box.need("fs.write", box.paths.rel(resolved.parent), " (to create its folder)")
    data = line["body"].encode("utf-8")
    fields = {"agent": box.agent.id, "path": rel, "entry": name, "entry_id": entry_id,
              "entry_author": line.get("author"), "bytes": len(data), "sha256": _sha256(data),
              "replaces_sha256": current}
    record = box.agent.record
    record.write("fs_write_started", **fields)
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        _write(resolved, data, current)
    except (OSError, ToolError) as error:
        record.write("fs_write", ok=False, error=str(error), **fields)
        raise
    record.write("fs_write", ok=True, **fields)
    return {"path": rel, "entry": name, "entry_id": entry_id, "bytes": len(data),
            "sha256": fields["sha256"], "replaced_sha256": current}


def _write(resolved, data, expected):
    """Create the file exclusively, or replace it only if it still has sha256 `expected`,
    checked on the same open handle."""
    if expected is None:
        with open(resolved, "xb") as handle:  # bytes: no newline translation
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return
    with open(resolved, "r+b") as handle:
        if _sha256(handle.read()) != expected:
            raise ToolError("the file changed while it was being replaced: nothing written")
        handle.seek(0)
        handle.truncate()
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


@tool("python_exec",
      "Run a Python script from the harness scripts folder, within your fs.exec. Its output "
      f"is recorded as a ledger entry by the harness, which you can read. Scripts run "
      f"isolated, with a minimal environment, for at most {EXEC_TIMEOUT} s; anything a "
      "script starts is ended with it. Scripts cannot be written by agents: you can draft "
      "one with fs_write, but only a human can promote it to the scripts folder.",
      {"script": string("path relative to the nimoi folder"),
       "args": {"type": "array", "items": {"type": "string"},
                "description": f"command-line arguments, at most {MAX_ARGS}"}},
      ("script",), layers=WORKERS, needs="fs.exec")
def python_exec(box, args):
    script, script_args = args["script"], args.get("args", [])
    if (not isinstance(script_args, list) or len(script_args) > MAX_ARGS
            or not all(isinstance(a, str) and len(a) <= 500 for a in script_args)):
        raise ToolError(f"args must be a list of at most {MAX_ARGS} strings")
    resolved, rel = box.paths.resolve(script)
    box.need("fs.exec", rel)
    if resolved.suffix.lower() != ".py" or not resolved.is_file():
        raise ToolError("refused: only .py files run")
    if box.can_write_near(rel):
        raise ToolError("refused: never execute where you can write")
    agent, record = box.agent, box.agent.record
    fields = {"args": script_args, "script_sha256": _sha256(resolved.read_bytes())}
    record.write("python_exec_started", agent=agent.id, script=rel, **fields)
    tmp = box.workspace / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "PATH": os.path.dirname(sys.executable), "TEMP": str(tmp), "TMP": str(tmp)}
    run = runner.run([sys.executable, "-I", "-B", "-X", "utf8", str(resolved), *script_args],
                     cwd=box.workspace, env=env, timeout=EXEC_TIMEOUT,
                     max_bytes=EXEC_OUTPUT_BYTES)
    stdout, stderr = _text(run["stdout"]), _text(run["stderr"])
    output = stdout + (f"\n--- stderr ---\n{stderr}" if stderr else "")
    result = {key: run[key] for key in ("exit_code", "timed_out", "duration_ms",
                                        "stdout_truncated", "stderr_truncated",
                                        "left_running", "pipes_held")}
    name, text_id, _ = record.exec_output(agent.id, rel, output, **fields, **result)
    agent.produced.add(name)  # its own output is always readable to it
    return {**result, "output_entry": name, "output_id": text_id,
            "stdout": stdout[:4000], "stderr": stderr[:2000]}


def _text(data):
    """Script output as text. Line ends are normalised: Python's text mode on Windows
    writes \\r\\n, which is not part of what the script printed."""
    return data.decode("utf-8", "replace").replace("\r\n", "\n")
