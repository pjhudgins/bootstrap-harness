"""Task 7: projects from a config file.

Covers:
  - the config and its validation (every problem at once);
  - the ledger-directory prompt (7d);
  - the derived bounds;
  - the fixed denials: the ledger directory (7e), writes outside the project (7f), writes into scripts/;
  - the governor's writes and the owners' ceiling (founder, 2026-09-29);
  - a promotion into a project's own scripts/;
  - the launcher.

The tree (a read root with two projects):
    origins/onboarding_1.02.md
    docs/a.md
    elsewhere/                 outside every project
    projects/p1/ledger/        p1's ledger directory      projects/p1/scripts/safe_probe.py
    projects/p1/notes/         a place to write           projects/p2/  (no ledger directory yet)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.support import HARNESS, SAFE_PROBE, scribe
from tests.fake_client import FakeFactory

from bounds import Bounds  # noqa: E402
from events import EventBus  # noqa: E402
from files import FsAccess  # noqa: E402
from harness import build_institution  # noqa: E402
from launch import prepare, run  # noqa: E402
from ledgerlog import LedgerLog  # noqa: E402
from project import ConfigError, ensure_ledger_dir, load_config, parse_project, select  # noqa: E402
from session import AgentSession  # noqa: E402

CONFIG = """
[[project]]
name = "p1"
port = 18801
project_dir = "root/projects/p1"
onboarding = "root/origins/onboarding_1.02.md"
read_root = "root"
read_exclude = ["secret"]

[[project]]
name = "p2"
port = 18802
project_dir = "root/projects/p2"
onboarding = "root/origins/onboarding_1.02.md"
read_root = "root"
ledger = "p2-ledger"
budget_usd = 2.5
"""


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "root"
        for d in ("origins", "docs", "elsewhere", "secret", "projects/p1/ledger", "projects/p1/scripts",
                  "projects/p1/notes", "projects/p2"):
            (self.root / d).mkdir(parents=True)
        (self.root / "origins" / "onboarding_1.02.md").write_text("line 1\nline 2\n", encoding="utf-8")
        (self.root / "docs" / "a.md").write_text("alpha\n", encoding="utf-8")
        (self.root / "projects" / "p1" / "notes" / "n.md").write_text("note\n", encoding="utf-8")
        shutil.copy(SAFE_PROBE, self.root / "projects" / "p1" / "scripts")
        self.config = self.tmp / "projects.toml"
        self.config.write_text(CONFIG, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def p1(self):
        return select(load_config(self.config), "p1")


class ConfigTests(ProjectCase):
    def test_a_valid_config(self):
        p1, p2 = load_config(self.config)
        self.assertEqual((p1.name, p1.port, p1.ledger_name, p1.budget_usd), ("p1", 18801, "p1", 5.0))
        self.assertEqual((p2.ledger_name, p2.budget_usd), ("p2-ledger", 2.5))
        self.assertEqual(p1.project_dir, (self.root / "projects" / "p1").resolve())  # relative to the config file
        self.assertEqual((p1.project_target, p1.ledger_target, p1.scripts_target, p1.onboarding_target),
                         ("/projects/p1", "/projects/p1/ledger", "/projects/p1/scripts", "/origins/onboarding_1.02.md"))
        self.assertEqual(p1.read_exclude, ("/secret",))
        with self.assertRaisesRegex(ConfigError, "name one"):
            select([p1, p2], None)

    def test_every_problem_is_reported(self):
        self.config.write_text(textwrap.dedent("""
            colour = "blue"
            [[project]]
            name = "Bad Name"
            port = 80
            project_dir = "root/elsewhere/missing"
            onboarding = "root/origins/none.md"
            read_root = "root"
            budget_usd = -1
            extra = 1

            [[project]]
            name = "a"
            port = 18801
            project_dir = "."
            onboarding = "root/origins/onboarding_1.02.md"
            read_root = "root"

            [[project]]
            name = "b"
            port = 18803
            project_dir = "root/projects/p1"
            onboarding = "root/origins/onboarding_1.02.md"
            read_root = "root"
            read_exclude = ["projects"]

            [[project]]
            name = "c"
            port = 18803
            project_dir = "root/projects/p1"
            onboarding = "root/projects/p1/ledger/onb.md"
            read_root = "root"
            """), encoding="utf-8")
        (self.root / "projects" / "p1" / "ledger" / "onb.md").write_text("x", encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.config)
        message = str(caught.exception)
        for expected in ("unknown top-level key 'colour'", "unknown key 'extra'", "name must be lowercase",
                         "port must be an integer", "project_dir .* is not a folder", "onboarding .* is not a file",
                         "budget_usd must be a positive number", "project_dir .* is not within read_root",
                         "read_exclude /projects would hide the project folder",
                         "onboarding may not be inside the project's ledger directory"):
            self.assertRegex(message, expected)

    def test_duplicates_are_refused(self):
        self.config.write_text(CONFIG.replace('name = "p2"', 'name = "p1"').replace("18802", "18801")
                               .replace("root/projects/p2", "root/projects/p1"), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.config)
        for what in ("share a name", "share a port", "share a project folder"):
            self.assertIn(what, str(caught.exception))

    def test_the_installed_config_is_valid(self):
        """Whatever projects.toml ships with this copy of the harness (the swimlane's default project, or the
        packaged nimoi/harness and nimoi/doctrine) loads without a problem."""
        from project import CONFIG_FILE
        projects = load_config(CONFIG_FILE)
        self.assertTrue(projects)
        for p in projects:
            self.assertTrue(p.onboarding.is_file() and p.project_dir.is_dir(), p.name)


class LedgerDirectoryTests(ProjectCase):
    def test_asks_before_creating(self):
        p2 = select(load_config(self.config), "p2")
        asked = []
        self.assertFalse(ensure_ledger_dir(p2, lambda q: asked.append(q) or False))
        self.assertFalse(p2.ledger_dir.exists())  # no means nothing is created
        self.assertTrue(ensure_ledger_dir(p2, lambda q: asked.append(q) or True))
        self.assertEqual(sorted(f.name for f in p2.ledger_dir.iterdir()), [".gitattributes", ".gitignore"])
        self.assertIn("*.ledger -text", (p2.ledger_dir / ".gitattributes").read_text(encoding="utf-8"))
        self.assertEqual(len(asked), 2)
        self.assertTrue(ensure_ledger_dir(p2, lambda q: self.fail("asked again")))  # exists: no question

    def test_a_file_in_the_way_is_left_alone(self):
        p2 = select(load_config(self.config), "p2")
        p2.ledger_dir.write_text("not a directory", encoding="utf-8")
        self.assertFalse(ensure_ledger_dir(p2, lambda q: self.fail("asked")))


class BoundsTests(ProjectCase):
    def test_derived_bounds(self):
        p1 = self.p1()
        gov, ceiling = p1.governor_bounds(), p1.owner_ceiling()
        self.assertEqual(gov.scope("fs.write").allow, ("/projects/p1",))
        self.assertEqual(set(gov.scope("fs.write").exclude), {"/projects/p1/ledger", "/projects/p1/scripts"})
        self.assertEqual(gov.scope("fs.exec").allow, ())  # the governor never executes
        self.assertEqual(ceiling.scope("fs.exec").allow, ("/projects/p1/scripts",))
        self.assertEqual((gov.invariant_problems(), ceiling.invariant_problems()), ([], []))
        self.assertFalse(ceiling.permits("fs.read", "/projects/p1/ledger/p1/x.ledger"))
        self.assertFalse(ceiling.permits("fs.read", "/secret/x"))
        self.assertTrue(ceiling.permits("fs.read", "/docs/a.md"))
        with self.assertRaisesRegex(Exception, "not within"):  # the ceiling cannot grant writes elsewhere
            Bounds.parse("fs.read /docs\nfs.write /elsewhere").within(ceiling)

    def test_the_invariant_counts_exclusions(self):
        self.assertEqual(Bounds.parse("fs.write /p !/p/s\nfs.exec /p/s").invariant_problems(), [])
        self.assertTrue(Bounds.parse("fs.write /p\nfs.exec /p/s").invariant_problems())
        self.assertTrue(Bounds.parse("fs.write /p !/p/other\nfs.exec /p/s").invariant_problems())
        self.assertEqual(Bounds.parse("fs.write /p/s/drafts\nfs.exec /p/s !/p/s/drafts").invariant_problems(), [])


class FixedDenialTests(ProjectCase):
    """Whatever the bounds say: no file tool reaches the ledger directory, no write lands outside the project
    folder, and no write lands in scripts/."""

    def access(self) -> FsAccess:
        p1 = self.p1()
        everything = Bounds.parse("fs.read /\nfs.write /\nfs.exec /")
        return FsAccess(p1.read_root, everything, p1.never_writable(), p1.never_accessible(), p1.project_target)

    def test_the_ledger_directory_is_closed(self):
        fs = self.access()
        for scope in ("fs.read", "fs.write", "fs.exec"):
            d = fs.check(scope, "/projects/p1/ledger/p1/x")
            self.assertFalse(d.allow, scope)
            self.assertIn("only the ledger tools reach", d.reason)
        listing = fs.list_dir("/projects/p1")
        self.assertNotIn("ledger", [e["name"] for e in listing["entries"]])

    def test_writes_stay_in_the_project(self):
        fs = self.access()
        self.assertTrue(fs.check("fs.write", "/projects/p1/notes/new.md").allow)
        for outside in ("/elsewhere/x.md", "/docs/a.md", "/projects/p2/x.md", "/"):
            d = fs.check("fs.write", outside)
            self.assertFalse(d.allow, outside)
            self.assertIn("outside the project folder", d.reason)
        self.assertIn("scripts directory", fs.check("fs.write", "/projects/p1/scripts/new.py").reason)
        self.assertTrue(fs.check("fs.read", "/projects/p1/scripts/safe_probe.py").allow)  # readable, not writable


class InstitutionTests(ProjectCase):
    """The governor and owners over a project, with the bounds project.py derives."""

    def setUp(self) -> None:
        super().setUp()
        self.project = self.p1()
        self.ledger = scribe.Scribe.open(self.project.ledger_dir, "p1", session_author=HARNESS, create=True)
        self.bus = EventBus(LedgerLog(scribe, self.ledger, HARNESS))
        self.factory = FakeFactory({"governor": []})
        self.env, self.gov, self.inst = build_institution(scribe, self.ledger, self.bus, project=self.project,
                                                          max_turns=10, client_factory=self.factory)

    def tearDown(self) -> None:
        if self.ledger.is_open:
            self.ledger.close()
        self.assertEqual(scribe.load(self.project.ledger_dir, "p1").findings, [])
        super().tearDown()

    def test_the_governor_writes_but_never_runs_or_spawns(self):
        tools = self.gov.allowed_tools
        self.assertIn("mcp__fs__write", tools)  # founder, 2026-09-29
        self.assertFalse({"mcp__exec__python", "mcp__agents__spawn"} & tools)
        self.assertTrue(self.gov.files.check("fs.write", "/projects/p1/notes/plan.md").allow)
        self.assertFalse(self.gov.files.check("fs.write", "/docs/plan.md").allow)
        self.assertIn("project `p1`", self.gov.system_prompt)
        self.assertIn(str(self.project.onboarding), self.gov.system_prompt)
        self.assertNotRegex(self.gov.system_prompt, r"\{[a-z_]+\}")

    def test_dispatch_is_bounded_by_the_project(self):
        async def main():
            self.bus.bind_loop(asyncio.get_running_loop())
            self.gov.guard.write("gov/assignments/a", "do it")
            ok = self.inst.check_dispatch(self.gov, model="claude-fable-5-1", assignment="gov/assignments/a",
                                          bounds_text="fs.read /origins /projects/p1\nfs.write /projects/p1/notes\n"
                                                      "ledger.read *\nledger.write work/a")
            refused = []
            for bounds in ("fs.read /origins\nfs.write /elsewhere\nledger.read *",
                           "fs.read /origins\nfs.write /projects/p1/scripts\nledger.read *",
                           "fs.read /origins /projects/p1/ledger\nledger.read *"):
                try:
                    self.inst.check_dispatch(self.gov, model="claude-fable-5-1", assignment="gov/assignments/a",
                                             bounds_text=bounds)
                except Exception as e:
                    refused.append(str(e))
            return ok, refused
        (bounds, onboarding, *_), refused = asyncio.run(main())
        self.assertEqual(onboarding, "/origins/onboarding_1.02.md")  # the project's fixed onboarding
        self.assertIn("/projects/p1/ledger", bounds.scope("fs.read").exclude)  # inherited from the ceiling
        self.assertEqual(len(refused), 3, refused)
        self.assertTrue(all("ceiling" in r for r in refused))

    def test_promotion_lands_in_the_project_scripts(self):
        draft = self.project.project_dir / "notes" / "probe3.py"
        draft.write_bytes(b"print('hi')\n")
        shutil.rmtree(self.project.scripts_dir)  # created on the first promotion

        async def main():
            self.bus.bind_loop(asyncio.get_running_loop())
            s = AgentSession(self.gov, self.inst)
            await s.start()
            self.gov.guard.write("gov/assignments/a", "say: waiting")
            await self.inst.dispatch(self.gov, model="claude-fable-5-1", assignment="gov/assignments/a",
                                     bounds_text="fs.read /origins /projects/p1\nledger.read *\nledger.write work/a")
            owner = self.inst.owners["owner.1"].core
            request = asyncio.create_task(self.inst.request(owner, "promote_script", "a probe",
                                                            {"source": "/projects/p1/notes/probe3.py",
                                                             "name": "probe3.py"}))
            for _ in range(200):
                if "req.1" in self.inst.requests:
                    break
                await asyncio.sleep(0.01)
            await self.inst.resolve(self.gov, "req.1", "approve", "harmless")
            self.inst.human_decide("approval.1", True, "ok")
            result = await request
            await s.stop()
            return result
        result = asyncio.run(main())
        self.assertEqual(result["decision"], "approved")
        self.assertEqual((self.project.scripts_dir / "probe3.py").read_bytes(), b"print('hi')\n")
        self.assertEqual(json.loads(json.dumps(result["applied"]))["script"], "/projects/p1/scripts/probe3.py")


class LauncherTests(ProjectCase):
    def test_prepare_asks_and_checks_ports(self):
        projects = load_config(self.config)
        said = []
        ready = prepare(projects, ask=lambda q: False, free=lambda port: True, say=said.append)
        self.assertEqual([p.name for p in ready], ["p1"])  # p2 has no ledger directory and the human said no
        self.assertIn("no ledger directory", said[0])
        ready = prepare(projects, ask=lambda q: True, free=lambda port: port != 18801, say=said.append)
        self.assertEqual([p.name for p in ready], ["p2"])  # created on yes; p1's port is taken
        self.assertIn("port 18801 is in use", said[-1])

    def test_run_relays_each_server_and_returns_exit_codes(self):
        said = []
        stub = "import sys; print('serving', sys.argv[1]); sys.exit(int(sys.argv[2]))"
        codes = run({"p1": [sys.executable, "-c", stub, "p1", "0"],
                     "p2": [sys.executable, "-c", stub, "p2", "3"]}, say=said.append)
        self.assertEqual(codes, {"p1": 0, "p2": 3})
        self.assertIn("[p1] serving p1", said)
        self.assertIn("[p2] serving p2", said)


class SelfProtectionTests(unittest.TestCase):
    """The harness protects its own installation (packaging, 2026-09-29): a project whose folder contains the
    harness cannot write it, and no project's agents reach the harness's runtime state. Real paths, read-only."""

    def test_a_project_around_the_harness(self):
        from project import TASK_DIR, Project
        from scribe_import import NIMOI_ROOT
        around = Project(name="around", port=0, project_dir=TASK_DIR.parent, onboarding=NIMOI_ROOT / "origins" /
                         "onboarding_1.12.md", read_root=NIMOI_ROOT, ledger_name="around")
        install = around.install_target
        self.assertEqual(install, "/" + TASK_DIR.relative_to(NIMOI_ROOT).as_posix())
        self.assertIn(install, around.owner_ceiling().scope("fs.write").exclude)
        self.assertIn(install, around.never_writable())
        self.assertIn(around.runtime_target, around.never_accessible())
        self.assertEqual(around.owner_ceiling().invariant_problems(), [])
        fs = FsAccess(around.read_root, Bounds.parse("fs.read /\nfs.write /"), around.never_writable(),
                      around.never_accessible(), around.project_target)
        self.assertFalse(fs.check("fs.write", install + "/agent.py").allow)
        self.assertTrue(fs.check("fs.read", install + "/agent.py").allow)  # readable, never writable
        self.assertFalse(fs.check("fs.read", around.runtime_target + "/x").allow)

    def test_a_project_inside_the_harness_folder_is_not_blocked(self):
        from project import TASK_DIR, Project
        from scribe_import import NIMOI_ROOT
        inside = Project(name="inside", port=0, project_dir=TASK_DIR / "projects" / "default",
                         onboarding=NIMOI_ROOT / "origins" / "onboarding_1.12.md", read_root=NIMOI_ROOT,
                         ledger_name="inside")
        self.assertIsNone(inside.install_target)  # its writes are confined to its own folder anyway
        with self.assertRaisesRegex(ConfigError, "harness's own folder"):
            parse_project({"name": "self", "port": 18899, "project_dir": str(TASK_DIR),
                           "onboarding": str(NIMOI_ROOT / "origins" / "onboarding_1.12.md"),
                           "read_root": str(NIMOI_ROOT)}, TASK_DIR)


class ParseTests(ProjectCase):
    def test_a_project_folder_equal_to_the_read_root_is_allowed(self):
        p = parse_project({"name": "whole", "port": 18810, "project_dir": "root", "read_root": "root",
                           "onboarding": "root/origins/onboarding_1.02.md"}, self.tmp)
        self.assertEqual((p.project_target, p.ledger_target), ("/", "/ledger"))
        self.assertEqual(p.owner_ceiling().invariant_problems(), [])


if __name__ == "__main__":
    unittest.main()
