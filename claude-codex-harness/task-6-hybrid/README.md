# task-6-hybrid (claude-codex-harness)

Task 5's harness, grown into three layers on two engines (rules.md task 6). The human
chats with a **governor** (Claude Opus, on the Claude Agent SDK). The governor writes
tickets and dispatches **task owners** (Claude Opus/Fable on the SDK, or GPT Astra/Sol on
Codex App Server), which own their tickets and delegate to one-turn **subagents**
(Claude Sonnet or higher, GPT Terra or higher). A task owner asks the governor, blocking,
for what it may not do; the governor grants up to its own bounds, and the human approves
or denies anything beyond on the page. Design, founder answers and findings:
`../mem/task-6-plan.md`.

```powershell
python ui.py                                   # live: your Claude and Codex logins
python ui.py --fake-model --ledger-root .runtime\fake-ledgers   # scripted models, offline
python -m unittest discover -s tests -t .      # 55 tests, including both real engines offline
python check_ledger.py --closed                 # check the newest session after a run
```

`--fake-model` runs both engines against local stand-ins (`fake_model.py` for Codex,
`fake_claude.py` for the Claude CLI) with the swimlane's offline Codex and Claude homes:
no tokens, and neither `~/.codex` nor `~/.claude` is touched. Each line
`tool: <name> <json>` of a message is one scripted tool call, for any agent.

## The page (rule 6d)
Left: the chat with the governor, with the harness's news for it ("Harness → governor")
and a card for each request; a request the governor refers to you has Approve and Deny.
Middle: every task owner's and subagent's activity, tagged with its id (`gov.1`,
`gov.1.1`). Right: the agent tree, the ledger, the governor's bounds, restrictions and
usage. **Stop** interrupts every running turn and every unfinished subagent; task owners
stay open (the governor closes them). **End conversation** closes everything.

## Layers and tools (rule 6c)
| Layer | Models | Tools |
|---|---|---|
| governor `gov` | claude-opus-5-5 | read (fs, ledger), ledger writes (`tickets/`, `agent/`), `owner_start`, `owner_message`, `owner_status`, `owner_close`, `request_list`, `request_answer`. No file writes, scripts or subagents: it does not do task owners' work. |
| task owner `gov.N` | claude-opus-5-5, claude-fable-5-1, gpt-6-astra, gpt-6-sol | task 5's set (ledger, `fs_write`, `python_exec`, `subagent_spawn`/`subagent_wait`), `governor_request`, `governor_wait`. All offered from the start; the bounds decide each call, so a grant takes effect at once. |
| subagent `gov.N.M` | claude-sonnet-5, claude-opus-5-5, claude-fable-5-1, gpt-5.6-terra, gpt-5.6-sol, gpt-6-sol, gpt-6-astra | the tools its fixed bounds make usable (task 5); no subagents, no requests. |

The governor's bounds are task 5's root bounds plus `tickets/`: what it may delegate.
A task owner lives until the governor closes it: its ticket is its first turn, the
governor's messages its next ones, and each turn's report goes to the governor as a
harness message. The instructions of every agent come from `prompts/` (one template per
layer; the notation and fixed rules once, in `prompts/bounds.md`).

## Requests (rule 6f)
`governor_request(kind, justification, …)`: `bounds`, `promote_script`, `question`,
`other`. The request is a ledger entry by the task owner (`requests/…`); the call blocks
until it is decided (or says "pending", and `governor_wait` waits again). The governor
decides with `request_answer`: `grant` (bounds only, and only inside its own bounds),
`refuse`, `answer`, or `ask_human`. Asked, the human approves or denies on the page; the
harness then applies the grant (even beyond the governor's bounds, never past the fixed
rules) or promotes the script (a `.py` from the workspace copied byte for byte into the
scripts folder, never over an existing script), and records who decided.

## Engines
- **Codex** (`codex_agent.py`, from task 5): one app-server per agent. Rule 5g: the GPT
  models allowed here are all code-mode, so JavaScript `exec` (and `wait`,
  `clock.sleep`) is allowed and recorded; everything else task 5 switched off stays
  off. Codex's own `collaboration.*` sub-agent tools **cannot be switched off** for
  these models: a call to one stops the turn (5f). Documented in `policy.py`.
- **Claude** (`claude_agent.py`, new; from claude-anthropic-harness and
  gpt-anthropic-harness): one Claude CLI per agent, all on one asyncio loop; one driver
  task per client. No built-in tools, no setting sources, strict MCP config, auto
  memory off, prompts verbatim, no session persistence; the init tool inventory is
  checked, and a hook refuses anything that is not ours.
- **Environment:** the harness removes every `CLAUDE*`, `ANTHROPIC_*` and `MCP_*`
  variable from its own environment first, so no agent starts as the child of a Claude
  session it happens to be launched from (their names are recorded, never values).
- Tool calls may block for minutes on both engines (a 75 s call was tested on each);
  the idle limit pauses while one of our tools runs.

## The record
As task 5 (record format 2: one label per entry, streamed fragments summarised per
message in `message_stream`), with these kinds added:

| kind | when |
|---|---|
| `owner_start` → `owner_closed`, `owner_close`, `owner_report` | a task owner's life, the governor's close, each turn's report |
| `request` (links `requests/…`, by the task owner) → `request_decided` | a request and who decided it |
| `approval_asked`, `bounds_granted`, `script_promoted` | referred to the human; a grant (by whom, before, after); a promotion (source, target, sha256) |
| `agent_init`, `result`, `claude_system`, `api_error`, `tool_denied` | the Claude CLI's init (its tool inventory), each turn's result, other system messages |
| `claude_home_changes`, `codex_home_changes` | every change under each home during the run |
| `model_request` | fake-model runs: tools offered and each new input item, summarised |

## rules.md, point by point
| | How |
|---|---|
| 5a-f | as task 5 (see `../task-5-subagent/README.md`), for task owners and subagents on both engines |
| 5g | `policy.py` and `codex_agent.py`: code-mode models keep exec, recorded; collaboration tools stop the turn |
| 6a | derived from task 5's revised harness: same tools, notation, record, onboarding gate |
| 6b | the Claude engine drawn from the two Anthropic lanes (isolation recipe, SDK findings) |
| 6c | three layers with their own models, tools and instructions |
| 6d | the page: chat with the governor, everyone else visible |
| 6f | `governor_request` / `request_answer` / the page's approvals |
