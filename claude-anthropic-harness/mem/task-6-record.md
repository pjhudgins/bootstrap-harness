# task-6-hybrid — record of phases 2–4 (2026-09-29)

**Bar:** a working three-layer, two-lineage prototype, with every action and failure on the record. Boundaries are enforced where the platform allows, and stated honestly where it does not. Not production.

**State:** completed; awaiting founder review. Phase 1 is in `task-6-phase1.md`. The back-spec for tasks 5 and 6 is `task-6-spec.md`.

## Founder direction received at the phase-1 pause (2026-09-29; recorded as task instructions)
1. **No harness-enforced `Bar:`.** Rationale, verbatim: "Task may be defined in a ticket or other instructions, or a complex continuation package. Optional for spawn - but there should be no bar passed governor->owner, the governor does not set the success bar for the owner, only the rules under which success can be achieved."
2. **Keep `.runtime`.**
3. **"Go ahead with all phases unless you find a reason to pause."**

How it was applied:
- The governor's prompt says it sets the rules, never the bar.
- The owner's prompt says the owner sets its own bar.
- The harness refuses an assignment or follow-up message from the governor that carries a `Bar:` line.
- The audit re-checks every dispatched assignment.
- Subagent briefs may carry a bar or not.

The first item is an interpretation that goes beyond the founder's words: the founder said there should be no bar, and the harness now enforces that.

## Phase 2: roles and the governor/owner protocol
- **`governance.py`.** `Institution`, with:
  - dispatch in the background;
  - owners, each with its own backend session and inbox;
  - the four request kinds;
  - human approvals;
  - events to the governor;
  - `status`, `message_owner`, `stop_owner` and `close_all`.
- **`session.py`.**
  - Human messages queue (up to 3) and go first.
  - Harness events are delivered between turns, merged into one message.
  - `decide()` handles approvals.
  - Scripted runs can name their author.
- **`agent.py`.** Per-agent `max_turns`, which both backends enforce and which can be raised during a turn. `tool_bounds` (owners' tools come from the ceiling), live `set_bounds`, and role labels on ledger entries.
- **Prompts.** `governor.md`, `owner.md`, and lineage-aware notes. Bounds files: `governor_bounds.txt` ("Read + ledger + dispatch") and `owner_ceiling.txt`.
- **`audit.py`.** Checks 8–10: owners, requests and promotions.
- **`ledgerlog.py`.** Reserved record fields are refused.
- **Tests.** `tests/test_governance.py`: 14 tests. Every test uses fake Claude clients, and the GPT-owner test also runs the real codex binary against the fake model. The suite totals 80.

## Phase 3: the UI, then a live three-layer run
- **UI** (`static/`, `web.py`):
  - three columns: the agent tree and the approval cards; the governor chat, with harness messages collapsible; activity, filterable by agent;
  - `POST /api/approve`;
  - `app.py` launches the governor by default, or task 5's single agent with `--pilot`.
- **Checked in the browser pane** with `tests/ui_demo.py`, fake agents playing a scripted scenario. What was verified:
  - dispatch;
  - a promotion request, forwarded to the human and approved from its card;
  - the governor told of the decision, and the owner finishing;
  - the page rebuilt after a reload;
  - the per-agent filter.

  Screenshots timed out (the pane was behind another window), so the checks read the page's content.
- **Found by the UI check.** `approval_open` passed a field called `name`, which the log's own `name` replaced on the page. `model_call` had the same defect in phase 1. The log now refuses the reserved names (`name`, `id`, `seq`, `ts`).
- **Live three-layer run:** see the next section.

## Live three-layer run: session 20260929T123834Z (audit ok)
The run was driven by `live_institution.py`. Its message was authored `developer:claude-code-session`, under the founder's authorization (a `run_driver` record says so). It asked the governor for:
- two owners in parallel, a Claude one (claude-fable-5-1) and a GPT one (gpt-5.6-sol);
- the same task for both: read `rules.md` and record a plain summary of task 6;
- in each owner, the reading and a first draft delegated to one subagent (claude-sonnet-5 and gpt-5.6-terra), which the owner then checks.

| Agent | Model | What it did | Usage |
|---|---|---|---|
| governor | claude-opus-5-5 | Wrote two pinned assignments under `gov/assignments/`. They carry no bar and say "the success bar is yours to set, per the founder". Dispatched both owners, reported to the driver, stopped each owner once it had reported, and wrote a run note (`gov/runs/20260929-two-owner-test`). | $0.44, 7 turns |
| owner.1 | claude-fable-5-1 | Wrote a brief (with a `Bar:`), spawned owner.1.1 with narrow bounds, read `rules.md` in full itself, and checked the draft line by line. It fixed an omission and removed an invented phrase. It recorded `work/claude-owner/task6-summary`, with its own `Bar:`, line citations and three `DISCREPANCY:` lines about `rules.md`. | $0.93 |
| owner.1.1 | claude-sonnet-5 | Onboarding, then the draft under `work/claude-owner/draft/`. | $0.21 |
| owner.2 | gpt-5.6-sol | Spawned owner.2.1 without `ledger.write`, so the draft came back in its reply. It recorded the draft and its own `work/gpt-owner/task6-summary`, with its own `Bar:`. | 73,745 tokens |
| owner.2.1 | gpt-5.6-terra | Onboarding, `rules.md`, the draft. | 37,816 tokens |

Claude spend was $1.59 of the $5 launch budget. There were no refusals, no requests, no stops and no turn limits. `~/.codex` changed in `logs_2.sqlite-wal` and `models_cache.json` (the same pattern as phase 1).

Findings from the run:
- **A subagent's budget ignored its owner's allowance (fixed).** The governor caught it from the record: owner.2.1 was logged with `budget_usd` 4.79, the whole remaining launch budget, while owner.1.1 had 1.50. A Claude owner with a $1.50 allowance could have given its subagent the whole launch budget. Now:
  - every owner has an allowance covering its subtree;
  - a Claude subagent's budget is capped by what is left of it;
  - GPT subagents carry no dollar budget;
  - the owner's turn check counts the subtree.

  There is a new test.
- **`tokens: 0` beside a real cost (fixed).** Claude children's results now say `tokens: null`; their tokens are not tracked. Reported by owner.1.
- **The governor searched for a "refusal" label that does not exist.** Its run note labels that claim `[working]`. The governor's prompt now lists the record kinds to read when supervising (`log.tool_denied`, `log.tool_call`, and others).
- **Onboarding's bar rule and the founder's direction pull against each other.** Onboarding 1.12 says a worker given no bar stops and asks; the founder's direction is that the governor passes none. It was resolved in practice by the owner prompt and by the governor's own assignment text ("yours to set"): both owners set their own bars and neither stopped. **For the founder:** whether onboarding should say so.
- **Discrepancies owner.1 recorded in `bootstrap-harness/rules.md`, reported here and not repaired (the file is the founder's):**
  - task 6 has no item "e";
  - item c's sub-items are labelled b1–b3;
  - fable, astra, sol and terra are never defined, nor is what "or higher" ranks by.

  The first two match this lane's own reading in `task-6-plan.md`.
- **`live_institution.py` exited 1.** The cause was a `KeyError` in its summary printout, which came after the owners had stopped, the `~/.codex` diff was recorded and the ledger was closed. Fixed.

## Phase 4: documentation
- `task-6-spec.md` is the back-spec of tasks 5 and 6. It builds on `task-4-spec.md` and is the pending task-5 back-spec, folded in.
- This record, `task-6-phase1.md`, and the notebook.

## Discrepancies
- DISCREPANCY: ledger `claude-anthropic-harness-t6`, session 20260928T190527Z (phase 1's live GPT turn). Expected: the message authored by whoever wrote it, which was the developer agent. Found: authored `human:session-user`, because `live_turn.py` sent it through the UI's path. Found 2026-09-29 while writing the three-layer driver. Not repaired. Since then, scripted runs author their messages `developer:claude-code-session` and announce themselves with a `run_driver` record.
- DISCREPANCY: records `model_call` (phase 1) and `approval_open` (phase 3). Expected: a field named `name` shown on the page as given. Found: the log's own `name` (the ledger entry name) replaced it on the record the UI receives; the ledger body kept the right value. Found through the UI demo, 2026-09-29. Fixed: the fields are renamed, and the log refuses reserved names.
