"use strict";
const $ = id => document.getElementById(id);
const transcript = $("transcript"), prompt = $("prompt");
let currentStatus = "starting", lastRevision = -1, sending = false, ended = false;
const messageNodes = new Map();
const number = value => Number.isFinite(value) ? value.toLocaleString() : "—";

function error(text) { $("error").textContent = text || ""; $("error").hidden = !text; }
function controls() {
  prompt.disabled = currentStatus !== "ready" || sending || ended;
  $("send").disabled = prompt.disabled || !prompt.value.trim();
  $("try-add").disabled = prompt.disabled;
  $("working").hidden = currentStatus !== "busy";
}
function renderMessages(messages) {
  const nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 85;
  $("empty").hidden = messages.length > 0;
  for (const message of messages) {
    let node = messageNodes.get(message.id);
    if (!node) {
      node = document.createElement("article");
      const label = document.createElement("div"), body = document.createElement("div");
      label.className = "message-label"; body.className = "message-body";
      label.textContent = message.role === "user" ? "You" : "Agent";
      node.append(label, body); $("messages").append(node); messageNodes.set(message.id, node);
    }
    node.className = "message " + message.role + (message.phase === "commentary" ? " commentary" : "");
    const body = node.querySelector(".message-body");
    if (body.textContent !== message.text) body.textContent = message.text;
  }
  if (nearBottom) transcript.scrollTop = transcript.scrollHeight;
}
function windowName(minutes) {
  if (!Number.isFinite(minutes)) return "Allowance";
  if (minutes % 1440 === 0) return `${minutes / 1440}-day allowance`;
  if (minutes % 60 === 0) return `${minutes / 60}-hour allowance`;
  return `${minutes}-minute allowance`;
}
function renderLimits(buckets) {
  const target = $("limits"); target.replaceChildren();
  let count = 0;
  for (const [bucketId, bucket] of Object.entries(buckets || {})) {
    for (const key of ["primary", "secondary"]) {
      const window = bucket[key]; if (!window) continue;
      count++;
      const wrapper = document.createElement("div"), line = document.createElement("div");
      wrapper.className = "limit"; line.className = "limit-line";
      const label = document.createElement("span"), value = document.createElement("span");
      label.textContent = windowName(window.windowDurationMins);
      label.title = bucket.limitName || bucketId;
      value.textContent = Number.isFinite(window.usedPercent) ? `${window.usedPercent}% used` : "Unavailable";
      line.append(label, value); wrapper.append(line);
      if (Number.isFinite(window.usedPercent)) {
        const bar = document.createElement("progress"); bar.max = 100;
        bar.value = Math.min(100, Math.max(0, window.usedPercent));
        bar.setAttribute("aria-label", `${label.textContent}: ${value.textContent}`); wrapper.append(bar);
      }
      if (Number.isFinite(window.resetsAt)) {
        const reset = document.createElement("small");
        reset.textContent = "Resets " + new Date(window.resetsAt * 1000).toLocaleString([], {month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});
        wrapper.append(reset);
      }
      target.append(wrapper);
    }
  }
  if (!count) { const note = document.createElement("p"); note.className = "muted"; note.textContent = "Account limits unavailable."; target.append(note); }
}
function renderTools(tools) {
  $("tool-count").textContent = tools.length;
  if (!tools.length) return;
  const target = $("tools"); target.replaceChildren();
  for (const tool of tools) {
    const node = document.createElement("div"), title = document.createElement("div"), result = document.createElement("div");
    node.className = "tool" + (tool.success ? "" : " failed"); title.className = "tool-title"; result.className = "tool-result";
    title.textContent = `${tool.tool} · Python ${tool.success ? "returned" : "failed"}`;
    result.textContent = tool.tool === "add" && tool.success
      ? `${tool.arguments.a} + ${tool.arguments.b} = ${tool.output.sum}`
      : JSON.stringify(tool.success ? tool.output : {error:tool.output.error});
    node.append(title, result); target.append(node);
  }
}
function render(state) {
  currentStatus = state.status;
  const labels = {starting:"Connecting",ready:"Ready",busy:"Responding",error:"Needs attention",stopping:"Ending",stopped:"Ended"};
  $("status").textContent = labels[currentStatus] || currentStatus;
  $("status").className = "status " + currentStatus;
  $("model").textContent = state.model || "Connecting to Codex";
  $("session-id").textContent = state.run_id;
  error(state.error);
  renderMessages(state.messages); renderTools(state.tools); renderLimits(state.limits);
  const usage = state.usage?.total;
  $("total-tokens").textContent = number(usage?.totalTokens);
  $("input-tokens").textContent = number(usage?.inputTokens);
  $("cached-tokens").textContent = number(usage?.cachedInputTokens);
  $("output-tokens").textContent = number(usage?.outputTokens);
  if (state.notices.length) $("restriction-note").textContent = state.notices.join("\n\n");
  controls();
}
async function poll() {
  if (ended) return;
  try {
    const response = await fetch("/api/state", {cache:"no-store"});
    if (!response.ok) throw new Error("Could not read the conversation.");
    const state = await response.json();
    if (ended) return;
    if (state.revision !== lastRevision) { render(state); lastRevision = state.revision; }
  } catch (problem) {
    if (ended) return;
    currentStatus = "error"; $("status").textContent = "Disconnected";
    error("The local server is unavailable. Check its terminal, or relaunch the driver for a new conversation.");
    controls(); lastRevision = -1;
  }
  if (!ended) setTimeout(poll, 450);
}
async function post(path, body) {
  const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json","X-Nimoi-UI":"1"}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed.");
  return result;
}
$("composer").addEventListener("submit", async event => {
  event.preventDefault(); if (currentStatus !== "ready" || sending || !prompt.value.trim()) return;
  const text = prompt.value; sending = true; controls(); error(null);
  try { await post("/api/messages", {text}); prompt.value = ""; currentStatus = "busy"; lastRevision = -1; }
  catch (problem) { error(problem.message); }
  finally { sending = false; controls(); }
});
prompt.addEventListener("input", controls);
prompt.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("composer").requestSubmit(); }
});
$("try-add").addEventListener("click", () => { prompt.value = "Use the add tool to calculate 19.25 + 22.75."; controls(); prompt.focus(); });
$("end-session").addEventListener("click", async () => {
  $("end-session").disabled = true;
  try {
    await post("/api/stop", {}); ended = true; currentStatus = "stopped"; controls();
    $("status").textContent = "Ended"; $("working").hidden = true;
    error("Session ended. Your log is saved locally. Relaunch the driver for a new conversation.");
  } catch (problem) { error(problem.message); $("end-session").disabled = false; }
});
poll();
