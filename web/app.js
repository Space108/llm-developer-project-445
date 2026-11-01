/* Чат абонента: ответ, карточка подтверждения. Служебные события (plan, tools) не показываем. */
const BACKEND = 'http://127.0.0.1:8000';

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/^[-*] /gm, '• ');
const log = $('log');
let sessionId = null;
let busy = false;

const ACTION_LABELS = {
  tariff_change: 'смена тарифа',
  toggle_service: 'изменение услуги',
  contacts_update: 'обновление контактов',
};

function addMsg(cls, text, extra) {
  const div = document.createElement('div');
  div.className = `msg ${cls}`;
  if (cls === 'bot') div.innerHTML = md(text);
  else div.textContent = text;
  if (extra) {
    const src = document.createElement('span');
    src.className = 'src';
    src.textContent = extra;
    div.appendChild(src);
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function addCard(pendingId, card) {
  const div = document.createElement('div');
  div.className = 'card';
  const action = card.action || '';
  const actionLabel = ACTION_LABELS[action] || 'подтверждение действия';
  const rows = Object.entries(card.rows || card)
    .filter(([k, v]) => typeof v !== 'object' && k !== 'action' && k !== 'ttl_sec')
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td><b>${esc(v)}</b></td></tr>`).join('');
  const ttl = card.ttl_sec || 120;
  div.innerHTML = `
    <h4>Нужно ваше подтверждение</h4>
    <table>${rows}</table>
    <div class="ttl">${esc(actionLabel)} · действует ${esc(ttl)} с</div>
    <div class="btns">
      <button class="yes">Подтверждаю</button>
      <button>Отменить</button>
    </div>`;
  const [yes, no] = div.querySelectorAll('button');
  const decide = async answer => {
    [yes, no].forEach(b => { b.disabled = true; });
    div.classList.add('done');
    try {
      const r = await fetch(`${BACKEND}/api/cards/${pendingId}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, answer }),
      });
      const data = await r.json();
      const text = data.text || (answer === 'yes' ? 'Готово.' : 'Отменено.');
      addMsg('bot', text);
    } catch {
      addMsg('bot', 'Не удалось подтвердить. Попробуйте ещё раз.');
    }
  };
  yes.onclick = () => decide('yes');
  no.onclick = () => decide('no');
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function openSession() {
  const user = $('user').value;
  try {
    const r = await fetch(`${BACKEND}/api/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user }),
    });
    const data = await r.json();
    if (data.error) {
      $('session-state').textContent = 'нет связи с сервером';
      $('session-sub').textContent = '';
      addMsg('sys', 'Не удалось начать диалог. Проверьте, что сервис чата запущен.');
      return;
    }
    sessionId = data.session_id;
    $('session-state').textContent = data.name || 'Абонент';
    const roleLabel = data.role === 'operator' ? 'оператор' : 'клиент';
    $('session-sub').textContent = roleLabel;
    $('text').disabled = false;
    $('send').disabled = false;
  } catch {
    $('session-state').textContent = 'нет связи';
    addMsg('sys', 'Не удалось начать диалог. Проверьте, что сервис чата запущен.');
  }
}

async function send(text) {
  addMsg('user', text);
  busy = true;
  $('send').disabled = true;
  const typing = addMsg('sys', 'печатает…');
  try {
    const r = await fetch(`${BACKEND}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let gotVisible = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = chunk.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === 'plan' || ev.type === 'done') continue;
        if (!gotVisible) {
          typing.remove();
          gotVisible = true;
        }
        handleEvent(ev);
      }
    }
    if (!gotVisible) typing.remove();
  } catch {
    typing.remove();
    addMsg('bot', 'Связь прервалась. Отправьте сообщение ещё раз.');
  } finally {
    busy = false;
    $('send').disabled = false;
    $('text').focus();
  }
}

function handleEvent(ev) {
  if (ev.type === 'plan' || ev.type === 'done') return;
  if (ev.type === 'card') {
    addCard(ev.pending_id, ev.card);
    return;
  }
  if (ev.type === 'reply') {
    // источники уже в тексте ответа редактора («источник: …») — дублировать id не нужно
    addMsg('bot', ev.text || '');
    return;
  }
  if (ev.type === 'error') {
    addMsg('bot', 'Сейчас не получилось ответить. Попробуйте ещё раз чуть позже.');
  }
}

$('form').onsubmit = e => {
  e.preventDefault();
  if (busy || !sessionId) return;
  const text = $('text').value.trim();
  if (!text) return;
  $('text').value = '';
  send(text);
};

async function switchUser() {
  busy = false;
  log.innerHTML = '';
  sessionId = null;
  $('session-state').textContent = 'подключение…';
  $('session-sub').textContent = '';
  $('text').disabled = true;
  $('send').disabled = true;
  localStorage.setItem('demo_user', $('user').value);
  await openSession();
}

$('user').onchange = () => { switchUser(); };

const savedUser = localStorage.getItem('demo_user');
if (savedUser && [...$('user').options].some(o => o.value === savedUser)) {
  $('user').value = savedUser;
}
openSession();
