const log = document.getElementById('log');
const form = document.getElementById('form');
const input = document.getElementById('msg');
const statusEl = document.getElementById('status');

function add(cls, text) {
  const li = document.createElement('li');
  li.className = cls;
  li.textContent = text;
  log.appendChild(li);
  li.scrollIntoView({ block: 'end' });
  return li;
}

function renderConfirm(ev) {
  const li = document.createElement('li');
  li.className = 'confirm';
  const label = document.createElement('div');
  label.textContent = 'Confirm action: ' + ev.name + '(' + JSON.stringify(ev.arguments) + ')';
  li.appendChild(label);
  const ok = document.createElement('button');
  ok.textContent = 'Confirm';
  const no = document.createElement('button');
  no.textContent = 'Decline';
  async function send(decision) {
    ok.disabled = no.disabled = true;
    const r = await fetch('/api/assistant/confirm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(decision ? { id: ev.id } : { id: ev.id, decision: 'decline' })
    });
    const body = await r.json().catch(() => ({ ok: false }));
    const out = document.createElement('div');
    out.className = 'confirm-result';
    out.textContent = decision
      ? (body.ok ? ('Done: ' + (body.result || '')).slice(0, 400) : ('Failed: ' + (body.error || r.status)))
      : 'Declined.';
    li.appendChild(out);
  }
  ok.addEventListener('click', () => send(true));
  no.addEventListener('click', () => send(false));
  li.appendChild(ok);
  li.appendChild(no);
  log.appendChild(li);
  li.scrollIntoView({ block: 'end' });
}

function addThinking() {
  const li = document.createElement('li');
  li.className = 'thinking';
  li.innerHTML = '<span class="tdots"><i></i><i></i><i></i></span>'
               + '<span class="tdots-label">Thinking…</span>';
  log.appendChild(li);
  li.scrollIntoView({ block: 'end' });
  return li;
}

function setThinking(li, text) {
  const label = li.querySelector('.tdots-label');
  if (label) label.textContent = text;
  log.appendChild(li);                 // keep the spinner at the bottom (most recent)
  li.scrollIntoView({ block: 'end' });
}

async function sendMessage(message) {
  message = (message || '').trim();
  if (!message) return;
  add('user', message);
  const thinking = addThinking();      // immediate feedback, before any network round-trip
  try {
    const resp = await fetch('/api/assistant/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message })
    });
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const blocks = buf.split('\n\n');
      buf = blocks.pop();
      for (const block of blocks) {
        const line = block.trim();
        if (!line.startsWith('data:')) continue;
        const ev = JSON.parse(line.slice(5).trim());
        if (ev.type === 'status') setThinking(thinking, ev.content);
        else if (ev.type === 'tool') {
          add('tool', '→ ' + ev.name + '(' + JSON.stringify(ev.arguments) + ')');
          setThinking(thinking, 'Running ' + ev.name + '…');
        }
        else if (ev.type === 'tool_result') add('tool', '  ' + ev.content.slice(0, 300));
        else if (ev.type === 'final') { thinking.remove(); add('final', ev.content); }
        else if (ev.type === 'error') { thinking.remove(); add('error', ev.content); }
        else if (ev.type === 'confirm_required') renderConfirm(ev);
      }
    }
  } catch (err) {
    add('error', 'Connection lost: ' + ((err && err.message) || err));
  } finally {
    thinking.remove();                 // idempotent — clears the spinner on any exit path
  }
}

fetch('/api/assistant/health').then(r => r.json()).then(h => {
  statusEl.textContent = h.mcp_ready
    ? 'MCP ready · model: ' + h.model_base_url
    : 'MCP not ready — is the model/server running?';
}).catch(() => statusEl.textContent = 'assistant offline');

const quickEl = document.getElementById('quick');
fetch('/api/assistant/prompts').then(r => r.json()).then(d => {
  for (const p of (d.prompts || [])) {
    const b = document.createElement('button');
    b.textContent = p.title;
    b.addEventListener('click', () => sendMessage(p.text));
    quickEl.appendChild(b);
  }
}).catch(() => {});

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const message = input.value.trim();
  input.value = '';
  sendMessage(message);
});
