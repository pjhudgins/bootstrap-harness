"""Projects (rules.md task 7): what one server works on, read from a config file (projects.toml).

A project is:
    name          the server's name, and its ledger's default name
    port          the server's port on 127.0.0.1
    project_dir   the project folder: agents write here, as the governor directs (7f)
    onboarding    one fixed file that every agent reads in full first (7c; founder 2026-09-29:
                  a fixed file only)
    read_root     "/" of the bounds notation: agents read under it (7g)
optional:
    read_exclude  folders under the read root that no agent reads (the default project excludes
                  candidate_repos, which NIMOI onboarding designates untrusted)
    ledger        the ledger's name inside <project>/ledger/ (default: the project name)
    budget_usd    the launch budget for the server's Claude agents (default 5.00)
Relative paths resolve from the config file's folder.

The harness protects its own installation. If the harness's folder lies inside a project's folder
(nimoi/harness/prod inside nimoi/harness), no agent may write it: agents cannot change the harness that
constrains them. The harness's runtime state (.runtime/) is closed to every file tool, wherever the
harness is installed.

The layout inside a project folder is fixed:
    ledger/   the project's ledger directory. Only the ledger tools reach it (7e). It must exist,
              or the human agrees to its creation (7d: ensure_ledger_dir).
    scripts/  what agents may run (fs.exec). No agent writes there with the file tools: scripts
              arrive only by promotion, on the human's approval (founder, 2026-09-29).

Derived bounds, in the notation of bounds.py ("/" = the read root; L = ledger/, S = scripts/):
    governor       fs.read / !excl !L   fs.write <project> !L !S   ledger.read *   ledger.write gov/
    owner ceiling  fs.read / !excl !L   fs.write <project> !L !S   fs.exec S   ledger.read *   ledger.write work/
    pilot          as the ceiling, with ledger.write pilot/
The governor writes in the project folder too (founder, 2026-09-29), but has no exec and no
subagents. Nothing outside the project folder can be written (7f): no ceiling allows it, and the
file tools refuse it whatever an agent's bounds say. They also refuse L entirely, and writes into
S (files.FsAccess fixed denials), so a mistaken bound cannot open any of them.

Every problem in the config is reported at once. A launch with any problem starts nothing.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bounds import Bounds, BoundsError, covers, fs_target

TASK_DIR = Path(__file__).resolve().parent
CONFIG_FILE = TASK_DIR / "projects.toml"
LEDGER_TEMPLATES = TASK_DIR / "ledger_templates"  # .gitattributes, .gitignore for a new ledger directory
LEDGER_DIRNAME, SCRIPTS_DIRNAME = "ledger", "scripts"
DEFAULT_BUDGET_USD = 5.00
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
LEDGER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
KEYS = {"name", "port", "project_dir", "onboarding", "read_root", "read_exclude", "ledger", "budget_usd"}


class ConfigError(ValueError):
    """The config file is unusable; the message lists every problem."""


@dataclass(frozen=True)
class Project:
    name: str
    port: int
    project_dir: Path
    onboarding: Path
    read_root: Path
    read_exclude: tuple[str, ...] = ()  # bounds targets, e.g. "/candidate_repos"
    ledger_name: str = ""
    budget_usd: float = DEFAULT_BUDGET_USD
    config_path: Path | None = None

    # ---- layout -------------------------------------------------------------------------------

    @property
    def ledger_dir(self) -> Path:
        return self.project_dir / LEDGER_DIRNAME

    @property
    def scripts_dir(self) -> Path:
        return self.project_dir / SCRIPTS_DIRNAME

    def target(self, path: Path) -> str:
        target = fs_target(path, self.read_root)
        if target is None:
            raise ConfigError(f"{path} is outside the read root {self.read_root}")
        return target

    @property
    def project_target(self) -> str:
        return self.target(self.project_dir)

    @property
    def ledger_target(self) -> str:
        return self.target(self.ledger_dir)

    @property
    def scripts_target(self) -> str:
        return self.target(self.scripts_dir)

    @property
    def onboarding_target(self) -> str:
        return self.target(self.onboarding)

    @property
    def install_target(self) -> str | None:
        """The harness's own folder, when it lies inside this project's folder: never written by agents."""
        if TASK_DIR.is_relative_to(self.project_dir):
            return fs_target(TASK_DIR, self.read_root)
        return None

    @property
    def runtime_target(self) -> str | None:
        """The harness's runtime state (Codex), when under the read root: closed to every file tool."""
        return fs_target(TASK_DIR / ".runtime", self.read_root)

    # ---- derived bounds -----------------------------------------------------------------------

    def _fs(self) -> tuple[str, str]:
        hidden = [*self.read_exclude, self.ledger_target, *([self.runtime_target] if self.runtime_target else [])]
        read = " ".join(["/", *(f"!{x}" for x in hidden)])
        protected = [self.ledger_target, self.scripts_target, *([self.install_target] if self.install_target else [])]
        write = " ".join([self.project_target, *(f"!{x}" for x in protected)])
        return read, write

    def governor_bounds(self) -> Bounds:
        read, write = self._fs()
        return Bounds.parse(f"fs.read {read}\nfs.write {write}\nledger.read *\nledger.write gov/")

    def owner_ceiling(self) -> Bounds:
        read, write = self._fs()
        return Bounds.parse(f"fs.read {read}\nfs.write {write}\nfs.exec {self.scripts_target}\nledger.read *\n"
                            "ledger.write work/")

    def pilot_bounds(self) -> Bounds:
        read, write = self._fs()
        return Bounds.parse(f"fs.read {read}\nfs.write {write}\nfs.exec {self.scripts_target}\nledger.read *\n"
                            "ledger.write pilot/")

    def never_accessible(self) -> list[str]:
        """Fixed denial for every file tool: the ledger directory (7e), and the harness's runtime state."""
        return [self.ledger_target, *([self.runtime_target] if self.runtime_target else [])]

    def never_writable(self) -> list[str]:
        """Fixed denial for writes: the scripts directory (founder, 2026-09-29), and the harness's own
        installation when it lies inside the project folder."""
        return [self.scripts_target, *([self.install_target] if self.install_target else [])]

    def facts(self) -> dict[str, Any]:
        """What the ledger records about the project at the start of each session."""
        return {"project": self.name, "project_dir": str(self.project_dir), "project_target": self.project_target,
                "read_root": str(self.read_root), "read_exclude": list(self.read_exclude),
                "onboarding": self.onboarding_target, "port": self.port,
                "config": str(self.config_path) if self.config_path else None}


# ---- the config file --------------------------------------------------------------------------

def _path(value: Any, base: Path, what: str, problems: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{what} must be a path")
        return None
    p = Path(value).expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def parse_project(raw: Any, base: Path, config_path: Path | None = None) -> Project:
    """One [[project]] table; raises ConfigError listing its problems."""
    if not isinstance(raw, dict):
        raise ConfigError("a project must be a table")
    problems = [f"unknown key {k!r}" for k in sorted(set(raw) - KEYS)]
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        problems.append("name must be lowercase letters, digits and hyphens")
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        problems.append("port must be an integer from 1024 to 65535")
    read_root = _path(raw.get("read_root"), base, "read_root", problems)
    project_dir = _path(raw.get("project_dir"), base, "project_dir", problems)
    onboarding = _path(raw.get("onboarding"), base, "onboarding", problems)
    if read_root and not read_root.is_dir():
        problems.append(f"read_root {read_root} is not a folder")
    if project_dir and not project_dir.is_dir():
        problems.append(f"project_dir {project_dir} is not a folder")
    if onboarding and not onboarding.is_file():
        problems.append(f"onboarding {onboarding} is not a file")
    if read_root and project_dir and not project_dir.is_relative_to(read_root):
        problems.append(f"project_dir {project_dir} is not within read_root {read_root}")
    if read_root and onboarding and not onboarding.is_relative_to(read_root):
        problems.append(f"onboarding {onboarding} is not within read_root {read_root}")
    if project_dir and onboarding and onboarding.is_relative_to(project_dir / LEDGER_DIRNAME):
        problems.append("onboarding may not be inside the project's ledger directory")
    if project_dir and project_dir == TASK_DIR:
        problems.append("project_dir may not be the harness's own folder")
    excludes: list[str] = []
    raw_excludes = raw.get("read_exclude", [])
    if not isinstance(raw_excludes, list) or not all(isinstance(x, str) for x in raw_excludes):
        problems.append("read_exclude must be a list of paths under the read root")
    elif read_root:
        for x in raw_excludes:
            target = fs_target("/" + x.replace("\\", "/").lstrip("/"), read_root)
            if target in (None, "/"):
                problems.append(f"read_exclude {x!r} must name a folder inside the read root")
            else:
                excludes.append(target)
    ledger_name = raw.get("ledger", name)
    if not isinstance(ledger_name, str) or not LEDGER_NAME_RE.fullmatch(ledger_name):
        problems.append("ledger must be a ledger name (letters, digits, '.', '_', '-')")
    budget = raw.get("budget_usd", DEFAULT_BUDGET_USD)
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget <= 0:
        problems.append("budget_usd must be a positive number")
    if problems:
        raise ConfigError("; ".join(problems))
    project = Project(name=name, port=port, project_dir=project_dir, onboarding=onboarding, read_root=read_root,
                      read_exclude=tuple(dict.fromkeys(excludes)), ledger_name=ledger_name, budget_usd=float(budget),
                      config_path=config_path)
    for x in project.read_exclude:  # agents must be able to read their own project and onboarding
        for what, target in (("the project folder", project.project_target),
                             ("the onboarding", project.onboarding_target)):
            if covers("fs.read", x, target):
                problems.append(f"read_exclude {x} would hide {what}")
    try:  # the derived bounds must satisfy the write/exec invariant (bounds.py)
        for bounds in (project.governor_bounds(), project.owner_ceiling()):
            if bounds.invariant_problems():
                problems += bounds.invariant_problems()
    except BoundsError as e:
        problems.append(f"derived bounds: {e}")
    if problems:
        raise ConfigError("; ".join(problems))
    return project


def load_config(path: Path | str = CONFIG_FILE) -> list[Project]:
    """Every project in the config file. Raises ConfigError listing every problem in the file."""
    path = Path(path).resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    entries = data.get("project")
    unknown = sorted(set(data) - {"project"})
    problems = [f"unknown top-level key {k!r}" for k in unknown]
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path}: no [[project]] tables" + ("; " + "; ".join(problems) if problems else ""))
    projects: list[Project] = []
    for i, raw in enumerate(entries, 1):
        label = raw.get("name", f"#{i}") if isinstance(raw, dict) else f"#{i}"
        try:
            projects.append(parse_project(raw, path.parent, path))
        except ConfigError as e:
            problems.append(f"project {label}: {e}")
    for attr, what in (("name", "name"), ("port", "port"), ("project_dir", "project folder")):
        seen: dict[Any, str] = {}
        for p in projects:
            key = getattr(p, attr)
            if key in seen:
                problems.append(f"projects {seen[key]} and {p.name} share a {what}")
            seen[key] = p.name
    if problems:
        raise ConfigError(f"{path}:\n  " + "\n  ".join(problems))
    return projects


def select(projects: list[Project], name: str | None) -> Project:
    """The named project, or the only one."""
    if name is None:
        if len(projects) == 1:
            return projects[0]
        raise ConfigError(f"the config has {len(projects)} projects; name one ({', '.join(p.name for p in projects)})")
    for p in projects:
        if p.name == name:
            return p
    raise ConfigError(f"no project {name!r}; projects: {', '.join(p.name for p in projects)}")


# ---- the ledger directory (7d) ----------------------------------------------------------------

def console_ask(question: str) -> bool:
    """Ask on the console; anything but yes (including no console) is no."""
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lstrip("\ufeff").lower() in ("y", "yes")  # a piped answer may start with a BOM


def ensure_ledger_dir(project: Project, ask: Callable[[str], bool] = console_ask) -> bool:
    """The project's ledger directory must exist. If it does not, the human is asked before it is
    created, with its git attributes (ledgers are byte-exact) and its ignore rule (lease.json).
    Returns whether it exists afterwards; nothing is created without a yes."""
    d = project.ledger_dir
    if d.is_dir():
        return True
    if d.exists():
        return False  # something else has that name: not the harness's to touch
    if not ask(f"Project {project.name!r} has no ledger directory. Create {d}?"):
        return False
    d.mkdir()
    for f in sorted(LEDGER_TEMPLATES.iterdir()):
        shutil.copy2(f, d / f.name)
    return True
