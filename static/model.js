/* Data model view: the tables behind the analysis, and how they connect.

   The architecture is drawn in three columns because that is what the model
   is - the transaction views, the tables keyed straight off them, and the
   tables only reachable once a first lookup has brought their key across.
   Every wire is a join the query engine can actually make, so a reader can
   tell what is answerable before asking. */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const modelView = document.getElementById('modelView');
const analysisView = document.querySelector('.layout');
const tabAnalysis = document.getElementById('tabAnalysis');
const tabModel = document.getElementById('tabModel');
const overviewEl = document.getElementById('modelOverview');
const detailEl = document.getElementById('modelDetail');
const backBtn = document.getElementById('modelBack');
const crumbEl = document.getElementById('modelCrumb');
const searchEl = document.getElementById('modelSearch');

let model = null;
let loading = null;

const fmt = (n) => Number(n).toLocaleString('en-GB');

/* ── switching workspaces ──────────────────────────────────────── */

function showWorkspace(which) {
  const isModel = which === 'model';
  analysisView.hidden = isModel;
  modelView.hidden = !isModel;
  tabAnalysis.classList.toggle('active', !isModel);
  tabModel.classList.toggle('active', isModel);
  tabAnalysis.setAttribute('aria-selected', String(!isModel));
  tabModel.setAttribute('aria-selected', String(isModel));
  if (isModel) loadModel();
}

tabAnalysis.addEventListener('click', () => showWorkspace('analysis'));
tabModel.addEventListener('click', () => showWorkspace('model'));

/* ── overview ──────────────────────────────────────────────────── */

async function loadModel() {
  if (model || loading) return loading;
  loading = fetch('/api/model')
    .then((r) => r.json())
    .then((data) => { model = data; renderOverview(); })
    .catch((e) => {
      overviewEl.innerHTML = `<div class="hint">Could not load the data model: ${esc(e.message)}</div>`;
    })
    .finally(() => { loading = null; });
  return loading;
}

function renderOverview() {
  const layers = model.layers || [];
  const columns = layers.map((name, i) => {
    const tables = model.tables.filter((t) => t.layer === i);
    const cards = tables.map((t) => {
      const out = model.links.filter((l) => l.from_table === t.name);
      const chips = out.map((l) => `
        <button class="wire-chip" data-goto="${esc(l.to_table)}"
                title="${esc(l.from_column)} matches ${esc(l.to_table)}.${esc(l.to_column)}">
          ${esc(l.from_column)} <span class="wire-arrow">&rarr;</span> ${esc(l.to_table)}
        </button>`).join('');
      return `
        <article class="mtable ${esc(t.kind)}" data-table="${esc(t.name)}" tabindex="0">
          <h3>${esc(t.label)}</h3>
          <p class="mtable-grain">${esc(t.grain || '')}</p>
          <div class="mtable-meta">
            <span>${fmt(t.rows)} rows</span>
            <span>${fmt(t.column_count)} cols</span>
            ${t.key ? `<span class="mkey">key ${esc(t.key)}</span>` : ''}
          </div>
          ${chips ? `<div class="wire-chips">${chips}</div>` : ''}
        </article>`;
    }).join('');
    return `
      <div class="mlayer">
        <h2>${esc(name)}</h2>
        ${cards || '<div class="hint">none</div>'}
      </div>`;
  }).join('');

  overviewEl.innerHTML = `
    <svg class="model-wires" id="modelWires" aria-hidden="true"></svg>
    <div class="mgrid">${columns}</div>
    <p class="model-foot">${model.tables.length} tables, ${model.links.length}
       joins. ${esc(model.data_classification || '')}</p>`;

  overviewEl.querySelectorAll('.mtable').forEach((card) => {
    const open = () => openTable(card.dataset.table);
    card.addEventListener('click', (e) => {
      if (e.target.closest('.wire-chip')) return;
      open();
    });
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    card.addEventListener('mouseenter', () => focusWires(card.dataset.table));
    card.addEventListener('mouseleave', () => focusWires(null));
  });
  overviewEl.querySelectorAll('.wire-chip').forEach((chip) => {
    chip.addEventListener('click', (e) => {
      e.stopPropagation();
      openTable(chip.dataset.goto);
    });
  });

  scheduleWires();
}

/* Drawn on the next frame so the cards have been laid out, with a timer
   behind it: a background tab is not compositing, so its animation frames
   never run and the wires would be missing until someone looked at it. */
function scheduleWires() {
  requestAnimationFrame(() => requestAnimationFrame(drawWires));
  setTimeout(drawWires, 160);
}

function cardFor(name) {
  return overviewEl.querySelector(`.mtable[data-table="${CSS.escape(name)}"]`);
}

/* Wires are drawn after layout because they are positioned from where the
   cards actually landed, not from a guess at the grid geometry. */
function drawWires() {
  const svg = document.getElementById('modelWires');
  if (!svg || !model) return;
  const host = overviewEl.getBoundingClientRect();
  svg.setAttribute('width', host.width);
  svg.setAttribute('height', host.height);
  svg.setAttribute('viewBox', `0 0 ${host.width} ${host.height}`);

  const parts = [];
  model.links.forEach((link, i) => {
    const a = cardFor(link.from_table);
    const b = cardFor(link.to_table);
    if (!a || !b) return;
    const ra = a.getBoundingClientRect();
    const rb = b.getBoundingClientRect();
    const forward = rb.left >= ra.right - 4;

    let x1, y1, x2, y2, c1, c2;
    y1 = ra.top + ra.height / 2 - host.top;
    y2 = rb.top + rb.height / 2 - host.top;
    if (forward) {
      x1 = ra.right - host.left;
      x2 = rb.left - host.left;
      const bend = Math.max(28, (x2 - x1) * 0.45);
      c1 = `${x1 + bend},${y1}`;
      c2 = `${x2 - bend},${y2}`;
    } else {
      // Same column: bow out to the right and come back, so the two cards
      // are visibly joined without the line cutting through the ones between.
      x1 = ra.right - host.left;
      x2 = rb.right - host.left;
      c1 = `${x1 + 46},${y1}`;
      c2 = `${x2 + 46},${y2}`;
    }
    const d = `M ${x1},${y1} C ${c1} ${c2} ${x2},${y2}`;
    const mx = (x1 + x2) / 2 + (forward ? 0 : 34);
    const my = (y1 + y2) / 2;
    parts.push(`<path d="${d}" data-from="${esc(link.from_table)}"
                      data-to="${esc(link.to_table)}" class="wire"/>`);
    parts.push(`<text x="${mx}" y="${my - 4}" text-anchor="middle"
                      data-from="${esc(link.from_table)}"
                      data-to="${esc(link.to_table)}"
                      class="wire-label">${esc(link.from_column)}</text>`);
  });
  svg.innerHTML = parts.join('');
}

function focusWires(name) {
  const svg = document.getElementById('modelWires');
  if (!svg) return;
  svg.classList.toggle('has-focus', Boolean(name));
  svg.querySelectorAll('.wire, .wire-label').forEach((el) => {
    const on = name && (el.dataset.from === name || el.dataset.to === name);
    el.classList.toggle('on', Boolean(on));
  });
  overviewEl.querySelectorAll('.mtable').forEach((card) => {
    const linked = name && model.links.some((l) =>
      (l.from_table === name && l.to_table === card.dataset.table)
      || (l.to_table === name && l.from_table === card.dataset.table));
    card.classList.toggle('linked', Boolean(linked));
    card.classList.toggle('is-focus', card.dataset.table === name);
  });
}

let wireTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(wireTimer);
  wireTimer = setTimeout(drawWires, 120);
});

/* ── one table ─────────────────────────────────────────────────── */

async function openTable(name) {
  crumbEl.textContent = name;
  backBtn.hidden = false;
  overviewEl.hidden = true;
  detailEl.hidden = false;
  detailEl.innerHTML = '<div class="hint">loading&hellip;</div>';
  try {
    const res = await fetch(`/api/model/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    renderDetail(await res.json());
  } catch (e) {
    detailEl.innerHTML = `<div class="hint">Could not open ${esc(name)}: ${esc(e.message)}</div>`;
  }
}

function renderDetail(d) {
  const linkedCols = new Map();
  d.columns.forEach((c) => { if (c.links_to.length) linkedCols.set(c.name, c.links_to); });

  const head = d.sample_columns.map((col) => {
    const links = linkedCols.get(col) || [];
    const arrows = links.map((l) => `
      <button class="col-jump" data-goto="${esc(l.table)}"
              title="Follow ${esc(col)} to ${esc(l.table)}.${esc(l.column)}">
        &uarr; ${esc(l.table)}
      </button>`).join('');
    return `<th>${arrows}<div>${esc(col)}</div></th>`;
  }).join('');

  const body = d.sample.map((row) => `<tr>${
    d.sample_columns.map((c) => `<td>${esc(row[c])}</td>`).join('')
  }</tr>`).join('');

  const columns = d.columns.map((c, i) => {
    const jumps = c.links_to.map((l) => `
      <button class="col-jump" data-goto="${esc(l.table)}">
        &rarr; ${esc(l.table)}.${esc(l.column)}
      </button>`).join('');
    let values;
    if (c.kind === 'number' || c.kind === 'date') {
      values = `<div class="col-range">from <b>${esc(c.min)}</b> to <b>${esc(c.max)}</b></div>`;
    } else {
      values = `<div class="col-values">${
        c.values.map((v) => `<span>${esc(v)}</span>`).join('')
      }</div>${c.truncated ? `<p class="hint">showing ${c.values.length} of ${fmt(c.distinct)}</p>` : ''}`;
    }
    return `
      <div class="col-row${c.is_key ? ' is-key' : ''}">
        <div class="col-head" role="button" tabindex="0" aria-expanded="false">
          <span class="col-name">${esc(c.name)}${c.is_key ? ' <em>key</em>' : ''}</span>
          <span class="col-type">${esc(c.dtype)}</span>
          <span class="col-count">${fmt(c.distinct)} distinct${c.missing ? `, ${fmt(c.missing)} missing` : ''}</span>
          ${jumps}
        </div>
        <div class="col-body" hidden>
          ${c.description ? `<p class="col-desc">${esc(c.description)}</p>` : ''}
          ${values}
        </div>
      </div>`;
  }).join('');

  const reached = d.links_in.map((l) => `
    <button class="wire-chip" data-goto="${esc(l.table)}">
      ${esc(l.table)}.${esc(l.column)} <span class="wire-arrow">&rarr;</span> ${esc(d.name)}
    </button>`).join('');

  detailEl.innerHTML = `
    <header class="mdetail-head">
      <div>
        <div class="eyebrow">${esc(d.domain)}</div>
        <h2>${esc(d.label)}</h2>
        <p class="mtable-grain">${esc(d.grain || '')}</p>
      </div>
      <div class="mdetail-meta">
        <span>${fmt(d.rows)} rows</span>
        <span>${fmt(d.columns.length)} columns</span>
        ${d.key ? `<span class="mkey">key ${esc(d.key)}</span>` : ''}
      </div>
    </header>
    ${reached ? `<div class="mdetail-block"><h3>Reached from</h3>
                  <div class="wire-chips">${reached}</div></div>` : ''}
    <div class="mdetail-block">
      <h3>First ${d.sample.length} rows</h3>
      <div class="tablewrap extract"><table><thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody></table></div>
    </div>
    <div class="mdetail-block">
      <h3>Columns &mdash; click one to see the values it holds</h3>
      <div class="col-list">${columns}</div>
    </div>`;

  detailEl.querySelectorAll('.col-head').forEach((btn) => {
    const toggle = () => {
      const body = btn.closest('.col-row').querySelector('.col-body');
      const open = body.hidden;
      body.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
    };
    btn.addEventListener('click', (e) => {
      if (e.target.closest('.col-jump')) return;
      toggle();
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });
  detailEl.querySelectorAll('[data-goto]').forEach((btn) => {
    btn.addEventListener('click', (e) => { e.stopPropagation(); openTable(btn.dataset.goto); });
  });
  detailEl.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

backBtn.addEventListener('click', () => {
  detailEl.hidden = true;
  overviewEl.hidden = false;
  backBtn.hidden = true;
  crumbEl.textContent = 'Data architecture';
  scheduleWires();
});

/* ── search ────────────────────────────────────────────────────── */

searchEl.addEventListener('input', () => {
  const term = searchEl.value.trim().toLowerCase();
  if (!overviewEl.hidden) {
    overviewEl.querySelectorAll('.mtable').forEach((card) => {
      card.classList.toggle('dim', Boolean(term) && !card.dataset.table.toLowerCase().includes(term));
    });
  }
});
