// NIMOI task 5: the conversation page. It renders server events and posts user actions.
// Every event names its agent: "pilot" (the root) or a subagent ("pilot.1", "pilot.1.2").
// To show a new kind of event: publish it in conversation.py or agent.py, add a renderer
// below. Events without a renderer are still shown, as a generic line.
"use strict";

const ROOT = "pilot";
const token = document.querySelector('meta[name="nimoi-token"]').content;
const $ = (id) => document.getElementById(id);
const log = $("log");
const bubbles = new Map();  // "<agent>|<item id>" -> element, for streaming deltas
const subagents = new Map();  // id -> list item in the Subagents panel
const runningSubagents = new Set();
let ended = false;
let rootState = "starting";

const isSub = (e) => e.agent && e.agent !== ROOT;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;  // never innerHTML: model text is untrusted
  return node;
}

function append(node) {
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.appendChild(node);
  if (atBottom) log.scrollTop = log.scrollHeight;
  return node;
}

function tag(node, e) {
  if (isSub(e)) {
    node.classList.add("sub");
    node.prepend(el("span", "agent-tag", e.agent));
  }
  return node;
}

function eventLine(e, className, label, text) {
  const node = el("div", `event ${className}`);
  if (label) node.appendChild(el("span", "label", label));
  node.appendChild(document.createTextNode(text));
  return append(tag(node, e));
}

function fmt(n) { return n == null ? "–" : Number(n).toLocaleString(); }

function setStatus(text, className) {
  const status = $("status");
  status.textContent = text;
  status.className = `badge status ${className || ""}`;
}

function setComposer(state) {
  rootState = state;
  const idle = state === "idle";
  $("input").disabled = !idle;
  $("send").disabled = !idle;
  $("end").disabled = state === "ended";
  updateStop();
  if (idle) $("input").focus();
}

// Stop interrupts every running agent: the pilot's turn and any subagent still working.
function updateStop() {
  $("stop").disabled = ended || !(rootState === "running" || runningSubagents.size > 0);
}

function check(list, ok, text, soft) {
  $(list).appendChild(el("li", ok ? "pass" : soft ? "note" : "fail", text));
}

function bubble(e, create) {
  const key = `${e.agent}|${e.item_id}`;
  let node = bubbles.get(key);
  if (!node && create) {
    node = append(tag(el("div", "msg agent"), e));
    bubbles.set(key, node);
  }
  return node;
}

function textOf(node) { return node.querySelector(".body") || node.appendChild(el("span", "body", "")); }

const renderers = {
  session(e) {
    $("model-badge").textContent = `model ${e.model}${e.fake_model ? " (scripted fake)" : ""}`;
    $("s-model").textContent = `${e.model}${e.effort ? `, effort ${e.effort}` : ""}`;
    $("s-account").textContent = e.fake_model ? "none (fake model)"
      : `${e.account.type || "none"}${e.account.plan ? `, ${e.account.plan}` : ""}`;
    $("s-thread").textContent = e.thread_id;
    $("l-file").textContent = e.ledger_file;
    $("l-name").textContent = `${e.ledger.name} (${e.ledger.sessions} session file(s))`;
    $("l-session").textContent = e.ledger.session;
    $("l-harness").textContent = e.ledger.harness_author;
    $("l-agent").textContent = e.ledger.agent_author;
    $("b-bounds").textContent = JSON.stringify(e.bounds, null, 1);
    $("b-limits").textContent = `Subagents: depth ≤ ${e.limits.max_depth}, at most ` +
      `${e.limits.max_subagents} per conversation, ${e.limits.max_running} at once. ` +
      `Onboarding: ${e.onboarding}`;
    const r = e.restrictions;
    const entry = r.catalog_entry || {};
    check("restrictions", r.model_tool_mode_direct,
      r.model_tool_mode_direct ? "model has direct tools: no JavaScript exec, no Codex sub-agents"
        : `model is ${entry.tool_mode || "unknown"}: it has JavaScript exec or Codex sub-agents`);
    check("restrictions", !r.mcp_with_tools.length,
      r.mcp_with_tools.length ? `MCP tools present: ${r.mcp_with_tools.join(", ")}`
        : `MCP servers off (${r.mcp_configured.join(", ") || "none configured"})`);
    check("restrictions", !r.features_still_on.length,
      r.features_still_on.length ? `server still reports on: ${r.features_still_on.join(", ")}`
        : "all disabled features reported off", true);
    check("restrictions", true, "execution only of scripts/ via python_exec; writes only " +
      "of ledger entries into workspace/; approvals declined; no environment");
    const restricted = r.model_tool_mode_direct && !r.mcp_with_tools.length;
    const badge = $("restriction-badge");
    badge.textContent = restricted ? "bounded" : "NOT fully restricted";
    badge.className = `badge ${restricted ? "ok" : "bad"}`;
    $("t-ours").textContent = e.tools.join(", ");
    for (const [name, why] of Object.entries(e.reviewed_tools)) {
      $("t-reviewed").appendChild(el("li", "", `${name}: ${why}`));
    }
    eventLine(e, "quiet", "", `New conversation started (${e.model}). Ledger session: ${e.ledger_file}`);
  },
  state(e) {
    setStatus(e.state, e.state === "running" ? "running" : "");
    setComposer(e.state);
  },
  user_message(e) { append(el("div", "msg user", e.text)); },
  turn_started(e) {
    // This agent's earlier bubbles are finished; never merge a new turn's text into them.
    for (const key of [...bubbles.keys()]) if (key.startsWith(`${e.agent}|`)) bubbles.delete(key);
  },
  agent_delta(e) {
    const node = bubble(e, true);
    node.classList.add("streaming");
    textOf(node).textContent += e.delta;
  },
  agent_message(e) {
    const node = bubble(e, true);
    textOf(node).textContent = e.text;  // the completed text replaces the streamed deltas
    node.classList.remove("streaming");
    if (e.phase === "commentary") node.classList.add("commentary");
  },
  tool_call(e) {
    eventLine(e, e.success ? "tool" : "warn", `Python tool ${e.tool}`,
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
  subagent_started(e) {
    eventLine(e, "spawn", `Subagent ${e.agent} started`,
      `by ${e.parent}; model ${e.model}; instructions [[${e.instructions}]]; ` +
      `bounds ${JSON.stringify(e.bounds)}`);
    const item = el("li", "", `${e.agent}: running (${e.model})`);
    subagents.set(e.agent, item);
    $("subagents").appendChild(item);
    runningSubagents.add(e.agent);
    updateStop();
  },
  subagent_finished(e) {
    const tokens = e.token_usage_total ? `, ${fmt(e.token_usage_total.totalTokens)} tokens` : "";
    eventLine(e, e.state === "done" ? "spawn" : "bad", `Subagent ${e.agent} ${e.state}`,
      `${e.report ? `report: ${e.report}` : "no report"}${e.error ? ` · error: ${e.error}` : ""}` +
      `${tokens}${e.report_entry ? ` · [[${e.report_entry}]]` : ""}`);
    const item = subagents.get(e.agent);
    if (item) item.textContent = `${e.agent}: ${e.state} (${e.model}${tokens})`;
    runningSubagents.delete(e.agent);
    updateStop();
  },
  usage(e) {
    if (isSub(e)) return;  // a subagent's total is shown when it finishes
    $("u-last").textContent = e.last ? `${fmt(e.last.totalTokens)} tokens` : "–";
    $("u-total").textContent = e.total
      ? `${fmt(e.total.totalTokens)} (in ${fmt(e.total.inputTokens)}, cached ${fmt(e.total.cachedInputTokens)}, out ${fmt(e.total.outputTokens)})`
      : "–";
    $("u-context").textContent = e.context_window ? `${fmt(e.context_window)} window` : "–";
  },
  rate_limits(e) {
    const box = $("limits");
    box.textContent = "";
    for (const name of ["primary", "secondary"]) {
      const w = e[name];
      if (!w) continue;
      const resets = w.resets_at ? `, resets ${new Date(w.resets_at * 1000).toLocaleString()}` : "";
      const window = w.window_mins ? ` of ${w.window_mins} min window` : "";
      box.appendChild(el("div", "", `${name}: ${w.used_percent}% used${window}${resets}`));
      const meter = box.appendChild(el("div", "meter"));
      meter.appendChild(el("span")).style.width = `${Math.min(100, w.used_percent || 0)}%`;
    }
    if (!box.childNodes.length) box.textContent = `no windows reported (${e.source})`;
  },
  turn_completed(e) {
    const seconds = e.duration_ms != null ? ` in ${(e.duration_ms / 1000).toFixed(1)} s` : "";
    if (e.status === "completed") eventLine(e, "quiet", "", `turn completed${seconds}`);
    else eventLine(e, "bad", `Turn ${e.status}`, e.error || "");
    for (const [key, node] of bubbles) {
      if (!key.startsWith(`${e.agent}|`)) continue;
      // a reply still streaming when the turn ends was cut off (Stop, or a failure)
      if (node.classList.contains("streaming") && e.status !== "completed") node.classList.add("cut");
      node.classList.remove("streaming");
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
    const agents = Object.entries(e.summary.agents || {})
      .map(([id, a]) => `${id} ${a.state}, ${a.turns} turn(s), ${a.tool_calls} tool call(s)` +
        (a.unreviewed_calls.length ? `, UNREVIEWED ${a.unreviewed_calls.join(" ")}` : ""))
      .join("; ");
    eventLine(e, "quiet", "", `Conversation ended: ${agents}. Ledger session: ${e.ledger_file}. ` +
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
  else eventLine({}, "warn", "Not sent", result.body.error || `the agent is ${result.body.state}`);
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
