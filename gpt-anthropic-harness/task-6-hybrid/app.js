const $ = id => document.getElementById(id);
let renderedRequests = '';
let revision = -1, sessionId = null, disconnected = false, ready = false, closing = false, sending = false;
const labels = {starting: 'Connecting', ready: 'Ready', thinking: 'Thinking…', stopping: 'Closing', closed: 'Closed', error: 'Needs attention'};
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
function availability() { $('send').disabled = !ready || sending || !$('prompt').value.trim(); $('prompt').disabled = !ready || sending; }
function number(n) { return n == null ? '—' : new Intl.NumberFormat().format(n); }
function render(state) {
  if (state.id !== sessionId) {
    sessionId = state.id; revision = -1; error(state.error);
  }
  ready = state.status === 'ready' && !closing;
  availability();
  if (state.revision === revision) return;
  revision = state.revision;
  $('status').textContent = labels[state.status] || state.status;
  $('model').textContent = state.model;
  $('account').textContent = state.account.subscriptionType || 'Existing Claude authentication';
  $('session-id').textContent = `Conversation ${state.id.slice(0, 8)}`;
  $('journal').textContent = state.journal;
  if (state.error) error(state.error);
  const transcript = $('transcript');
  const nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
  $('welcome').hidden = state.messages.length > 0;
  $('messages').replaceChildren(...state.messages.map(message => {
    const box = document.createElement('article'); box.className = `message ${message.role}`;
    if (message.actor && message.actor !== 'governor') box.classList.add('child-message');
    const role = document.createElement('p'); role.className = 'role'; role.textContent = message.role === 'user' ? 'You' : (message.label || 'Governor · Opus');
    const text = document.createElement('div'); text.className = 'text'; text.textContent = message.text;
    box.append(role, text); return box;
  }));
  if (nearBottom) transcript.scrollTop = transcript.scrollHeight;
  const money = value => value == null ? '—' : '$' + value.toFixed(5);
  $('family-cost').textContent = money(state.family_cost_usd);
  const children = Object.values(state.children || {});
  const expandedChildren = new Set([...$('children').querySelectorAll('details[open]')]
    .map(details => details.parentElement.dataset.agentId + ':' + details.dataset.kind));
  $('children').replaceChildren(...children.map(child => {
    const box = document.createElement('div'); box.className = 'child-job';
    box.dataset.agentId = child.agent_id;
    const title = document.createElement('strong'); title.textContent = `${child.role} · ${child.agent_id} · ${child.model} · ${child.status}`;
    const cost = document.createElement('p'); cost.className = 'small';
    cost.textContent = `SDK cost: ${money(state.agent_usage?.[child.agent_id]?.total_cost_usd)}`;
    const details = document.createElement('details');
    details.dataset.kind = 'bounds';
    details.open = expandedChildren.has(child.agent_id + ':bounds');
    const summary = document.createElement('summary'); summary.textContent = 'Instructions, bounds and result';
    const body = document.createElement('pre'); body.textContent = JSON.stringify({instructions_id: child.instructions_id,
      bounds: child.bounds, result: child.result || {name: child.result_name}, error: child.error}, null, 2);
    details.append(summary, body);
    const transcript = document.createElement('details');
    transcript.dataset.kind = 'messages';
    transcript.open = expandedChildren.has(child.agent_id + ':messages');
    const transcriptTitle = document.createElement('summary'); transcriptTitle.textContent = 'Agent messages';
    const messages = document.createElement('div'); messages.className = 'agent-transcript';
    messages.textContent = (state.agent_messages?.[child.agent_id] || []).map(m => m.text).join('\n\n');
    transcript.append(transcriptTitle, messages); box.append(title, cost, details, transcript); return box;
  }));
  if (!children.length) $('children').textContent = 'No owners dispatched.';
  const requestsKey = JSON.stringify(state.requests || {});
  if (requestsKey !== renderedRequests) {
  renderedRequests = requestsKey;
  const drafts = new Map([...$('requests').querySelectorAll('textarea')].map(t => [t.dataset.requestId, t.value]));
  $('requests').replaceChildren(...Object.values(state.requests || {}).map(request => {
    const card = document.createElement('article'); card.className = 'request-card';
    const title = document.createElement('strong'); title.textContent = request.request_id + ' · ' + request.owner_id + ' · ' + request.status;
    const text = document.createElement('p'); text.textContent = request.justification;
    card.append(title, text);
    if (request.governor_note) {
      const note = document.createElement('p'); note.textContent = 'Governor: ' + request.governor_note; card.append(note);
    }
    if (request.status === 'needs_human') {
      const input = document.createElement('textarea'); input.rows = 2; input.maxLength = 4000;
      input.placeholder = 'Explain your decision'; input.setAttribute('aria-label', 'Decision explanation for ' + request.request_id);
      input.dataset.requestId = request.request_id; input.value = drafts.get(request.request_id) || '';
      card.append(input);
      for (const decision of ['approve', 'deny']) {
        const button = document.createElement('button'); button.textContent = decision === 'approve' ? 'Approve request' : 'Deny request';
        button.addEventListener('click', async () => {
          if (!input.value.trim()) { error('Add a decision explanation.'); input.focus(); return; }
          button.disabled = true;
          try { await post('/api/decision', {request_id:request.request_id, decision, justification:input.value}); error(''); }
          catch(e) { error(e.message); button.disabled = false; }
        });
        card.append(button);
      }
    }
    return card;
  }));
  $('request-section').hidden = !Object.keys(state.requests || {}).length;
  }
  $('activity').replaceChildren(...state.activity.map(event => {
    const box = document.createElement('div'); box.className = 'item';
    const name = document.createElement('strong'); name.textContent = event.label;
    const text = document.createElement('span'); text.textContent = event.summary || event.reason || event.name || '';
    box.append(name, text); return box;
  }));
  if (!state.activity.length) $('activity').textContent = 'Tool calls will appear here.';
  if (state.usage) {
    const usage = state.usage.last_prompt;
    const inputParts = [usage.input_tokens, usage.cache_read_input_tokens, usage.cache_creation_input_tokens];
    $('input').textContent = inputParts.every(n => typeof n === 'number') ? number(inputParts.reduce((a,b) => a+b, 0)) : '—';
    $('cache').textContent = `Input includes ${number(usage.cache_read_input_tokens)} cache-read and ${number(usage.cache_creation_input_tokens)} cache-write tokens.`;
    $('output').textContent = number(state.usage.last_prompt.output_tokens);
    $('cost').textContent = state.usage.total_cost_usd == null ? '—' : '$' + state.usage.total_cost_usd.toFixed(5);
  } else {
    for (const id of ['input', 'output', 'cost']) $(id).textContent = '—';
    $('cache').textContent = '';
  }
  if (state.limits) {
    const l = state.limits;
    const reset = l.resets_at == null ? '' : ` · resets ${new Date(l.resets_at * 1000).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}`;
    $('limits').textContent = `${(l.type || 'Rate limit').replaceAll('_', ' ')}: ${l.status || 'unknown'}${reset}`;
  } else {
    $('limits').textContent = 'Governor rate information appears when available.';
  }
}
async function post(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
async function poll() {
  if (closing) return;
  try {
    const response = await fetch('/api/state');
    if (!response.ok) throw new Error('Unable to read conversation');
    if (disconnected) { error(''); disconnected = false; }
    render(await response.json());
  } catch (e) {
    disconnected = true;
    ready = false; availability(); $('status').textContent = 'Disconnected';
    error('The local server is unavailable. Reopen its current URL, or launch the Python driver for a fresh conversation.');
  }
  if (!closing) setTimeout(poll, 700);
}
$('composer').addEventListener('submit', async event => {
  event.preventDefault();
  const text = $('prompt').value;
  if (!ready || sending || !text.trim()) return;
  sending = true; availability(); error('');
  try { await post('/api/message', {text}); $('prompt').value = ''; ready = false; }
  catch (e) { error(e.message + ' Your draft has been kept. Check the conversation before retrying.'); }
  finally { sending = false; availability(); }
});
$('prompt').addEventListener('input', availability);
$('prompt').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('composer').requestSubmit(); } });
$('example').addEventListener('click', () => { $('prompt').value = 'Read the latest NIMOI onboarding. Explain your governance role and how you dispatch task owners, then wait for my direction.'; availability(); $('prompt').focus(); });
$('close').addEventListener('click', async () => {
  if (closing) return;
  try {
    await post('/api/shutdown', {}); closing = true; ready = false; availability(); $('close').disabled = true;
    $('status').textContent = 'Closing';
    error('Session closing; active agents and scripts are being stopped. Launch the Python driver for a new conversation.');
  } catch (e) { error(e.message); }
});
poll();
