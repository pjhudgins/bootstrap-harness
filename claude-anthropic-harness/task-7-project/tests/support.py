"""Shared fixture for the offline tests: a fake nimoi tree, a real scribe ledger, the real harness
wired with the fake client (fake_client.py). Nothing touches the real nimoi, except the
tests that probe its 8.3 short names (read-only), and nothing calls a model.

Fake nimoi, which is also the test project (task 7: project folder = read root = the tree):
    ledger/               the project's ledger directory (the file tools never reach it)
    origins/onboarding_1.01.md, onboarding_1.02.md (the project's onboarding, ONBOARDING_LINES lines)
    docs/a.md ("needle"), docs/sub/b.md ("needle"), docs/.env (secret), docs/big.bin (binary)
    candidate_repos/x/README.md ("needle"; excluded by TOP)
    ledgers/old/20260101T000000Z.ledger and lease.json (raw ledger files: always denied)
    work/                 (fs.write)
    scripts/              (fs.exec): safe_probe.py, notes.txt, loud.py, slow.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TASK_DIR))
sys.dont_write_bytecode = True

from scribe_import import import_scribe  # noqa: E402

scribe = import_scribe()

from bounds import Bounds  # noqa: E402
from events import EventBus  # noqa: E402
from harness import build_harness  # noqa: E402
from ledgerlog import LedgerLog  # noqa: E402
from project import Project  # noqa: E402

from tests.fake_client import FakeFactory  # noqa: E402

HARNESS = "harness:test"
ONBOARDING_LINES = 30
TOP = Bounds.parse("""
fs.read       /  !/candidate_repos/
fs.write      /work/
fs.exec       /scripts/
ledger.read   *
ledger.write  pilot/
""")


SAFE_PROBE = TASK_DIR / "tests" / "fixtures" / "safe_probe.py"  # the harmless test script (rules 5d)


def make_project(root: Path, name: str = "t") -> Project:
    """The test tree as a project: its folder and its read root are the tree itself."""
    return Project(name=name, port=0, project_dir=root, onboarding=root / "origins" / "onboarding_1.02.md",
                   read_root=root, read_exclude=("/candidate_repos",), ledger_name=name)


def make_tree(root: Path) -> None:
    (root / "ledger").mkdir(parents=True)
    (root / "origins").mkdir(parents=True)
    (root / "origins" / "onboarding_1.01.md").write_text("old onboarding\n", encoding="utf-8")
    (root / "origins" / "onboarding_1.02.md").write_text(
        "".join(f"onboarding line {i}\n" for i in range(1, ONBOARDING_LINES + 1)), encoding="utf-8")
    (root / "docs" / "sub").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("alpha\nneedle one\n", encoding="utf-8")
    (root / "docs" / "sub" / "b.md").write_text("needle two\n", encoding="utf-8")
    (root / "docs" / ".env").write_text("SECRET=needle\n", encoding="utf-8")
    (root / "docs" / "big.bin").write_bytes(b"needle\x00\x01")
    (root / "candidate_repos" / "x").mkdir(parents=True)
    (root / "candidate_repos" / "x" / "README.md").write_text("needle excluded\n", encoding="utf-8")
    (root / "ledgers" / "old").mkdir(parents=True)
    (root / "ledgers" / "old" / "20260101T000000Z.ledger").write_text('{"needle": 1}\n', encoding="utf-8")
    (root / "ledgers" / "old" / "lease.json").write_text("{}", encoding="utf-8")
    (root / "work").mkdir()
    (root / "scripts").mkdir()
    shutil.copy(SAFE_PROBE, root / "scripts")
    (root / "scripts" / "notes.txt").write_text("not python", encoding="utf-8")
    (root / "scripts" / "loud.py").write_text("import sys\nsys.stdout.write('x' * 400_000)\n", encoding="utf-8")
    (root / "scripts" / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")


class HarnessCase(unittest.TestCase):
    """A fresh tree, ledger and harness per test. The ledger must reload with no findings."""

    scripts: dict[str, list[list[tuple]]] = {}
    init_tools: list[str] | None = None
    top_model = "claude-sonnet-5"

    def make_codex(self) -> Any:
        """Codex settings for GPT agents (test_codex.py overrides this); None: Codex not configured."""
        return None

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "nimoi"
        make_tree(self.root)
        self.project = make_project(self.root)
        self.ledger = scribe.Scribe.open(self.project.ledger_dir, "t", session_author=HARNESS, create=True)
        self.bus = EventBus(LedgerLog(scribe, self.ledger, HARNESS))
        self.factory = FakeFactory(dict(self.scripts), self.init_tools)
        self.env, self.top = build_harness(scribe, self.ledger, self.bus, project=self.project, model=self.top_model,
                                           top_bounds=TOP, budget_usd=1.0, max_turns=5, client_factory=self.factory,
                                           codex=self.make_codex())

    def tearDown(self) -> None:
        if self.ledger.is_open:
            self.ledger.close()
        self.assertEqual(scribe.load(self.project.ledger_dir, "t").findings, [], "the test ledger must reload clean")
        self._tmp.cleanup()

    # ---- helpers ----

    def run_async(self, coro: Any) -> Any:
        async def main() -> Any:
            self.bus.bind_loop(asyncio.get_running_loop())
            return await coro
        return asyncio.run(main())

    def records(self, kind: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self.bus.history if (kind is None or r["kind"] == kind)
                and (agent is None or r.get("agent") == agent)]

    def view(self) -> Any:
        return scribe.load(self.project.ledger_dir, "t")
