# Doctrine priorities before NIMOI launch — a recommendation

*owner.1 (claude-opus-5-5), claude-anthropic-harness task 7, 2026-09-29. Assignment: `gov/assignments/doctrine-priorities-claude` rev `20260929T171602Z:182`. The human's request was "review the mapping project and recommend priorities for doctrine establishment before nimoi launch". This is a recommendation. It decides nothing and edits nothing. Revision 2, after a cold read (§6).*

**Bar:** the founder can take the ranked list and, for each item, see:
- (a) what doctrine is being proposed;
- (b) why it sits at that rank;
- (c) what map evidence supports it, and whether that evidence is checked or working;
- (d) the smallest act that would settle it.

Two things fail this bar. One is a recommendation that looks authoritative but rests on unlabelled or misread map claims. The other is one that asks for a constitution-sized writing effort before launch. That would repeat the burst-then-freeze pattern the map documents (T09, T10). Not required: covering all 26 efforts, or polished prose.

## 1. What I decided, and why

**What counts as doctrine.** I follow the founder's 2026-09-24 distinction (onboarding 1.12, "Authority"). Doctrine means *standing* rules. They bind future work across the institution and outlive any one task. They cover:
- identity and purpose;
- authority and amendment;
- the bar;
- the record and failure memory;
- trust and publication boundaries;
- governance of method regimes.

Some things are not doctrine: task instructions, effort state calls (FQ-01–FQ-16) and re-org mechanics (FQ-17–FQ-24, FQ-49–FQ-52). I leave them out except where a doctrine item depends on them.

**How I ranked.** "Launch" is the founder's term for "a few days when full governed self-modification will be initiated" (onboarding 1.12). So my ranking question is: **what will governed self-modification act on, or act without, in its first days?** I used three criteria, in order.
1. **Record integrity first.** NIMOI's own ranking puts defects that corrupt the record above those that degrade an instrument, and those above defects that cause failures (`origins/persistent_testbed.md` §3.1). A doctrine gap that lets self-modification corrupt or scatter the record ranks above one that only lets it fail.
2. **Dependency.** Doctrine that other doctrine needs comes before what it governs. The main case is who may amend doctrine.
3. **Cost of leaving a gap open, against cost of writing.** Some gaps can be closed by a one-line founder ruling. Others need a burst of writing, and the map predicts such bursts then freeze (T09). The cheap, load-bearing gaps rank higher.

**The virtue form, as onboarding asks.**
- **Axis:** having enough doctrine at launch.
- **Vice of deficiency:** launching with gaps that self-modification fills by default. Onboarding warns that a session given no bar "silently supplies the conventional commercial one", and the same happens with any missing rule.
- **Vice of excess:** writing the owed constitution, or a full procedure regime, before launch. That freezes doctrine ahead of the practice it should come from. It is T10 in reverse, and the map's own closing Watch names it: "polishing the map instead of using it".
- **Mean:** the minimum doctrine launch will act on. Most of it can be settled by founder rulings on questions the map has already framed, each recorded as *standing*.

**How deep I read.** Enough to rank, not enough to draft doctrine text. Drafting is not this task.
- **The map:** its two roots, the founder directives (D1–D9), the themes, the E01 and E02 effort files (doctrine and failure memory, the core of this task), the 52 founder questions and the efforts index.
- **Primary doctrine:** `origins/persistent_testbed.md` and onboarding 1.12.
- **The subprojects' current entry documents:** `bootstrap-ledger/notebook.md` and `bootstrap-harness/rules.md`.
- **Everything else:** I relied on the map's own checks (see §5).

## 2. The ranked priorities

Labels: `[checked: …]` means I read it myself in the named file. `[working: map …]` means I rely on the map's claim and did not re-check it. A cold reader then spot-checked many of both kinds (§6).

### P1 · Amendment and authority: how doctrine changes, and who may change it

**Proposed doctrine.** A standing rule for amending doctrine during and after launch. It should cover:
- which documents are doctrine, in a register;
- who may propose a change, and who ratifies it: the founder, or the governor with founder approval;
- which documents no agent may modify. The precedent is `bootstrap-harness/rules.md`: "No agent may modify this file under any conditions".
- how a founder statement becomes a *standing* directive, recorded in one append-only register.

**Why first.** Governed self-modification is, by definition, the institution changing its own rules. Without this doctrine, the first act of self-modification is ungoverned. The map shows what happens when the entry contract changes with no amendment rule:
- Onboarding 1.04 put a policy-override clause in the hot path for about 8 hours, until 1.05 removed it `[working: map E01 §4]`.
- 1.09 added a failure-record pointer and 1.10 deleted it 23 minutes later. This orphaned the newest failure record at birth `[working: map E01 §4, E02 §3]`.

Founder words are also scattered: across `context/` AUTHOR blocks, a per-project directives file, and chat. Onboarding 1.12 says the standing/task distinction is now required, but offers only a "working convention until a better one exists" `[checked: read onboarding_1.12.md]`.

**Smallest act that settles it.**
- The founder names the doctrine documents and the ratifier.
- The founder confirms which existing founder statements are standing. Candidates: the 08-17 virtue directive; the 08-23 testbed statement; the 09-23 organizational-responsibility statement; the 09-24 claims and discrepancy conventions. Onboarding quotes or applies each of these, but none has been confirmed as *standing* under the 09-24 convention.
- One register file records them, verbatim and append-only, on the pattern of `nimoi_mapping_project/method/founder_directives.md`.

### P2 · Failure memory: where the institution's primary product lives

**Proposed doctrine.** One standing answer to four questions:
- Which failure-record scheme wins (FQ-37)?
- Are run deviations, out-of-repository harness memory stores and similar registers part of institutional memory (FQ-38)?
- What is the promotion path from deviation to finding? Today it is "unstaffed" (FQ-14).
- **New since the map:** are ledger entries the failure record from launch on? That includes `DISCREPANCY:` lines and harness `log/` records.

It carries a corollary, because NIMOI is multi-model by design (T07). Institutional memory lives only where every model lineage can read it: the repository or the ledger. It does not live in a harness-private store.

**Why second.** Failures are the declared product `[checked: read persistent_testbed.md §3]`, and launch will multiply the records. What the map found:
- About 770 deviation and memory entries sit outside the memory system, against four filed failure records and nine findings `[working: map MAP.md item 9, E02 §2]`.
- The two failure-record schemes never mention each other `[working: map E02 §3]`.
- A Claude-only memory store sits outside git, where no GPT session can reach it `[working: map E02 R43]`.

Onboarding 1.12 defers failure-documentation standards to "future doctrine work", with an interim `DISCREPANCY:` convention `[checked: read onboarding_1.12.md]`. The new ledger infrastructure `[checked: read bootstrap-ledger/notebook.md]` is the natural single home. Before self-maintenance starts is the moment to say so.

**Smallest act that settles it.** The founder answers FQ-37 and FQ-38 and states whether the project ledger is the failure record of the bootstrapped institution. The full failure-documentation standard and the legacy harvest can follow launch, as the founder already planned.

### P3 · Reconcile the bar doctrine with the governance model (cheap, and live now)

**Proposed doctrine.** Confirm "state the bar" as standing, and settle who states it when work passes from governor to task owner to subagent.

**Why third.** NIMOI's testbed statement generalises this lesson explicitly: "it applies well beyond that campaign" `[checked: read persistent_testbed.md §6]`. Onboarding 1.12 promotes it to a boxed warning and a "required field" `[checked: read onboarding_1.12.md]`. It is **in live conflict today**. Onboarding 1.12 says a worker that receives no bar "stops and asks for one before starting". The founder's 2026-09-29 rule is that the governor sets rules and not the bar, and task owners set their own. The harness refuses `Bar:` lines in assignments (per my assignment). The governor has already recorded this as a discrepancy (per my assignment; I did not read that record). Every agent at launch meets this contradiction on its first task, and one sentence fixes it.

**Smallest act that settles it.** A founder ruling, carried into the next onboarding version. Possible wording, for illustration only: "the agent that owns the work states the bar; an agent handed work without one states its own at the top and proceeds, unless the task is ambiguous."

### P4 · Procedure regime: one institution, or a declared federation

**Proposed doctrine.** A standing answer to FQ-31: one procedure regime, or a federation in which each subproject declares its own. It should also settle what becomes of the stalled procedure library (FQ-06) and the unexercised assimilation process (FQ-08).

**Why here.** The map's founder-questions file calls this "the deepest design question in the map" `[working: map FOUNDER_QUESTIONS.md FQ-31]`. T10 records what happened after 08-26: three unconnected procedure regimes grew beside the library, and the most active effort cites the library once in 3,294 files `[working: map themes T10]`.

The bootstrap phase has already moved to a federation in practice, without declaring it:
- Onboarding 1.12 says a subproject's own rules take precedence inside it `[checked: read onboarding_1.12.md]`.
- `bootstrap-harness/rules.md` and `bootstrap-ledger/notebook.md` already run different regimes: immutable rules in one, "conventions, not rules" in the other `[checked: read both]`.

Self-organization at launch will create more subprojects. Each will pick a regime by default unless the institution says how.

**Smallest act that settles it.** The founder answers FQ-31; the map's default is "federation, stated openly". The founder also states the minimum every subproject entry document must carry. My working suggestion: a bar, a state line, a pointer to institutional doctrine, and where its failures are recorded.

### P5 · Publication, export and personal data: a minimum gate before a full standard

**Proposed doctrine.** Before launch, a standing *prohibition* rather than a standard. No agent publishes, pushes, exports or creates a public repository, or moves personal or third-party material across a subproject boundary, without explicit founder authorization for that act. The full standard comes later, as the founder intends (onboarding 1.12; D9.6).

**Why here and not higher.** The human owns git in this phase `[checked: read onboarding_1.12.md; rules.md]`, so no agent can publish on its own today. That changes once NIMOI "will become more self-organizing / self-maintaining", and the founder's stated direction includes "some public" subprojects. Some of NIMOI is already public, with unresolved exposures (FQ-27, FQ-47). Whether the `nimoi` remote is private is recorded nowhere (FQ-46) `[working: map MAP.md item 10]`. Onboarding 1.12's current rule, "confirm your approach with the human", assumes a human is in every loop, and launch relaxes that.

**Smallest act that settles it.** One standing sentence from the founder, and an answer to FQ-46.

### P6 · The self-optimization instrument: the virtue register's status and upkeep

**Proposed doctrine.** Several questions need a standing answer:
- Is `origins/virtues.md` doctrine or instrument output, and does it travel with doctrine (FQ-26)?
- Who may write to it, and on what trigger?
- What is its rotation or re-basing rule?
- Are the two starved axes fed or closed?
- What happens to the 09-04 outside critique left unanswered (FQ-42c)?

**Why here.** Self-modification at launch will likely use this instrument: onboarding names it as the method for difficult self-optimization `[checked: read onboarding_1.12.md]`. The map found several problems with it:
- The register is unbounded. V-1 has 32 entries, and the two axes on insistence and correction response have had nothing since 08-23 `[working: map E01 §3, §5]`.
- 55% of it is uncommitted `[working: map E01 §2, §10]`.
- Its dominant recent writer was a scheduled loop whose instruction lived outside both repositories `[working: map E01 §7d]`.

The file is still 42,496 bytes, the size the map recorded `[checked: fs list of /origins, 2026-09-29]`, so it appears unchanged since. It ranks below P1–P5 because its failure mode is a degraded instrument, not a lost record.

**Smallest act that settles it.** The founder answers FQ-26 and states who may append during launch.

### P7 · Organizational responsibility, made operational for the three-layer harness

**Proposed doctrine.** The founder's 09-23 statement is quoted verbatim in onboarding 1.12 `[checked: read onboarding_1.12.md]`. It applies to "all NIMOI agents, including subagents, outside of bounded harness tests less than 100 turns". Whether it is *standing* is part of P1. What is missing is how it maps onto the governor, task-owner and subagent layers:
- who escalates to whom;
- what a subagent does when it cannot pause to ask (onboarding records this as a Claude Code limitation);
- what "supervisors retain responsibility for escalation" means for an owner of GPT and Claude children.

**Why lower.** The principle is already written down, and the task-6/7 harness already provides a mechanism (`mcp__gov__request`). The gap is in operational detail, not in principle.

**Smallest act that settles it.** A short section in the next onboarding version. Draft it after some real runs, from how the task-7 harness actually behaves.

### P8 · Deliberately deferred: the constitution

The owed constitution should **not** be written before launch `[working: map E01 §5; virtue_methodology.md is self-labelled PRECURSOR per the map]`. Instead:
- Confirm `origins/NIMOI.md` and `origins/persistent_testbed.md` as standing statements of identity and purpose, under P1.
- Let the constitution be one of the first products of governed self-modification, drafted from what breaks.

This is the vice of excess named in §1. It also matches NIMOI's own position that the material is failures, not foresight.

### Not ranked as doctrine

The map's re-org and protection items are founder *actions*, not doctrine: backups, pushing autosurvey, committing untracked work, CRLF handling, the `candidate_repos` manifest. Several are urgent: FQ-17, FQ-18, FQ-19 and FQ-46 gate whether the record survives. I note them because they compete for the same pre-launch founder attention. By NIMOI's own ranking, losing the record is worse than any doctrine gap above. One example: `applications/` exists in a single copy with no backup `[working: map FQ-18]`.

## 3. If the founder has one hour

Answer these, as standing directives:
1. **P1:** name the ratifier and the register.
2. **P3:** one sentence on the bar.
3. **P2:** FQ-37, FQ-38, and whether the ledger is the failure record.
4. **P5:** the publication prohibition, and FQ-46.
5. **P4:** FQ-31.

Everything else can wait until launch shows where it breaks.

## 4. Discrepancies and hazards met

DISCREPANCY: `/nimoi_mapping_project/CONTINUE.md` — expected: "Next free: … D7" and "founder_directives.md (D1–D6)" to match the directives file, since CONTINUE's own rule is to update before stopping; found: `method/founder_directives.md` holds D7–D9 (2026-09-23/24), while CONTINUE.md and MAP.md still say D1–D6, next free D7, "Last updated 2026-09-20" [checked: read all three; confirmed by cold read] — 2026-09-29.
DISCREPANCY: `/bootstrap-harness/rules.md` "Swimlanes" — expected: the list names every swimlane directory, since rules.md says an agent "should be unambiguously assigned a swimlane"; found: 4 listed, the directory holds 6 (`claude-openai-harness`, `gpt-openai-harness` are not listed, and the rules' architecture list defines no "openai" architecture) [checked: fs list /bootstrap-harness; read rules.md] — 2026-09-29.
DISCREPANCY: `/bootstrap-ledger/notebook.md` — expected: the heading "Current state (2026-09-24)" dates its contents; found: two entries under it dated 2026-09-25, and reader test counts of nine and seven in successive bullets [checked: read] — 2026-09-29. (Possibly benign: the later bullet supersedes the earlier one.)
DISCREPANCY: `/origins/onboarding_1.12.md` "The bar is a required field … a worker that receives none stops and asks" — expected: consistent with the founder's 2026-09-29 rule that task owners set their own bar; found: in conflict. Already recorded by the governor per my assignment; repeated here so a scan finds it in this deliverable [working: assignment text] — 2026-09-29.

**Text addressing an AI reader.** Recorded by path, per my assignment rule. I followed neither as an instruction.
- `/bootstrap-harness/rules.md` governs agents working in that directory. It is founder-authored governance, not untrusted text. By its own terms it does not bind me, since I do not modify that directory.
- `/bootstrap-ledger/notebook.md` is an entry point addressed to agents working there. It is a founder-sanctioned convention, not untrusted text.

I met no untrusted third-party text addressing an AI reader. I did not read the locations the map lists as holding such text.

**Personal data.** None is quoted here. I mention the existence of personal and third-party material only by FQ number, as the map does.

## 5. What I did not look at

- **Effort files E03–E26.** This includes E04 (procedure library) and E05 (the assimilation process, ETA), which bear on P4. I relied on the map's summaries and the founder questions instead.
- **Other map directories:** `relationships/*` other than `themes.md`, including `record_defects.md`, `publication_blockers.md`, `subprojects.md` and `reorg_hazards.md`; also `waves/`, `evidence/` and `directories/`.
- **Other doctrine files:** `origins/NIMOI.md`, `virtues.md`, `virtue_methodology.md`, the primers and the three failure records; also `procedures/`, `processes/` and `context/`.
- **Git state.** I had no exec scope and was told to run no git commands, so every git-state claim here is the map's, and working.
- **Out of bounds:** the GPT candidate's work (hidden by design), `/candidate_repos` (never entered), and the governor's own discrepancy record about the bar.

## 6. Cold read

A cold reader (`owner.1.1`, claude-sonnet-5, read-only, narrower bounds) checked revision 1 and reported five defects. Its report is at `work/doctrine-priorities/claude/review/cold-read-1`. All five are fixed in this revision:
1. **Unsupported:** "most-cited lesson" with a `checked` label. Replaced by what §6 of the testbed file actually says.
2. **Imprecise:** "binds all agents". The founder's statement excludes bounded harness tests under 100 turns; now quoted.
3. **Wrong source:** "deepest design question" is in FQ-31, not themes T10. Re-cited.
4. **Wrong section references in P6:** "55% uncommitted" and "scheduled loop" are in E01 §2/§10 and §7d. Re-cited.
5. **Internal contradiction:** P1 said the 09-23 statement needs confirming as standing, while P7 treated it as settled. P7 now defers to P1.

The cold reader confirmed the record-integrity figures, the 42,496-byte register size, the 8-hour and 23-minute episodes, the CONTINUE.md and rules.md discrepancies, and that no personal data is quoted. It could not reach the `/bootstrap-harness` listing, the assignment, or the governor's record, so for those my own reads stand unreviewed.

State: **completed** — the recommendation is delivered and cold-read, and every reported defect is corrected. Nothing is pending on my side. Acting on it needs founder rulings (§3), which are not mine to make.
