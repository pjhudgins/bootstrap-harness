# task-7-project — back-specification (delta on task 6)

**Purpose.** This spec lets someone reproduce what claude-anthropic-harness built for task 7. It records requirements and design decisions, not implementation. Written 2026-09-29 from the built and tested harness (`task-7-project/`) and its live checks.

**Bar:** as for task 6. What breaks must be informative and its record must survive. A boundary the harness cannot enforce is stated. Not production.

**Base.** `task-6-spec.md` (R12–R20, D9–D15), on top of `task-4-spec.md`, still holds, with the changes noted below. Numbering and sources follow `task-6-spec.md`: `rules 7c` is `bootstrap-harness/rules.md` task 7 item c, and `founder` is a founder decision with its date.

---

## Requirements

### R21. One server per configured project [rules 7a, 7b]
- **R21.1.** A config file lists one or more projects. Each is served on its own port.
- **R21.2.** Each server is an independent task-6 solution. It has its own:
  - governor, task owners and subagents;
  - ledger session;
  - launch budget;
  - Codex state.

  One server failing does not stop the others.
- **R21.3.** The config is checked before anything starts, and every problem is reported at once. A project with any problem starts nothing, and no ledger is touched.
- **R21.4.** Each server can also run alone, for one project.

### R22. A project: a folder, an onboarding file and a read root [rules 7c, 7g]
- **R22.1.** The onboarding is one fixed file. Every agent reads it in full before any other tool (for owners and subagents, the gate of task 6). [founder 2026-09-29: "Fixed file only"]
- **R22.2.** The read root is the notation's `/`. Agents read under it, within their bounds.
  - The project folder and the onboarding must lie within it.
  - A project may exclude folders under it from reading.
- **R22.3.** The project folder may be the read root itself.

### R23. The ledger directory [rules 7d, 7e]
- **R23.1.** The project folder has a ledger directory, `<project>/ledger/`, holding the project's ledger, with a new session per launch.
- **R23.2.** If the directory is missing, the human is asked before it is created. On a no, or with no console to ask, the project does not start. [rules 7d]
- **R23.3.** A newly created ledger directory carries the ledger's git hygiene: byte-exact ledger files, and the lock file never committed.
- **R23.4.** Agents reach the ledger directory only through the ledger tools. Every file tool refuses it (read, list, search, write, exec), whatever an agent's bounds say. [rules 7e]

### R24. Writes stay in the project folder [rules 7f]
- **R24.1.** The project folder is writable, minus its ledger and scripts directories. Who writes where is decided at the governor's direction: the owners' ceiling is the project folder, and dispatch grants inside it.
- **R24.2.** Nothing outside the project folder can be written, by any agent, whatever its bounds say.
- **R24.3.** The governor itself may write in the project folder. It still has no exec and no subagents, and it still does not do owners' work. This supersedes task 6's "Read + ledger + dispatch" for task 7. [founder 2026-09-29]

### R25. Each project has its own scripts [founder 2026-09-29]
- **R25.1.** `<project>/scripts/` is what agents may run (`fs.exec`).
- **R25.2.** No agent writes there with the file tools. Scripts arrive only by a promotion the human approves, which copies exactly the bytes reviewed. ("put a scripts directory in each project and make direct writes forbidden")
- **R25.3.** No target is ever both writable and executable. This holds even though `scripts/` lies inside the writable project folder. [rules 5d]

### R26. The default setup [rules 7h]
- **R26.1.** One project, `default`, with its folder in this swimlane: `task-7-project/projects/default/`. [founder 2026-09-29: inside the task folder]
- **R26.2.** Its onboarding is NIMOI's current onboarding, `origins/onboarding_1.12.md`, as a fixed file. A new NIMOI version means editing the config.
- **R26.3.** Its read root is nimoi, and `candidate_repos/` is excluded from reading (untrusted, per NIMOI onboarding).

---

## Design decisions

### D16. The config file [R21, R22]
- **D16.1.** `projects.toml` (TOML), human-edited. It has one `[[project]]` table per project, with these keys:
  - `name` (lowercase, digits, hyphens);
  - `port`;
  - `project_dir`;
  - `onboarding`;
  - `read_root`;
  - optionally `read_exclude`, `ledger` (default: the name) and `budget_usd` (default 5.00).
- **D16.2.** Relative paths resolve from the config file's folder.
- **D16.3.** The layout inside a project folder is fixed: `ledger/` and `scripts/`.
- **D16.4. Validation**, all problems at once:
  - unknown keys;
  - bad names, ports or budgets;
  - missing folders or files;
  - the project folder or the onboarding outside the read root;
  - the onboarding inside the ledger directory;
  - an exclusion that would hide the project folder or the onboarding;
  - duplicate names, ports or project folders;
  - derived bounds that break the write/exec invariant.

### D17. Processes [R21]
- **D17.1.** A launcher (`launch.py`) does the pre-start work, then starts one child process per project (`app.py --project NAME`). The pre-start work:
  - checks the config;
  - asks about missing ledger directories;
  - checks the ports.

  Children get no stdin. Their output is relayed, each line prefixed with the project's name.
- **D17.2.** Each child ends from its UI's End session button. Ctrl+C in the launcher's console reaches every child, and each closes its ledger session.
- **D17.3.** A killed launcher leaves its children running on Windows. Each can still be ended from its UI. [finding 2026-09-29]
- **D17.4.** The console prompt accepts `y` or `yes`, and treats anything else, or no console, as no. A leading byte-order mark is ignored. [finding: PowerShell's pipe added one, so a piped "y" read as no]

### D18. Derived bounds and fixed denials [R23, R24, R25]
- **D18.1.** Bounds come from the project, in the notation, with `/` = the read root, L = `ledger/` and S = `scripts/`:
  - **governor:** `fs.read / !excl !L`; `fs.write <project> !L !S`; `ledger.read *`; `ledger.write gov/`;
  - **owner ceiling:** the same reads and writes, plus `fs.exec S`; `ledger.write work/`;
  - **pilot:** as the ceiling, with `ledger.write pilot/`.
- **D18.2.** The file tools have three fixed denials, applied whatever the bounds say:
  - L is refused to every scope;
  - writes into S are refused;
  - writes outside the project folder are refused.

  A mistaken bound therefore cannot open any of them. Listings hide L.
- **D18.3.** The write/exec invariant is judged with exclusions. A write entry and an exec entry overlap when one covers the other, unless the outer scope excludes the inner entry. This keeps `fs.write <project> !S` with `fs.exec S` valid, and still refuses real overlaps. [derived from R25.3]

### D19. Per-project state [R21.2]
- **D19.1.** Ledgers live at `<project>/ledger/<ledger name>/`. Session stamps may coincide across projects started in the same second; they are in different ledgers.
- **D19.2.** Codex state lives in `.runtime/<project>/<session>/<agent>/`, so projects never share it.
- **D19.3.** Each server records its own `~/.codex` diff. Servers running at once overlap in time, and their diffs show that only as `while_running`.

### D20. Prompts [R22, R23, R24]
- **D20.1.** Every agent is told:
  - its project;
  - the project folder (full path and notation);
  - the read root;
  - the closed ledger directory;
  - the scripts directory and how scripts arrive;
  - the project's onboarding file by its full path.
- **D20.2.** The governor is told it writes for the project's organization and governance, never the owners' tasks. [founder 2026-09-29]
- **D20.3.** The UI names the project in its header and title.

---

## Open at time of writing
- **The onboarding goes stale.** A fixed file must be updated by hand when NIMOI releases a new version (the founder's choice).
- **The read root must contain the project folder.** A project outside its read root is refused, not supported.
- **A killed launcher leaves its children running** (D17.3).
- **Live checks covered Claude agents only.** They were one server alone and two servers side by side, with a governor writing and being refused the ledger directory. GPT agents in a project ran only in the offline tests (the real codex binary with a fake model), not live.
