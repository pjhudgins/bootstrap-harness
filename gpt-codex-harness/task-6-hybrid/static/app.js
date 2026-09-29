"use strict";
const $ = id => document.getElementById(id);
let state, selected = "main", revision = -1, ended = false, sending = false, requestsKey = "";
const nodes = new Map();
function node(tag, text, className) {
  const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
  if (className) el.className = className; return el;
}
function error(text) { $("error").textContent = text || ""; $("error").hidden = !text; }
function controls() {
  $("prompt").disabled = ended || sending || state?.status !== "ready";
  $("send").disabled = $("prompt").disabled || !$("prompt").value.trim();
  $("try-task").disabled = $("prompt").disabled;
  $("working").hidden = state?.status !== "busy";
}
function disclosure(title, value, key) {
  const d = node("details"), summary = node("summary", title);
  d.dataset.key = key; d.append(summary, node("pre", typeof value === "string" ? value : JSON.stringify(value, null, 2))); return d;
}
function messages() {
  const items = state.messages.filter(m => m.actor === "main"), target = $("transcript");
  const nearBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 85;
  $("empty").hidden = items.length > 0;
  for (const m of items) {
    let el = nodes.get(m.id);
    if (!el) {
      el = node("article", undefined, "message " + m.role);
      el.append(node("div", m.role === "user" ? "You" : "Governor · Opus", "message-label"), node("div", "", "message-body"));
      nodes.set(m.id, el); $("messages").append(el);
    }
    el.lastChild.textContent = m.text;
  }
  if (nearBottom) target.scrollTop = target.scrollHeight;
}
function requests() {
  const key = JSON.stringify(state.requests); if (key === requestsKey) return;
  requestsKey = key; $("requests").replaceChildren();
  for (const r of state.requests) {
    const pending = r.status.startsWith("pending"), card = node("div", undefined, "request-card");
    if (!pending) continue;
    card.append(node("strong", `${r.owner} · ${r.status === "pending_human" ? "Your decision is needed" : "Waiting for governor"}`));
    card.append(disclosure("Owner's request", r.request.body, r.id));
    if (r.status === "pending_human") {
      card.append(node("p", r.decision.body, "approval-body"));
      card.append(node("p", "This decision records authorization. It does not change permissions or execute the action.", "small-note"));
      const comment = node("textarea"); comment.placeholder = "Optional comment"; comment.maxLength = 4000;
      comment.setAttribute("aria-label", `Comment for ${r.id}`); card.append(comment);
      const buttons = node("div", undefined, "approval-actions");
      for (const [approved, label] of [[true, "Approve"], [false, "Deny"]]) {
        const b = node("button", label, approved ? "send" : "quiet");
        b.addEventListener("click", async () => {
          for (const button of buttons.children) button.disabled = true;
          try { await post("/api/approval", {id:r.id, approved, comment:comment.value}); revision = -1; }
          catch (e) { error(e.message); for (const button of buttons.children) button.disabled = false; }
        }); buttons.append(b);
      }
      card.append(buttons);
    }
    $("requests").append(card);
  }
}
function family() {
  const target = $("agents"); target.replaceChildren();
  function append(agent, depth) {
    const b = node("button", undefined, "agent-card" + (selected === agent.id ? " selected" : ""));
    b.style.marginLeft = `${depth * 12}px`;
    const label = agent.role === "governor" ? "Governor" : agent.id;
    b.append(node("strong", `${label} · ${agent.status}`), node("span", agent.model));
    b.setAttribute("aria-pressed", String(selected === agent.id));
    b.addEventListener("click", () => { selected = agent.id; family(); detail(); }); target.append(b);
    for (const child of state.agents.filter(a => a.parent === agent.id)) append(child, depth + 1);
  }
  for (const a of state.agents.filter(a => !a.parent)) append(a, 0);
}
function detail() {
  const a = state.agents.find(a => a.id === selected); if (!a) return;
  const area = $("agent-detail");
  const open = new Set([...area.querySelectorAll("details[open]")].map(d => d.dataset.key)); area.replaceChildren();
  $("selected-label").textContent = `${a.role} · ${a.id}`;
  if (a.error) area.append(node("p", a.error, "error"));
  area.append(disclosure("Usage reported by provider", a.usage || "No usage yet.", "usage"));
  area.append(disclosure("Author, assignment and bounds", {author:a.author, instructions:a.instructions, bounds:a.bounds}, "bounds"));
  if (selected !== "main") {
    const texts = state.messages.filter(m => m.actor === selected).map(m => m.text);
    area.append(disclosure("Agent messages", texts.join("\n\n") || "No messages yet.", "messages"));
  }
  const calls = state.tools.filter(t => t.actor === selected);
  area.append(node("p", `${calls.length} completed tool calls`, "small-note"));
  for (let i = calls.length - 1; i >= 0; i--) {
    const t = calls[i];
    const d = disclosure(`${t.tool} · ${t.success ? "returned" : "refused"}`, {arguments:t.arguments, output:t.output}, `tool-${i}`);
    d.className = "tool" + (t.success ? "" : " failed"); area.append(d);
  }
  for (const d of area.querySelectorAll("details")) d.open = open.has(d.dataset.key);
}
function render(next) {
  state = next;
  const labels = {starting:"Connecting", ready:"Ready", busy:"Responding", error:"Needs attention", stopping:"Ending", stopped:"Ended"};
  $("status").textContent = labels[state.status] || state.status;
  $("status").className = "status " + state.status;
  error(state.error); controls(); messages(); requests(); family(); detail();
  $("limits").textContent = JSON.stringify(state.limits, null, 2);
  $("bounds").textContent = JSON.stringify(state.delegation_bounds, null, 2);
  $("workspace").textContent = state.workspace;
  $("restriction-note").textContent = state.notices.join("\n\n");
  $("session-id").textContent = state.run_id;
}
async function post(path, body) {
  const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json", "X-Nimoi-UI":"1"}, body:JSON.stringify(body)});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || "Request failed."); return result;
}
async function poll() {
  if (ended) return;
  try {
    const response = await fetch("/api/state", {cache:"no-store"}); if (!response.ok) throw new Error();
    const next = await response.json(); if (next.revision !== revision) { render(next); revision = next.revision; }
  } catch (_) { error("Local server unavailable. Check the driver terminal."); revision = -1; }
  if (!ended) setTimeout(poll, 500);
}
$("composer").addEventListener("submit", async e => {
  e.preventDefault(); if ($("send").disabled) return;
  sending = true; controls();
  try { await post("/api/messages", {text:$("prompt").value}); $("prompt").value = ""; revision = -1; }
  catch (problem) { error(problem.message); } finally { sending = false; controls(); }
});
$("prompt").addEventListener("input", controls);
$("prompt").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
$("try-task").addEventListener("click", () => {
  $("prompt").value = "Delegate to a GPT Astra task owner: use the add tool to calculate 19.25 + 22.75 and report the receipt. Bar: one checked arithmetic result, with failures recorded. Grant only onboarding reads and the ledger access needed for this assignment.";
  controls(); $("prompt").focus();
});
$("end-session").addEventListener("click", async () => {
  $("end-session").disabled = true;
  try { await post("/api/stop", {}); ended = true; state.status = "stopped"; controls(); $("status").textContent = "Ended"; }
  catch (problem) { error(problem.message); $("end-session").disabled = false; }
});
poll();
