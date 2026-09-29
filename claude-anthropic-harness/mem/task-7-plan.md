# task-7-project — plan (2026-09-29; completed, awaiting founder review)

**Bar:** the task-6 harness, runnable against any configured project folder. Each project is its own server, with its own record. Its boundaries are enforced by the harness, and a boundary that can't be enforced is stated. Not production.

## Reading of rules.md task 7 (assumptions to confirm)
- **Folder.** `task-7-project/`, derived from `task-6-hybrid/`: code, tests, prompts and UI; not the ledger sessions or `.runtime`. [7a]
- **One server per project,** on the project's own port. [7a, 7b]
  (Decisions and their consequences: see Founder decisions below.)
  - A launcher starts one child process per project.
  - Each child is an independent task-6 app: governor, owners, ledger session, launch budget, Codex state and `~/.codex` diff.
  - One project crashing does not stop the others.
- **The config file** (`projects.toml`, human-edited) lists the projects. [7c]
  - Each project has: a name, a port, the project folder, the onboarding file and the read root. Optional: read exclusions, a ledger name and a budget.
  - Relative paths resolve from the config file.
- **The ledger directory** is `<project>/ledger/`, holding one ledger (by default named after the project), with a new session per launch. [7d]
  - If the directory is missing, the launcher asks on the console before creating it, with its git attributes.
  - If the answer is no, that project does not start.
- **The ledger directory is off-limits to the file tools** (read, list, search, write), whatever the bounds say. [7e]
  - It is a fixed denial, like secret files.
  - Agents reach the ledger only through the ledger tools.
- **Writes happen only in the project folder, minus its ledger directory,** under the governor's direction. [7f]
  - The owners' ceiling is `fs.write` = the project folder.
  - The governor grants owners write areas inside it at dispatch.
  - Nothing outside can be granted, since the ceiling refuses it.
- **The read root is the notation's `/`.** [7g]
  - Bounds paths are written from the read root.
  - The project folder, the onboarding file and the scripts folder must lie within it (checked at launch).
  - Read exclusions, such as `candidate_repos/` for nimoi, come from the config.
- **The default setup** is a single project in this swimlane, with the latest NIMOI onboarding and `nimoi/` as the read root. [7h]

## Unchanged from task 6
- The governor, the owners, subagents, requests and approvals.
- The Claude and Codex backends.
- The audit.
- The founder decisions of 2026-09-28/29:
  - Codex on `~/.codex`, with the diff recorded;
  - GPT exec monitored and stopped;
  - the governor is "Read + ledger + dispatch", plus writes in the project folder for task 7 (decision 4 below);
  - the governor passes rules, never a bar;
  - `.runtime` is kept.

## Founder decisions (2026-09-29)
1. **Default project:** inside the task folder, `task-7-project/projects/default/`. The harness code, the notebook and `mem/` stay out of agents' write reach.
2. **Scripts:** "update - put a scripts directory in each project and make direct writes forbidden".
   - Each project has `<project>/scripts/`, its `fs.exec`.
   - No agent may write there with the file tools: a fixed denial, as for the ledger directory.
   - Scripts arrive only by promotion, on the human's approval.
   - This replaces the plan's harness-level scripts folder.
3. **Onboarding:** a fixed file only. The default project names NIMOI's current `origins/onboarding_1.12.md`; a new version means editing the config.
4. **Governor writes:** yes. The governor also writes files in the project folder, minus its ledger and scripts directories. It still has no exec and no subagents. For task 7 this supersedes task 6's "Read + ledger + dispatch".

Consequences:
- **Write/exec separation.** `fs.exec` = `<project>/scripts/` now lies inside `fs.write` = `<project>/`. The invariant "fs.write and fs.exec never overlap" is therefore judged with exclusions: the scripts directory is excluded from `fs.write` in every derived bound, and a fixed denial also refuses writes there.
- **The launch-time check.** A launch is still refused if any target could be both written and executed.

## Phases, with pauses where a founder decision is needed
1. **Project configuration.**
   - The config file, its validation, the ledger-directory prompt, and project-scoped bounds and fixed denials.
   - The launcher and the per-project app.
   - Offline tests: config, prompt, fixed denials, write confinement, and two servers on two ports.
   - **Done:** 97 tests (81 from task 6, 16 new).
2. **Live checks.**
   - Two projects served at once, with no model call: independent ledgers, sessions and ports.
   - Then one short governor exchange on the default project.
   - **Done:** see `task-7-record.md`.
3. **Setup and documentation.**
   - The default single project (7h).
   - The notebook, the record, and `task-7-spec.md` (a delta on `task-6-spec.md`).
   - **Done:** `task-7-spec.md`, `task-7-record.md`, the notebook, and the default project (`projects.toml`).
