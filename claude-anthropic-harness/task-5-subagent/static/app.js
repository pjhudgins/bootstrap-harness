// task-4-ledger front end (from task-3-ui). Every record from /api/events is a ledger entry;
// its ledger id is shown at the right of each side-panel line.
// Extend by registering a renderer: renderers[kind] = (rec) => { ...add to chat and/or side... }.
// A kind with no renderer still shows up in the side panel as raw JSON.
"use strict";

const $ = (id) => document.getElementById(id);
const chatLog = $("chat-log"), eventLog = $("event-log"), input = $("input");
const seen = new Set();
let lastState = null;

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

// One side-panel line: seq, kind, summary, ledger id; click to expand the full record.
function side(rec, summary, cls = "") {
  const d = el("details", "ev " + cls + (rec.ledger_error ? " error" : ""));
  const s = el("summary");
  const lid = rec.id ? rec.id.split(":").pop() : "not in ledger";
  s.append(el("span", "seq", rec.seq), el("span", "kind", rec.kind), el("span", "text", summary), el("span", "lid", lid));
  s.title = rec.name ? `${rec.name}  (${rec.id})` : (rec.ledger_error || "");
  d.append(s);
  d.addEventListener("toggle", () => {
    if (d.open && !d.querySelector("pre")) d.append(el("pre", "", JSON.stringify(rec, null, 2)));
  });
  stick(eventLog, d);
}

const short = (v, n = 140) => { const s = typeof v === "string" ? v : JSON.stringify(v); return s.length > n ? s.slice(0, n) + "…" : s; };
const money = (x) => (x == null ? "n/a" : "$" + Number(x).toFixed(4));
const toolLabel = (name) => (name || "").replace(/^mcp__calc__/, "").replace(/^mcp__ledger__/, "ledger.")
  .replace(/^mcp__fs__/, "fs.").replace(/^mcp__exec__/, "exec.").replace(/^mcp__agents__/, "agents.");

function setComposer(state) {
  const idle = state === "idle";
  input.disabled = !idle; $("send").disabled = !idle; $("stop").disabled = state !== "busy";
  $("end").disabled = ["stopped", "failed", "ended", "disconnected", "starting"].includes(state);
  if (state === "ledger_failed") chat("bubble error", "The ledger can no longer record, so the harness has stopped acting (fail closed). End the session.");
}

// ---- renderers ---------------------------------------------------------------------------

// Message text arrives first as a `text` record (its own ledger entry); the `message` record
// that follows carries "[[name]]" in its place. Text still inline in a message means the
// ledger write failed, so that text is drawn from the message instead.
const isLink = (t) => typeof t === "string" && /^\[\[[^\]]+\]\]$/.test(t);

const renderers = {
  text(rec) {
    const sub = rec.agent && rec.agent !== "pilot";
    if (rec.name) {
      if (sub) {  // a subagent's conversation: indented, labelled with who wrote it to whom
        const who = rec.direction === "to_agent" ? `${rec.author} → ${rec.agent}` : `${rec.agent} (${rec.author})`;
        const b = el("div", "bubble sub " + (rec.direction === "to_agent" ? "user" : "agent"));
        b.append(el("div", "who", who), el("div", "", rec.text));
        stick(chatLog, b);
      } else chat(rec.direction === "to_agent" ? "bubble user" : "bubble agent", rec.text);
    }
    side(rec, `${rec.agent || ""} ${rec.direction === "to_agent" ? "→" : "←"} ${rec.author} · ${short(rec.text, 80)}`);
  },

  subagent_start(rec) {
    chat("chip", `⤷ ${rec.agent} spawns ${rec.child} (${rec.model}, depth ${rec.depth}, budget $${Number(rec.budget_usd).toFixed(2)}) · instructions ${rec.instructions}`);
    side(rec, `${rec.child} · ${rec.model} · bounds: ${rec.bounds.replace(/\s+/g, " ")}`, "allow");
  },
  subagent_session(rec) { side(rec, `${rec.agent} as ${rec.agent_author} · tools ${rec.tools.map(toolLabel).join(", ")}`); },
  subagent_end(rec) {
    chat("chip" + (rec.status === "success" ? "" : " deny"), `⤶ ${rec.child} ${rec.status} · $${Number(rec.cost_usd).toFixed(4)} · onboarding ${rec.onboarding_read ? "read" : "NOT read"}`);
    side(rec, `${rec.child} ${rec.status} · ${rec.num_turns} turns · $${Number(rec.cost_usd).toFixed(4)} · session $${Number(rec.session_cost_usd).toFixed(4)}`, rec.status === "success" ? "allow" : "error");
  },
  subagent_error(rec) { side(rec, rec.error, "error"); },
  onboarding_read(rec) { side(rec, `${rec.agent} read ${rec.path}; the gate is open`, "allow"); },
  tool_denied(rec) { side(rec, `${rec.agent} ${toolLabel(rec.tool_name)} — ${rec.reason}`, "deny"); },

  message(rec) {
    const m = rec.message || {};
    if (m._type === "prompt") {
      if (!isLink(m.text)) chat("bubble user", m.text);
      if (rec.text_id) {  // a subagent's pinned instructions: linked to the parent's entry, not copied
        chat("chip", `→ ${rec.agent}: instructions ${m.text} @ ${rec.text_id} (by ${rec.text_author})`);
      }
      side(rec, `→ prompt ${isLink(m.text) ? m.text : ""}${rec.text_id ? " @ " + rec.text_id : ""}`, "raw");
      return;
    }
    if (m._type === "AssistantMessage") {
      for (const b of m.content || []) {
        if (b._type === "TextBlock" && b.text.trim() && !isLink(b.text)) chat("bubble agent", b.text);
        else if (b._type === "ToolUseBlock") chat("chip", `→ ${toolLabel(b.name)} ${short(b.input, 100)}`);
      }
    } else if (m._type === "UserMessage" && Array.isArray(m.content)) {
      for (const b of m.content) {
        if (b._type === "ToolResultBlock") chat("chip" + (b.is_error ? " deny" : ""), `← ${short(b.content, 100)}`);
      }
    }
    side(rec, `${rec.direction} ${m._type}${m.subtype ? " " + m.subtype : ""}`, "raw");
  },

  status(rec) {
    lastState = rec.state;
    const b = $("state");
    b.textContent = rec.state.replace("_", " ");
    b.className = "badge " + rec.state;
    $("cost").textContent = `${money(rec.session_cost_usd)} / $${Number(rec.budget_usd).toFixed(2)}`;
    setComposer(rec.state);
    if (rec.state === "idle") input.focus();
    if (rec.state === "over_budget") chat("bubble error", `Budget of $${Number(rec.budget_usd).toFixed(2)} reached. End the session and relaunch for a new one.`);
    if (rec.state === "failed" || rec.state === "stopped") chat("bubble error", `Agent session ${rec.state}.`);
    side(rec, `${rec.state} · turn ${rec.turn}`);
  },

  session_start(rec) {
    $("model").textContent = rec.model;
    $("ledger").textContent = `ledger ${rec.ledger} · session ${rec.ledger_session}`;
    const a = rec.account || {};
    $("account").textContent = [a.subscriptionType, a.apiProvider].filter(Boolean).join(" · ");
    side(rec, `${rec.model} as ${rec.agent_author} · ${rec.scribe_id} · ${rec.ledger_version} · session ${rec.ledger_sessions} of this ledger · budget $${rec.budget_usd} · bounds: ${rec.bounds.replace(/\s+/g, " ")}`);
  },

  file_write_started(rec) { side(rec, `${rec.agent} writing ${rec.path} ← ${rec.entry} @ ${rec.entry_id}`, "raw"); },
  file_write(rec) {
    side(rec, `${rec.agent} wrote ${rec.path} ← ${rec.entry} @ ${rec.entry_id} (${rec.bytes} bytes${rec.replaced_sha256 ? ", replaced" : ", new"}, sha256 ${String(rec.sha256).slice(0, 12)}…)`, "allow");
  },
  file_write_failed(rec) { side(rec, `${rec.agent} could not write ${rec.path}: ${rec.error}`, "error"); },

  exec_started(rec) { side(rec, `${rec.agent} running ${rec.script} ${short(rec.args, 60)} (sha256 ${String(rec.script_sha256).slice(0, 12)}…)`, "raw"); },
  exec(rec) {
    const how = rec.timed_out ? "TIMED OUT" : rec.overflowed ? "OUTPUT CAP" : `exit ${rec.exit_code}`;
    side(rec, `${rec.agent} ran ${rec.script} ${short(rec.args, 60)} → ${how} in ${rec.duration_ms} ms · ${short(rec.stdout, 80)}`,
         rec.exit_code === 0 && !rec.timed_out && !rec.overflowed ? "allow" : "error");
  },

  tool_inventory_mismatch(rec) {
    chat("bubble error", `${rec.agent}'s session reports tools it was not built with: extra ${rec.extra.join(", ") || "none"}; missing ${rec.missing.join(", ") || "none"}`);
    side(rec, `extra ${rec.extra.join(", ") || "-"} · missing ${rec.missing.join(", ") || "-"}`, "warn");
  },
  unexpected_tool_use(rec) { side(rec, `${rec.agent} called ${rec.tool_name}, which it was not given`, "warn"); },

  ledger_findings(rec) { side(rec, rec.findings.join(" | "), "warn"); },
  shutdown_requested(rec) { chat("bubble error", "Session ending: the ledger session will close."); side(rec, `via ${rec.via}`, "warn"); },
  session_tools(rec) { side(rec, (rec.tools || []).map(toolLabel).join(", ")); },

  tool_call(rec) {
    if (rec.phase === "pre") side(rec, `${toolLabel(rec.tool_name)} ${short(rec.tool_input)} — policy: ${rec.policy_reason}`, rec.policy_allow ? "allow" : "deny");
    else if (rec.phase === "post") {
      const denied = JSON.stringify(rec.tool_response || "").includes("error: denied");
      side(rec, `${toolLabel(rec.tool_name)} → ${short(rec.tool_response)}`, denied ? "deny" : "allow");
    } else side(rec, `${toolLabel(rec.tool_name)} failed: ${short(rec.error)}`, "error");
  },

  permission(rec) { side(rec, `${rec.allow ? "allow" : "deny"} ${toolLabel(rec.tool_name)} — ${rec.reason}`, rec.allow ? "allow" : "deny"); },

  usage(rec) {
    const u = rec.usage || {};
    if (rec.is_error) {
      const api = rec.api_error_status ? ` · API error ${rec.api_error_status}` : "";
      chat("bubble error", `Turn ${rec.turn} failed (subtype ${rec.subtype}${api})${rec.result ? ": " + rec.result : ""}`);
    }
    const tokens = `in ${u.input_tokens ?? "?"} · out ${u.output_tokens ?? "?"} · cache w ${u.cache_creation_input_tokens ?? 0} r ${u.cache_read_input_tokens ?? 0}`;
    side(rec, `${rec.subtype} · ${rec.num_turns} turns · this turn ${money(rec.turn_cost_usd)} · session ${money(rec.session_cost_usd)} · ${tokens}`, rec.is_error ? "error" : "");
  },

  cost_anomaly(rec) { side(rec, `reported total ${money(rec.reported)} is below previous ${money(rec.previous)}`, "warn"); },

  rate_limit(rec) {
    const i = rec.info || {};
    const reset = i.resets_at ? new Date(i.resets_at * 1000).toLocaleString() : "?";
    $("ratelimit").textContent = `${i.rate_limit_type}: ${i.status}`;
    side(rec, `${i.rate_limit_type} ${i.status}${i.utilization != null ? " · " + i.utilization : ""} · resets ${reset}`, i.status === "allowed" ? "" : "warn");
  },

  mcp_status(rec) {
    const names = (rec.servers || []).map((s) => `${s[0]} (${s[2]})`).join(", ");
    side(rec, rec.unexpected.length ? `UNEXPECTED: ${rec.unexpected.join(", ")} · ${names}` : names, rec.unexpected.length ? "warn" : "");
  },

  context_usage(rec) {
    if (rec.error) { side(rec, rec.error, "error"); return; }
    const cats = (rec.usage.categories || []).filter((c) => c.name !== "Free space").map((c) => `${c.name} ${c.tokens}`);
    const memory = rec.usage.memoryFiles || [];
    if (memory.length) chat("bubble error", `${rec.agent}'s context holds memory files: ${memory.map((f) => f.path).join(", ")}`);
    side(rec, `${rec.usage.totalTokens} tokens · ${cats.join(" · ")}${memory.length ? " · MEMORY FILES " + memory.length : ""}`,
         memory.length ? "warn" : "");
  },

  turn_error(rec) { chat("bubble error", rec.error); side(rec, rec.error, "error"); },
  session_error(rec) { chat("bubble error", rec.error); side(rec, rec.error, "error"); },
  cli_stderr(rec) { side(rec, rec.line, "warn"); },
};

function render(rec) {
  if (seen.has(rec.seq)) return;  // replay after reconnect
  seen.add(rec.seq);
  if (rec.ledger_error && !seen.has("ledger_error")) {
    seen.add("ledger_error");
    chat("bubble error", `Ledger write failed; records from here on are NOT in the ledger: ${rec.ledger_error}`);
  }
  (renderers[rec.kind] || ((r) => side(r, short(Object.fromEntries(Object.entries(r).filter(([k]) => !["seq", "ts", "kind", "turn", "id", "name"].includes(k)))))))(rec);
}

// ---- wiring ------------------------------------------------------------------------------

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) chat("bubble error", `${path}: ${data.error || r.status}`);
  return data;
}

$("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value;
  if (!text.trim() || lastState !== "idle") return;
  input.value = "";
  input.disabled = true; $("send").disabled = true;  // the status record re-enables
  const data = await post("/api/send", { text });
  if (data.error) { input.value = text; input.disabled = false; $("send").disabled = false; }
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
});

$("stop").addEventListener("click", () => post("/api/interrupt", {}));
$("end").addEventListener("click", async () => {
  if (!confirm("End this session? The ledger session closes and the server stops. Relaunch for a new conversation.")) return;
  $("end").disabled = true;
  await post("/api/shutdown", {});
});
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
