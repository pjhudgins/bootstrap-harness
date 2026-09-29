# Task 6 — hybrid harness (work paused)

Bar: useful three-layer behavior, informative failures, and surviving records.

The prototype connects an Opus governor to task owners from Claude or Codex, and
their subordinate agents from either family. It derives from this lane's task 5,
with Claude SDK isolation patterns attributed in ../mem/task-6-record.md.

**Status: parked for founder guidance.** The first live run worked mechanically,
but the governor independently reviewed the owner's artifact and report wording,
contrary to its intended role. Rules.md requires escalation. The test driver is
stopped; the ledger is closed and retained. See the task record before continuing.

Entry point after authorization to resume:

```powershell
python -B -X utf8 serve.py --no-browser
```

Existing subscription logins are used; no API-key extraction. The governor is
claude-opus-5-5. Worker model allowlists are in roles.py. bounds.json narrows the
session ceiling. Governor operating tools omit add, fs_write and python_execute;
its delegation authority uses the ceiling, not its narrower operating toolset.
Owners and their subagents receive immutable subset bounds and pinned briefs.

An owner's request_governor call blocks until resolution or shutdown. Governor
decisions are pinned ledger entries; needs_human displays a concrete proposal in
the UI. Human approval records authorization, never mutates permissions or executes
an action. Current workers have one assignment turn; there is no ticket system.

Native SDK delegation is disabled and disallowed. Claude checks initialized model
and tool inventory and uses a pre-tool allowlist. Codex requests native tools off
and monitors raw calls after model output; Astra still requires JavaScript exec.
Approved Python scripts have normal process privileges, not OS isolation. Claude
usage includes SDK-internal model usage beyond the role's selected model; preserve
the distinction between assigned agent identity and provider implementation.

Offline verification (normal-terminal environment can import the installed SDK):

```powershell
python -B -X utf8 -m unittest discover -v
python -B -X utf8 audit_surface.py --model gpt-6-astra --real-config
python -B -X utf8 inspect_ledger.py <launch-name> --closed
```

The 16-test suite passed before the final streaming adjustment; four focused SDK
tests passed afterward. One real App Server/fake-provider capture and one live
Opus -> Astra -> Sonnet run were inspected. Final source and the other model-role
arrangement still need live verification. Details and all discrepancies:
../mem/task-6-record.md.
