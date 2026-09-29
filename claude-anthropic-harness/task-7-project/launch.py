"""task-7-project: start one server per configured project (rules.md 7a, 7b).

    python launch.py                       # every project in projects.toml
    python launch.py --project default     # only the named projects (repeatable)
    python launch.py --config other.toml --no-browser

In order:
  1. The config is read and checked (project.load_config). Any problem stops the launch, and no
     ledger is touched.
  2. Each project's ledger directory must exist. For a missing one, the human is asked on this
     console before it is created (7d); a project without one does not start.
  3. Each project's port must be free.
  4. Each project starts as its own process (app.py --project NAME), an independent task-6
     server with its own governor, owners, ledger session, launch budget and Codex state (7b).
     A crash in one does not stop the others. Their output is shown here, each line prefixed
     with [name].
  5. The launcher waits. Each server ends from its UI's End session button. Ctrl+C here reaches
     every server (they share this console), and each closes its ledger session.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # before the harness modules are imported

import argparse  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Callable, Sequence  # noqa: E402

from app import port_free  # noqa: E402
from project import CONFIG_FILE, ConfigError, Project, console_ask, ensure_ledger_dir, load_config, select  # noqa: E402

APP = Path(__file__).resolve().parent / "app.py"
STOP_WAIT_S = 60


def child_command(project: Project, config: Path, no_browser: bool) -> list[str]:
    cmd = [sys.executable, "-B", str(APP), "--config", str(config), "--project", project.name]
    return cmd + (["--no-browser"] if no_browser else [])


def prepare(projects: Sequence[Project], ask: Callable[[str], bool] = console_ask,
            free: Callable[[int], bool] = port_free, say: Callable[[str], None] = print) -> list[Project]:
    """The projects that can start: a ledger directory (asking before creating one) and a free port."""
    ready = []
    for p in projects:
        if not ensure_ledger_dir(p, ask):
            say(f"[{p.name}] not started: no ledger directory at {p.ledger_dir}")
        elif not free(p.port):
            say(f"[{p.name}] not started: port {p.port} is in use")
        else:
            ready.append(p)
    return ready


def run(commands: dict[str, list[str]], say: Callable[[str], None] = print) -> dict[str, int]:
    """Start every command, relay its output prefixed with its name, and wait for all. Returns exit codes."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    procs = {name: subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", env=env)
             for name, cmd in commands.items()}

    def relay(name: str, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            say(f"[{name}] {line.rstrip()}" if not line.startswith(f"[{name}]") else line.rstrip())

    threads = [threading.Thread(target=relay, args=(n, p), daemon=True) for n, p in procs.items()]
    for t in threads:
        t.start()
    codes: dict[str, int] = {}
    try:
        for name, proc in procs.items():
            codes[name] = proc.wait()
    except KeyboardInterrupt:  # the servers got the same Ctrl+C: give them time to close their ledgers
        say("launcher: waiting for the servers to close their ledger sessions ...")
        for name, proc in procs.items():
            try:
                codes[name] = proc.wait(STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                codes[name] = proc.wait()
                say(f"[{name}] killed after {STOP_WAIT_S}s: its ledger may keep lease.json for a human to clear")
    for t in threads:
        t.join(timeout=5)
    for proc in procs.values():
        proc.stdout.close()
    return codes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="task-7-project: one server per configured project")
    parser.add_argument("--config", default=str(CONFIG_FILE), help="the projects file (default: projects.toml)")
    parser.add_argument("--project", action="append", help="start only this project (repeatable)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    config = Path(args.config).resolve()
    try:
        projects = load_config(config)
        if args.project:
            projects = [select(projects, name) for name in dict.fromkeys(args.project)]
    except ConfigError as e:
        print(f"The config is not usable, so nothing was started:\n{e}", file=sys.stderr)
        return 2
    ready = prepare(projects)
    if not ready:
        print("No project can start.", file=sys.stderr)
        return 2
    for p in ready:
        print(f"launcher: starting {p.name} on http://127.0.0.1:{p.port}/ (project folder {p.project_dir})")
    codes = run({p.name: child_command(p, config, args.no_browser) for p in ready})
    for name, code in codes.items():
        print(f"launcher: {name} exited with {code}")
    return max(codes.values(), default=0)


if __name__ == "__main__":
    sys.exit(main())
