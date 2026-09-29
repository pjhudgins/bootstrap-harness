"""Bounds: what an agent may read, write and execute. One notation for every agent.

A bounds value has five keys, each a list of entries:

    fs.read   fs.write   fs.exec      paths relative to the nimoi folder, "/"-separated
    ledger.read   ledger.write        ledger entry names

- An entry ending in "/" covers that folder or name and everything under it:
  "origins/" covers "origins" and "origins/NIMOI.md".
- Any other entry covers exactly itself: "scripts/hello.py" covers only that file.
- "*" covers everything. [] (or a missing key) covers nothing.
- ledger.write also grants ledger.read of what it covers: an agent can read back what it
  may write, and it needs to, to cite a name's current id when it supersedes it.

Subset: a child's bounds are valid only when each child entry is covered by some entry
the parent holds for the same key (for ledger.read: in the parent's ledger.read or
ledger.write).
Invariant, for every agent: no fs.write entry reaches into the folder of an fs.exec
entry, so an agent never writes where scripts run (rules.md 5d). An exact fs.exec file
counts as its whole folder.

The fixed rules that no bounds change are stated once, for agents, in prompts/bounds.md,
and enforced by paths.py and the tools.
"""

import json
import os
import re

KEYS = ("fs.read", "fs.write", "fs.exec", "ledger.read", "ledger.write")
EVERYTHING = "*"
# Windows paths compare case-insensitively; ledger names never do.
FS_CASE_INSENSITIVE = os.name == "nt"


class BoundsError(ValueError):
    """Invalid bounds; the message lists every problem, one per line."""


def _norm_entry(key, entry):
    if entry == EVERYTHING:
        return entry
    if not isinstance(entry, str) or not entry.strip():
        raise BoundsError(f"{key}: an entry must be a non-empty string, got {entry!r}")
    entry = entry.replace("\\", "/")
    if entry.startswith("/") or re.match(r"^[A-Za-z]:", entry):
        raise BoundsError(f"{key}: {entry!r} must be relative "
                          f"({'to the nimoi folder' if key.startswith('fs.') else 'a ledger name'})")
    if any(segment in ("", ".", "..") for segment in entry.rstrip("/").split("/")):
        raise BoundsError(f"{key}: {entry!r} has an empty, '.' or '..' segment")
    return entry


def covers(key, entry, item):
    """Does `entry` (for bound `key`) cover the path or name `item`?"""
    if entry == EVERYTHING:
        return True
    if key.startswith("fs.") and FS_CASE_INSENSITIVE:
        entry, item = entry.lower(), item.lower()
    if entry.endswith("/"):
        return item == entry[:-1] or item.startswith(entry)
    return item == entry


def overlaps(key, a, b):
    """Could some path or name be covered by both entries?"""
    return covers(key, a, b.rstrip("/")) or covers(key, b, a.rstrip("/"))


def inside(key, child, held):
    """Is everything `child` covers also covered by `held`?"""
    if held == EVERYTHING:
        return True
    if child == EVERYTHING:
        return False
    if child.endswith("/"):  # a subtree fits only inside a subtree
        return held.endswith("/") and covers(key, held, child[:-1])
    return covers(key, held, child)


def folder_of(entry):
    """The folder a script covered by an fs.exec entry runs from, as a subtree entry: the
    entry itself for a subtree or "*"; otherwise its parent folder ("*" for a file at the
    top of the nimoi folder, whose folder is all of it)."""
    if entry == EVERYTHING or entry.endswith("/"):
        return entry
    parent = entry.rpartition("/")[0]
    return parent + "/" if parent else EVERYTHING


class Bounds:
    def __init__(self, mapping=None):
        mapping = dict(mapping or {})
        unknown = sorted(set(mapping) - set(KEYS))
        if unknown:
            raise BoundsError(f"unknown keys {unknown}; the keys are {list(KEYS)}")
        self._entries = {}
        problems = []
        for key in KEYS:
            value = mapping.get(key, [])
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                problems.append(f"{key}: must be a list of entries, got {value!r}")
                continue
            try:
                entries = [_norm_entry(key, e) for e in value]
            except BoundsError as error:
                problems.append(str(error))
                continue
            self._entries[key] = (EVERYTHING,) if EVERYTHING in entries \
                else tuple(dict.fromkeys(entries))
        if problems:
            raise BoundsError("\n".join(problems))

    def __getitem__(self, key):
        return self._entries[key]

    def _held(self, key):
        """The entries that grant `key`: ledger.write also grants ledger.read."""
        if key == "ledger.read":
            return self._entries["ledger.read"] + self._entries["ledger.write"]
        return self._entries[key]

    def allows(self, key, item):
        return any(covers(key, entry, item) for entry in self._held(key))

    def problems_as_child_of(self, parent):
        """Every entry of self not covered by `parent`, as readable lines."""
        return [f"{key}: {entry!r} is not inside your {key} {list(parent._held(key))}"
                for key in KEYS for entry in self._entries[key]
                if not any(inside(key, entry, held) for held in parent._held(key))]

    def invariant_problems(self):
        """fs.write must never reach into a folder that scripts run from."""
        return [f"fs.write {w!r} reaches into {folder_of(x)!r}, where fs.exec {x!r} runs: "
                f"an agent may not write where scripts run"
                for w in self._entries["fs.write"] for x in self._entries["fs.exec"]
                if overlaps("fs.write", w, folder_of(x))]

    def as_dict(self):
        return {key: list(self._entries[key]) for key in KEYS}

    def render(self):
        """The one textual form, used in instructions, tool arguments and records."""
        return json.dumps(self.as_dict())

    def __eq__(self, other):
        return isinstance(other, Bounds) and self.as_dict() == other.as_dict()

    def __repr__(self):
        return f"Bounds({self.render()})"


def child_bounds(parent, requested):
    """Validate a parent's request for a child: well-formed, a subset, and the invariant.
    Returns Bounds or raises BoundsError listing every problem."""
    child = Bounds(requested)
    problems = child.problems_as_child_of(parent) + child.invariant_problems()
    if problems:
        raise BoundsError("\n".join(problems))
    return child
