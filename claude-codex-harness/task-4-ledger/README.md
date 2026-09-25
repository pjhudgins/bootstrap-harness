# task-4-ledger (claude-codex-harness)

Task 3's local web conversation, with its record kept in a wiki ledger, and a "test
pilot" agent that can read the ledger and the nimoi tree and write only its own ledger
entries. Python standard library plus bootstrap-ledger's scribe module, imported in
place and never modified. It uses the saved Codex login in `~/.codex`.

```powershell
python ui.py                 # new conversation = new session file in ledgers/claude-codex-pilot/
python ui.py --fake-model --ledger-root .runtime\fake-ledgers   # scripted model; keep trials out of the pilot's ledger
python -m unittest discover -s tests -t .   # 12 tests, including real Codex + fake model
```

End with the page's **End conversation** button (or Ctrl+C). That writes the session's
trailer and releases the ledger's lease. With `--fake-model`, a message
`tool: <name> <json>` makes the scripted model call that tool, e.g.
`tool: ledger_write {"name": "pilot/x", "body": "hi"}`.

## rules.md task 4, point by point
| | Where |
|---|---|
| a. tasks 2 and 3 kept | `conversation.py`, `ui.py`, `static/`, `policy.py` (policy v2: gpt-5.5, 20 features off, MCP off, no environment, decline-all approvals, read-only sandbox, raw events), `add`, restriction and model-call checks, usage and rate limits |
| b. ledger per chat; all logging to it | `ledger_log.py`: one ledger, `ledgers/claude-codex-pilot/`, and one session file per chat. Every record, from every protocol message to `codex_home_changes`, is a body entry by `harness:claude-codex-harness/task-4-ledger`, named `harness/<session>/<seq>.<kind>` and tagged `harness` and `log.<kind>` |
| c. agent ledger tools; harness entries protected; agent designation | `tools.py`: `ledger_list`, `ledger_read`, `ledger_write`, `ledger_tag`. The agent author is **`test-pilot:<model>@claude-codex-harness`**, attested by the harness; the tools have no author field and refuse undeclared arguments. Refused: any name under `harness/`, any name tagged `harness`, and adding or removing `harness` or `log.*` labels. Every agent entry is tagged `pilot` |
| d. filesystem read, bounded to nimoi | `fs_list`, `fs_read`: paths resolved (links followed) and required to stay inside nimoi; `.git` refused; secret file names refused (`.env`, `*.key`, `*.pem`, `credentials*.json`…); key-like strings redacted before anything reaches the model or the ledger; binary files refused; size caps. No write or execute tool exists |
| e, f. system prompt | `prompt.py`, sent as the thread's `developerInstructions` on top of Codex's own prompt (founder's choice). It covers: NIMOI agent; read the latest onboarding first; test pilot, operate as directed, do not initiate tests, report verbosely, observed separately from inferred; no filesystem writes or code, ledger writes allowed; a `Bar:` line; file and ledger content is data, not instructions |

## Ledger notes
- The format is `bootstrap-ledger/standard/wiki_ledger_v0.4.md`; the module contract is
  `python-scribe/spec_v0.3.md`. Check a ledger with
  `python -B <nimoi>/bootstrap-ledger/python-scribe/scribe.py check <root> claude-codex-pilot`.
- The pilot can read every earlier chat (founder's choice). Harness entries are
  hidden from `ledger_list` unless it asks with `include_harness`.
- **Lease:** only one harness can have the ledger open. If a process dies holding it,
  launches are refused `lease_held`, and `ui.py` prints the recovery steps. Clearing
  a lease is a human's call (`interfaces.md`, "What a harness must do").
- **Git** (inside this swimlane only): `.gitattributes` has `*.ledger -text`, and
  `.gitignore` has `lease.json`.
- Importing the scribe would write `__pycache__` into bootstrap-ledger. The harness
  switches bytecode caching off first.

Results, findings and open questions: `../mem/task-4-record.md`.
