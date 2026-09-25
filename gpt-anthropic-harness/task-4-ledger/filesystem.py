"""Bounded, read-only NIMOI text access. No shell or filesystem mutations."""
import fnmatch
from pathlib import Path, PureWindowsPath
import stat

BLOCKED_DIRS = {".git", ".codex", ".claude", ".agents", ".venv", "venv", "env", "__pycache__", ".runtime", "run"}
SECRET_NAMES = (".env", ".env.*", "*.env", "*.token", "*.key", "*.pem", "credentials*.json", "secrets*.json", "lease.json")


class ReadOnlyFiles:
    def __init__(self, root, clean):
        self.root = Path(root).resolve(strict=True)
        self.clean = clean

    def resolve(self, path):
        if not isinstance(path, str) or len(path) > 1000 or "\x00" in path:
            raise ValueError("Use a relative NIMOI path.")
        win = PureWindowsPath(path)
        if win.drive or win.root or ":" in path:
            raise ValueError("Absolute, device, UNC and alternate-stream paths are not allowed.")
        parts = path.replace("\\", "/").split("/")
        candidate = self.root
        for part in parts:
            if part in ("", "."):
                continue
            lower = part.lower()
            if (part == ".." or part.endswith((" ", ".")) or lower in BLOCKED_DIRS
                    or any(fnmatch.fnmatchcase(lower, pattern) for pattern in SECRET_NAMES)):
                raise ValueError("Path is outside the read policy or names a protected file.")
            candidate /= part
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Symlinks and reparse points are not readable.")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path escapes the NIMOI root.")
        info = resolved.stat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError("Multiply-linked files are not readable.")
        return resolved

    def list(self, path=".", offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("offset >= 0 and limit 1–100 are required.")
        directory = self.resolve(path)
        if not directory.is_dir():
            raise ValueError("Not a directory.")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda p: p.name):
            relative = child.relative_to(self.root).as_posix()
            try:
                resolved = self.resolve(relative)
                info = resolved.stat()
            except (OSError, ValueError):
                continue
            if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
                entries.append({"name": child.name, "path": relative,
                                "type": "directory" if child.is_dir() else "file"})
        return self.clean({"path": directory.relative_to(self.root).as_posix(),
                           "entries": entries[offset:offset+limit], "total": len(entries),
                           "next_offset": offset+limit if offset+limit < len(entries) else None})

    def read(self, path, start_line=1, max_lines=200):
        if type(start_line) is not int or start_line < 1 or type(max_lines) is not int or not 1 <= max_lines <= 300:
            raise ValueError("start_line >= 1 and max_lines 1–300 are required.")
        target = self.resolve(path)
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000:
            raise ValueError("Only regular text files up to 2 MB are readable.")
        # Bound the actual read too, in case the file grew after stat.
        with target.open("rb") as handle:
            content = handle.read(2_000_001)
        if len(content) > 2_000_000 or b"\x00" in content:
            raise ValueError("File is too large or binary.")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("File is not UTF-8 text.") from exc
        lines = text.splitlines(keepends=True)
        chosen = lines[start_line-1:start_line-1+max_lines]
        count = 0
        kept = []
        for line in chosen:
            if count + len(line) > 30000:
                if not kept:
                    raise ValueError("A single line exceeds the 30,000-character response limit.")
                break
            kept.append(line)
            count += len(line)
        end = start_line-1+len(kept)
        return self.clean({"path": target.relative_to(self.root).as_posix(), "start_line": start_line,
                           "text": "".join(kept), "total_lines": len(lines),
                           "next_line": end+1 if end < len(lines) else None})
