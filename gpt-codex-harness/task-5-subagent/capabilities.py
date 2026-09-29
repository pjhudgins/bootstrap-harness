"""Ledger-to-workspace materialization and trusted script execution."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from tool_support import args_only, integer
from privacy import redact


class FileCapabilities:
    timeout = 30
    output_limit = 128000

    def __init__(self, files, bounds, ledger, stop_event, actor):
        self.files, self.bounds, self.ledger = files, bounds, ledger
        self.stop = stop_event or threading.Event()
        self.actor = actor

    def record(self, kind, data):
        self.ledger.journal.write(kind, data, actor=self.actor)

    def checked(self, value, action, missing=False):
        path = self.bounds.expand(value)
        self.bounds.require('fs', action, str(path))
        try:
            return self.files.path(str(path), allow_missing=missing)
        except OSError as error:
            raise ValueError(f'Filesystem access failed: {error.strerror}') from None

    def list(self, arguments):
        args_only(arguments, ('path', 'offset', 'limit'), ('path',))
        path = self.checked(arguments['path'], 'read')
        return self.files.list({**arguments, 'path': str(path)},
                               visible=lambda child: self.bounds.allows('fs', 'read', str(child)))

    def read(self, arguments):
        args_only(arguments, ('path', 'offset', 'limit'), ('path',))
        path = self.checked(arguments['path'], 'read')
        return self.files.read({**arguments, 'path': str(path)})

    def write(self, arguments):
        args_only(arguments, ('source', 'path', 'expected_sha256'), ('source', 'path', 'expected_sha256'))
        entry = self.ledger.resolve(arguments['source'])
        path = self.checked(arguments['path'], 'write', missing=True)
        expected = arguments['expected_sha256']
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected)):
            raise ValueError('expected_sha256 must be a lowercase SHA-256 or null for creation.')
        raw = entry.body.encode('utf-8')
        if len(raw) > 2000000:
            raise ValueError('File materialization is limited to 2 MB.')
        self.record('file_write_started', {'source': arguments['source'], 'path': str(path), 'expected_sha256': expected})
        try:
            if self.stop.is_set():
                raise ValueError('Session is stopping.')
            # Parent creation is inside the permitted target path; links are checked again.
            path.parent.mkdir(parents=True, exist_ok=True)
            self.checked(str(path), 'write', missing=True)
            with path.open('xb' if expected is None else 'r+b') as file:
                self.files._check_handle(file)
                if expected is not None:
                    old = file.read(2000001)
                    if len(old) > 2000000 or hashlib.sha256(old).hexdigest() != expected:
                        raise ValueError('Stale file hash; read the current file before replacing it.')
                    file.seek(0)
                file.write(raw)
                file.truncate()
                file.flush()
                os.fsync(file.fileno())
        except (OSError, ValueError) as error:
            self.record('file_write_failed', {'path': str(path), 'error': str(error)})
            raise ValueError(str(error)) from None
        result = {'path': str(path), 'source': arguments['source'], 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.record('file_write_completed', result)
        return result

    def execute(self, arguments):
        args_only(arguments, ('path', 'output_name'), ('path', 'output_name'))
        path = self.checked(arguments['path'], 'execute')
        if path.suffix.lower() != '.py':
            raise ValueError('Execute accepts approved .py files only.')
        # Script verification need not imply fs.read permission for the agent.
        script = self.files.read({'path': str(path)})
        self.ledger.reserve_output(arguments['output_name'])
        self.record('script_started', {'path': str(path), 'sha256': script['sha256'], 'output_name': arguments['output_name']})
        chunks = {'stdout': bytearray(), 'stderr': bytearray()}
        overflow = threading.Event()
        readers = []
        process = None
        reason = None
        error = None
        try:
            if self.stop.is_set():
                raise ValueError('Session is stopping.')
            env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
            process = subprocess.Popen([sys.executable, '-I', '-B', '-X', 'utf8', str(path)], cwd=self.bounds.mounts['scripts'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            def drain(stream, key):
                while True:
                    data = stream.read(4096)
                    if not data:
                        break
                    remaining = self.output_limit - len(chunks[key])
                    chunks[key].extend(data[:max(0, remaining)])
                    if len(data) > remaining:
                        overflow.set()
            for key in chunks:
                reader = threading.Thread(target=drain, args=(getattr(process, key), key), daemon=True)
                reader.start()
                readers.append(reader)
            deadline = time.monotonic() + self.timeout
            while process.poll() is None:
                if self.stop.is_set() or self.ledger.journal.failed:
                    reason = 'cancelled'
                elif overflow.is_set():
                    reason = 'output_limit'
                elif time.monotonic() >= deadline:
                    reason = 'timeout'
                if reason:
                    process.kill()
                    break
                self.stop.wait(0.03)
            process.wait(timeout=5)
        except (OSError, ValueError) as problem:
            error, reason = str(problem), 'failed'
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for reader in readers:
                    reader.join(timeout=3)
                process.stdout.close()
                process.stderr.close()
        stdout = redact(chunks['stdout'].decode('utf-8', errors='replace'))
        stderr = redact(chunks['stderr'].decode('utf-8', errors='replace'))
        body = stdout + ('\n[stderr]\n' + stderr if stderr else '')
        if error:
            body += '\n[launch error]\n' + redact(error)
        truncated = overflow.is_set()
        if truncated:
            body += '\n[output truncated at capture limit]\n'
            reason = reason or 'output_limit'
        ref = self.ledger.output(arguments['output_name'], body)
        result = {'output': ref, 'returncode': process.returncode if process else None,
                  'status': reason or ('completed' if process.returncode == 0 else 'failed'),
                  'truncated': truncated}
        self.record('script_completed', result)
        return result
