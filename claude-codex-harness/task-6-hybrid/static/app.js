// NIMOI task 6: the page. A chat with the governor, and everything else visible.
// Events name their agent: "gov" (the governor, whose events go to the chat), task owners
// ("gov.1") and subagents ("gov.1.1"), whose events go to the activity column. Requests
// and the human's approvals are cards in the chat. To show a new kind of event: publish
// it in conversation.py or agent.py, add a renderer below. Events without a renderer are
// still shown, as a generic line.
"use strict";

const GOV = "gov";
const token = document.querySelector('meta[name="nimoi-token"]').content;
const $ = (id) => document.getElementById(id);
const chat = $("chat"), activity = $("activity");
const bubbles = new Map();   // "<agent>|<item id>" -> element, for streaming fragments
const agents = new Map();    // id -> list item in the Agents panel
const cards = new Map();     // request id -> card element
const running = new Set();   // task owners and subagents in a turn (Stop reaches them)
let ended = false;
let govState = "starting";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;  // never innerHTML: model text is untrusted
  return node;
}

const isGov = (e) => !e.agent || e.agent === GOV;
const logFor = (e) => (isGov(e) ? chat : activity);
const depth = (id) => (id.match(/\./g) || []).length;

function append(log, node) {
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.appendChild(node);
  if (atBottom) log.scrollTop = log.scrollHeight;
  return node;
}

function tag(node, e) {
  if (!isGov(e)) {
    node.classList.add("sub", `d${Math.min(depth(e.agent), 2)}`);
    node.prepend(el("span", "agent-tag", e.agent));
  }
  return node;
}

function eventLine(e, className, label, text) {
  const node = el("div", `event ${className}`);
  if (label) node.appendChild(el("span", "label", label));
  node.appendChild(document.createTextNode(text));
  return append(logFor(e), tag(node, e));
}

function fmt(n) { return n == null ? "–" : Number(n).toLocaleString(); }

function setStatus(text, className) {
  const status = $("status");
  status.textContent = text;
  status.className = `badge status ${className || ""}`;
}

function setComposer(state) {
  govState = state;
  const idle = state === "idle";
  $("input").disabled = !idle;
  $("send").disabled = !idle;
  $("end").disabled = state === "ended";
  updateStop();
  if (idle) $("input").focus();
}

function updateStop() {
  $("stop").disabled = ended || !(govState === "running" || running.size > 0);
}

function bubble(e, create) {
  const key = `${e.agent}|${e.item_id}`;
  let node = bubbles.get(key);
  if (!node && create) {
    node = append(logFor(e), tag(el("div", "msg agent"), e));
    bubbles.set(key, node);
  }
  return node;
}

function textOf(node) { return node.querySelector(".body") || node.appendChild(el("span", "body", "")); }

function setAgent(id, text) {
  let item = agents.get(id);
  if (!item) {
    item = el("li", `d${Math.min(depth(id), 2)}`);
    agents.set(id, item);
    $("agents").appendChild(item);
  }
  item.textContent = text;
}

// An agent's line in the Agents panel ends with its state: "<id> · … · <state>".
function agentState(id, state) {
  const item = agents.get(id);
  if (item) item.textContent = item.textContent.replace(/ · [^·]*$/, ` · ${state}`);
}

function updateApprovals() {
  const waiting = [...cards.values()].filter((c) => c.classList.contains("asked")).length;
  const badge = $("approvals-badge");
  badge.hidden = waiting === 0;
  badge.textContent = `${waiting} awaiting you`;
}

function requestCard(e) {
  let card = cards.get(e.request);
  if (!card) {
    card = append(chat, el("div", "card"));
    cards.set(e.request, card);
  }
  card.textContent = "";
  card.className = `card ${e.state}`;
  const head = `Request ${e.request} from ${e.owner} · ${e.request_kind} · ${e.state}`;
  card.appendChild(el("div", "label", head));
  card.appendChild(el("div", "", e.justification));
  if (e.details && Object.keys(e.details).length) {
    card.appendChild(el("pre", "mono small bounds", JSON.stringify(e.details, null, 1)));
  }
  if (e.governor_note) card.appendChild(el("div", "note", `Governor: ${e.governor_note}`));
  if (e.decision) {
    card.appendChild(el("div", "note",
      `Decided by ${e.decision.by}: ${e.decision.decision}${e.decision.message ? ` — ${e.decision.message}` : ""}`));
  }
  if (e.state === "asked_human" && !ended) {
    const note = el("input", "note-input");
    note.placeholder = "A note for the governor (optional)";
    const approve = el("button", "", "Approve");
    const deny = el("button", "secondary", "Deny");
    const decide = async (yes) => {
      approve.disabled = deny.disabled = true;
      const result = await post("/api/decide", { request: e.request, approve: yes, note: note.value });
      if (result.status !== 200) {
        approve.disabled = deny.disabled = false;
        card.appendChild(el("div", "note bad", result.body.error || "not decided"));
      }
    };
    approve.addEventListener("click", () => decide(true));
    deny.addEventListener("click", () => decide(false));
    const row = el("div", "actions");
    row.append(note, deny, approve);
    card.appendChild(row);
  }
  updateApprovals();
}

const renderers = {
  session(e) {
    $("model-badge").textContent = `governor ${e.model}${e.fake_model ? " (scripted fake)" : ""}`;
    $("s-model").textContent = e.model;
    $("s-engine").textContent = e.engine + (e.fake_model ? ", fake models" : "");
    $("s-stripped").textContent = `${e.host_agent_variables_removed} host-agent environment variables removed`;
    $("l-name").textContent = `${e.ledger.name} (${e.ledger.sessions} session file(s))`;
    $("l-session").textContent = e.ledger.session;
    $("l-harness").textContent = e.ledger.harness_author;
    $("l-agent").textContent = e.ledger.agent_author;
    $("l-file").textContent = e.ledger_file;
    $("b-bounds").textContent = JSON.stringify(e.bounds, null, 1);
    $("b-models").textContent = `Task owners: ${e.layer_models["task-owner"].join(", ")}. ` +
      `Subagents: ${e.layer_models.subagent.join(", ")}. At most ${e.limits.max_owners} task ` +
      `owners, ${e.limits.max_subagents} subagents (${e.limits.max_running} at once).`;
    const r = e.restrictions || {};
    const list = $("restrictions");
    list.appendChild(el("li", (r.unexpected_mcp_servers || []).length ? "fail" : "pass",
      (r.unexpected_mcp_servers || []).length ? `unexpected MCP servers: ${r.unexpected_mcp_servers.join(", ")}`
        : "governor: only this harness's MCP server"));
    list.appendChild(el("li", "pass", "no built-in tools; setting sources off; strict MCP; auto memory off"));
    list.appendChild(el("li", "note", "GPT agents are code-mode: JavaScript exec allowed (rule 5g); Codex's own sub-agent tools stop a turn"));
    $("t-ours").textContent = e.tools.join(", ");
    setAgent(GOV, `gov · governor · ${e.model} · idle`);
    eventLine(e, "quiet", "", `The governor is ready (${e.model}). Ledger session: ${e.ledger_file}`);
  },
  state(e) {
    setStatus(e.state, e.state === "running" ? "running" : "");
    setComposer(e.state);
    setAgent(GOV, `gov · governor · ${e.state}`);
  },
  user_message(e) { append(chat, el("div", "msg user", e.text)); },
  inbox(e) { eventLine(e, "inbox", "Harness → governor", e.text); },
  turn_started(e) {
    for (const key of [...bubbles.keys()]) if (key.startsWith(`${e.agent}|`)) bubbles.delete(key);
    if (!isGov(e)) { running.add(e.agent); updateStop(); agentState(e.agent, "running"); }
  },
  agent_delta(e) {
    const node = bubble(e, true);
    node.classList.add("streaming");
    textOf(node).textContent += e.delta;
  },
  agent_message(e) {
    const node = bubble(e, true);
    textOf(node).textContent = e.text;
    node.classList.remove("streaming");
    if (e.phase === "commentary") node.classList.add("commentary");
  },
  tool_call(e) {
    eventLine(e, e.success ? "tool" : "warn", `tool ${e.tool}`,
      `${JSON.stringify(e.arguments)} → ${e.output}`);
  },
  model_call(e) {
    if (e.reviewed) eventLine(e, "quiet", "", `model called ${e.name}`);
    else eventLine(e, "bad", "Unreviewed tool call", `${e.name}: ${e.payload}`);
  },
  tool_item(e) {
    if (e.item_type === "dynamicToolCall") return;  // shown by tool_call
    eventLine(e, e.execution ? "bad" : "warn", e.execution ? "Execution" : "Tool",
      `${e.item_type} (${e.status || "?"}): ${e.detail}`);
  },
  declined(e) { eventLine(e, "warn", "Declined", `${e.method}: ${e.detail}`); },
  agent_started(e) {
    eventLine(e, "spawn", `${e.layer === "task-owner" ? "Task owner" : "Subagent"} ${e.agent} started`,
      `by ${e.parent}; ${e.model} on ${e.engine}; [[${e.instructions}]]; bounds ${JSON.stringify(e.bounds)}`);
    setAgent(e.agent, `${e.agent} · ${e.layer} · ${e.model} (${e.engine}) · starting`);
  },
  agent_finished(e) {
    const tokens = e.token_usage_total ? `, ${fmt(e.token_usage_total.totalTokens)} tokens` : "";
    eventLine(e, ["done", "closed"].includes(e.state) ? "spawn" : "bad", `${e.agent} ${e.state}`,
      `${e.report ? `report: ${e.report}` : ""}${e.error ? ` · error: ${e.error}` : ""}${tokens}`);
    setAgent(e.agent, `${e.agent} · ${e.model} · ${e.state}${tokens}`);
    running.delete(e.agent);
    updateStop();
  },
  request(e) { requestCard(e); eventLine(e, "warn", `request ${e.request}`, `${e.request_kind}: ${e.justification}`); },
  approval(e) { requestCard(e); },
  request_decided(e) { requestCard(e); },
  bounds_granted(e) {
    eventLine(e, "spawn", `bounds granted (${e.request})`, `by ${e.by}: now ${JSON.stringify(e.bounds)}`);
  },
  usage(e) {
    if (!isGov(e)) return;  // others' totals are shown when they finish
    $("u-total").textContent = e.total ? `${fmt(e.total.totalTokens)} tokens` +
      (e.total.costUsd != null ? ` (running cost $${Number(e.total.costUsd).toFixed(4)})` : "") : "–";
  },
  claude_rate_limits(e) {
    if (!isGov(e)) return;
    $("limits").textContent = `Claude: ${e.status || "?"} (${e.rate_limit_type || "?"})` +
      (e.utilization != null ? `, ${e.utilization} used` : "") +
      (e.resets_at ? `, resets ${new Date(e.resets_at * 1000).toLocaleString()}` : "");
  },
  rate_limits(e) {
    if (isGov(e)) return;
    const w = e.primary;
    if (w) eventLine(e, "quiet", "", `rate limits: ${w.used_percent}% of ${w.window_mins} min window`);
  },
  turn_completed(e) {
    const seconds = e.duration_ms != null ? ` in ${(e.duration_ms / 1000).toFixed(1)} s` : "";
    if (e.status === "completed") eventLine(e, "quiet", "", `turn completed${seconds}`);
    else eventLine(e, "bad", `Turn ${e.status}`, e.error || "");
    for (const [key, node] of bubbles) {
      if (!key.startsWith(`${e.agent}|`)) continue;
      if (node.classList.contains("streaming") && e.status !== "completed") node.classList.add("cut");
      node.classList.remove("streaming");
    }
    if (!isGov(e)) {
      running.delete(e.agent);
      updateStop();
      agentState(e.agent, e.status === "completed" ? "idle" : e.status);
    }
  },
  notice(e) { eventLine(e, "warn", "Note", e.text); },
  error(e) {
    eventLine(e, e.will_retry ? "warn" : "bad", e.will_retry ? "Retrying" : "Error", e.message || "");
  },
  ended(e) {
    ended = true;
    source.close();
    setStatus("ended", "");
    setComposer("ended");
    for (const card of cards.values()) card.querySelectorAll("button, input").forEach((b) => { b.disabled = true; });
    const summary = Object.entries(e.summary.agents || {})
      .map(([id, a]) => `${id} ${a.state}, ${a.turns} turn(s)` +
        (a.unreviewed_calls.length ? `, UNREVIEWED ${a.unreviewed_calls.join(" ")}` : ""))
      .join("; ");
    eventLine(e, "quiet", "", `Conversation ended: ${summary}. Ledger session: ${e.ledger_file}. ` +
      "Relaunch ui.py for a new conversation.");
  },
};

function renderGeneric(e) {
  const { seq, t, type, ...rest } = e;
  eventLine(e, "quiet", type, JSON.stringify(rest));
}

const source = new EventSource(`/api/events?token=${encodeURIComponent(token)}`);
source.onmessage = (message) => {
  const event = JSON.parse(message.data);
  (renderers[event.type] || renderGeneric)(event);
};
source.onerror = () => { if (!ended) setStatus("reconnecting…", ""); };

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-NIMOI-Token": token },
    body: JSON.stringify(body || {}),
  });
  return { status: response.status, body: await response.json().catch(() => ({})) };
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("input").value.trim();
  if (!text) return;
  const result = await post("/api/send", { text });
  if (result.status === 202) $("input").value = "";
  else eventLine({}, "warn", "Not sent", result.body.error || `the governor is ${result.body.state}`);
});
$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("stop").addEventListener("click", () => post("/api/interrupt"));
$("end").addEventListener("click", async () => {
  if (!confirm("End this conversation? Every agent stops; a new one needs a relaunch.")) return;
  $("end").disabled = true;
  await post("/api/end");
});
