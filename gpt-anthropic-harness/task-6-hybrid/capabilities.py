"""Enforce grants on every operation; executable and draft trees never overlap."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from access import LedgerAccess
from filesystem import ReadOnlyFiles


class Capabilities:
    def __init__(self, root, log, bounds, author):
        self.root, self.log, self.bounds, self.author = Path(root), log, bounds, author
        self.files = ReadOnlyFiles(self.root, log.clean)
        self.access = LedgerAccess(log, bounds, author)
        self.cancel_event = threading.Event()

    def list(self, path=".", offset=0, limit=100):
        path = self.bounds.mounts.relative(path)
        self.bounds.require("fs.read", path)
        return self.files.list(path, offset, limit,
                               visible=lambda name: self.bounds.permits("fs.read", name))

    def read(self, path, **kwargs):
        path = self.bounds.mounts.relative(path)
        self.bounds.require("fs.read", path)
        return self.files.read(path, **kwargs)

    def write(self, entry_id, path):
        path = self.bounds.mounts.relative(path)
        self.bounds.require("fs.write", path)
        entry = self.access.resolve(entry_id)
        # Reuse all path protection (secret names, links, traversal), including for
        # a not-yet-existing filename by checking its existing parent and a probe.
        destination = self.root / path
        self.files.resolve(destination.parent.relative_to(self.root).as_posix())
        try:
            self.files.resolve(path)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Destination exists; choose a new filename (no overwrite).")
        content = entry["body"].encode("utf-8")
        if len(content) > 2_000_000:
            raise ValueError("Body exceeds the 2 MB file limit.")
        self.log.write("file_write_attempt", actor=self.author, path=path, source_id=entry_id)
        # Exclusive create preserves an existing file even if it appears after checks.
        with destination.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self.log.write("file_written", actor=self.author, path=path, source_id=entry_id,
                       bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        return {"path": path, "source_id": entry_id, "bytes": len(content)}

    def execute(self, path, output_name):
        path = self.bounds.mounts.relative(path)
        self.bounds.require("fs.execute", path)
        script = self.files.resolve(path)
        if script.suffix != ".py" or not script.is_file():
            raise ValueError("Only approved Python script files may execute.")
        if self.log.stop_event.is_set() or self.cancel_event.is_set():
            raise RuntimeError("Execution cancelled before launch.")
        self.access.reserve_output(output_name)
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        self.log.write("execution_start", actor=self.author, path=path, sha256=digest, output_name=output_name)
        env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
        chunks, overflow = [], threading.Event()
        proc, reader, error, status = None, None, None, "completed"

        def collect():
            remaining = 65536
            while block := proc.stdout.read(4096):
                chunks.append(block[:remaining])
                if len(block) > remaining:
                    overflow.set()
                    break
                remaining -= len(block)

        try:
            proc = subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", str(script)],
                cwd=self.root / self.bounds.mounts.scripts, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            deadline = time.monotonic() + 10
            while proc.poll() is None:
                if self.log.stop_event.is_set() or self.cancel_event.is_set():
                    status = "cancelled"
                elif overflow.is_set():
                    status = "output_limit"
                elif time.monotonic() >= deadline:
                    status = "timeout"
                if status != "completed":
                    proc.kill()
                    break
                self.cancel_event.wait(0.03)
            proc.wait(timeout=5)
        except OSError as exc:
            status, error = "failed", str(exc)
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if reader:
                    reader.join(timeout=5)
                    if reader.is_alive():
                        raise RuntimeError("Script output pipe did not close; trusted script spawned a descendant.")
                if proc.stdout:
                    proc.stdout.close()
        self.log.check()  # A failed ledger stops the family; no fallback writes.
        output = b"".join(chunks).decode("utf-8", errors="replace")
        if error:
            output += "\n[Launch failed] " + error
        if overflow.is_set() and status == "completed":
            status = "output_limit"
        if proc and proc.returncode and status == "completed":
            status = "failed"
        ref = self.log.finish_output(output_name, output, tag="execution.output")
        result = {"path": path, "exit_code": proc.returncode if proc else None, "status": status,
                  "timed_out": status == "timeout", "truncated": overflow.is_set(), "output": ref}
        self.log.write("execution_complete", actor=self.author, **result)
        return result

