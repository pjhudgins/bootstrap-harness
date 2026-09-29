# task-5-subagent (claude-codex-harness)

Task 4's test-pilot conversation, extended. The pilot can write files (only an exact
version of a ledger entry, only into a workspace) and run Python (only vetted scripts,
only from a scripts folder). It can also delegate to **harness-owned subagents** that
run in parallel, each within a subset of its parent's bounds. Python standard library
plus bootstrap-ledger's scribe module. It uses the saved Codex login in `~/.codex`.

```powershell
python ui.py                 # new conversation = new session file in ledgers/claude-codex-subagents/
python ui.py --fake-model --ledger-root .runtime\fake-ledgers --codex-home ..\.runtime\offline-codex-home
python -m unittest discover -s tests -t .   # 50 tests, including real Codex + fake model
python check_ledger.py --closed              # check the newest session after a run
```

End with **End conversation** (or Ctrl+C). That stops every agent, writes the trailer
and releases the lease. **Stop** interrupts the pilot's turn and every subagent that has
not finished; a subagent stopped before its turn began never starts it. With
`--fake-model`, each line `tool: <name> <json>` of a message is one scripted tool call;
that goes for a subagent too, whose message is its instructions.

## Bounds: one notation for every agent (rules 5b, 5e)
```json
{"fs.read": ["*"],
 "fs.write": ["bootstrap-harness/claude-codex-harness/task-5-subagent/workspace/"],
 "fs.exec": ["bootstrap-harness/claude-codex-harness/task-5-subagent/scripts/"],
 "ledger.read": ["*"], "ledger.write": ["agent/"]}
```
That is the root pilot's. Five keys, each a list of entries: paths relative to nimoi/,
or ledger names. An entry ending in `/` covers that folder or name and everything under
it; any other entry covers exactly itself; `"*"` is everything; `[]` or a missing key
is nothing. `ledger.write` also grants reading what it covers. A child is valid when
each of its entries lies inside one of its parent's, and when its `fs.write` stays out
of the folder of every `fs.exec` entry (an exact `fs.exec` file counts as its whole
folder). The same JSON appears in every agent's instructions, in `subagent_spawn`'s
arguments, in the ledger and on the page.

The fixed rules that no bounds change (always readable, never read, never written,
never run) are stated once, in [prompts/bounds.md](prompts/bounds.md), which every
agent's instructions include. `paths.py` and the tools enforce them.

## Modules
| | |
|---|---|
| `ui.py` | the page's HTTP server; binds its port exclusively before opening the ledger |
| `conversation.py` | the tree of agents: spawn, wait, Stop, caps, shutdown |
| `agent.py` | one agent: its app-server, thread and turn loop; message streams |
| `tools.py` | every tool, declared once each in `ledger_tools.py`, `fs_tools.py`, `subagent_tools.py`; `toolkit.py` offers, checks and dispatches them |
| `bounds.py`, `paths.py`, `runner.py` | the notation; the fixed filesystem rules; running one script |
| `prompt.py`, `prompts/*.md` | instructions for the pilot and subagents, from Markdown templates |
| `ledger_log.py`, `check_ledger.py` | the harness's record in the ledger; the post-run checker |
| `codex_client.py`, `policy.py`, `fake_model.py` | from tasks 2-4: the app-server client, the restriction policy, the offline model |

## The record (record format 2)
One session file per conversation. Every entry the harness writes has exactly one
label. Harness records are `harness/<session>/<seq>.<kind>`, labelled `log.<kind>`;
message texts are `transcript/<session>/<n>-<agent>-<role>` by their writers, labelled
`transcript.<role>`; script output is `exec/<session>/<n>-<script>` by the harness,
labelled `exec`. Agents write under `agent/`.

| kind | when |
|---|---|
| `run`, `summary`, `codex_home_changes` | a conversation's start and end |
| `agent_started`, `restrictions` | an agent's thread starts, with its bounds, tools and instructions |
| `message` | a text to or from an agent: links `[[transcript/...]]`, or, for a subagent's task, the exact version of its instructions entry, with that version's author |
| `message_stream` | per agent message: how many fragments streamed, when, and whether they add up to the text (the fragments themselves are not recorded) |
| `tool_call`, `model_call`, `tool_item`, `declined` | what the model called and what came back |
| `fs_write_started` → `fs_write` | a file write: intent first, then the result |
| `python_exec_started` → `python_exec` | a script run: intent first, then the result, linking its `exec/` output |
| `subagent_spawn` → `subagent_finished` | a subagent's life, with its report |
| `stop`, `interrupt`, `turn` | Stop on the page and whom it reached; a turn interrupted, and why (`before_start`: it never began); a turn's end |
| `send`, `recv`, `stderr`, `spawn`, `exit` | the app-server protocol, as sent and received |
| `usage`, `response_usage`, `rate_limits` | tokens and limits |

## rules.md task 5, point by point
| | How |
|---|---|
| a. tasks 2–4 kept | policy v2, `add`, the model-call checks (an unreviewed call now stops the turn), usage and rate limits, the page, the ledger (all logging, transcript by author, harness entries protected), fs read, the test-pilot prompt |
| b. bounds notation | `bounds.py`: five keys of entries, the subset test and the folder rule |
| c. fs write | `fs_write(id, path, replace_sha256)`: the body of one exact ledger entry version, byte for byte, to a path in `fs.write`. It creates new files; it replaces one only given that file's current sha256. The harness records the intent, then the result. |
| d. python execute | `python_exec(script, args)`: a `.py` in `fs.exec`, run isolated (`-I -B -X utf8`) with the workspace as working folder, a minimal environment, a 30 s timeout and output caps, inside a Windows job object that ends everything the script starts. The output is an `exec/…` entry by the harness, always readable to the agent that ran it. `scripts/hello_safe.py` is the safe test script. No agent can write to `scripts/`; promotion is human-only (`scripts/README.md`). |
| e. subagents | `subagent_spawn(model, bounds, instructions)`: the parent writes instructions to the ledger and passes the name; the harness pins the version current then, and the child's first message links to it. It returns an id at once; `subagent_wait(subagent, seconds)` collects the report. The child must read onboarding (enforced: its tools stay shut until it has), then runs one turn. |
| f. not Codex's own | each subagent is a separate Codex app-server process with its own thread, owned by the harness. Models are limited to direct-tool models, which have no Codex sub-agent tools. |

Limits: nesting depth 2, at most 6 subagents per conversation and 3 at once. A turn is
interrupted after 5 minutes without any event, or at its cap: 30 minutes for the
pilot, 15 for a subagent. Agent ids show the tree (`pilot`, `pilot.1`, `pilot.1.2`),
and so do authors (`subagent:<model>@claude-codex-harness#pilot.1`).

Results, decisions and findings: `../mem/task-5-record.md`.
