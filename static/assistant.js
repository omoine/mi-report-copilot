/* The in-app assistant: a draggable bubble that opens into a chat panel.

   Before a report exists it helps shape the question; once one is built it
   explains what is on screen. Both are answered by the server, which grounds
   the second case in the exact document the user can export - so the panel and
   the export cannot describe the same figures differently. */

(function () {
  const POSITION_KEY = 'mi-assistant-position';
  let history = [];
  let open = false;
  let busy = false;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const root = document.createElement('div');
  root.className = 'assistant-widget';
  root.innerHTML = `
    <button class="assistant-bubble" type="button" aria-label="Open the assistant">
      <span class="assistant-bubble-dot"></span>
      <span class="assistant-bubble-text">Ask</span>
    </button>
    <div class="assistant-panel" hidden>
      <div class="assistant-head">
        <div>
          <div class="assistant-title">Assistant</div>
          <div class="assistant-mode" id="assistantMode">Ready</div>
        </div>
        <button class="assistant-close" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="assistant-log" id="assistantLog"></div>
      <div class="assistant-input">
        <textarea id="assistantInput" rows="2"
          placeholder="Ask what a view would show, or what you are looking at&hellip;"></textarea>
        <button class="assistant-send" type="button">Send</button>
      </div>
    </div>`;
  document.body.appendChild(root);

  const bubble = root.querySelector('.assistant-bubble');
  const panel = root.querySelector('.assistant-panel');
  const head = root.querySelector('.assistant-head');
  const log = root.querySelector('#assistantLog');
  const input = root.querySelector('#assistantInput');
  const sendBtn = root.querySelector('.assistant-send');
  const modeEl = root.querySelector('#assistantMode');

  /* ---------- position: dragging, and remembering where it was ---------- */

  function place(x, y) {
    const w = root.offsetWidth || 340;
    const h = root.offsetHeight || 90;
    const clampedX = Math.min(Math.max(8, x), window.innerWidth - w - 8);
    const clampedY = Math.min(Math.max(8, y), window.innerHeight - h - 8);
    root.style.left = `${clampedX}px`;
    root.style.top = `${clampedY}px`;
    root.style.right = 'auto';
    root.style.bottom = 'auto';
  }

  const stored = localStorage.getItem(POSITION_KEY);
  if (stored) {
    try {
      const { x, y } = JSON.parse(stored);
      place(x, y);
    } catch { /* fall back to the default corner */ }
  }

  // A hand never holds perfectly still, so a click always carries a pixel or
  // two of movement. Treating any movement as a drag made the bubble swallow
  // ordinary clicks and feel dead; only past this distance is it a drag.
  const DRAG_THRESHOLD = 5;

  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;
  let startX = 0;
  let startY = 0;
  let moved = false;

  function startDrag(event) {
    // Let the close button and the textarea behave normally.
    if (event.target.closest('.assistant-close, textarea, .assistant-send')) return;
    dragging = true;
    moved = false;
    const rect = root.getBoundingClientRect();
    const point = event.touches ? event.touches[0] : event;
    startX = point.clientX;
    startY = point.clientY;
    offsetX = point.clientX - rect.left;
    offsetY = point.clientY - rect.top;
  }

  function onDrag(event) {
    if (!dragging) return;
    const point = event.touches ? event.touches[0] : event;
    if (!moved) {
      const far = Math.hypot(point.clientX - startX, point.clientY - startY);
      if (far < DRAG_THRESHOLD) return;
      moved = true;
      root.classList.add('is-dragging');
    }
    place(point.clientX - offsetX, point.clientY - offsetY);
    if (event.cancelable) event.preventDefault();
  }

  function endDrag() {
    if (!dragging) return;
    dragging = false;
    root.classList.remove('is-dragging');
    if (!moved) return;  // a click, not a drag: leave the position alone
    const rect = root.getBoundingClientRect();
    localStorage.setItem(POSITION_KEY, JSON.stringify({ x: rect.left, y: rect.top }));
  }

  [bubble, head].forEach((handle) => {
    handle.addEventListener('mousedown', startDrag);
    handle.addEventListener('touchstart', startDrag, { passive: true });
  });
  document.addEventListener('mousemove', onDrag);
  document.addEventListener('touchmove', onDrag, { passive: false });
  document.addEventListener('mouseup', endDrag);
  document.addEventListener('touchend', endDrag);
  window.addEventListener('resize', () => {
    const rect = root.getBoundingClientRect();
    place(rect.left, rect.top);
  });

  /* ---------- opening and closing ---------- */

  function setOpen(next) {
    open = next;
    panel.hidden = !open;
    bubble.classList.toggle('is-open', open);
    if (open) {
      refreshMode();
      if (!log.children.length) greet();
      // The panel expands downward from wherever the bubble sits, so opening
      // near the bottom would push it off screen. Re-clamp once it has size.
      if (root.style.left) {
        const rect = root.getBoundingClientRect();
        requestAnimationFrame(() => place(rect.left, rect.top));
      }
      input.focus();
    }
  }

  bubble.addEventListener('click', () => {
    // Swallow only the click that ends a real drag, not an ordinary one.
    if (moved) { moved = false; return; }
    setOpen(!open);
  });
  root.querySelector('.assistant-close').addEventListener('click', () => setOpen(false));

  /* ---------- conversation ---------- */

  function add(role, html) {
    const el = document.createElement('div');
    el.className = `assistant-msg assistant-${role}`;
    el.innerHTML = html;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  function hasReport() {
    return !!document.querySelector('.headline, .table-scroll');
  }

  function refreshMode() {
    modeEl.textContent = hasReport()
      ? 'Explaining the report on screen'
      : 'Helping you shape a view';
  }

  function greet() {
    add('assistant', hasReport()
      ? 'Ask me anything about this report &mdash; what a figure means, why '
        + 'something is caveated, or what it is not telling you. I answer from '
        + 'the same document you can export, so we will not disagree.'
      : 'Tell me the view you have in mind and I will check it will actually '
        + 'produce that, before you run it. I know what is in the data and what '
        + 'this tool will refuse to do.');
  }

  async function send() {
    const text = input.value.trim();
    if (!text || busy) return;

    add('user', esc(text));
    input.value = '';
    busy = true;
    sendBtn.disabled = true;
    const thinking = add('assistant', '<span class="assistant-dots"><i></i><i></i><i></i></span>');

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: window.sessionId || null,
          message: text,
          history,
        }),
      });
      const data = await res.json().catch(() => ({}));
      thinking.remove();

      if (!res.ok) {
        add('error', esc(data.detail || `Request failed (${res.status})`));
        return;
      }
      if (data.session_id) window.sessionId = data.session_id;

      let html = esc(data.reply).replace(/\n/g, '<br>');
      if (data.suggestion) {
        html += `<button class="assistant-try" type="button"
                   data-prompt="${esc(data.suggestion)}">Use this: ${esc(data.suggestion)}</button>`;
      }
      add('assistant', html);

      history.push({ role: 'user', content: text });
      history.push({ role: 'assistant', content: data.reply });
      history = history.slice(-12);
      refreshMode();
    } catch (err) {
      thinking.remove();
      add('error', esc(err.message || 'Could not reach the assistant.'));
    } finally {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  // A suggested prompt goes straight into the main ask box, so the wording the
  // assistant checked is the wording that gets run.
  log.addEventListener('click', (e) => {
    const btn = e.target.closest('.assistant-try');
    if (!btn) return;
    const box = document.getElementById('queryInput');
    if (box) {
      box.value = btn.dataset.prompt;
      box.focus();
      box.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    setOpen(false);
  });

  // Follow the main flow, so the panel is in the right mode when opened.
  new MutationObserver(refreshMode).observe(
    document.getElementById('conversation'), { childList: true, subtree: true });
})();
