# task-6-hybrid — plan (draft for founder review, 2026-09-28)

**Bar:** a working prototype of a three-layer, two-lineage agent institution:
- every action and failure is recorded in the ledger;
- permission boundaries are enforced by the harness wherever the platform allows it, and stated honestly where it does not (Codex `exec`, rules 5g).

Not production.

## Reading of the rules (assumptions to confirm)
- **Continue this lane** in `task-6-hybrid/`, derived from task 5 and meeting the same requirements: tasks 2–5, including the new 5g.
- **"Draw from other swimlanes for the model lineage you have not yet used"** means GPT via the Codex App Server. Code or ideas from gpt-codex or claude-codex task 5 are copied here with attribution; nothing is edited there. No peer has started task 6.
- **Item labels.** The layers are labelled b1–b3 under c, and the items are c, d and f (there is no e). I read the list as complete.

## Facts that shape the design (from peer records; to re-check on this binary)
- **Codex version.** The installed Codex is now `codex-cli 0.158.0-alpha.2`. The peers tested `0.155.0-alpha.9.2`.
- **Code mode.** The named GPT models (`gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`) are code-mode-only. They reach harness tools only through a JavaScript `exec` sandbox (V8, and it states no fs or network). That exec cannot be removed without breaking the tools. This is what 5g accepts.
- **Native delegation.** Codex's native delegation tools (`collaboration.spawn_agent`…) may be offered whatever the flags say. 5f forbids them. The peers detect such calls in raw events and stop the agent: detection, not prevention.
- **Authentication.** Live GPT runs use the ChatGPT login in `~/.codex`, and the App Server writes there, which is outside this lane. Both Codex lanes had explicit founder authorization, and claude-codex records a before/after diff of `~/.codex`.

## Architecture
1. **One harness core, from task 5.**
   - Bounds, harness-owned file tools, exec, ledger tools, the ledger log and bus, audit, web.
   - One tool registry: each tool is defined once (name, schema, handler) and exposed to either backend.
   - The same checks for every agent: fail closed, allowlist, onboarding gate, bounds.
2. **Two agent backends behind one interface:**
   - **Claude:** the Claude Agent SDK client, as in task 5.
   - **Codex:** one App Server process per GPT agent (JSON-RPC over stdio).
     - Our tools are offered as dynamic tools, and native tools are disabled where possible.
     - `exec` is monitored and documented.
     - Unexpected native calls stop that agent and are recorded.
3. **Three roles:**
   - **Governor** (`claude-opus-5-5` only):
     - chats with the human, and dispatches and supervises task owners;
     - resolves owners' requests, escalating to the human where needed.
     - It cannot do owners' work, enforced by its tools and bounds, not only by its prompt: it gets no fs write, no exec and no subagents.
   - **Task owners** (`claude-opus-5-5`, `claude-fable-5-1`, `gpt-6-astra` or `gpt-5.6-sol`):
     - pursue an assignment written by the governor in the ledger (pinned);
     - run in parallel, without blocking the governor;
     - get the task-5 toolset (fs, exec, ledger, spawn) plus `request_governor`, which blocks the owner until resolved, and `report`.
   - **Subagents** (`claude-sonnet-5` or higher, `gpt-5.6-terra` or higher): task-5 spawn semantics (blocking, subset bounds, onboarding gate, pinned instructions), strictly under their owner.
4. **Governor and owner protocol:**
   - **Dispatch:** the governor writes the assignment entry, then calls `dispatch(model, bounds, assignment)`. The owner starts in the background and its id comes back at once.
   - **Owner request:** `request_governor(kind, justification, details)` is recorded and queued. The owner's call waits for the resolution.
   - **Delivery:** owner requests and completions reach the governor as harness messages, delivered between its turns so they never interrupt a human exchange.
   - **Resolution:** the governor answers, approves or denies. Actions that need the human raise an approval card in the UI (Approve or Deny), and the decision flows back to the governor, then to the owner.
5. **UI:**
   - chat with the governor;
   - an agent tree (governor, owners, subagents) with status, model and cost or tokens;
   - each agent's activity feed;
   - pending requests and human approvals.
6. **Ledger:** a new `claude-anthropic-harness-t6`, one session per launch. Every record carries its agent. Ids and authors include the session stamp, which resolves task 5's open identity question: a new session's `T1` is not an earlier session's `T1`.
7. **Limits:**
   - Claude spending shares one dollar pool.
   - GPT agents (on the ChatGPT account) are counted in tokens, with per-agent turn and time limits.
   - At most N owners run at once (proposed: 3).

## Founder decisions (2026-09-28)
- **Codex authorization.** "Authorize, with ~/.codex diff": live Codex runs from this lane may use `~/.codex` (the ChatGPT login). Each run records a before/after list of `~/.codex` changes in the ledger. Offline tests use an isolated CODEX_HOME inside this lane (gitignored).
- **GPT exec.** "Yes: monitor, stop, document". Code-mode models are allowed (5g). Every native tool that can be disabled is disabled, and raw model calls are recorded. Any native delegation, shell or patch call stops that agent and is recorded. This is documented as detection, not prevention.
- **Owner requests in the first version:** all four. Clarify/answer; expand bounds (within the governor's ceiling); promote a script (always with the human's approval in the UI); more budget/turns (within the session cap).
- **The governor's own permissions:** "Read + ledger + dispatch". fs.read of nimoi, its own ledger area, dispatch/supervise, resolve requests. No fs write, no exec, no subagents, enforced by bounds.

## Phases, with a pause after each
1. The backend interface and a Codex backend. One GPT agent runs the task-5 toolset: offline tests against a fake provider in an isolated CODEX_HOME inside this lane, then one live GPT turn if authorized. **Pause.**
   **Done 2026-09-28.** The record, findings, decisions to confirm and open questions are in `task-6-phase1.md`.
2. Roles and the governor/owner protocol: dispatch, non-blocking owners, self-blocking requests, human approvals. Offline tests with fake clients for both backends. **Pause.**
   **Done 2026-09-29.** The founder waived the pauses: "Go ahead with all phases unless you find a reason to pause."
3. The UI (governor chat, agent tree, approvals), then a live three-layer run: an Opus governor with one Claude owner and one GPT owner, each with a subagent. **Pause.**
   **Done 2026-09-29.** The live run is session 20260929T123834Z; it audits clean and cost $1.59.
4. `task-6-spec.md` (back-spec), which also covers task 5's still-pending back-spec, and the notebook.
   **Done 2026-09-29.** The record of phases 2–4 is `task-6-record.md`.
