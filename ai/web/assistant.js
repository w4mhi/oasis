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

fetch('/api/assistant/health').then(r => r.json()).then(h => {
  statusEl.textContent = h.mcp_ready
    ? 'MCP ready · model: ' + h.model_base_url
    : 'MCP not ready — is the model/server running?';
}).catch(() => statusEl.textContent = 'assistant offline');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  add('user', message);
  input.value = '';

  const resp = await fetch('/api/assistant/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  });
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
      if (ev.type === 'tool') add('tool', '→ ' + ev.name + '(' + JSON.stringify(ev.arguments) + ')');
      else if (ev.type === 'tool_result') add('tool', '  ' + ev.content.slice(0, 300));
      else if (ev.type === 'final') add('final', ev.content);
      else if (ev.type === 'error') add('error', ev.content);
    }
  }
});
