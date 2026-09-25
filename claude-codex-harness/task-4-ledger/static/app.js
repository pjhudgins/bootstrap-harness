// NIMOI task 3: the conversation page. It renders server events and posts user actions.
// To show a new kind of event: publish it in conversation.py, add a renderer below.
// Events without a renderer are still shown, as a generic line.
"use strict";

const token = document.querySelector('meta[name="nimoi-token"]').content;
const $ = (id) => document.getElementById(id);
const log = $("log");
const agentBubbles = new Map();  // item_id -> element, for streaming deltas
let ended = false;

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

function eventLine(className, label, text) {
  const node = el("div", `event ${className}`);
  if (label) node.appendChild(el("span", "label", label));
  node.appendChild(document.createTextNode(text));
  return append(node);
}

function fmt(n) { return n == null ? "–" : Number(n).toLocaleString(); }

function setStatus(text, className) {
  const status = $("status");
  status.textContent = text;
  status.className = `badge status ${className || ""}`;
}

function setComposer(state) {
  const idle = state === "idle";
  $("input").disabled = !idle;
  $("send").disabled = !idle;
  $("stop").disabled = state !== "running";
  $("end").disabled = state === "ended";
  if (idle) $("input").focus();
}

function check(list, ok, text, soft) {
  $(list).appendChild(el("li", ok ? "pass" : soft ? "note" : "fail", text));
}

const renderers = {
  session(e) {
    $("model-badge").textContent = `model ${e.model}${e.fake_model ? " (scripted fake)" : ""}`;
    $("s-model").textContent = `${e.model}${e.effort ? `, effort ${e.effort}` : ""}`;
    $("s-account").textContent = e.fake_model ? "none (fake model)"
      : `${e.account.type || "none"}${e.account.plan ? `, ${e.account.plan}` : ""}`;
    $("s-thread").textContent = e.thread_id;
    $("s-journal").textContent = e.journal;
    $("l-name").textContent = `${e.ledger.name} (${e.ledger.sessions} session file(s))`;
    $("l-session").textContent = e.ledger.session;
    $("l-harness").textContent = e.ledger.harness_author;
    $("l-agent").textContent = e.ledger.agent_author;
    const r = e.restrictions;
    const entry = r.catalog_entry || {};
    check("restrictions", r.model_tool_mode_direct,
      r.model_tool_mode_direct ? "model has direct tools: no JavaScript exec, no sub-agents"
        : `model is ${entry.tool_mode || "unknown"}: it has code execution (exec) or sub-agents`);
    check("restrictions", !r.mcp_with_tools.length,
      r.mcp_with_tools.length ? `MCP tools present: ${r.mcp_with_tools.join(", ")}`
        : `MCP servers off (${r.mcp_configured.join(", ") || "none configured"})`);
    check("restrictions", !r.features_still_on.length,
      r.features_still_on.length ? `server still reports on: ${r.features_still_on.join(", ")}`
        : "all disabled features reported off", true);
    check("restrictions", true, "approvals declined; read-only sandbox; no environment");
    const restricted = r.model_tool_mode_direct && !r.mcp_with_tools.length;
    const badge = $("restriction-badge");
    badge.textContent = restricted ? "restricted: no code execution" : "NOT fully restricted";
    badge.className = `badge ${restricted ? "ok" : "bad"}`;
    $("t-ours").textContent = e.tools.join(", ");
    for (const [name, why] of Object.entries(e.reviewed_tools)) {
      $("t-reviewed").appendChild(el("li", "", `${name}: ${why}`));
    }
    eventLine("quiet", "", `New conversation started (${e.model}). Ledger session: ${e.journal}`);
  },
  state(e) {
    setStatus(e.state, e.state === "running" ? "running" : "");
    setComposer(e.state);
  },
  user_message(e) { append(el("div", "msg user", e.text)); },
  turn_started() {
    // Bubbles from earlier turns are finished; never merge a new turn's text into them,
    // even if an item id repeats (the fake model's did).
    agentBubbles.clear();
  },
  agent_delta(e) {
    let bubble = agentBubbles.get(e.item_id);
    if (!bubble) {
      bubble = append(el("div", "msg agent streaming", ""));
      agentBubbles.set(e.item_id, bubble);
    }
    bubble.textContent += e.delta;
  },
  agent_message(e) {
    let bubble = agentBubbles.get(e.item_id);
    if (!bubble) {
      bubble = append(el("div", "msg agent"));
      agentBubbles.set(e.item_id, bubble);
    }
    bubble.textContent = e.text;  // the completed text replaces the streamed deltas
    bubble.classList.remove("streaming");
    if (e.phase === "commentary") bubble.classList.add("commentary");
  },
  tool_call(e) {
    eventLine(e.success ? "tool" : "warn", `Python tool ${e.tool}`,
      `${JSON.stringify(e.arguments)} → ${e.output}`);
  },
  model_call(e) {
    if (e.reviewed) {
      eventLine("quiet", "", `model called ${e.name}`);
    } else {
      eventLine("bad", "Unreviewed tool call", `${e.name}: ${e.payload}`);
    }
  },
  tool_item(e) {
    if (e.item_type === "dynamicToolCall") return;  // shown by tool_call
    eventLine(e.execution ? "bad" : "warn", e.execution ? "Execution" : "Tool",
      `${e.item_type} (${e.status || "?"}): ${e.detail}`);
  },
  declined(e) { eventLine("warn", "Declined", `${e.method}: ${e.detail}`); },
  offered_tools(e) {
    const offered = $("t-offered");
    offered.hidden = false;
    offered.textContent = `Offered by Codex (fake model capture): ${e.names.join(", ")}` +
      (e.unreviewed.length ? `. NOT reviewed: ${e.unreviewed.join(", ")}` : "");
  },
  usage(e) {
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
    if (e.status === "completed") eventLine("quiet", "", `turn completed${seconds}`);
    else eventLine("bad", `Turn ${e.status}`, e.error || "");
    for (const bubble of agentBubbles.values()) {
      // a reply still streaming when the turn ends was cut off (Stop, or a failure)
      if (bubble.classList.contains("streaming") && e.status !== "completed") {
        bubble.classList.add("cut");
      }
      bubble.classList.remove("streaming");
    }
  },
  notice(e) { eventLine("warn", "Note", e.text); },
  error(e) {
    eventLine(e.will_retry ? "warn" : "bad", e.will_retry ? "Retrying" : "Error", e.message || "");
  },
  ended(e) {
    ended = true;
    source.close();
    setStatus("ended", "");
    setComposer("ended");
    const s = e.summary;
    eventLine("quiet", "", `Conversation ended: ${s.turns} turn(s), ${s.tool_calls} Python tool ` +
      `call(s), unreviewed model calls: ${s.unreviewed_calls.length}. Ledger session: ${e.journal}. ` +
      "Relaunch ui.py for a new conversation.");
  },
};

function renderGeneric(e) {
  const { seq, t, type, ...rest } = e;
  eventLine("quiet", type, JSON.stringify(rest));
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
  else eventLine("warn", "Not sent", result.body.error || `the agent is ${result.body.state}`);
});
$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("stop").addEventListener("click", () => post("/api/interrupt"));
$("end").addEventListener("click", async () => {
  if (!confirm("End this conversation? A new one needs a relaunch of ui.py.")) return;
  $("end").disabled = true;
  await post("/api/end");
});
