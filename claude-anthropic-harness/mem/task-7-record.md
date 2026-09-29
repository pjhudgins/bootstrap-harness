# task-7-project — record (2026-09-29)

**Bar:** the task-6 harness, runnable against any configured project folder, one server per project, each with its own record; boundaries enforced by the harness, and stated where they can't be. Not production.

**State:** completed; awaiting founder review. The plan and founder decisions are in `task-7-plan.md`, and the back-spec is `task-7-spec.md`.

## Founder decisions (2026-09-29, at the start of task 7)
1. The default project lives inside the task folder: `task-7-project/projects/default/`.
2. "update - put a scripts directory in each project and make direct writes forbidden". So each project has its own `scripts/`, and no agent writes there.
3. The onboarding is a fixed file only. The default names `origins/onboarding_1.12.md`.
4. The governor writes files too, in the project folder. It still has no exec and no subagents.

## What was built (`task-7-project/`, from `task-6-hybrid/`)
- **`project.py`.** New. It holds:
  - the `Project` and the config file (`projects.toml`) with its validation;
  - the derived bounds (governor, owner ceiling, pilot);
  - the fixed-denial targets;
  - `ensure_ledger_dir`, which asks before creating a ledger directory.
- **`launch.py`.** New. It checks the config, asks about ledger directories and checks ports. It then starts one `app.py --project NAME` process per project, relays their output with a `[name]` prefix, and waits.
- **`app.py`.** One project's server: `--config`, `--project`, and optional `--port`, `--budget`, `--pilot` and `--no-codex`.
- **The core:**
  - **`bounds.py`:** the invariant is judged with exclusions.
  - **`files.py`:** new fixed denials: the ledger directory (every scope), and writes outside the project folder.
  - **`agent.py` / `harness.py`:** the environment carries the project's onboarding, write root and denials, and everything is built from a `Project`. Codex state goes in `.runtime/<project>/`.
  - **`governance.py`:** promotions come from the project folder and land in `<project>/scripts/`, created on the first promotion.
  - **`exec_tool.py`:** scripts run in the project folder.
- **Prompts.** They are project-aware: the project, its folder, the read root, the closed ledger directory, the scripts, and the fixed onboarding. The governor's prompt states its writes and their purpose.
- **The UI** shows the project.
- **`audit.py`, `live_turn.py` and `live_institution.py`** take `--config` and `--project`.
- **Tests: 97.** 81 are task 6's, now run over a test project; 16 are new in `tests/test_project.py`: config, the ledger prompt, derived bounds, the invariant, fixed denials, governor writes, dispatch confinement, promotion into project scripts, and the launcher.
- **The default project (7h).** `projects/default/` holds:
  - `scripts/safe_probe.py`;
  - `ledger/`, created through the launcher's prompt during the live check;
  - `notes/server-note.md`, written by the live governor.

## Live checks (Claude only; no owners dispatched)
| Check | Result |
|---|---|
| Launcher with two projects, `default` (8769) and a scratch project (8770), with no model call | The launcher asked before creating each missing ledger directory. Both servers came up as independent governors, with separate ledgers, sessions, onboarding, read roots, bounds and budgets ($5 and $1). Both shut down through their APIs, each closed its session, no lease was left, and the launcher exited 0. |
| One short governor exchange on `default` (session 20260929T161949Z, $0.20) | The governor read onboarding 1.12, recorded a note as `gov/notes/server-note`, and wrote it to `projects/default/notes/server-note.md` (978 bytes). Its `mcp__fs__list` of `ledger/` was refused: "is in the project's ledger directory, which only the ledger tools reach". The project folder's listing hides `ledger/`. Audit ok. |

## Findings
- **A piped "yes" read as no.** The first live launch piped its answers through PowerShell, and the first answer arrived with a byte-order mark. The launcher correctly refused to create the default ledger directory on what it read as a no. The prompt now ignores a leading BOM.
- **A killed launcher leaves its children running.** I killed that first, stuck launcher, and the scratch server kept running; I ended it through its API, and its ledger closed cleanly. Documented (spec D17.3).
- **The governor's note is honest about its own evidence.** It marks the claims it only read from its instructions `[working]`, separating them from the one closure it tested.

## Discrepancies
- DISCREPANCY: none new in the repository. The live check's piped-answer failure is a finding about console input, recorded above; it was not a defect in the record.

## Packaged into nimoi (2026-09-29, at the founder's direction)
Founder, verbatim: "create nimoi/harness, and configure it as a project with empty ledger and scripts directories. Also create nimoi/doctrine as a project. In nimoi/harness/prod, package your task-7 solution. Configure its projects.toml to for the two created projects, both using the standard onboarding file and read root. In nimoi/harness/dev, create a notebook explaining the properties of the solution, a brief history, and a map of relevant bootstrapping materials that an agent working the harness project can reference"

**Created:**
- `/harness/ledger` and `/doctrine/ledger`, each holding only the founder-approved git-hygiene pair (`*.ledger -text`, and `lease.json` ignored);
- `/harness/scripts` and `/doctrine/scripts`, empty;
- `/harness/prod`: the package, 25 modules plus prompts, UI, tests and templates, with its own `projects.toml` (harness 8771, doctrine 8772; NIMOI onboarding 1.12, read root nimoi, `candidate_repos` excluded), `README.md` and `MANIFEST.sha256`. The packaged code equals this task folder's, file for file;
- `/harness/dev/notebook.md`.

**Changed in this task's source before packaging:** tests 97 → 99, and they pass in both places.
- **The harness protects itself.** When its folder lies inside a project's folder, as `prod` does inside `/harness`, no agent may write it (a fixed denial, and excluded in the derived bounds). The harness's `.runtime/`, which holds Codex state, is closed to every file tool.
- **`scribe_import`** finds the nimoi root by searching upward for the scribe, not by a fixed depth.
- **The harness's author** names where it is installed: `harness:harness/prod` for the package.
- **Tests.** The safe-probe fixture moved to `tests/fixtures/`. The config test is generic. There are two new self-protection tests.

**Not done:** git for the new folders. `nimoi/.gitignore` ignores only `bootstrap-harness/`, so `harness/` and `doctrine/` are untracked in the parent repository until the human decides.

