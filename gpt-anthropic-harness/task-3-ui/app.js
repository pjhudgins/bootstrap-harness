const $ = id => document.getElementById(id);
let revision = -1, ready = false, closing = false, sending = false;
const labels = {starting: 'Connecting', ready: 'Ready', thinking: 'Thinking…', stopping: 'Closing', closed: 'Closed', error: 'Needs attention'};
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
function availability() { $('send').disabled = !ready || sending || !$('prompt').value.trim(); $('prompt').disabled = !ready || sending; }
function number(n) { return n == null ? '—' : new Intl.NumberFormat().format(n); }
function render(state) {
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
    const role = document.createElement('p'); role.className = 'role'; role.textContent = message.role === 'user' ? 'You' : 'Claude';
    const text = document.createElement('div'); text.className = 'text'; text.textContent = message.text;
    box.append(role, text); return box;
  }));
  if (nearBottom) transcript.scrollTop = transcript.scrollHeight;
  if (state.activity.length) $('activity').replaceChildren(...state.activity.map(event => {
    const box = document.createElement('div'); box.className = 'item';
    const name = document.createElement('strong'); name.textContent = event.label;
    const text = document.createElement('span'); text.textContent = event.result != null ? `${event.a} + ${event.b} = ${event.result}` : event.reason || event.name || '';
    box.append(name, text); return box;
  }));
  if (state.usage) {
    const usage = state.usage.last_prompt;
    const inputParts = [usage.input_tokens, usage.cache_read_input_tokens, usage.cache_creation_input_tokens];
    $('input').textContent = inputParts.every(n => typeof n === 'number') ? number(inputParts.reduce((a,b) => a+b, 0)) : '—';
    $('cache').textContent = `Input includes ${number(usage.cache_read_input_tokens)} cache-read and ${number(usage.cache_creation_input_tokens)} cache-write tokens.`;
    $('output').textContent = number(state.usage.last_prompt.output_tokens);
    $('cost').textContent = state.usage.total_cost_usd == null ? '—' : '$' + state.usage.total_cost_usd.toFixed(5);
  }
  if (state.limits) {
    const l = state.limits;
    const reset = l.resets_at == null ? '' : ` · resets ${new Date(l.resets_at * 1000).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}`;
    $('limits').textContent = `${(l.type || 'Rate limit').replaceAll('_', ' ')}: ${l.status || 'unknown'}${reset}`;
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
    render(await response.json());
  } catch (e) {
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
$('example').addEventListener('click', () => { $('prompt').value = 'Use the add tool to calculate 19.25 + 22.75.'; availability(); $('prompt').focus(); });
$('close').addEventListener('click', async () => {
  if (closing) return;
  try {
    await post('/api/shutdown', {}); closing = true; ready = false; availability(); $('close').disabled = true;
    $('status').textContent = 'Closing';
    error('Session closing after any active reply. Launch the Python driver to start a new conversation.');
  } catch (e) { error(e.message); }
});
poll();
