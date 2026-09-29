"""Bounds: one notation for every agent's permissions (rules.md task 5b).

    fs.read       /  !/candidate_repos/
    fs.write      /bootstrap-harness/claude-anthropic-harness/task-6-hybrid/workspace/
    fs.exec       /bootstrap-harness/claude-anthropic-harness/task-6-hybrid/scripts/
    ledger.read   *
    ledger.write  pilot/

- Five scopes, one line each. A missing scope, or one with no entries, permits nothing.
- An entry allows; an entry starting with ! excludes.
- Filesystem entries are paths from the nimoi root: "/" is nimoi itself.
  Ledger entries are name prefixes: "*" is every name.
- An entry covers itself and everything below it, by /-separated segments:
  "pilot" covers "pilot/x", not "pilot2". A trailing "/" is optional.
  Filesystem comparison ignores case (Windows).
- ONE RULE for every scope: a target is permitted iff some allow entry covers it
  and no exclusion covers it.
- CHILD RULE: each child allow entry must be permitted by the parent's scope.
  The parent's exclusions that fall inside the child's allows are inherited.
- INVARIANT: no fs.write entry may overlap an fs.exec entry (the same path, or
  one inside the other). An agent can draft scripts but never run what it wrote.

Rendering gives the canonical text. That exact text is what agents see and what
parents pass when they spawn children.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SCOPES = ("fs.read", "fs.write", "fs.exec", "ledger.read", "ledger.write")
FS_SCOPES = SCOPES[:3]
LEDGER_SCOPES = SCOPES[3:]
_LEDGER_ENTRY = re.compile(r"[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*")
_FS_SEGMENT_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')


class BoundsError(ValueError):
    """The text is not valid bounds notation, or the bounds break a rule."""


def _normalize(scope: str, entry: str) -> str:
    if scope in FS_SCOPES:
        e = entry.replace("\\", "/")
        if not e.startswith("/"):
            raise BoundsError(f"{scope}: {entry!r} must start with / (the nimoi root)")
        parts = [p for p in e.split("/") if p]
        if any(p in (".", "..") for p in parts):
            raise BoundsError(f"{scope}: {entry!r} may not contain . or .. segments")
        if any(_FS_SEGMENT_BAD.search(p) for p in parts):
            raise BoundsError(f"{scope}: {entry!r} contains characters not allowed in a path (no wildcards)")
        return "/" + "/".join(parts)
    if entry == "*":
        return "*"
    e = entry.strip("/")
    if not _LEDGER_ENTRY.fullmatch(e):
        raise BoundsError(f"{scope}: {entry!r} is not a ledger name prefix (or *)")
    return e


def covers(scope: str, entry: str, target: str) -> bool:
    """Whether `entry` covers `target`: itself and everything below it."""
    if entry in ("*", "/"):
        return True
    if scope in FS_SCOPES:
        entry, target = entry.casefold(), target.casefold()
    return target == entry or target.startswith(entry + "/")


@dataclass(frozen=True)
class Scope:
    name: str
    allow: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def permits(self, target: str) -> bool:
        return (any(covers(self.name, a, target) for a in self.allow)
                and not any(covers(self.name, x, target) for x in self.exclude))

    def excluded_below(self, target: str) -> list[str]:
        """Exclusions at or below `target` (for searches that descend from it)."""
        return [x for x in self.exclude if covers(self.name, target, x)]

    def render(self) -> str:
        return " ".join([*self.allow, *("!" + x for x in self.exclude)])


@dataclass(frozen=True)
class Bounds:
    scopes: dict[str, Scope] = field(default_factory=dict)

    # ---- notation ----------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "Bounds":
        seen: dict[str, Scope] = {}
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            name, *entries = line.split()
            if name not in SCOPES:
                raise BoundsError(f"line {lineno}: unknown scope {name!r}; scopes are {', '.join(SCOPES)}")
            if name in seen:
                raise BoundsError(f"line {lineno}: {name} appears twice")
            allow, exclude = [], []
            for entry in entries:
                if entry.startswith("!"):
                    if entry in ("!", "!*"):
                        raise BoundsError(f"line {lineno}: {entry!r} is not a valid exclusion")
                    exclude.append(_normalize(name, entry[1:]))
                else:
                    allow.append(_normalize(name, entry))
            seen[name] = Scope(name, tuple(dict.fromkeys(allow)), tuple(dict.fromkeys(exclude)))
        return cls({s: seen.get(s, Scope(s)) for s in SCOPES})

    def render(self) -> str:
        width = max(len(s) for s in SCOPES) + 2
        return "\n".join(f"{s:<{width}}{self.scope(s).render()}".rstrip() for s in SCOPES)

    def scope(self, name: str) -> Scope:
        return self.scopes.get(name, Scope(name))

    def permits(self, scope: str, target: str) -> bool:
        return self.scope(scope).permits(target)

    # ---- rules -------------------------------------------------------------------------

    def invariant_problems(self) -> list[str]:
        """fs.write and fs.exec must not overlap."""
        problems = []
        for w in self.scope("fs.write").allow:
            for e in self.scope("fs.exec").allow:
                if covers("fs.write", w, e) or covers("fs.write", e, w):
                    problems.append(f"fs.write {w} overlaps fs.exec {e}: an agent may not execute where it can write")
        return problems

    def subset_problems(self, parent: "Bounds") -> list[str]:
        """Why these bounds are not within `parent` (empty list: they are)."""
        problems = []
        for name in SCOPES:
            mine, theirs = self.scope(name), parent.scope(name)
            for a in mine.allow:
                if not theirs.permits(a):
                    problems.append(f"{name} {a} is not within the parent's {name} ({theirs.render() or 'nothing'})")
        return problems

    def with_exclusion(self, scope: str, entry: str) -> "Bounds":
        """These bounds with one more exclusion in `scope` (a no-op if nothing it allows covers it)."""
        s = self.scope(scope)
        entry = _normalize(scope, entry)
        if entry in s.exclude or not any(covers(scope, a, entry) for a in s.allow):
            return self
        return Bounds({**self.scopes, scope: Scope(scope, s.allow, (*s.exclude, entry))})

    def within(self, parent: "Bounds") -> "Bounds":
        """Validate against `parent` and return these bounds with the parent's relevant
        exclusions inherited. Raises BoundsError with every problem found."""
        problems = self.subset_problems(parent) + self.invariant_problems()
        if problems:
            raise BoundsError("; ".join(problems))
        scopes = {}
        for name in SCOPES:
            mine, theirs = self.scope(name), parent.scope(name)
            inherited = [x for x in theirs.exclude if any(covers(name, a, x) for a in mine.allow)]
            scopes[name] = Scope(name, mine.allow, tuple(dict.fromkeys([*mine.exclude, *inherited])))
        return Bounds(scopes)


def fs_target(path: str | Path, root: Path) -> str | None:
    """A filesystem path as a bounds target ("/a/b" from the nimoi root), or None if outside it.

    Accepts a notation path ("/a/b" or "\\a\\b", from the nimoi root), a real absolute path
    (with a drive, or already under the root), or a path relative to the root.

    The path is RESOLVED FIRST: symlinks, junctions and Windows 8.3 short names (CANDID~1)
    become the real long path, and only then is the target computed. Allow and deny rules
    therefore judge the file that will actually be opened; a deny rule checked against a
    spelling fails open (peer review, 2026-09-25). Only harness-owned tools use this: the
    CLI's built-in file tools, which resolve paths their own way, are not loaded (files.py)."""
    root = root.resolve()
    raw = str(path)
    p = Path(raw).expanduser()
    if p.drive or (p.is_absolute() and (p == root or p.is_relative_to(root))):
        pass  # a real absolute path
    elif raw.startswith(("/", "\\")):
        p = root / raw.lstrip("/\\")  # notation: "/" is the nimoi root
    else:
        p = root / p
    resolved = p.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        return None
    rel = resolved.relative_to(root).as_posix()
    return "/" if rel == "." else "/" + rel


def fs_path(target: str, root: Path) -> Path:
    """The absolute path of a filesystem bounds target."""
    return root.resolve() / target.lstrip("/")
