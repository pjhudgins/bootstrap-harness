"""Tool policy for task 4: what the agent may call, and where the read-only tools may look.

Derived from task-2-tooling/policy.py. Changes (founder decisions, 2026-09-24):
  - root is the nimoi directory, not a task folder (rules.md task 4d);
  - candidate_repos/ is excluded: onboarding calls it untrusted data, a
    prompt-injection surface;
  - ledger tools join the allowlist (their write limits live in ledger_tools.py).

Enforced by the PreToolUse hook (every call) and can_use_tool (when the CLI asks).

Excluded directories are also protected from searches that would walk into
them. Glob or Grep whose search base contains an excluded directory is
allowed only if the pattern cannot reach it:
  - Glob: a literal first segment that is not excluded (e.g. "origins/*.md"),
    or a single-segment pattern without ** (top-level only);
  - Grep: always refused there, since it recurses; search a subdirectory instead.
Known limits: names are screened, not contents; symlinks are resolved before
checking.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

from calc_tool import SERVER_NAME as CALC_SERVER
from ledger_tools import TOOL_NAMES as LEDGER_TOOLS

ALLOWED_BUILTINS = ["Read", "Glob", "Grep"]
ADD_TOOL = f"mcp__{CALC_SERVER}__add"
ALLOWED_TOOLS = frozenset(ALLOWED_BUILTINS + [ADD_TOOL, *LEDGER_TOOLS])
EXCLUDED_DIRS = ("candidate_repos",)

# Mirrors the secrets block of bootstrap-harness/.gitignore.
SECRET_NAME_PATTERNS = (
    ".env", ".env.*", "*.env", "*.token", "*.key", "*.pem",
    "credentials*.json", "secrets*.json",
)
GLOB_CHARS = set("*?[]{}")


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


def _is_secret_name(name: str) -> bool:
    name = name.lower()
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_NAME_PATTERNS)


def _within(path: Path, base: Path) -> bool:
    return path == base or path.is_relative_to(base)


class ToolPolicy:
    def __init__(self, root: Path, excluded: tuple[str, ...] = EXCLUDED_DIRS):
        self.root = Path(root).resolve()
        self.excluded = [(self.root / e).resolve() for e in excluded]

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        if tool_name not in ALLOWED_TOOLS:
            return Decision(False, f"{tool_name} is not on the allowlist {sorted(ALLOWED_TOOLS)}")
        if tool_name == "Read":
            d, _ = self._check_path(tool_input.get("file_path"), required=True)
            return d
        if tool_name == "Glob":
            d, base = self._check_path(tool_input.get("path"), required=False)
            if not d.allow:
                return d
            pattern = tool_input.get("pattern", "")
            d = self._check_pattern(pattern)
            return d if not d.allow else self._check_glob_reach(base, pattern)
        if tool_name == "Grep":
            d, base = self._check_path(tool_input.get("path"), required=False)
            if not d.allow:
                return d
            d = self._check_pattern(tool_input.get("glob") or "")
            if not d.allow:
                return d
            hidden = self._excluded_under(base)
            if hidden:
                return Decision(False, f"Grep from {base} would search excluded {hidden}; "
                                       "give a path inside a subdirectory instead")
            return Decision(True, "search base clear of excluded directories")
        return Decision(True, "allowlisted")

    def _check_path(self, raw: Any, *, required: bool) -> tuple[Decision, Path]:
        if not raw:
            if required:
                return Decision(False, "no path given"), self.root
            return Decision(True, "defaults to root"), self.root
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = self.root / p  # on Windows this also anchors "/x" to root's drive
        resolved = p.resolve()
        if not _within(resolved, self.root):
            return Decision(False, f"path {raw!r} resolves outside {self.root}"), resolved
        for ex in self.excluded:
            if _within(resolved, ex):
                return Decision(False, f"path {raw!r} is inside excluded {ex.name}/ (untrusted)"), resolved
        if any(_is_secret_name(part) for part in resolved.relative_to(self.root).parts):
            return Decision(False, f"path {raw!r} looks like a secret file"), resolved
        return Decision(True, "inside root"), resolved

    def _check_pattern(self, pattern: str) -> Decision:
        if not pattern:
            return Decision(True, "no pattern")
        pp = PurePath(pattern)
        if pp.anchor or pattern.startswith(("/", "\\", "~")):
            return Decision(False, f"pattern {pattern!r} is absolute")
        if ".." in pp.parts:
            return Decision(False, f"pattern {pattern!r} climbs out of root")
        if any(_is_secret_name(part) for part in pp.parts):
            return Decision(False, f"pattern {pattern!r} targets secret-looking files")
        return Decision(True, "pattern inside root")

    def _excluded_under(self, base: Path) -> list[str]:
        """Excluded directories at or below `base`."""
        return [ex.name for ex in self.excluded if _within(ex, base)]

    def _check_glob_reach(self, base: Path, pattern: str) -> Decision:
        if not self._excluded_under(base):
            return Decision(True, "search base clear of excluded directories")
        parts = PurePath(pattern.replace("\\", "/")).parts
        first = parts[0] if parts else ""
        if first and not (GLOB_CHARS & set(first)):
            start = (base / first).resolve()
            if any(_within(start, ex) for ex in self.excluded) or self._excluded_under(start):
                return Decision(False, f"pattern {pattern!r} reaches excluded {self._excluded_under(base)}")
            return Decision(True, f"pattern starts in {first}, clear of excluded directories")
        if len(parts) == 1 and "**" not in pattern:
            return Decision(True, "top-level pattern; does not descend")
        return Decision(False, f"pattern {pattern!r} from {base} could descend into "
                               f"{self._excluded_under(base)}; start it with a subdirectory name")
