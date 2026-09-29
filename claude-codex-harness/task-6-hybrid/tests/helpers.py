"""Shared test fixtures. No login, no model, no tokens: temporary nimoi trees, ledgers
and Codex and Claude state stay in ../.runtime/test-tmp and the swimlane's offline homes
(rules.md: no writes outside the swimlane).
"""

import ctypes
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import ledger_log  # first: switches off bytecode caching before scribe is imported
from ledger_log import scribe

TASK_DIR = Path(__file__).resolve().parents[1]
SCRATCH = TASK_DIR / ".runtime" / "test-tmp"
OFFLINE_HOME = TASK_DIR.parent / ".runtime" / "offline-codex-home"
ONBOARDING = "origins/onboarding_9.99.md"
ROOT = {"fs.read": ["*"], "fs.write": ["workspace/"], "fs.exec": ["scripts/"],
        "ledger.read": ["*"], "ledger.write": ["agent/"]}
LEAVER = '''import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         stdout=sys.stdout, stderr=sys.stderr)
print(f"started {child.pid}", flush=True)
if "--hang" in sys.argv:
    time.sleep(60)
'''


def scratch_dir():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


def make_tree(root):
    """A small nimoi: onboarding, notes, secrets, ledger-like files, scripts, workspace."""
    # No .git folder, even here: the .git rule is tested on paths that do not exist.
    for folder in ("origins", "notes", "workspace", "scripts", ".runtime"):
        (root / folder).mkdir(parents=True)
    (root / ONBOARDING).write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "notes" / "a.txt").write_text("note\n", encoding="utf-8")
    (root / "notes" / "x.ledger").write_text('{"name": "harness/secret-ish"}\n', encoding="utf-8")
    (root / "notes" / "lease.json").write_text("{}\n", encoding="utf-8")
    (root / ".runtime" / "state.txt").write_text("runtime\n", encoding="utf-8")
    (root / ".env").write_text("KEY=sk-never\n")
    shutil.copy(TASK_DIR / "scripts" / "hello_safe.py", root / "scripts")
    (root / "scripts" / "env_probe.py").write_text("import os\nprint(sorted(os.environ))\n",
                                                   encoding="utf-8")
    (root / "scripts" / "leaver.py").write_text(LEAVER, encoding="utf-8")
    (root / "scripts" / "notes.txt").write_text("not a script")


def short_name(path):
    """The Windows 8.3 short form of an existing file's name, or None if it has none."""
    if os.name != "nt":
        return None
    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024):
        return None
    name = Path(buffer.value).name
    return name if name != Path(path).name else None


def process_running(pid):
    """Is a Windows process still running?"""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


class LedgerCase(unittest.TestCase):
    """A temporary nimoi tree and harness ledger for each test. tearDown closes the
    ledger and checks that a fresh load finds nothing wrong, unless the test broke the
    ledger on purpose (ledger_broken)."""
    ledger_broken = False

    def setUp(self):
        tmp = scratch_dir()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "nimoi"
        make_tree(self.root)
        self.ledger_root = self.tmp / "ledgers"
        self.record = ledger_log.LedgerRecord(self.ledger_root)

    def tearDown(self):
        if self.ledger_broken:
            return
        self.record.close()
        view = scribe.load(self.ledger_root, ledger_log.LEDGER_NAME)
        self.assertEqual([str(f) for f in view.findings], [])

    def harness_kinds(self):
        """Kinds of the harness records written so far, in order."""
        s = self.record.scribe
        names = sorted(n for n in s.names() if n.startswith(ledger_log.HARNESS_PREFIX))
        return [s.current(n).body["kind"] for n in names]


class StubConv:
    """The conversation, as the governance and subagent tools see it: every call is
    recorded and answered with a stand-in result."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            return {"called": name}
        return call


class StubAgent:
    """What a Toolbox needs from its agent, without an engine."""

    def __init__(self, record, bounds, *, layer="task-owner", instructions=None,
                 read_onboarding=True, agent_id="gov.1"):
        self.id, self.layer = agent_id, layer
        self.author = f"{layer}:fake@claude-codex-harness#{agent_id}"
        self.bounds, self.record, self.scribe = bounds, record, record.scribe
        self.instructions_name, self.onboarding = instructions, ONBOARDING
        self.produced, self.conv = set(), StubConv()
        self._done = read_onboarding

    def onboarding_done(self):
        return self._done

    def note_onboarding_lines(self, start, end, total):
        self._done = self._done or (start == 1 and end >= total)
