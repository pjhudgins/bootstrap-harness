// task-6-hybrid front end (from task 5's, which came from task 3's). Every record from /api/events
// is a ledger entry; its ledger id is shown at the right of each activity line.
//   left:   the agent tree (governor, task owners, subagents) with state and usage; what waits
//           for the human (approval cards)
//   centre: the conversation with the governor (or, with --pilot, the single agent), including
//           the harness's messages to it
//   right:  activity, every record, for all agents or the one selected in the tree
// Extend by registering a renderer: renderers[kind] = (rec) => { ... }. A kind with no renderer
// still shows up as an activity line with its fields.
"use strict";

const $ = (id) => document.getElementById(id);
const chatLog = $("chat-log"), eventLog = $("event-log"), input = $("input"), tree = $("agent-tree");
const seen = new Set();
const agents = new Map();     // id -> {id, role, model, parent, state, lineage, cost, tokens, turns}
const requests = new Map();   // request id -> its request_open record
const cards = new Map();      // approval id -> card element
let lastState = null, mainAgent = null, harnessAuthor = null, selected = null;

// ---- helpers -----------------------------------------------------------------------------

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function stick(container, node) {
  const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
  container.appendChild(node);
  if (atBottom) container.scrollTop = container.scrollHeight;
}

function chat(cls, text) { stick(chatLog, el("div", cls, text)); }

const short = (v, n = 140) => { const s = typeof v === "string" ? v : JSON.stringify(v); return s && s.length > n ? s.slice(0, n) + "…" : (s || ""); };
const money = (x) => (x == null ? "n/a" : "$" + Number(x).toFixed(4));
const oneLine = (s) => String(s || "").replace(/\s+/g, " ");
const toolLabel = (name) => (name || "").replace(/^mcp__calc__/, "").replace(/^mcp__([a-z]+)__/, "$1.");
const isLink = (t) => typeof t === "string" && /^\[\[[^\]]+\]\]$/.test(t);

// The agents a record is about: its agent, and the owner or child it names.
const about = (rec) => [rec.agent, rec.owner, rec.child].filter(Boolean);

// One activity line: seq, kind, summary, ledger id; click to expand the full record.
function side(rec, summary, cls = "") {
  const d = el("details", "ev " + cls + (rec.ledger_error ? " error" : ""));
  d.dataset.agents = about(rec).join(" ");
  const s = el("summary");
  const lid = rec.id ? rec.id.split(":").pop() : "not in ledger";
  s.append(el("span", "seq", rec.seq), el("span", "kind", rec.kind), el("span", "who", rec.agent || ""),
           el("span", "text", summary), el("span", "lid", lid));
  s.title = rec.name ? `${rec.name}  (${rec.id})` : (rec.ledger_error || "");
  d.append(s);
  d.addEventListener("toggle", () => {
    if (d.open && !d.querySelector("pre")) d.append(el("pre", "", JSON.stringify(rec, null, 2)));
  });
  d.hidden = !visible(d);
  stick(eventLog, d);
}

function visible(node) { return !selected || (node.dataset.agents || "").split(" ").includes(selected); }

function select(id) {
  selected = id;
  $("activity-title").textContent = id ? `Activity · ${id}` : "Activity · all agents";
  for (const n of eventLog.children) n.hidden = !visible(n);
  drawTree();
  eventLog.scrollTop = eventLog.scrollHeight;
}

// ---- the agent tree ------------------------------------------------------------------------

function upsert(id, fields) {
  if (!id) return;
  const a = agents.get(id) || { id };
  for (const [k, v] of Object.entries(fields)) if (v !== undefined) a[k] = v;
  agents.set(id, a);
  drawTree();
}

function usageText(a) {
  if (a.lineage === "gpt") return `${a.tokens ?? 0} tokens`;
  return a.cost != null ? money(a.cost) : "";
}

function drawTree() {
  tree.replaceChildren();
  const children = (id) => [...agents.values()].filter((a) => a.parent === id);
  const roots = [...agents.values()].filter((a) => !a.parent || !agents.has(a.parent));
  const node = (a) => {
    const li = el("li", "agent" + (a.id === selected ? " selected" : ""));
    const row = el("button", "agent-row");
    row.type = "button";
    row.title = `Show ${a.id}'s activity`;
    row.append(el("span", "agent-id", a.id), el("span", "badge " + (a.state || ""), a.state || "…"));
    const meta = el("div", "agent-meta", [a.role, a.model, usageText(a), a.turns ? `${a.turns} turns` : ""]
      .filter(Boolean).join(" · "));
    row.append(meta);
    row.addEventListener("click", () => select(a.id === selected ? null : a.id));
    li.append(row);
    const kids = children(a.id);
    if (kids.length) { const ul = el("ul"); kids.forEach((k) => ul.append(node(k))); li.append(ul); }
    return li;
  };
  roots.forEach((a) => tree.append(node(a)));
}

// ---- approval cards ------------------------------------------------------------------------

function countApprovals() {
  const open = [...cards.values()].filter((c) => !c.classList.contains("decided")).length;
  $("approval-count").textContent = open ? `${open}` : "";
  $("approvals").querySelector(".empty").hidden = cards.size > 0;
}

function approvalCard(rec) {
  const req = requests.get(rec.request) || {};
  const details = req.details || {};
  const card = el("div", "card");
  card.append(el("div", "card-head", `${rec.approval} · ${rec.request_kind} · asked by ${rec.owner} (${rec.request})`));
  const what = el("div", "card-what");
  what.append("Promote ", el("code", "", rec.script), ` (${rec.bytes} bytes, sha256 ${String(rec.sha256).slice(0, 12)}…) from `,
              el("code", "", rec.source), " into the scripts directory, where agents can run it.");
  card.append(what);
  card.append(el("div", "label", "Owner's justification"), el("div", "quote", req.justification || "(not shown)"));
  card.append(el("div", "label", "Governor's assessment"), el("div", "quote", rec.assessment || ""));
  const script = el("details", "script");
  script.append(el("summary", "", "Script text (exactly what would be promoted)"), el("pre", "", details.text || "(not recorded)"));
  card.append(script);
  const note = el("input", "note");
  note.placeholder = "Note (optional)";
  const buttons = el("div", "card-buttons");
  const approve = el("button", "approve", "Approve"), deny = el("button", "deny", "Deny");
  approve.type = deny.type = "button";
  const decide = async (decision) => {
    approve.disabled = deny.disabled = true;
    const data = await post("/api/approve", { approval: rec.approval, decision, note: note.value });
    if (data.error) approve.disabled = deny.disabled = false;
  };
  approve.addEventListener("click", () => decide("approve"));
  deny.addEventListener("click", () => decide("deny"));
  buttons.append(note, approve, deny);
  card.append(buttons);
  cards.set(rec.approval, card);
  $("approvals").append(card);
  countApprovals();
}

// ---- renderers -----------------------------------------------------------------------------

// Message text arrives first as a `text` record (its own ledger entry); the `message` record
// that follows carries "[[name]]" in its place.
const renderers = {
  text(rec) {
    if (rec.name && rec.agent === mainAgent) {
      if (rec.direction === "from_agent") chat("bubble agent", rec.text);
      else if (rec.author === harnessAuthor) {  // harness events to the governor, between its turns
        const d = el("details", "bubble harness");
        const first = rec.text.split("\n")[0];
        d.append(el("summary", "", first.length > 200 ? first.slice(0, 200) + "…" : first), el("div", "full", rec.text));
        stick(chatLog, d);
      } else chat("bubble user", rec.text);
    }
    side(rec, `${rec.direction === "to_agent" ? "→" : "←"} ${rec.author} · ${short(rec.text, 90)}`,
         rec.agent === mainAgent ? "" : "said");
  },

  message(rec) {
    const m = rec.message || {};
    if (m._type === "prompt") {
      if (!isLink(m.text) && rec.agent === mainAgent) chat("bubble user", m.text);
      side(rec, `→ prompt ${isLink(m.text) ? m.text : ""}${rec.text_id ? " @ " + rec.text_id + " by " + rec.text_author : ""}`, "raw");
      return;
    }
    side(rec, `${rec.direction} ${m._type || ""}${m.subtype ? " " + m.subtype : ""}`, "raw");
  },

  tool_call(rec) {
    if (rec.phase === "pre") {
      if (rec.agent === mainAgent) chat("chip" + (rec.policy_allow ? "" : " deny"), `→ ${toolLabel(rec.tool_name)} ${short(rec.tool_input, 100)}`);
      side(rec, `${toolLabel(rec.tool_name)} ${short(rec.tool_input)} — ${rec.policy_reason}`, rec.policy_allow ? "allow" : "deny");
    } else if (rec.phase === "post") {
      const denied = JSON.stringify(rec.tool_response || "").includes("error: denied");
      if (rec.agent === mainAgent) chat("chip" + (denied ? " deny" : ""), `← ${short(rec.tool_response, 100)}`);
      side(rec, `${toolLabel(rec.tool_name)} → ${short(rec.tool_response)}`, denied ? "deny" : "allow");
    } else side(rec, `${toolLabel(rec.tool_name)} failed: ${short(rec.error)}`, "error");
  },
  tool_denied(rec) { side(rec, `${toolLabel(rec.tool_name)} — ${rec.reason}`, "deny"); },
  permission(rec) { side(rec, `${rec.allow ? "allow" : "deny"} ${toolLabel(rec.tool_name)} — ${rec.reason}`, rec.allow ? "allow raw" : "deny"); },

  status(rec) {
    if (rec.agent !== mainAgent && mainAgent) { side(rec, rec.state); return; }
    lastState = rec.state;
    const b = $("state");
    b.textContent = rec.state.replace("_", " ") + (rec.queued ? ` · ${rec.queued} queued` : "");
    b.className = "badge " + rec.state;
    $("cost").textContent = `${money(rec.session_cost_usd)} / $${Number(rec.budget_usd).toFixed(2)}`;
    upsert(rec.agent, { state: rec.state });
    setComposer(rec.state);
    if (rec.state === "over_budget") chat("bubble error", `The launch budget of $${Number(rec.budget_usd).toFixed(2)} is spent. End the session and relaunch for a new one.`);
    if (rec.state === "failed" || rec.state === "stopped") chat("bubble error", `The ${rec.agent} session is ${rec.state}.`);
    side(rec, `${rec.state} · turn ${rec.turn}${rec.queued ? ` · ${rec.queued} queued` : ""}${rec.events_waiting ? ` · ${rec.events_waiting} events waiting` : ""}`, "raw");
  },

  session_start(rec) {
    mainAgent = rec.agent;
    harnessAuthor = rec.harness_author;
    $("mode").textContent = rec.role === "governor" ? "governor" : "test pilot";
    $("model").textContent = rec.model;
    $("ledger").textContent = `ledger ${rec.ledger} · session ${rec.ledger_session}`;
    const a = rec.account || {};
    $("account").textContent = [a.subscriptionType, a.apiProvider].filter(Boolean).join(" · ");
    input.placeholder = rec.role === "governor" ? "Message the governor. Enter sends, Shift+Enter adds a new line."
                                                : "Message the agent. Enter sends, Shift+Enter adds a new line.";
    upsert(rec.agent, { role: rec.role, model: rec.model, lineage: rec.lineage });
    side(rec, `${rec.role} ${rec.model} (${rec.backend}) as ${rec.agent_author} · bounds: ${oneLine(rec.bounds)}`);
  },

  owner_start(rec) {
    upsert(rec.owner, { role: "owner", model: rec.model, parent: rec.agent, state: "starting" });
    chat("chip", `⤷ ${rec.agent} dispatched ${rec.owner} (${rec.model}) on ${rec.assignment} @ ${rec.assignment_id}`);
    side(rec, `${rec.owner} · ${rec.model} · ${rec.assignment} · bounds: ${oneLine(rec.bounds)}`, "allow");
  },
  owner_session(rec) {
    upsert(rec.agent, { lineage: rec.lineage });
    side(rec, `${rec.agent} (${rec.backend}) as ${rec.agent_author} · tools ${rec.tools.map(toolLabel).join(", ")}`);
  },
  owner_state(rec) {
    upsert(rec.owner, { state: rec.state, cost: rec.cost_usd, tokens: rec.tokens, turns: rec.turns });
    side(rec, `${rec.owner} ${rec.state} · ${rec.turns} turns · max ${rec.max_turns} rounds/message`, "raw");
  },
  owner_end(rec) {
    upsert(rec.owner, { state: rec.status.startsWith("failed") ? "failed" : rec.status });
    chat("chip" + (rec.status === "closed" ? "" : " deny"), `⤶ ${rec.owner} ${rec.status}${rec.reason ? " (" + rec.reason + ")" : ""}`);
    side(rec, `${rec.owner} ${rec.status} · ${rec.turns} turns · onboarding ${rec.onboarding_read ? "read" : "NOT read"}`, rec.status === "closed" ? "allow" : "warn");
  },
  owner_error(rec) { side(rec, rec.error, "error"); },
  owner_stop_requested(rec) { side(rec, `${rec.owner}: ${rec.reason}`, "warn"); },

  request_open(rec) {
    requests.set(rec.request, rec);
    side(rec, `${rec.request} (${rec.request_kind}): ${short(rec.justification, 100)}`, "warn");
  },
  request_resolved(rec) {
    side(rec, `${rec.request} ${rec.decision}: ${short(rec.text, 100)}`, rec.decision === "denied" || rec.decision === "cancelled" ? "deny" : "allow");
  },
  approval_open(rec) {
    approvalCard(rec);
    chat("chip warn", `⚑ ${rec.approval}: ${rec.owner} asks to promote ${rec.script}; the governor forwarded it. See "Waiting for you".`);
    side(rec, `${rec.approval} → the human: ${rec.request_kind} ${rec.script}`, "warn");
  },
  human_decision(rec) {
    const card = cards.get(rec.approval);
    if (card) {
      card.classList.add("decided", rec.decision);
      card.querySelectorAll("button, input").forEach((b) => (b.disabled = true));
      card.querySelector(".card-head").textContent += ` · ${rec.decision} by you`;
      countApprovals();
    }
    side(rec, `${rec.approval} ${rec.decision} by the human${rec.note ? ": " + rec.note : ""}`, rec.decision === "approved" ? "allow" : "deny");
  },
  script_promoted(rec) { side(rec, `${rec.target} ← ${rec.source} (sha256 ${String(rec.sha256).slice(0, 12)}…)`, "allow"); },
  bounds_changed(rec) { side(rec, `${rec.owner}: ${oneLine(rec.new)}`, "allow"); },
  budget_changed(rec) { side(rec, `${rec.owner}: ${short(Object.fromEntries(Object.entries(rec).filter(([k]) => ["allowance_usd", "max_turns", "note"].includes(k))))}`, "allow"); },

  subagent_start(rec) {
    upsert(rec.child, { role: "subagent", model: rec.model, parent: rec.agent, state: "working" });
    side(rec, `${rec.child} · ${rec.model} · bounds: ${oneLine(rec.bounds)}`, "allow");
  },
  subagent_session(rec) {
    upsert(rec.agent, { lineage: rec.lineage });
    side(rec, `${rec.agent} (${rec.backend}) as ${rec.agent_author}`);
  },
  subagent_end(rec) {
    upsert(rec.child, { state: rec.status === "success" ? "done" : "failed", cost: rec.cost_usd, tokens: rec.tokens });
    side(rec, `${rec.child} ${rec.status} · onboarding ${rec.onboarding_read ? "read" : "NOT read"}`, rec.status === "success" ? "allow" : "error");
  },
  subagent_error(rec) { side(rec, rec.error, "error"); },
  onboarding_read(rec) { side(rec, `read ${rec.path}; the gate is open`, "allow"); },

  usage(rec) {
    upsert(rec.agent, { cost: rec.lineage === "claude" ? rec.agent_cost_usd : undefined, tokens: rec.agent_tokens, lineage: rec.lineage });
    if (rec.is_error && rec.agent === mainAgent) chat("bubble error", `Turn ${rec.turn} ended with ${rec.subtype}${rec.api_error_status ? " (API error " + rec.api_error_status + ")" : ""}.`);
    const cost = rec.lineage === "gpt" ? `${rec.agent_tokens} tokens` : `this turn ${money(rec.turn_cost_usd)} · session ${money(rec.session_cost_usd)}`;
    side(rec, `${rec.subtype} · ${rec.num_turns ?? "?"} rounds · ${cost}`, rec.is_error ? "error" : "");
  },
  turn_limit(rec) { side(rec, `${rec.reason}: ${short(rec)}`, "warn"); },
  turn_refused(rec) { side(rec, rec.reason, "warn"); },
  rate_limit(rec) {
    const i = rec.info || {};
    if (i.rate_limit_type) $("ratelimit").textContent = `${i.rate_limit_type}: ${i.status}`;
    side(rec, rec.error ? rec.error : short(i, 120), "raw");
  },

  // Codex (GPT agents)
  codex_rpc(rec) { side(rec, `${rec.direction === "to_codex" ? "→" : "←"} ${rec.rpc}`, "raw"); },
  codex_start(rec) { side(rec, `${rec.model} · ${rec.version} · exec host ${rec.exec_host ? "on" : "off"} · CODEX_HOME ${rec.live_home ? "~/.codex" : rec.codex_home}`); },
  codex_preflight(rec) { side(rec, `${(rec.account || {}).apiProvider} · skills off ${rec.skills_disabled.length} · plugins off ${rec.plugins_disabled.length} · MCP off ${rec.mcp_servers_disabled.length}`, "raw"); },
  codex_restrictions(rec) { side(rec, rec.caveat, "warn"); },
  codex_exit(rec) { side(rec, `exit ${rec.code}${rec.killed ? " (killed)" : ""}`, rec.code === 0 ? "" : "warn"); },
  codex_stderr(rec) { side(rec, rec.line, "warn"); },
  codex_home_changes(rec) { side(rec, `${rec.changes.length} changes under ${rec.codex_home}: ${short(rec.changes.map((c) => c.change + " " + c.path), 200)}`, "warn"); },
  model_call(rec) { side(rec, `${rec.call} ${rec.permitted ? "(permitted)" : "NOT PERMITTED"}`, rec.permitted ? "raw" : "error"); },
  native_call_stop(rec) {
    chat("bubble error", `${rec.agent} was stopped: ${rec.reason} (${rec.note}).`);
    side(rec, rec.reason, "error");
  },
  approval_declined(rec) { side(rec, rec.method, "deny"); },
  user_input_declined(rec) { side(rec, short(rec.questions), "warn"); },

  // Claude SDK details
  session_tools(rec) { side(rec, (rec.tools || []).map(toolLabel).join(", "), "raw"); },
  mcp_status(rec) { side(rec, rec.unexpected.length ? `UNEXPECTED: ${rec.unexpected.join(", ")}` : (rec.servers || []).map((s) => s[0]).join(", "), rec.unexpected.length ? "warn" : "raw"); },
  context_usage(rec) { side(rec, rec.error ? rec.error : `${rec.usage.totalTokens} tokens in context`, rec.error ? "error" : "raw"); },
  tool_inventory_mismatch(rec) {
    chat("bubble error", `${rec.agent}'s session reports tools it was not built with: extra ${rec.extra.join(", ") || "none"}; missing ${rec.missing.join(", ") || "none"}`);
    side(rec, `extra ${rec.extra.join(", ") || "-"} · missing ${rec.missing.join(", ") || "-"}`, "warn");
  },
  unexpected_tool_use(rec) { side(rec, `called ${rec.tool_name}, which it was not given`, "warn"); },
  cli_stderr(rec) { side(rec, rec.line, "warn"); },

  file_write(rec) { side(rec, `wrote ${rec.path} ← ${rec.entry} @ ${rec.entry_id} (${rec.bytes} bytes)`, "allow"); },
  file_write_failed(rec) { side(rec, `could not write ${rec.path}: ${rec.error}`, "error"); },
  exec(rec) {
    const how = rec.timed_out ? "TIMED OUT" : rec.overflowed ? "OUTPUT CAP" : `exit ${rec.exit_code}`;
    side(rec, `ran ${rec.script} → ${how} in ${rec.duration_ms} ms · ${short(rec.stdout, 80)}`, rec.exit_code === 0 && !rec.timed_out ? "allow" : "error");
  },

  ledger_findings(rec) { side(rec, rec.findings.join(" | "), "warn"); },
  shutdown_requested(rec) { chat("bubble error", "Session ending: task owners stop, and the ledger session closes."); side(rec, `via ${rec.via}`, "warn"); },
  turn_error(rec) { chat("bubble error", `${rec.agent}: ${rec.error}`); side(rec, rec.error, "error"); },
  session_error(rec) { chat("bubble error", `${rec.agent}: ${rec.error}`); side(rec, rec.error, "error"); },
};

function render(rec) {
  if (seen.has(rec.seq)) return;  // replay after reconnect
  seen.add(rec.seq);
  if (rec.ledger_error && !seen.has("ledger_error")) {
    seen.add("ledger_error");
    chat("bubble error", `Ledger write failed; records from here on are NOT in the ledger: ${rec.ledger_error}`);
  }
  const fallback = (r) => side(r, short(Object.fromEntries(Object.entries(r).filter(([k]) => !["seq", "ts", "kind", "turn", "id", "name", "agent"].includes(k)))));
  (renderers[rec.kind] || fallback)(rec);
}

// ---- wiring ------------------------------------------------------------------------------

function setComposer(state) {
  const open = state === "idle" || state === "busy";  // a message sent while busy waits its turn
  input.disabled = !open; $("send").disabled = !open; $("stop").disabled = state !== "busy";
  $("end").disabled = ["stopped", "failed", "ended", "disconnected", "starting"].includes(state);
  if (state === "ledger_failed") chat("bubble error", "The ledger can no longer record, so the harness has stopped acting (fail closed). End the session.");
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) chat("bubble error", `${path}: ${data.error || r.status}`);
  return data;
}

$("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value;
  if (!text.trim() || !["idle", "busy"].includes(lastState)) return;
  input.value = "";
  const data = await post("/api/send", { text });
  if (data.error) input.value = text;
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("stop").addEventListener("click", () => post("/api/interrupt", {}));
$("end").addEventListener("click", async () => {
  if (!confirm("End this session? Task owners stop, the ledger session closes and the server stops. Relaunch for a new conversation.")) return;
  $("end").disabled = true;
  await post("/api/shutdown", {});
});
$("show-all").addEventListener("click", () => select(null));
$("show-raw").addEventListener("change", (e) => document.body.classList.toggle("show-raw", e.target.checked));

let conversation = null;
const source = new EventSource("/api/events");
source.addEventListener("hello", (e) => {
  const id = JSON.parse(e.data).conversation;
  if (conversation !== null && id !== conversation) { source.close(); location.reload(); return; }  // server relaunched
  conversation = id;
});
source.onmessage = (e) => render(JSON.parse(e.data));
source.onerror = () => {
  const b = $("state");
  if (source.readyState !== EventSource.OPEN) { b.textContent = "disconnected"; b.className = "badge disconnected"; setComposer("disconnected"); }
};
