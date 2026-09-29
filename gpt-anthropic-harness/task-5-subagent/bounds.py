"""Small explicit grants. Parse once; diagnose every invalid child grant."""
from dataclasses import dataclass, field
import json

KEYS = ("fs.read", "fs.write", "fs.execute", "ledger.read", "ledger.write", "ledger.tags")
TASK = "bootstrap-harness/gpt-anthropic-harness/task-5-subagent"
WORKSPACE = TASK + "/workspace"
SCRIPTS = TASK + "/scripts"
TAGS = ("pilot.note", "pilot.observation", "pilot.question")


class Denied(ValueError):
    """A well-formed request exceeds capabilities or violates policy."""


def canonical(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Use nonempty relative names with forward slashes.")
    if value == ".":
        return value
    if any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in value.split("/")):
        raise ValueError("No absolute paths, empty segments or traversal.")
    if any(ord(c) < 32 for c in value) or "*" in value or "?" in value:
        raise ValueError("Use exact names or prefix/** grants.")
    return value


@dataclass(frozen=True)
class Mounts:
    """Fixed NIMOI-relative roots, shared by all agents in one launch."""
    workspace: str = WORKSPACE
    scripts: str = SCRIPTS

    def relative(self, value):
        for mount, base in (("nimoi:/", "."), ("workspace:/", self.workspace), ("scripts:/", self.scripts)):
            if isinstance(value, str) and value.startswith(mount):
                tail = value[len(mount):]
                if not tail:
                    return base
                canonical(tail)
                return tail if base == "." else base + "/" + tail
        return canonical(value)


@dataclass(frozen=True)
class Selector:
    path: str
    tree: bool = False

    @classmethod
    def parse(cls, value, mounts=None):
        if value == "**":
            return cls("", True)
        tree = value.endswith("/**")
        name = value[:-2] if tree and value.endswith(":/**") else value[:-3] if tree else value
        path = mounts.relative(name) if mounts else canonical(name)
        if mounts and path == "." and tree:
            path = ""
        return cls(path.casefold() if mounts else path, tree)

    def contains(self, other):
        if self.path == "" and self.tree:
            return True
        if not self.tree:
            return not other.tree and self.path == other.path
        return other.path == self.path or other.path.startswith(self.path + "/")


def covers(grant, requested, *, filesystem=False):
    mounts = Mounts() if filesystem else None
    return Selector.parse(grant, mounts).contains(Selector.parse(requested, mounts))


@dataclass(frozen=True)
class Bounds:
    grants: tuple
    mounts: Mounts = field(default_factory=Mounts)
    selectors: tuple = ()

    @classmethod
    def parse(cls, data, mounts=None):
        mounts = mounts or Mounts()
        if not isinstance(data, dict) or set(data) != set(KEYS):
            raise ValueError("Bounds require exactly: " + ", ".join(KEYS))
        problems, parsed = [], []
        for key in KEYS:
            patterns = data[key]
            if not isinstance(patterns, list) or len(patterns) > 20 or any(not isinstance(p, str) for p in patterns):
                problems.append(f"{key}: use a list of at most 20 strings; [] denies.")
                continue
            values = []
            for pattern in patterns:
                try:
                    if key == "ledger.tags":
                        if pattern not in TAGS:
                            raise ValueError("Only pilot note tags may be granted.")
                        values.append(Selector(pattern))
                    else:
                        values.append(Selector.parse(pattern, mounts if key.startswith("fs.") else None))
                except ValueError as exc:
                    problems.append(f"{key}: {pattern!r}: {exc}")
            parsed.append((key, tuple(values)))
        if problems:
            raise ValueError("\n".join(problems))
        result = cls(tuple((k, tuple(data[k])) for k in KEYS), mounts, tuple(parsed))
        for key, maximum in (("fs.write", mounts.workspace + "/**"),
                             ("fs.execute", mounts.scripts + "/**"), ("ledger.write", "pilot/**")):
            ceiling = Selector.parse(maximum, mounts if key.startswith("fs.") else None)
            for name, selector in zip(result.get(key), result._selectors(key)):
                if not ceiling.contains(selector):
                    problems.append(f"{key}: {name!r} exceeds fixed boundary {maximum!r}.")
        for w in result._selectors("fs.write"):
            for x in result._selectors("fs.execute"):
                if w.contains(x) or x.contains(w):
                    problems.append("fs.write and fs.execute overlap.")
        if problems:
            raise Denied("\n".join(problems))
        return result

    def get(self, key):
        return dict(self.grants)[key]

    def _selectors(self, key):
        return dict(self.selectors)[key]

    def permits(self, key, value):
        value = self.mounts.relative(value) if key.startswith("fs.") else canonical(value)
        target = Selector(value.casefold() if key.startswith("fs.") else value)
        return any(grant.contains(target) for grant in self._selectors(key))

    def require(self, key, value):
        if not self.permits(key, value):
            raise Denied(f"Outside {key} bounds: {value}; held: {list(self.get(key))}")

    def problems_as_child_of(self, parent):
        return [f"{key}: {name!r} is not covered by parent grants {list(parent.get(key))}."
                for key in KEYS for name, selector in zip(self.get(key), self._selectors(key))
                if not any(p.contains(selector) for p in parent._selectors(key))]

    def subset_of(self, parent):
        return not self.problems_as_child_of(parent)

    def data(self):
        return {key: list(values) for key, values in self.grants}

    def notation(self):
        return json.dumps(self.data(), ensure_ascii=False, indent=2)


def parent_bounds(mounts=None):
    return Bounds.parse({"fs.read": ["**"], "fs.write": ["workspace:/**"],
        "fs.execute": ["scripts:/safe_test.py"], "ledger.read": ["**"],
        "ledger.write": ["pilot/**"], "ledger.tags": list(TAGS)}, mounts)
