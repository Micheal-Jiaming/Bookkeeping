/* Bookkeeping front end.
 *
 * Deliberately dependency-free vanilla JS: the whole point of this application
 * is that it runs locally with nothing to install and nothing fetched from a
 * CDN at runtime. State is kept minimal -- the server is the source of truth and
 * every mutation re-reads what it needs.
 */

'use strict';

const state = {
  view: 'receipts',
  receipts: [],
  categories: [],
  selectedId: null,
  poller: null,
  reportRange: '90',
};

/* ------------------------------------------------------------------ helpers */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = { detail: text }; }
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : `HTTP ${response.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return payload;
}

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let toastTimer = null;
function toast(message, bad = false) {
  const node = $('#toast');
  node.textContent = message;
  node.classList.toggle('is-bad', bad);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, bad ? 8000 : 3500);
}

const money = (value) => (value === '' || value === null || value === undefined ? '—' : value);
const STATUS_LABEL = {
  uploaded: 'queued', scanning: 'scanning', needs_review: 'needs review',
  confirmed: 'confirmed', failed: 'failed',
};

/* --------------------------------------------------------------------- tabs */

function showView(name) {
  state.view = name;
  $$('.tab').forEach((tab) => tab.classList.toggle('is-active', tab.dataset.view === name));
  $$('.view').forEach((view) => view.classList.toggle('is-active', view.id === `view-${name}`));
  if (name === 'reports') loadReport();
  if (name === 'rules') { loadCategories().then(renderRulesView); }
  if (name === 'settings') loadSettings();
}

/* ----------------------------------------------------------------- receipts */

function filterParams() {
  const params = new URLSearchParams();
  const status = $('#filter-status').value;
  const q = $('#filter-q').value.trim();
  const from = $('#filter-from').value;
  const to = $('#filter-to').value;
  if (status) params.set('status', status);
  if (q) params.set('q', q);
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);
  return params;
}

async function loadReceipts() {
  const data = await api(`/api/receipts?${filterParams()}`);
  state.receipts = data.receipts;
  renderReceiptTable();
  schedulePoll();
}

function renderReceiptTable() {
  const body = $('#receipt-table tbody');
  body.textContent = '';
  $('#receipt-empty').hidden = state.receipts.length > 0;

  for (const receipt of state.receipts) {
    const row = el('tr', { class: receipt.id === state.selectedId ? 'is-selected' : '' },
      el('td', {}, receipt.purchased_at || '—'),
      el('td', {}, receipt.merchant || receipt.original_name || '(unread)'),
      el('td', {},
        receipt.category_name
          ? el('span', {},
              el('span', { class: 'chip-dot', style: `background:${receipt.category_color}` }),
              receipt.category_name)
          : '—'),
      el('td', { class: 'num' }, receipt.item_count ?? 0),
      el('td', { class: 'num' }, money(receipt.total)),
      el('td', {}, el('span', { class: 'status', 'data-s': receipt.status },
        STATUS_LABEL[receipt.status] || receipt.status)),
    );
    row.addEventListener('click', () => selectReceipt(receipt.id));
    body.append(row);
  }
}

function schedulePoll() {
  const busy = state.receipts.some((r) => r.status === 'scanning' || r.status === 'uploaded');
  clearTimeout(state.poller);
  if (!busy) return;
  // Poll only while something is actually being scanned; a scan takes a few
  // seconds, so 1.5s feels immediate without hammering the server.
  state.poller = setTimeout(async () => {
    await loadReceipts();
    if (state.selectedId) {
      const current = state.receipts.find((r) => r.id === state.selectedId);
      if (current && current.status !== 'scanning') await selectReceipt(state.selectedId, true);
    }
  }, 1500);
}

async function selectReceipt(id, quiet = false) {
  state.selectedId = id;
  renderReceiptTable();
  let receipt;
  try {
    receipt = await api(`/api/receipts/${id}`);
  } catch (error) {
    // The receipt can genuinely disappear underneath the pane -- deleted in
    // another window, or the database replaced. Recover rather than leaving an
    // unhandled rejection and a stale detail pane on screen.
    closeDetail();
    await loadReceipts();
    if (!quiet) toast(`Receipt #${id} is no longer there: ${error.message}`, true);
    return;
  }
  renderDetail(receipt);
  if (!quiet) $('#detail').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function closeDetail() {
  state.selectedId = null;
  $('#detail').hidden = true;
  $('.split').classList.remove('has-detail');
  renderReceiptTable();
}

function categorySelect(value, name = 'category_id') {
  const select = el('select', { name });
  select.append(el('option', { value: '' }, '—'));
  for (const category of state.categories) {
    const option = el('option', { value: category.id }, category.name);
    if (Number(value) === category.id) option.selected = true;
    select.append(option);
  }
  return select;
}

function renderDetail(receipt) {
  const pane = $('#detail');
  pane.hidden = false;
  $('.split').classList.add('has-detail');
  pane.textContent = '';

  const engineNote = [receipt.engine, receipt.model].filter(Boolean).join(' · ');
  const cost = receipt.cost_usd ? `$${Number(receipt.cost_usd).toFixed(4)}` : null;
  const meta = [
    engineNote,
    receipt.confidence !== null && receipt.confidence !== undefined
      ? `confidence ${Number(receipt.confidence).toFixed(2)}` : null,
    receipt.extract_ms ? `${(receipt.extract_ms / 1000).toFixed(1)}s` : null,
    cost,
  ].filter(Boolean).join(' · ');

  pane.append(
    el('div', { class: 'detail-head' },
      el('div', {},
        el('h2', {}, `Receipt #${receipt.id}`),
        el('div', { class: 'muted' }, meta || 'not scanned yet')),
      el('div', {},
        el('span', { class: 'status', 'data-s': receipt.status },
          STATUS_LABEL[receipt.status] || receipt.status),
        ' ',
        el('button', { class: 'ghost', onclick: closeDetail }, 'Close'))),
  );

  if (receipt.error) {
    pane.append(el('ul', { class: 'flags' }, el('li', { class: 'bad' }, receipt.error)));
  }
  if (receipt.review_flags && receipt.review_flags.length) {
    pane.append(el('ul', { class: 'flags' },
      receipt.review_flags.map((flag) => el('li', {}, flag))));
  }

  const form = el('form', { id: 'detail-form', onsubmit: (event) => event.preventDefault() });
  const fields = el('div', { class: 'fields' },
    labelled('Merchant', el('input', { name: 'merchant', value: receipt.merchant || '' })),
    labelled('Date', el('input', { name: 'purchased_at', type: 'date', value: receipt.purchased_at || '' })),
    labelled('Category', categorySelect(receipt.category_id)),
    labelled('Payment', el('input', { name: 'payment_method', value: receipt.payment_method || '' })),
    labelled('Subtotal', el('input', { name: 'subtotal', class: 'num', value: receipt.subtotal || '' })),
    labelled('Tax', el('input', { name: 'tax', value: receipt.tax || '' })),
    labelled('Tip', el('input', { name: 'tip', value: receipt.tip || '' })),
    labelled('Total', el('input', { name: 'total', value: receipt.total || '' })),
    labelled('Currency', el('input', { name: 'currency', value: receipt.currency || 'USD' })),
  );
  form.append(fields);
  form.append(labelled('Notes', el('textarea', { name: 'notes', rows: 2 }, receipt.notes || '')));

  const itemsBody = el('tbody');
  const table = el('table', { class: 'grid items-table' },
    el('thead', {}, el('tr', {},
      el('th', {}, 'Item'), el('th', { class: 'w-qty num' }, 'Qty'),
      el('th', { class: 'w-amount num' }, 'Amount'), el('th', { class: 'w-cat' }, 'Category'),
      el('th', {}, ''))),
    itemsBody);
  for (const item of receipt.items || []) addItemRow(itemsBody, item);
  form.append(el('h3', { style: 'margin:16px 0 6px;font-size:13px' }, 'Line items'), table);
  form.append(el('div', { class: 'row-actions' },
    el('button', { type: 'button', class: 'ghost', onclick: () => addItemRow(itemsBody, {}) },
      '+ Add line'),
    el('span', { class: 'muted', id: 'items-sum' })));

  const image = receipt.image_path
    ? el('img', { class: 'receipt-image', src: `/api/receipts/${receipt.id}/image`,
                  alt: `Receipt ${receipt.id}` })
    : el('p', { class: 'muted' }, 'No image (entered by hand).');

  pane.append(el('div', { class: 'detail-grid' }, el('div', {}, image), form));

  pane.append(el('div', { class: 'detail-actions' },
    el('button', { onclick: () => saveDetail(receipt.id, true) }, 'Save & confirm'),
    el('button', { class: 'ghost', onclick: () => saveDetail(receipt.id, false) }, 'Save draft'),
    receipt.image_path
      ? el('button', { class: 'ghost', onclick: () => rescan(receipt.id) }, 'Re-scan image')
      : null,
    (receipt.raw_response || receipt.raw_text)
      ? el('button', { class: 'ghost', onclick: (event) => {
          const box = $('#raw-box');
          if (box) { box.remove(); event.target.textContent = 'Show engine output'; return; }
          event.target.textContent = 'Hide engine output';
          pane.append(el('pre', { class: 'raw', id: 'raw-box' },
            receipt.raw_response || receipt.raw_text));
        } }, 'Show engine output')
      : null,
    el('button', { class: 'danger', onclick: () => removeReceipt(receipt.id) }, 'Delete'),
  ));

  updateItemsSum();
}

function labelled(text, control) {
  return el('label', { class: 'field' }, el('span', {}, text), control);
}

function addItemRow(body, item) {
  const row = el('tr', {},
    el('td', {}, el('input', { name: 'description', value: item.description || '',
                               placeholder: 'Item name' })),
    el('td', {}, el('input', { name: 'quantity', class: 'num',
                               value: item.quantity ?? '' })),
    el('td', {}, el('input', { name: 'amount', class: 'num', value: item.amount || '',
                               oninput: updateItemsSum })),
    el('td', {}, categorySelect(item.category_id, 'item_category')),
    el('td', {}, el('button', { type: 'button', class: 'ghost', title: 'Remove line',
      onclick: () => { row.remove(); updateItemsSum(); } }, '×')),
  );
  row.dataset.source = item.category_source || 'manual';
  body.append(row);
  updateItemsSum();
}

function readItems() {
  return $$('#detail-form .items-table tbody tr').map((row) => ({
    description: $('input[name="description"]', row).value,
    quantity: parseFloat($('input[name="quantity"]', row).value) || null,
    amount: $('input[name="amount"]', row).value || null,
    category_id: Number($('select[name="item_category"]', row).value) || null,
    category_source: row.dataset.source === 'manual' ? 'manual' : row.dataset.source,
  }));
}

function updateItemsSum() {
  const sum = readItems().reduce((acc, item) => acc + (parseFloat(item.amount) || 0), 0);
  const label = $('#items-sum');
  if (label) label.textContent = `Line items sum to ${sum.toFixed(2)}`;
}

async function saveDetail(id, confirm) {
  const form = $('#detail-form');
  const payload = {
    merchant: $('input[name="merchant"]', form).value,
    purchased_at: $('input[name="purchased_at"]', form).value,
    currency: $('input[name="currency"]', form).value || 'USD',
    subtotal: $('input[name="subtotal"]', form).value,
    tax: $('input[name="tax"]', form).value,
    tip: $('input[name="tip"]', form).value,
    total: $('input[name="total"]', form).value,
    payment_method: $('input[name="payment_method"]', form).value,
    category_id: Number($('select[name="category_id"]', form).value) || null,
    notes: $('textarea[name="notes"]', form).value,
    items: readItems(),
    confirm,
  };
  try {
    const saved = await api(`/api/receipts/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
    renderDetail(saved);
    await loadReceipts();
    toast(confirm ? `Receipt #${id} confirmed.` : `Receipt #${id} saved as a draft.`);
  } catch (error) {
    toast(`Could not save: ${error.message}`, true);
  }
}

async function rescan(id) {
  try {
    await api(`/api/receipts/${id}/rescan`, { method: 'POST' });
    toast('Re-scanning…');
    await loadReceipts();
    await selectReceipt(id, true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function removeReceipt(id) {
  if (!window.confirm(`Delete receipt #${id} and its image? This cannot be undone.`)) return;
  try {
    await api(`/api/receipts/${id}`, { method: 'DELETE' });
    closeDetail();
    await loadReceipts();
    toast(`Receipt #${id} deleted.`);
  } catch (error) {
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------- upload */

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const log = $('#upload-log');
  log.hidden = false;
  log.textContent = '';
  log.append(el('div', {}, `Uploading ${files.length} file(s)…`));

  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);
  try {
    const result = await api('/api/receipts/upload', { method: 'POST', body: form });
    log.textContent = '';
    for (const created of result.created) {
      log.append(el('div', {}, `#${created.id} ${created.file} — queued for scanning`));
    }
    for (const failed of result.errors || []) {
      log.append(el('div', { class: 'bad' }, `${failed.file}: ${failed.error}`));
    }
    await loadReceipts();
    if (result.created.length) await selectReceipt(result.created[0].id, true);
  } catch (error) {
    log.textContent = '';
    log.append(el('div', { class: 'bad' }, error.message));
  }
}

/* ------------------------------------------------------------------ reports */

function rangeToDates(range) {
  if (range === 'all') return { from: '', to: '' };
  const days = Number(range);
  const to = new Date();
  const from = new Date(to.getTime() - days * 86400000);
  const iso = (d) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(to) };
}

async function loadReport() {
  const params = new URLSearchParams();
  const from = $('#report-from').value;
  const to = $('#report-to').value;
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);
  params.set('status', $('#report-status').value);
  const data = await api(`/api/reports/summary?${params}`);
  $('#export-csv').href = `/api/export/items.csv?${params}`;
  renderReport(data);
}

function renderReport(data) {
  const root = $('#report-body');
  root.textContent = '';
  const t = data.totals;

  root.append(el('div', { class: 'tiles' },
    tile('Total spend', `$${t.spend || '0.00'}`, `${t.receipts} receipt(s)`),
    tile('Average receipt', `$${(t.average_cents / 100).toFixed(2)}`, `${t.items} line items`),
    tile('Tax paid', `$${t.tax || '0.00'}`, 'across the period'),
    tile('Awaiting review', String(data.pending_review),
      data.pending_review ? 'not counted in these figures' : 'nothing pending'),
  ));

  if (!data.by_category.length) {
    root.append(el('p', { class: 'empty' },
      'Nothing to report for this range yet. Confirm some receipts first.'));
    return;
  }

  // Spend by category: one measure across categories, so one hue. The row label
  // carries identity and the value is direct-labelled -- no legend needed.
  const max = Math.max(...data.by_category.map((b) => Math.abs(b.amount_cents)), 1);
  root.append(el('section', { class: 'card chart-card' },
    el('h2', {}, 'Spend by category'),
    el('div', { class: 'hbars' }, data.by_category.map((bucket) => el('div', { class: 'hbar' },
      el('span', { class: 'name', title: bucket.category }, bucket.category),
      el('div', { class: 'track' },
        el('div', { class: 'fill',
          style: `width:${Math.max(1, (Math.abs(bucket.amount_cents) / max) * 100)}%` })),
      el('span', { class: 'value' }, `$${bucket.amount}`)))),
  ));

  if (data.by_month.length) {
    const monthMax = Math.max(...data.by_month.map((m) => m.amount_cents), 1);
    root.append(el('section', { class: 'card chart-card' },
      el('h2', {}, 'Spend by month'),
      el('div', { class: 'columns-chart' }, data.by_month.map((month) => el('div',
        { class: 'col', tabindex: '0' },
        el('span', { class: 'tip' }, `${month.month} · $${month.amount}`),
        el('div', { class: 'bar',
          style: `height:${Math.max(2, (month.amount_cents / monthMax) * 100)}%` })))),
      el('div', { class: 'col-axis' }, data.by_month.map((month) =>
        el('span', {}, month.month.slice(2)))),
    ));
  }

  const categoryRows = data.by_category.map((bucket) => el('tr', {},
    el('td', {}, bucket.category),
    el('td', { class: 'num' }, bucket.items || '—'),
    el('td', { class: 'num' }, `$${bucket.amount}`),
    el('td', { class: 'num' }, `${(bucket.share * 100).toFixed(1)}%`)));
  const merchantRows = data.by_merchant.map((bucket) => el('tr', {},
    el('td', {}, bucket.merchant),
    el('td', { class: 'num' }, bucket.receipts),
    el('td', { class: 'num' }, `$${bucket.amount}`)));

  root.append(el('div', { class: 'columns' },
    el('section', { class: 'card' },
      el('h2', {}, 'Category table'),
      el('table', { class: 'grid' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Category'), el('th', { class: 'num' }, 'Lines'),
          el('th', { class: 'num' }, 'Amount'), el('th', { class: 'num' }, 'Share'))),
        el('tbody', {}, categoryRows))),
    el('section', { class: 'card' },
      el('h2', {}, 'Top merchants'),
      el('table', { class: 'grid' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Merchant'),
          el('th', { class: 'num' }, 'Receipts'), el('th', { class: 'num' }, 'Amount'))),
        el('tbody', {}, merchantRows))),
  ));
}

function tile(label, value, sub) {
  return el('div', { class: 'tile' },
    el('div', { class: 'label' }, label),
    el('div', { class: 'value' }, value),
    el('div', { class: 'sub' }, sub));
}

/* ------------------------------------------------------- categories & rules */

async function loadCategories() {
  const data = await api('/api/categories');
  state.categories = data.categories;
  return state.categories;
}

async function renderRulesView() {
  const body = $('#category-table tbody');
  body.textContent = '';
  for (const category of state.categories) {
    body.append(el('tr', {},
      el('td', {},
        el('span', { class: 'chip-dot', style: `background:${category.color}` }),
        category.name),
      el('td', { class: 'num' }, category.item_count),
      el('td', { class: 'num' }, category.name === 'Uncategorized' ? '' :
        el('button', { class: 'ghost', onclick: async () => {
          if (!window.confirm(`Delete '${category.name}'? Its lines move to Uncategorized.`)) return;
          await api(`/api/categories/${category.id}`, { method: 'DELETE' });
          await loadCategories();
          renderRulesView();
        } }, 'Delete'))));
  }

  const select = $('#rule-category');
  select.textContent = '';
  for (const category of state.categories) {
    select.append(el('option', { value: category.id }, category.name));
  }

  const rules = (await api('/api/rules')).rules;
  const ruleBody = $('#rule-table tbody');
  ruleBody.textContent = '';
  for (const rule of rules) {
    ruleBody.append(el('tr', {},
      el('td', { class: 'num' }, rule.priority),
      el('td', {}, rule.field === 'merchant' ? 'merchant' : 'item'),
      el('td', {}, el('code', {}, rule.pattern)),
      el('td', {}, rule.category_name),
      el('td', { class: 'num' }, el('button', { class: 'ghost', onclick: async () => {
        await api(`/api/rules/${rule.id}`, { method: 'DELETE' });
        renderRulesView();
      } }, '×'))));
  }
}

/* ----------------------------------------------------------------- settings */

async function loadSettings() {
  const settings = await api('/api/settings');
  const form = $('#settings-form');
  for (const [key, value] of Object.entries(settings)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === 'checkbox') field.checked = value === '1';
    else if (key === 'anthropic_api_key') field.placeholder = value || 'not set';
    else field.value = value;
  }
  const status = await api('/api/engines');
  const box = $('#engine-status');
  box.textContent = '';
  for (const engine of status.engines) {
    box.append(el('p', {},
      el('span', { class: `pill ${engine.available ? 'is-ok' : 'is-warn'}` },
        `${engine.name}: ${engine.available ? 'ready' : 'unavailable'}`),
      ' ', el('span', { class: 'muted' }, engine.detail)));
  }
  const health = await api('/api/health');
  $('#about').textContent =
    `Bookkeeping ${health.version} · local single-user app · no data leaves this machine `
    + 'except the receipt images sent to the Anthropic API when the Claude engine runs.';
  renderEnginePill(status);
}

function renderEnginePill(status) {
  const pill = $('#engine-pill');
  const ready = status.engines.filter((engine) => engine.available).map((e) => e.name);
  if (!ready.length) {
    pill.textContent = 'no engine ready';
    pill.className = 'pill is-warn';
    pill.title = 'Open Settings and add an API key, or install Tesseract.';
  } else {
    pill.textContent = `${status.preference}: ${ready.join(' + ')}`;
    pill.className = 'pill is-ok';
    pill.title = 'Recognition engines available';
  }
}

/* --------------------------------------------------------------------- wire */

function initTheme() {
  const stored = localStorage.getItem('bookkeeping-theme');
  const theme = stored || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.dataset.theme = theme;
  $('#theme-toggle').textContent = theme === 'dark' ? 'Light' : 'Dark';
}

function init() {
  initTheme();
  $('#theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('bookkeeping-theme', next);
    $('#theme-toggle').textContent = next === 'dark' ? 'Light' : 'Dark';
  });

  $$('.tab').forEach((tab) => tab.addEventListener('click', () => showView(tab.dataset.view)));

  const zone = $('#dropzone');
  $('#browse').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', (event) => uploadFiles(event.target.files));
  ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (event) => {
    event.preventDefault();
    zone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((name) => zone.addEventListener(name, (event) => {
    event.preventDefault();
    zone.classList.remove('is-over');
  }));
  zone.addEventListener('drop', (event) => uploadFiles(event.dataTransfer.files));

  let searchTimer = null;
  $('#filter-q').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadReceipts, 250);
  });
  ['#filter-status', '#filter-from', '#filter-to'].forEach((selector) =>
    $(selector).addEventListener('change', loadReceipts));

  $('#new-manual').addEventListener('click', async () => {
    const receipt = await api('/api/receipts/manual', { method: 'POST' });
    await loadReceipts();
    await selectReceipt(receipt.id);
  });

  $$('.chip[data-range]').forEach((chip) => chip.addEventListener('click', () => {
    $$('.chip[data-range]').forEach((other) => other.classList.remove('is-active'));
    chip.classList.add('is-active');
    const { from, to } = rangeToDates(chip.dataset.range);
    $('#report-from').value = from;
    $('#report-to').value = to;
    loadReport();
  }));
  ['#report-from', '#report-to', '#report-status'].forEach((selector) =>
    $(selector).addEventListener('change', loadReport));

  $('#category-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/categories', {
        method: 'POST',
        body: JSON.stringify({ name: form.name.value, color: form.color.value }),
      });
      form.reset();
      await loadCategories();
      renderRulesView();
      toast('Category added.');
    } catch (error) { toast(error.message, true); }
  });

  $('#rule-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/rules', {
        method: 'POST',
        body: JSON.stringify({
          field: form.field.value,
          pattern: form.pattern.value,
          category_id: Number(form.category_id.value),
          priority: Number(form.priority.value),
        }),
      });
      form.pattern.value = '';
      renderRulesView();
      toast('Rule added. Use "Re-apply rules" to backfill existing receipts.');
    } catch (error) { toast(error.message, true); }
  });

  $('#apply-rules').addEventListener('click', async () => {
    const result = await api('/api/rules/apply', { method: 'POST' });
    $('#apply-rules-result').textContent =
      `${result.changed} of ${result.examined} lines recategorised.`;
    await loadReceipts();
  });

  $('#settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const payload = {
      engine: form.engine.value,
      model: form.model.value,
      effort: form.effort.value,
      tesseract_cmd: form.tesseract_cmd.value,
      anthropic_base_url: form.anthropic_base_url.value,
      auto_confirm_clean: form.auto_confirm_clean.checked ? '1' : '0',
    };
    const key = form.anthropic_api_key.value.trim();
    if (key) payload.anthropic_api_key = key;
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
      form.anthropic_api_key.value = '';
      $('#settings-saved').textContent = 'Saved.';
      setTimeout(() => { $('#settings-saved').textContent = ''; }, 2500);
      await loadSettings();
    } catch (error) { toast(error.message, true); }
  });

  const { from, to } = rangeToDates('90');
  $('#report-from').value = from;
  $('#report-to').value = to;
  $$('.chip[data-range="90"]').forEach((chip) => chip.classList.add('is-active'));

  loadCategories().then(loadReceipts);
  api('/api/engines').then(renderEnginePill).catch(() => {});
}

document.addEventListener('DOMContentLoaded', init);
