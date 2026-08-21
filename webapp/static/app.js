'use strict';

const DIM_ORDER = ['stage', 'subject', 'version', 'grade', 'volume'];
const FMT_LABEL = { pdf: 'PDF', pptx: '课件' };

const state = {
  dimensions: [],          // [{key,label}] from the server
  filters: {},             // active dimension selections
  q: '', fmt: '', availability: '',
  page: 1, pageSize: 50, total: 0,
  selected: new Map(),     // id -> {id,title,formats}
  jobs: new Map(),
};

const $ = (id) => document.getElementById(id);
const fmtSize = (b) => !b ? '' : b >= 1073741824 ? (b / 1073741824).toFixed(1) + ' GB'
                                : b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB'
                                : (b / 1024).toFixed(0) + ' KB';

function queryString(extra = {}) {
  const p = new URLSearchParams();
  for (const key of DIM_ORDER) if (state.filters[key]) p.set(key, state.filters[key]);
  if (state.q) p.set('q', state.q);
  if (state.fmt) p.set('fmt', state.fmt);
  if (state.availability) p.set('availability', state.availability);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p.toString();
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}

// ---------------------------------------------------------------- filters

function renderDimensionFilters() {
  const host = $('dimension-filters');
  host.innerHTML = '';
  for (const dim of state.dimensions) {
    const label = document.createElement('label');
    label.className = 'field';
    label.innerHTML = `<span>${dim.label}</span>`;
    const select = document.createElement('select');
    select.id = `dim-${dim.key}`;
    select.addEventListener('change', () => {
      state.filters[dim.key] = select.value;
      // cascade: choosing a broader level clears the narrower ones below it
      const from = DIM_ORDER.indexOf(dim.key);
      DIM_ORDER.slice(from + 1).forEach((k) => { state.filters[k] = ''; });
      state.page = 1;
      refresh();
    });
    label.appendChild(select);
    host.appendChild(label);
  }
}

function fillFacets(facets) {
  for (const dim of state.dimensions) {
    const select = $(`dim-${dim.key}`);
    if (!select) continue;
    const current = state.filters[dim.key] || '';
    const options = facets[dim.key] || [];
    select.innerHTML = '';
    const all = new Option(`全部${dim.label}`, '');
    select.add(all);
    let matched = false;
    for (const opt of options) {
      select.add(new Option(`${opt.value}（${opt.count}）`, opt.value));
      if (opt.value === current) matched = true;
    }
    // keep a selection that the current facet set no longer offers
    if (current && !matched) select.add(new Option(`${current}（0）`, current));
    select.value = current;
  }
}

// ---------------------------------------------------------------- results

function badgesFor(book) {
  const bits = [];
  for (const [kind, size] of Object.entries(book.formats || {})) {
    bits.push(`<span class="badge ${kind}">${FMT_LABEL[kind] || kind} ${fmtSize(size)}</span>`);
  }
  if (!bits.length) {
    bits.push(book.status === 'restricted'
      ? '<span class="badge locked">无可下载文件</span>'
      : '<span class="badge">无可下载文件</span>');
  }
  return bits.join('');
}

function tagsFor(book) {
  return DIM_ORDER.map((k) => (book[k] || [])[0]).filter(Boolean).join(' · ') || '<span class="muted">未标注</span>';
}

function renderRows(books) {
  const tbody = $('rows');
  if (!books.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">没有符合条件的资源</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  for (const book of books) {
    const tr = document.createElement('tr');

    const check = document.createElement('td');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.selected.has(book.id);
    box.disabled = !Object.keys(book.formats || {}).length;
    box.addEventListener('change', () => {
      if (box.checked) state.selected.set(book.id, book);
      else state.selected.delete(book.id);
      renderSelection();
    });
    check.appendChild(box);

    const seq = document.createElement('td');
    seq.className = 'c-seq';
    seq.textContent = book.seq;

    const title = document.createElement('td');
    title.className = 'title-cell';
    title.textContent = book.title;

    const tags = document.createElement('td');
    tags.className = 'muted small';
    tags.innerHTML = tagsFor(book);

    const files = document.createElement('td');
    files.innerHTML = badgesFor(book);

    const actions = document.createElement('td');
    actions.className = 'c-act';
    for (const kind of Object.keys(book.formats || {})) {
      const link = document.createElement('a');
      link.href = `/api/download/${encodeURIComponent(book.id)}?fmt=${kind}`
                + `&title=${encodeURIComponent(book.title)}`;
      link.innerHTML = `<button class="mini">${FMT_LABEL[kind] || kind}</button>`;
      actions.appendChild(link);
    }

    tr.append(check, seq, title, tags, files, actions);
    tbody.appendChild(tr);
  }
}

function renderSelection() {
  const n = state.selected.size;
  $('selection-info').textContent = n ? `　已选 ${n} 项` : '';
  $('bulk-download').disabled = n === 0;
}

// ---------------------------------------------------------------- jobs

function statusText(item) {
  return { pending: '等待中', running: '下载中', done: '完成',
           failed: item.error || '失败', cancelled: '已取消', skipped: '已存在' }[item.status] || item.status;
}

function renderJobs() {
  const panel = $('jobs-panel');
  const host = $('jobs');
  const jobs = [...state.jobs.values()].sort((a, b) => b.created - a.created);
  panel.hidden = jobs.length === 0;
  host.innerHTML = '';

  for (const job of jobs) {
    const bytesDone = job.items.reduce((sum, i) => sum + (i.written || 0), 0);
    const bytesTotal = job.items.reduce((sum, i) => sum + (i.total || 0), 0);
    const pct = bytesTotal ? Math.min(100, (bytesDone / bytesTotal) * 100)
                           : (job.completed / job.total) * 100;

    const el = document.createElement('div');
    el.className = 'job';
    el.innerHTML = `
      <div class="job-head">
        <strong>${job.finished ? '任务完成' : '正在下载'} · ${job.completed}/${job.total}</strong>
        <span class="muted small">
          成功 ${job.succeeded}${job.failed ? ` · 失败 ${job.failed}` : ''}
          ${bytesTotal ? ` · ${fmtSize(bytesDone)} / ${fmtSize(bytesTotal)}` : ''}
        </span>
      </div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="job-items"></div>`;

    if (!job.finished) {
      const cancel = document.createElement('button');
      cancel.className = 'mini';
      cancel.textContent = '取消';
      cancel.addEventListener('click', () => fetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' }));
      el.querySelector('.job-head').appendChild(cancel);
    }

    const list = el.querySelector('.job-items');
    for (const item of job.items) {
      const row = document.createElement('div');
      row.className = 'job-item';
      const size = item.total ? ` (${fmtSize(item.written)} / ${fmtSize(item.total)})` : '';
      row.innerHTML = `<span>${item.title} <span class="muted">${FMT_LABEL[item.fmt] || item.fmt}</span></span>
                       <span class="st-${item.status}">${statusText(item)}${item.status === 'running' ? size : ''}</span>`;
      list.appendChild(row);
    }
    host.appendChild(el);
  }
}

function connectEvents() {
  const source = new EventSource('/api/events');
  source.onmessage = (event) => {
    const job = JSON.parse(event.data);
    state.jobs.set(job.id, job);
    renderJobs();
  };
  source.onerror = () => { /* EventSource retries on its own */ };
}

// ---------------------------------------------------------------- loading

let refreshToken = 0;
async function refresh() {
  const token = ++refreshToken;
  try {
    const [facetData, bookData] = await Promise.all([
      getJSON('/api/facets?' + queryString()),
      getJSON('/api/books?' + queryString({ page: state.page, page_size: state.pageSize })),
    ]);
    if (token !== refreshToken) return;   // a newer request already answered
    fillFacets(facetData.facets);
    state.total = bookData.total;
    renderRows(bookData.books);
    $('count').textContent = bookData.total.toLocaleString('zh-CN');
    const pages = Math.max(1, Math.ceil(bookData.total / state.pageSize));
    $('page-info').textContent = `第 ${state.page} / ${pages} 页`;
    $('prev').disabled = state.page <= 1;
    $('next').disabled = state.page >= pages;
    $('select-page').checked = false;
  } catch (err) {
    $('rows').innerHTML = `<tr><td colspan="6" class="empty">载入失败：${err.message}</td></tr>`;
  }
}

async function waitForIndex() {
  for (;;) {
    const status = await getJSON('/api/status');
    state.dimensions = status.dimensions;
    $('outdir').textContent = status.output_dir;
    if (status.ready) {
      $('status').textContent = '目录已就绪';
      return;
    }
    $('status').textContent = '正在建立索引，首次运行需要几分钟…';
    await new Promise((r) => setTimeout(r, 2000));
  }
}

function bindControls() {
  let typing;
  $('q').addEventListener('input', (e) => {
    clearTimeout(typing);
    typing = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; refresh(); }, 250);
  });
  $('fmt').addEventListener('change', (e) => { state.fmt = e.target.value; state.page = 1; refresh(); });
  $('availability').addEventListener('change', (e) => {
    state.availability = e.target.value; state.page = 1; refresh();
  });
  $('reset').addEventListener('click', () => {
    state.filters = {}; state.q = ''; state.fmt = ''; state.availability = ''; state.page = 1;
    $('q').value = ''; $('fmt').value = ''; $('availability').value = '';
    refresh();
  });
  $('prev').addEventListener('click', () => { if (state.page > 1) { state.page--; refresh(); } });
  $('next').addEventListener('click', () => { state.page++; refresh(); });

  $('select-page').addEventListener('change', (e) => {
    const boxes = $('rows').querySelectorAll('input[type="checkbox"]');
    boxes.forEach((box) => { if (box.checked !== e.target.checked && !box.disabled) box.click(); });
  });

  $('bulk-download').addEventListener('click', async () => {
    const choice = $('bulk-format').value;
    const items = [];
    for (const book of state.selected.values()) {
      const kinds = choice === 'all' ? Object.keys(book.formats || {}) : [choice];
      for (const kind of kinds) {
        if (book.formats && book.formats[kind]) items.push({ id: book.id, title: book.title, fmt: kind });
      }
    }
    if (!items.length) { alert('所选资源没有对应格式的文件'); return; }
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    state.selected.clear();
    renderSelection();
    refresh();
  });

  $('reindex').addEventListener('click', async () => {
    if (!confirm('重建索引需要重新扫描全部资源，可能耗时数分钟，确定继续？')) return;
    await fetch('/api/reindex', { method: 'POST' });
    $('status').textContent = '正在重建索引…';
    await waitForIndex();
    refresh();
  });
}

(async function init() {
  bindControls();
  connectEvents();
  await waitForIndex();
  renderDimensionFilters();
  await refresh();
})();
