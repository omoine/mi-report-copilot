/* MI Report Copilot - front end.
   Flow: ask -> confirm (with limitations shown) -> report -> refine -> export. */

let sessionId = null;
// Exposed so the assistant panel talks about the same session the
// main flow is working in, rather than a separate one.
Object.defineProperty(window, 'sessionId', {
  get: () => sessionId,
  set: (v) => { sessionId = v; },
});
let busy = false;

const conversation = document.getElementById('conversation');
const queryInput = document.getElementById('queryInput');
const askBtn = document.getElementById('askBtn');
const resetBtn = document.getElementById('resetBtn');
const statusBadge = document.getElementById('statusBadge');
const dataInfo = document.getElementById('dataInfo');

const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function clearEmptyState() {
  document.getElementById('emptyState')?.remove();
}

function addBlock(html, cls = 'assistant') {
  clearEmptyState();
  const div = document.createElement('div');
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  conversation.appendChild(div);
  div.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  return div;
}

function setBusy(state, label) {
  busy = state;
  askBtn.disabled = state;
  document.querySelectorAll('.flow-btn').forEach((b) => { b.disabled = state; });
  askBtn.textContent = state ? (label || 'Working...') : 'Ask';
}

async function api(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

/* ---------- step 1: ask ---------- */

async function ask() {
  const text = queryInput.value.trim();
  if (!text || busy) return;

  addBlock(`<div class="who">You asked</div>${escapeHtml(text)}`, 'user');
  queryInput.value = '';

  const thinking = addBlock('<span class="spinner"></span>Interpreting your request&hellip;');
  setBusy(true, 'Interpreting...');

  try {
    const data = await api('/api/query', { session_id: sessionId, query: text });
    sessionId = data.session_id;
    thinking.remove();
    renderInterpretation(data);
  } catch (err) {
    thinking.remove();
    addBlock(`<div class="who">Could not interpret that</div>${escapeHtml(err.message)}`, 'error');
  } finally {
    setBusy(false);
  }
}

function renderInterpretation(data) {
  const list = (items) => items.map((i) => `<li>${escapeHtml(i)}</li>`).join('');
  let html = `<div class="who">Assistant</div><p>${escapeHtml(data.understood)}</p>`;
  html += `<div class="query-summary">${escapeHtml(data.query_summary)}</div>`;

  if (data.limitations?.length || data.dependencies?.length) {
    html += '<div class="caveats">';
    if (data.limitations?.length) {
      html += `<h3>Limitations of this view</h3><ul>${list(data.limitations)}</ul>`;
    }
    if (data.dependencies?.length) {
      html += `<h3>Dependencies</h3><ul>${list(data.dependencies)}</ul>`;
    }
    html += '</div>';
  }

  html += renderUnavailable(data.unavailable);
  html += `<p>Would you like me to build this MI report?</p>
    <div class="btn-row">
      <button class="flow-btn" onclick="confirmBuild()">Yes, build the report</button>
      <button class="flow-btn secondary" onclick="cancelBuild()">No, let me rephrase</button>
    </div>`;
  addBlock(html);
}

function cancelBuild() {
  addBlock('<div class="who">Assistant</div>No problem &mdash; describe the view differently and I will try again.');
  queryInput.focus();
}

/* ---------- step 2: build ---------- */

async function confirmBuild() {
  if (busy) return;
  document.querySelectorAll('.flow-btn').forEach((b) => b.closest('.btn-row')?.remove());

  const building = addBlock('<span class="spinner"></span>Building the report&hellip;');
  setBusy(true, 'Building...');
  try {
    const data = await api('/api/confirm', { session_id: sessionId });
    building.remove();
    renderReport(data);
  } catch (err) {
    building.remove();
    addBlock(`<div class="who">Could not build the report</div>${escapeHtml(err.message)}`, 'error');
  } finally {
    setBusy(false);
  }
}

function renderTable(table) {
  if (!table?.columns?.length) return '<p class="hint">No rows returned.</p>';
  const head = table.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join('');
  const rows = table.rows
    .map((r) => `<tr>${r.map((v) => `<td>${escapeHtml(v)}</td>`).join('')}</tr>`)
    .join('');
  let html = `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  if (table.truncated) {
    html += `<p class="hint">Showing ${table.rows.length} of ${table.total_rows} rows. The export contains all rows.</p>`;
  }
  return html;
}

function renderUnavailable(items) {
  if (!items || !items.length) return '';
  const rows = items.map((u) => `
    <li><strong>${escapeHtml(u.concept)}</strong>
      ${u.reason ? ` &mdash; ${escapeHtml(u.reason)}` : ''}
      ${u.needed ? `<div class="gap-needed">Would need: ${escapeHtml(u.needed)}</div>` : ''}
    </li>`).join('');
  return `<div class="gaps">
      <h3>What this view cannot tell you</h3>
      <ul>${rows}</ul>
    </div>`;
}

function renderHeadline(items) {
  if (!items || !items.length) return '';
  return `<div class="headline">${items.map((h) => `
      <div class="headline-item">
        <div class="headline-label">${escapeHtml(h.label)}</div>
        <div class="headline-value">${escapeHtml(h.value)}</div>
        <div class="headline-detail">${escapeHtml(h.detail || '')}</div>
      </div>`).join('')}</div>`;
}

function renderReport(data) {
  let html = `<div class="who">Report</div><h3 style="margin:0 0 8px">${escapeHtml(data.title)}</h3>`;
  html += renderHeadline(data.headline);
  html += renderUnavailable(data.unavailable);

  if (data.chart_url) {
    html += `<div class="chart-wrap"><img src="${escapeHtml(data.chart_url)}" alt="${escapeHtml(data.title)}"></div>`;
  }
  (data.chart_notes || []).forEach((n) => {
    html += `<p class="chart-note">${escapeHtml(n)}</p>`;
  });

  if (data.narrative) html += `<p>${escapeHtml(data.narrative)}</p>`;
  html += renderTable(data.table);

  const p = data.provenance || {};
  html += `<div class="prov">Computed from ${escapeHtml(p.sheet || '')} &middot;
    ${escapeHtml(p.rows_after_filters)} of ${escapeHtml(p.rows_in_view)} rows after filters &middot;
    ${escapeHtml(p.aggregation || '')} of ${escapeHtml(p.measure || 'row count')} &middot;
    executed ${escapeHtml(p.executed_at || '')}</div>`;

  html += `<div style="margin-top:18px">
      <div class="section-label">Fine-tune this report</div>
      <textarea id="refineInput" placeholder="e.g. show only JPY and USD, or add share of total"></textarea>
      <div class="btn-row">
        <button class="flow-btn" onclick="refine()">Apply change</button>
        <button class="flow-btn secondary" onclick="exportReport()">Export</button>
      </div>
      <div class="section-label" style="margin-top:16px">Save this view</div>
      <div class="save-row">
        <input id="saveName" type="text" placeholder="Name it, e.g. Daily reconciliation breaks"
               maxlength="80" autocomplete="off">
        <button class="flow-btn secondary" onclick="saveView()">Save</button>
      </div>
    </div>`;
  addBlock(html);
}

/* ---------- saved views ---------- */

async function saveView(overwrite = false) {
  if (busy) return;
  const input = document.getElementById('saveName');
  const name = input?.value.trim();
  if (!name) { input?.focus(); return; }

  setBusy(true, 'Saving...');
  try {
    const data = await api('/api/views/save', {
      session_id: sessionId, name, overwrite,
    });
    addBlock(`<div class="who">Saved</div>${escapeHtml(data.message)}
      It is now in the saved views list, top left.`);
    if (input) input.value = '';
    await loadViewList();
  } catch (err) {
    // A name clash is a question, not a failure - offer to replace.
    if (/already exists/i.test(err.message)) {
      addBlock(`<div class="who">Name already used</div>
        ${escapeHtml(err.message)}
        <div class="btn-row">
          <button class="flow-btn" onclick="saveView(true)">Replace it</button>
        </div>`);
    } else {
      addBlock(`<div class="who">Could not save</div>${escapeHtml(err.message)}`, 'error');
    }
  } finally {
    setBusy(false);
  }
}

async function loadViewList() {
  const search = document.getElementById('viewSearch')?.value.trim() || '';
  const listEl = document.getElementById('viewList');
  if (!listEl) return;
  try {
    const url = '/api/views' + (search ? `?search=${encodeURIComponent(search)}` : '');
    const res = await fetch(url);
    const data = await res.json();
    const views = data.views || [];

    if (!views.length) {
      listEl.innerHTML = `<div class="view-empty">${
        search ? 'No saved view matches that.'
               : 'No saved views yet. Build one, then name and save it.'}</div>`;
      return;
    }
    listEl.innerHTML = views.map((v) => `
      <div class="view-item" role="option" tabindex="0"
           onclick="openView('${v.id}')"
           onkeydown="if(event.key==='Enter')openView('${v.id}')">
        <div class="view-item-main">
          <div class="view-item-name">${escapeHtml(v.name)}</div>
          <div class="view-item-meta">${escapeHtml(v.view || '')} &middot; ${
            escapeHtml((v.saved_at || '').slice(0, 10))}</div>
        </div>
        <button class="view-item-del" title="Delete this saved view"
                onclick="event.stopPropagation();deleteView('${v.id}','${
                  escapeHtml(v.name).replace(/'/g, "\\'")}')">&times;</button>
      </div>`).join('');
  } catch {
    listEl.innerHTML = '<div class="view-empty">Could not load saved views.</div>';
  }
}

async function openView(viewId) {
  if (busy) return;
  const working = addBlock('<span class="spinner"></span>Loading saved view and refreshing against current data&hellip;');
  setBusy(true, 'Loading...');
  try {
    const data = await api('/api/views/load', { session_id: sessionId, view_id: viewId });
    sessionId = data.session_id;
    working.remove();
    if (data.loaded_view) {
      addBlock(`<div class="who">Saved view</div>
        Re-ran <strong>${escapeHtml(data.loaded_view.name)}</strong> against the current data.`);
    }
    renderReport(data);
  } catch (err) {
    working.remove();
    addBlock(`<div class="who">Could not load that view</div>${escapeHtml(err.message)}`, 'error');
  } finally {
    setBusy(false);
  }
}

async function deleteView(viewId, name) {
  if (busy) return;
  const res = await fetch(`/api/views/${encodeURIComponent(viewId)}`, { method: 'DELETE' });
  if (res.ok) {
    addBlock(`<div class="who">Deleted</div>Removed the saved view <strong>${escapeHtml(name)}</strong>.`);
    await loadViewList();
  }
}

/* ---------- step 3: refine ---------- */

async function refine() {
  if (busy) return;
  const input = document.getElementById('refineInput');
  const text = input?.value.trim();
  if (!text) return;

  addBlock(`<div class="who">Fine-tuning request</div>${escapeHtml(text)}`, 'user');
  const working = addBlock('<span class="spinner"></span>Applying your change&hellip;');
  setBusy(true, 'Refining...');
  try {
    const data = await api('/api/refine', { session_id: sessionId, instruction: text });
    working.remove();
    renderReport(data);
  } catch (err) {
    working.remove();
    addBlock(`<div class="who">Could not apply that change</div>${escapeHtml(err.message)}`, 'error');
  } finally {
    setBusy(false);
  }
}

/* ---------- step 4: export ---------- */

async function exportReport() {
  if (busy) return;
  const working = addBlock('<span class="spinner"></span>Generating PDF and Markdown&hellip;');
  setBusy(true, 'Exporting...');
  try {
    const data = await api('/api/export', { session_id: sessionId });
    working.remove();
    const link = (file, label, alt) => file
      ? `<a ${alt ? 'class="alt" ' : ''}href="/api/download/${encodeURIComponent(file)}" download>${label}</a>`
      : '';
    addBlock(`<div class="who">Export complete</div>
      <p>${escapeHtml(data.message)}</p>
      <div class="downloads">
        ${link(data.pdf, 'PDF report')}
        ${link(data.excel, 'Excel (data + editable chart)', true)}
        ${link(data.svg, 'Chart as SVG', true)}
        ${link(data.markdown, 'Markdown documentation', true)}
      </div>`);
  } catch (err) {
    working.remove();
    addBlock(`<div class="who">Export failed</div>${escapeHtml(err.message)}`, 'error');
  } finally {
    setBusy(false);
  }
}

/* ---------- setup ---------- */

async function reset() {
  if (sessionId) await api('/api/reset', { session_id: sessionId }).catch(() => {});
  sessionId = null;
  conversation.innerHTML = `<div class="empty" id="emptyState">
      <span class="empty-mark">Awaiting request</span>
      Describe the management view you need.<br>
      Nothing is built until you confirm the interpretation.
    </div>`;
  queryInput.value = '';
  queryInput.focus();
}

askBtn.addEventListener('click', ask);
resetBtn.addEventListener('click', reset);
queryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
});
document.querySelectorAll('.ex').forEach((b) => {
  b.addEventListener('click', () => { queryInput.value = b.textContent; ask(); });
});

const viewSearch = document.getElementById('viewSearch');
if (viewSearch) {
  let debounce;
  viewSearch.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(loadViewList, 180);
  });
}
loadViewList();

fetch('/api/health').then((r) => r.json()).then((h) => {
  const views = Object.entries(h.views)
    .map(([k, v]) => `${k.replace(/_/g, ' ')} &mdash; ${v} rows`).join('<br>');
  dataInfo.innerHTML = `<strong>${escapeHtml(h.data_file)}</strong><br>${views}<br>
    <em>${escapeHtml(h.data_classification)}</em>`;
  if (h.api_key_configured) {
    statusBadge.textContent = `${h.provider} · ${h.model}`;
    statusBadge.className = 'badge ok';
  } else {
    statusBadge.className = 'badge err';
    if (h.api_key_present_but_unusable) {
      statusBadge.textContent = 'API key not usable';
      addBlock(`<div class="who">Setup needed</div>
        An <code>OPENAI_API_KEY</code> is set, but it is not an OpenAI key (those start with
        <code>sk-</code>). If it is an Azure OpenAI key, also set
        <code>AZURE_OPENAI_ENDPOINT</code> and <code>AZURE_OPENAI_DEPLOYMENT</code> in your
        <code>.env</code> file &mdash; see <code>.env.example</code> &mdash; then restart the server.`, 'error');
    } else {
      statusBadge.textContent = 'No API key configured';
      addBlock(`<div class="who">Setup needed</div>
        No <code>OPENAI_API_KEY</code> was found. Copy <code>.env.example</code> to <code>.env</code>,
        add your key, and restart the server.`, 'error');
    }
  }
}).catch(() => { statusBadge.textContent = 'Server unreachable'; });
