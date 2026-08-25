'use strict';

const DIM_ORDER = ['stage', 'subject', 'version', 'grade', 'volume'];
const FMT_LABEL = { pdf: 'PDF', pptx: '课件', mp3: '音频', ogg: '音频 OGG' };
const AUDIO_KINDS = ['mp3', 'ogg'];

// Audio is not in `formats` - a book's audio is dozens of files, so it is its
// own field - but it is downloadable all the same, and every place that asks
// "does this row have X" has to know that.
const hasKind = (book, kind) =>
  AUDIO_KINDS.includes(kind) ? !!book.audio : !!(book.formats || {})[kind];
const kindsOf = (book) =>
  [...Object.keys(book.formats || {}), ...(book.audio ? ['mp3'] : [])];

// weakest first: a row is labelled by the least reliable source it relies on
const SOURCE_MARKS = [
  ['namesakes', '≈', '该资源及其所属课程均未标注；此分类取自同名资源一致的标注，仅供参考'],
  ['title', '✎', '该资源未标注；此分类由其书名解析得出'],
  ['derived', '⊢', '该资源未标注；此学段由年级与学制推导得出'],
  ['course', '↑', '该资源本身未标注；此分类继承自其所属课程'],
  ['confirmed', '✓', '平台未提供；此分类经人工核实后写入 catalogue_overrides.json'],
];

const state = {
  dimensions: [],          // [{key,label}] from the server
  filters: {},             // active dimension selections
  q: '', fmt: '', kind: '',
  page: 1, pageSize: 50, total: 0,
  books: [],               // the rows the last refresh returned
  selected: new Map(),     // id -> {id,title,formats}
  expanded: new Set(),     // ids of course bundles showing their children
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
  if (state.kind) p.set('kind', state.kind);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p.toString();
}

// ---------------------------------------------------------------- history

// The view is fully described by the query string, so every filter, search and
// page change becomes a history entry: Back steps through the view instead of
// leaving the app, and a filtered view survives a reload or a shared link.
function locationQuery() {
  const p = new URLSearchParams(queryString());
  if (state.page > 1) p.set('page', String(state.page));
  return p.toString();
}

function syncHistory(mode) {
  if (mode === 'none') return;
  const qs = locationQuery();
  const url = qs ? `${location.pathname}?${qs}` : location.pathname;
  if (url === location.pathname + location.search) return;
  if (mode === 'replace') history.replaceState(null, '', url);
  else history.pushState(null, '', url);
}

function readLocation() {
  const p = new URLSearchParams(location.search);
  state.filters = {};
  for (const key of DIM_ORDER) if (p.get(key)) state.filters[key] = p.get(key);
  state.q = p.get('q') || '';
  state.fmt = p.get('fmt') || '';
  state.kind = p.get('kind') || '';
  state.page = Math.max(1, parseInt(p.get('page'), 10) || 1);
  // the dimension selects are restored by fillFacets() from state.filters
  $('q').value = state.q;
  $('fmt').value = state.fmt;
  $('kind').value = state.kind;
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
  if (book.audio) {
    bits.push(`<span class="badge audio">🎧 ${book.audio.count} 条 `
            + `${fmtSize(book.audio.size)}</span>`);
  }
  if ((book.alternates || []).length) {
    const identical = book.alternates.every((a) => a.same_file);
    bits.push(`<span class="badge alternate">♊ ${identical ? '同一文件重复' : '同名多版本'}</span>`);
  }
  if (!bits.length) {
    if (book.kind === 'course') {
      const n = (book.children || []).length;
      bits.push(`<span class="badge course">📦 课程合集</span>`);
      bits.push(`<span class="muted small">含 ${n} 本可下载教材</span>`);
    } else if (book.kind === 'duplicate') {
      bits.push('<span class="badge duplicate">♻️ 本条无文件</span>');
      bits.push(`<span class="muted small">见 #${book.duplicate_of.seq}</span>`);
    } else {
      bits.push('<span class="badge missing">⚠️ 缺失·无法解析</span>');
    }
  }
  return bits.join('');
}

function tagsFor(book) {
  const tags = DIM_ORDER.map((k) => (book[k] || [])[0]).filter(Boolean).join(' · ');
  if (!tags) return '<span class="muted">未标注</span>';
  // Show where the weakest value came from, so a derived 分类 is never read as
  // one the platform stated. Order matters: weakest wins the label.
  const sources = new Set(Object.values(book.tag_sources || {}));
  for (const [key, mark, hint] of SOURCE_MARKS) {
    if (!sources.has(key)) continue;
    return `<span class="src-${key}" title="${hint}">${mark} ${tags}</span>`;
  }
  return tags;
}

function checkboxCell(book) {
  const cell = document.createElement('td');
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = state.selected.has(book.id);
  box.disabled = !kindsOf(book).length;
  box.addEventListener('change', () => {
    if (box.checked) state.selected.set(book.id, book);
    else state.selected.delete(book.id);
    renderSelection();
  });
  cell.appendChild(box);
  return cell;
}

async function queue(items) {
  const res = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return false; }
  return true;
}

function downloadCell(book) {
  const cell = document.createElement('td');
  cell.className = 'c-act';
  for (const kind of Object.keys(book.formats || {})) {
    const link = document.createElement('a');
    link.href = `/api/download/${encodeURIComponent(book.id)}?fmt=${kind}`
              + `&title=${encodeURIComponent(book.title)}`;
    link.innerHTML = `<button class="mini">${FMT_LABEL[kind] || kind}</button>`;
    cell.appendChild(link);
  }
  if (book.audio) {
    // Not a link: a single response cannot carry a folder of tracks, so this
    // goes through the download queue and lands on disk beside the PDFs.
    const button = document.createElement('button');
    button.className = 'mini';
    button.textContent = `🎧 ${book.audio.count} 条`;
    button.title = `下载全部音轨到 ${book.title}/ 文件夹`;
    button.addEventListener('click', async () => {
      button.disabled = true;
      const ok = await queue([{ id: book.id, title: book.title, fmt: 'mp3' }]);
      button.disabled = false;
      if (ok) refresh();
    });
    cell.appendChild(button);
  }
  return cell;
}

function bundleToggle(book) {
  const open = state.expanded.has(book.id);
  const button = document.createElement('button');
  button.className = 'mini toggle';
  button.setAttribute('aria-expanded', open ? 'true' : 'false');
  button.textContent = open ? `▾ 收起子书单`
                            : `▸ 展开 ${book.children.length} 本子教材`;
  button.addEventListener('click', () => {
    if (open) state.expanded.delete(book.id);
    else state.expanded.add(book.id);
    renderRows(state.books);            // re-render in place, no reload
  });
  return button;
}

function bookRow(book) {
  const tr = document.createElement('tr');
  if (book.kind === 'missing') tr.className = 'row-missing';
  if (book.kind === 'duplicate') tr.className = 'row-duplicate';
  if (book.kind === 'course') tr.className = 'row-course';

  const seq = document.createElement('td');
  seq.className = 'c-seq';
  seq.textContent = book.seq;

  const title = document.createElement('td');
  title.className = 'title-cell';
  title.textContent = book.title;
  if (book.kind === 'course' && (book.children || []).length) {
    const bar = document.createElement('div');
    bar.className = 'child-links';
    bar.appendChild(bundleToggle(book));
    title.appendChild(bar);
  }
  if (book.kind === 'missing') {
    const note = document.createElement('div');
    note.className = 'child-links muted';
    note.textContent = '平台未提供可解析的文件（其所属课程不在目录中）';
    title.appendChild(note);
  }
  if (book.kind === 'duplicate') {
    // the file exists, just on another row: send the reader there rather than
    // implying the book cannot be had
    const note = document.createElement('div');
    note.className = 'child-links muted';
    const other = Object.entries(book.duplicate_of.formats || {})
      .map(([k, v]) => `${FMT_LABEL[k] || k} ${fmtSize(v)}`).join('、');
    note.append(`本条记录没有任何文件；平台把同一本书发布了两次，文件挂在另一条上（${other}）：`);
    const jump = document.createElement('a');
    jump.textContent = `#${book.duplicate_of.seq} 可下载`;
    jump.title = '跳转到有文件的那条记录';
    jump.addEventListener('click', () => {
      state.q = book.title; state.kind = ''; state.page = 1;
      $('q').value = book.title; $('kind').value = '';
      refresh();
    });
    note.appendChild(jump);
    title.appendChild(note);
  }

  // both copies have a file: say so, with sizes, so a bulk download is not
  // silently the same book twice
  for (const alt of book.alternates || []) {
    const note = document.createElement('div');
    note.className = 'child-links muted';
    const sizes = Object.entries(alt.formats || {})
      .map(([k, v]) => `${FMT_LABEL[k] || k} ${fmtSize(v)}`).join('、');
    note.append(alt.same_file
      ? '完全相同的文件（大小一字节不差）也发布在另一条记录上：'
      : '同名同分类的另一条记录，是另一份扫描件（体积不同）：');
    const jump = document.createElement('a');
    jump.textContent = `#${alt.seq} ${sizes}`;
    jump.title = '跳转查看另一版本';
    jump.addEventListener('click', () => {
      state.q = book.title; state.kind = ''; state.page = 1;
      $('q').value = book.title; $('kind').value = '';
      refresh();
    });
    note.appendChild(jump);
    title.appendChild(note);
  }

  const tags = document.createElement('td');
  tags.className = 'muted small';
  tags.innerHTML = tagsFor(book);

  const files = document.createElement('td');
  files.innerHTML = badgesFor(book);

  tr.append(checkboxCell(book), seq, title, tags, files, downloadCell(book));
  return tr;
}

function childRow(child, index, count) {
  const tr = document.createElement('tr');
  tr.className = 'row-child' + (index === count - 1 ? ' row-child-last' : '');

  const seq = document.createElement('td');
  seq.className = 'c-seq';
  seq.textContent = child.seq == null ? '' : `#${child.seq}`;

  const title = document.createElement('td');
  title.className = 'title-cell child-title';
  title.textContent = child.title;

  const tags = document.createElement('td');
  tags.className = 'muted small';
  tags.textContent = '（合集内教材）';

  const files = document.createElement('td');
  files.innerHTML = badgesFor(child);

  tr.append(checkboxCell(child), seq, title, tags, files, downloadCell(child));
  return tr;
}

function renderRows(books) {
  const tbody = $('rows');
  if (!books.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">没有符合条件的资源</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  for (const book of books) {
    tbody.appendChild(bookRow(book));
    if (book.kind !== 'course' || !state.expanded.has(book.id)) continue;
    const children = book.children || [];
    children.forEach((child, i) => tbody.appendChild(childRow(child, i, children.length)));
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
async function refresh(nav = 'push') {
  const token = ++refreshToken;
  syncHistory(nav);
  try {
    const [facetData, bookData] = await Promise.all([
      getJSON('/api/facets?' + queryString()),
      getJSON('/api/books?' + queryString({ page: state.page, page_size: state.pageSize })),
    ]);
    if (token !== refreshToken) return;   // a newer request already answered
    fillFacets(facetData.facets);
    state.total = bookData.total;
    state.books = bookData.books;
    renderRows(state.books);
    $('count').textContent = bookData.total.toLocaleString('zh-CN');
    const k = facetData.kinds || {};
    $('kind-summary').innerHTML =
      `　<span class="kind-key">` +
      `<span>✅ 可下载 ${(k.downloadable || 0).toLocaleString('zh-CN')}</span>` +
      `<span>📦 课程合集 ${(k.course || 0).toLocaleString('zh-CN')}</span>` +
      `<span>♊ 同名多版本 ${(k.alternate || 0).toLocaleString('zh-CN')}</span>` +
      `<span class="muted">（已含于可下载）</span>` +
      `<span>♻️ 本条无文件 ${(k.duplicate || 0).toLocaleString('zh-CN')}</span>` +
      `<span style="color:var(--bad)">⚠️ 缺失 ${(k.missing || 0).toLocaleString('zh-CN')}</span>` +
      `</span>`;
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
    // replace, not push: typing a query would otherwise bury the previous view
    // under one history entry per keystroke pause
    typing = setTimeout(() => {
      state.q = e.target.value.trim(); state.page = 1; refresh('replace');
    }, 250);
  });
  $('fmt').addEventListener('change', (e) => { state.fmt = e.target.value; state.page = 1; refresh(); });
  $('kind').addEventListener('change', (e) => {
    state.kind = e.target.value; state.page = 1; refresh();
  });
  window.addEventListener('popstate', () => { readLocation(); refresh('none'); });
  $('reset').addEventListener('click', () => {
    state.filters = {}; state.q = ''; state.fmt = ''; state.kind = ''; state.page = 1;
    $('q').value = ''; $('fmt').value = ''; $('kind').value = '';
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
      const kinds = choice === 'all' ? kindsOf(book) : [choice];
      for (const kind of kinds) {
        if (hasKind(book, kind)) items.push({ id: book.id, title: book.title, fmt: kind });
      }
    }
    if (!items.length) { alert('所选资源没有对应格式的文件'); return; }
    if (!await queue(items)) return;
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
  readLocation();
  await refresh('replace');
})();
