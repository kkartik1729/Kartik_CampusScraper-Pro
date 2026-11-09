// CampusScraper web UI. Plain JavaScript, no build step.
const $ = id => document.getElementById(id);
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function list(v) { return Array.isArray(v) ? v.join(', ') : (v ?? ''); }
function safeUrl(u) { return /^https?:\/\//i.test(u || '') ? u : '#'; }
// Long text keeps its row the same height as every other row; the whole value
// stays in the tooltip and in the exported file.
function clamp(v, lines = 2) {
  const text = list(v);
  return text ? `<div class="clamp${lines === 1 ? ' l1' : lines >= 3 ? ' l3' : ''}" title="${esc(text)}">${esc(text)}</div>` : '';
}
function say(el, text, kind = 'info') { el.textContent = text || ''; el.className = 'msg ' + kind; }

const FIELD_NAMES = { urls: 'Start URL', max_pages: 'Max pages', max_depth: 'Max link depth', delay_seconds: 'Delay',
  pages_per_site: 'Pages per college', colleges_at_once: 'Colleges at once', departments: 'Departments', render: 'JavaScript option', url: 'URL' };

// Turn any server answer into {ok, data, error} with a readable error.
async function api(url, opts = {}) {
  let r;
  try { r = await fetch(url, opts); }
  catch (e) { return { ok: false, error: 'Cannot reach the scraper. Is the "serve" window still open?' }; }
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (r.ok) return { ok: true, data };
  let error = `Error ${r.status}`;
  const d = data && data.detail;
  if (typeof d === 'string') error = d;
  else if (Array.isArray(d)) error = d.map(x => `${FIELD_NAMES[x.loc?.[x.loc.length - 1]] || x.loc?.[x.loc.length - 1] || 'Value'}: ${x.msg}`).join('; ');
  return { ok: false, error, status: r.status };
}
const postJSON = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

// Download without leaving the page; show a message if the file is not ready.
async function download(url, msgEl, btn) {
  btn && (btn.disabled = true);
  try {
    const r = await fetch(url);
    if (!r.ok) {
      let t = `Download failed (${r.status})`;
      try { const j = await r.json(); if (j.detail) t = String(j.detail); } catch (e) {}
      say(msgEl, t === 'nothing found yet' ? 'Nothing found yet. Try again in a few seconds.' : t, 'bad');
      return;
    }
    const blob = await r.blob();
    const cd = r.headers.get('content-disposition') || '';
    const name = (cd.match(/filename="?([^";]+)"?/) || [])[1] || url.split('/').pop();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    say(msgEl, `Downloaded ${name}`, 'ok');
  } catch (e) {
    say(msgEl, 'Cannot reach the scraper. Is the "serve" window still open?', 'bad');
  } finally { btn && (btn.disabled = false); }
}

// Number boxes: check before sending, with the field name in the message.
function num(id, label, min, max) {
  const v = $(id).value.trim();
  const n = Number(v);
  if (v === '' || !Number.isFinite(n) || n < min || n > max) throw new Error(`${label} must be a number from ${min} to ${max}.`);
  return n;
}

// ---- results table (single crawl or batch) ------------------------------------
let rows = [], offices = [], resultsFrom = '';
function showRows(newRows, from, newOffices) {
  rows = newRows || []; resultsFrom = from; $('results-card').hidden = false;
  if (newOffices) offices = newOffices;
  $('results-from').textContent = from ? `from ${from}` : '';
  render();
}
function render() {
  const q = $('filter').value.trim().toLowerCase();
  const shown = rows.filter(d => !q || JSON.stringify(d).toLowerCase().includes(q));
  $('count').textContent = `${shown.length} faculty`;
  renderOffices(q);
  if (!shown.length) {
    $('rows').innerHTML = `<tr><td colspan="11" class="empty">${rows.length ? 'Nobody matches this filter.' : 'No faculty found yet.'}</td></tr>`;
    return;
  }
  $('rows').innerHTML = shown.map(d => `<tr><td>${clamp(d.name)}${d.page_type === 'profile' ? '' : '<span class="pill">card</span>'}</td>
    <td>${clamp(d.designation)}</td><td>${clamp(d.department)}</td>
    <td class="contact">${clamp(d.email, 2)}</td><td class="contact">${clamp(d.phone, 1)}</td>
    <td class="muted">${clamp([d.contact_email, d.contact_phone].filter(Boolean).join('; '))}</td>
    <td>${clamp(d.institution || d.source_institution)}</td><td>${clamp(d.qualifications)}</td><td>${clamp(d.research_areas)}</td>
    <td>${d.google_scholar ? `<a href="${esc(safeUrl(d.google_scholar))}" target="_blank" rel="noopener">Scholar</a>` : ''}${d.orcid ? ` ORCID ${esc(d.orcid)}` : ''}</td>
    <td><a href="${esc(safeUrl(d.profile_url || d.source_url))}" target="_blank" rel="noopener">open</a></td></tr>`).join('');
}
const OFFICE_NAMES = { placement: 'Placement', higher_studies: 'Higher studies', student_chapter: 'Student chapter',
  alumni: 'Alumni', international: 'International', student_affairs: 'Student affairs' };
function renderOffices(q) {
  const shown = offices.filter(o => !q || JSON.stringify(o).toLowerCase().includes(q));
  $('office-count').textContent = `${shown.length} office${shown.length === 1 ? '' : 's'}`;
  $('office-rows').innerHTML = shown.length ? shown.map(o => `<tr><td><span class="pill">${esc(OFFICE_NAMES[o.office_type] || o.office_type)}</span></td>
    <td>${clamp(o.office_name)}<span class="sub">${esc(o.source_institution || o.institution)}</span></td>
    <td class="contact">${clamp(o.emails, 2)}</td><td class="contact">${clamp(o.phones, 2)}</td>
    <td>${clamp((o.coordinators || []).join('; '))}</td><td class="muted">${clamp((o.higher_studies_lines || []).join(' | '))}</td>
    <td class="muted">${o.skipped_student_lines || ''}</td>
    <td><a href="${esc(safeUrl(o.page_url))}" target="_blank" rel="noopener">open</a></td></tr>`).join('')
    : `<tr><td colspan="8" class="empty">No student offices found yet.</td></tr>`;
}
$('filter').oninput = render;

// Pause keeps everything found so far and waits; Resume carries on from the same place.
async function togglePause(btn, base, msgEl) {
  const action = btn.textContent === 'Pause' ? 'pause' : 'resume';
  btn.disabled = true;
  const r = await postJSON(`${base}/${action}`);
  btn.disabled = false;
  if (r.ok) btn.textContent = action === 'pause' ? 'Resume' : 'Pause';
  else say(msgEl, r.error, 'bad');
}

// ---- single website crawl -------------------------------------------------------
let jobId = null, timer = null;
function setJobButtons(running) {
  $('start').disabled = running; $('stop').disabled = !running; $('pause').disabled = !running;
  if (!running) $('pause').textContent = 'Pause';
}
function attachJob(id) {
  jobId = id;
  $('progress-card').hidden = false; $('downloads').hidden = false; $('error').textContent = '';
  setJobButtons(true);
  clearInterval(timer); timer = setInterval(poll, 1500); poll();
}
$('start').onclick = async () => {
  const urls = $('urls').value.split(/\s+/).filter(Boolean);
  if (!urls.length) { say($('msg'), 'Enter at least one URL, for example https://www.college.ac.in/faculty', 'bad'); $('urls').focus(); return; }
  let body;
  try {
    body = { urls, max_pages: num('max_pages', 'Max pages', 1, 5000), max_depth: num('depth', 'Max link depth', 0, 10),
      delay_seconds: num('delay', 'Delay', 0, 30), render: $('render').value, all_people: $('all').checked,
      use_sitemaps: $('sitemap').checked, include_subdomains: $('subdomains').checked,
      departments: $('departments').value.trim(), include_offices: $('offices').checked,
      workers: $('workers') ? num('workers', 'Concurrent workers', 1, 32) : 6 };
  } catch (e) { say($('msg'), e.message, 'bad'); return; }
  $('start').disabled = true;
  const r = await postJSON('/api/jobs', body);
  if (!r.ok) { $('start').disabled = false; say($('msg'), r.error, 'bad'); return; }
  say($('msg'), 'Crawl started.', 'ok');
  $('log').textContent = '';
  showRows([], 'this website crawl');
  attachJob(r.data.id);
};
$('stop').onclick = async () => {
  if (jobId) await postJSON(`/api/jobs/${jobId}/stop`);
  $('stop').disabled = true; $('pause').disabled = true;
  $('start').disabled = false;  // a new crawl can start while the old one finishes its last pages
  say($('msg'), 'Stopping: pages already being fetched finish, everything found is kept. You can start a new crawl now.', 'info');
};
$('pause').onclick = () => jobId && togglePause($('pause'), `/api/jobs/${jobId}`, $('msg'));
for (const fmt of ['xlsx', 'csv', 'json'])
  $('dl-' + fmt).onclick = () => jobId && download(`/api/jobs/${jobId}/download.${fmt}`, $('error'), $('dl-' + fmt));

async function poll() {
  const r = await api(`/api/jobs/${jobId}`);
  if (!r.ok) { say($('error'), r.error, 'bad'); return; }
  const j = r.data;
  $('state').textContent = j.state; $('state').className = 'state ' + j.state;
  $('s-fetched').textContent = j.progress.fetched; $('s-queued').textContent = j.progress.queued;
  $('s-doctors').textContent = j.progress.doctors_total; $('s-onco').textContent = j.progress.faculty;
  $('s-offices').textContent = j.progress.offices || 0;
  $('s-time').textContent = j.elapsed + 's';
  if ($('global-live-text')) {
    $('global-live-text').textContent = j.state === 'running' ? `Running crawl (${j.progress.fetched} pages, ${j.progress.faculty} faculty)` : j.state === 'paused' ? 'Crawl Paused' : 'Engine Ready';
  }
  $('bar').style.width = Math.min(100, 100 * j.progress.fetched / j.max_pages) + '%';
  const atBottom = $('log').scrollHeight - $('log').scrollTop - $('log').clientHeight < 30;
  $('log').innerHTML = j.log.map(l => {
    const cls = l.error ? 'x' : (l.type === 'profile' ? 'p' : (l.type === 'listing' ? 'l' : ''));
    return `<span class="${cls}">${esc((l.type || '').padEnd(8))} ${esc(l.status)} ${esc(l.url)}${l.doctors ? '  +' + l.doctors : ''}${l.error ? '  (' + esc(l.error) + ')' : ''}</span>`;
  }).join('\n');
  if (atBottom) $('log').scrollTop = $('log').scrollHeight;  // follow the log unless the user scrolled up
  if (resultsFrom === 'this website crawl' || !resultsFrom) showRows(j.doctors, 'this website crawl', j.offices);
  $('pause').textContent = j.state === 'paused' ? 'Resume' : 'Pause';
  if (j.state === 'stopping') { $('start').disabled = false; $('stop').disabled = true; $('pause').disabled = true; return; }
  if (j.state !== 'running' && j.state !== 'paused') {
    clearInterval(timer); setJobButtons(false);
    if (j.error) say($('error'), j.error, 'bad');
    else say($('msg'), `Crawl ${j.state}. ${j.progress.faculty} faculty and ${j.progress.offices || 0} student offices found.`, 'ok');
  }
}

// ---- test one page ---------------------------------------------------------------
$('page-btn').onclick = async () => {
  const url = $('page-url').value.trim();
  if (!/^https?:\/\//i.test(url)) { $('page-sum').hidden = false; $('page-sum').innerHTML = '<p class="msg bad">Enter a full address starting with http:// or https://</p>'; return; }
  $('page-btn').disabled = true; $('page-sum').hidden = false; $('page-sum').innerHTML = '<p class="msg info">Fetching…</p>';
  const r = await postJSON('/api/page', { url, render: $('render').value });
  $('page-btn').disabled = false;
  if (!r.ok) { $('page-sum').innerHTML = `<p class="msg bad">${esc(r.error)}</p>`; return; }
  const j = r.data;
  const docs = j.doctors || [];
  const head = j.error ? `<p class="msg bad">Could not read the page: ${esc(j.error)}</p>`
    : `<p class="msg ok">Page type: <b>${esc(j.page_type)}</b>${j.rendered ? ' (read with a real browser)' : ''} · ${docs.length} ${docs.length === 1 ? 'person' : 'people'} found${(j.offices || []).length ? ' · student office page' : ''}</p>`;
  const offs = (j.offices || []).map(o => `<dl><dt>Office</dt><dd><b>${esc(o.office_name)}</b> <span class="pill">${esc(OFFICE_NAMES[o.office_type] || o.office_type)}</span></dd>
    ${['emails', 'phones', 'coordinators', 'higher_studies_lines', 'skipped_student_lines'].filter(k => o[k] && (!Array.isArray(o[k]) || o[k].length))
      .map(k => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${esc(list(o[k]))}</dd>`).join('')}</dl>`).join('<hr>');
  const one = d => `<dl><dt>Name</dt><dd><b>${esc(d.name)}</b>${d.is_relevant ? ' <span class="pill">faculty</span>' : ''}</dd>
    ${['designation', 'department', 'qualifications', 'experience_years', 'institution', 'city', 'email', 'phone', 'contact_email', 'contact_phone', 'research_areas', 'subjects', 'google_scholar', 'orcid', 'scopus_id', 'vidwan', 'linkedin']
      .filter(k => d[k] && (!Array.isArray(d[k]) || d[k].length))
      .map(k => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${esc(list(d[k]))}</dd>`).join('')}</dl>`;
  $('page-sum').innerHTML = head + offs + docs.slice(0, 10).map(one).join('<hr>') + (docs.length > 10 ? `<p class="hint">…and ${docs.length - 10} more</p>` : '');
  $('page-raw').hidden = false; $('page-out').textContent = JSON.stringify(j, null, 2);
};
$('page-url').onkeydown = e => { if (e.key === 'Enter') $('page-btn').click(); };

// ---- college list batch ------------------------------------------------------------
let batchId = null, batchTimer = null, batchPolls = 0;
$('b-template').onclick = () => download('/api/template.xlsx', $('b-msg'), $('b-template'));
async function fileBody() {
  const f = $('b-file').files[0];
  if (!f) { say($('b-msg'), 'Choose your college list (.xlsx or .csv) first.', 'bad'); return null; }
  if (!/\.(xlsx|xlsm|csv)$/i.test(f.name)) { say($('b-msg'), 'The file must be .xlsx or .csv, made from the blank template.', 'bad'); return null; }
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return { filename: f.name, content_base64: btoa(bin) };
}
$('b-file').onchange = () => { $('b-check-out').hidden = true; say($('b-msg'), ''); };
$('b-check').onclick = async () => {
  const body = await fileBody(); if (!body) return;
  $('b-check').disabled = true;
  const r = await postJSON('/api/sources/check', body);
  $('b-check').disabled = false;
  const out = $('b-check-out');
  if (!r.ok) { out.hidden = true; say($('b-msg'), r.error, 'bad'); return; }
  const j = r.data; out.hidden = false; say($('b-msg'), '');
  const skipped = p => /^skipped/.test(p.problem);
  const srcRows = j.sources.map(s => `<tr><td>${s.row}</td><td>${esc(s.institution)}</td><td>${s.urls.length}</td><td>${esc(s.patterns.join(' '))}</td><td class="warn">${esc(s.warnings.join('; '))}</td></tr>`).join('');
  const probs = j.problems.map(p => `<tr><td>${p.row}</td><td>${esc(p.institution)}</td><td colspan="3" class="${skipped(p) ? 'muted' : 'bad'}">${esc(p.problem)}</td></tr>`).join('');
  const fix = j.problems.filter(p => !skipped(p)).length, skip = j.problems.length - fix;
  out.innerHTML = `<p><b class="${j.usable ? 'ok' : 'bad'}">${j.usable} college${j.usable === 1 ? '' : 's'} ready</b>${fix ? `, <b class="bad">${fix} row${fix === 1 ? '' : 's'} need fixing</b>` : ''}${skip ? `, ${skip} skipped (Enabled = no)` : ''}</p>
    <div class="tablewrap" style="max-height:300px"><table><colgroup><col style="width:60px"><col style="width:220px"><col style="width:90px"><col style="width:160px"><col style="width:240px"></colgroup><thead><tr><th>Row</th><th>College</th><th>Start URLs</th><th>Profile pattern</th><th>Warnings / problems</th></tr></thead><tbody>${probs}${srcRows}</tbody></table></div>`;
};
function setBatchButtons(running) {
  $('b-start').disabled = running; $('b-stop').disabled = !running; $('b-pause').disabled = !running;
  if (!running) $('b-pause').textContent = 'Pause';
}
function attachBatch(id) {
  batchId = id; batchPolls = 0;
  $('b-progress').hidden = false; $('b-dl').hidden = false;
  $('b-dl-btn').textContent = 'Download results so far';
  $('b-totals').textContent = 'Partial file: results from all finished and currently crawling colleges.';
  setBatchButtons(true);
  clearInterval(batchTimer); batchTimer = setInterval(pollBatch, 2000); pollBatch();
}
$('b-start').onclick = async () => {
  const body = await fileBody(); if (!body) return;
  try {
    Object.assign(body, { pages_per_site: num('b-pages', 'Pages per college', 1, 5000), delay_seconds: num('b-delay', 'Delay', 0, 30),
      render: $('b-render').value, all_people: $('b-all').checked, use_sitemaps: $('b-sitemap').checked,
      departments: $('b-departments').value.trim(), include_offices: $('b-offices').checked,
      colleges_at_once: num('b-at-once', 'Colleges at once', 1, 32),
      workers: $('b-workers') ? num('b-workers', 'Workers per college', 1, 32) : 6 });
  } catch (e) { say($('b-msg'), e.message, 'bad'); return; }
  $('b-start').disabled = true;
  const r = await postJSON('/api/batch', body);
  if (!r.ok) { $('b-start').disabled = false; say($('b-msg'), r.error, 'bad'); return; }
  $('b-sites').innerHTML = '';
  say($('b-msg'), `${r.data.colleges} colleges queued` + (r.data.problems.length ? `, ${r.data.problems.length} rows skipped` : '') + '.', 'ok');
  attachBatch(r.data.id);
};
$('b-stop').onclick = async () => {
  if (batchId) await postJSON(`/api/batch/${batchId}/stop`);
  $('b-stop').disabled = true; $('b-pause').disabled = true;
  $('b-start').disabled = true;
  say($('b-msg'), 'Stopping: colleges being crawled finish their current pages, everything found is kept.', 'info');
};
$('b-pause').onclick = () => batchId && togglePause($('b-pause'), `/api/batch/${batchId}`, $('b-msg'));
$('b-dl-btn').onclick = () => batchId && download(`/api/batch/${batchId}/download.xlsx`, $('b-msg'), $('b-dl-btn'));
$('b-show').onclick = async () => { await loadBatchDoctors(); $('results-card').scrollIntoView({ behavior: 'smooth' }); };
async function loadBatchDoctors() {
  const r = await api(`/api/batch/${batchId}/doctors`);
  if (r.ok) showRows(r.data.doctors, 'the college list batch', r.data.offices);
}
function batchDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  return seconds < 60 ? seconds + 's' : seconds < 3600
    ? Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's'
    : Math.floor(seconds / 3600) + 'h ' + Math.floor(seconds % 3600 / 60) + 'm';
}
async function pollBatch() {
  const r = await api(`/api/batch/${batchId}`);
  if (!r.ok) { say($('b-msg'), r.error, 'bad'); return; }
  const j = r.data;
  const terminal = ['done', 'stopped', 'failed'].includes(j.state);
  const finalizing = !terminal && j.phase === 'finalizing';
  $('b-idx').textContent = `${j.completed}/${j.total}`;
  $('b-time').textContent = batchDuration(j.elapsed);
  $('b-pagesnow').textContent = j.progress.fetched || 0;
  $('b-onconow').textContent = j.progress.faculty || 0;
  if ($('global-live-text')) {
    $('global-live-text').textContent = j.state === 'running' ? `Batch ${j.completed}/${j.total} colleges (${j.progress.faculty || 0} faculty)` : j.state === 'paused' ? 'Batch Paused' : 'Engine Ready';
  }
  $('b-bar').style.width = (100 * j.completed / Math.max(1, j.total)) + '%';
  $('b-current').textContent = terminal
    ? (j.state === 'done' ? `Batch complete — Excel ready.${j.failed ? ' ' + j.failed + ' colleges failed; review their notes.' : ''}` : `Batch ${j.state} — results may be incomplete.`)
    : finalizing ? 'Crawling finished — preparing the combined Excel file…'
    : j.state === 'paused' ? `Paused — ${j.current || 'waiting to start'}. Press Resume to continue.`
    : j.state === 'stopping' ? 'Stopping — finishing requests already in progress and saving partial results…'
    : `Crawling now: ${j.current || 'starting workers…'}`;
  $('b-remaining').textContent = `${j.completed} finished · ${j.active} crawling · ${j.queued} queued · ${j.failed} failed`
    + (terminal ? ` · ${j.total - j.completed} unfinished`
       : finalizing ? ' · Download will be ready after export.'
       : ' · Time remaining: unknown. Websites reveal new pages during crawling; page limits are ceilings, not completion targets.');
  $('b-pause').textContent = j.state === 'paused' ? 'Resume' : 'Pause';
  $('b-start').disabled = !terminal;
  $('b-stop').disabled = terminal || finalizing || j.state === 'stopping';
  $('b-pause').disabled = terminal || finalizing || j.state === 'stopping';
  const wrap = $('b-wrap'), keep = wrap.scrollTop;
  const labels = {done: 'Finished', resumed: 'Finished (saved)', crawling: j.state === 'paused' ? 'Paused' : 'Crawling',
    queued: 'Queued', stopped: 'Stopped', failed: 'Failed', error: 'Error', interrupted: 'Interrupted', 'not started': 'Not started'};
  $('b-sites').innerHTML = j.rows.map(s => {
    const active = s.status === 'crawling';
    const status = s.status === 'done' && s.note ? 'Finished — see note' : labels[s.status] || s.status;
    return `<tr${active ? ' style="background:var(--bg);font-weight:600"' : ''}><td title="Spreadsheet row ${s.row}">${s.sequence}</td>
      <td>${clamp(s.institution, 2)}</td><td class="${['error', 'failed', 'interrupted'].includes(s.status) ? 'bad' : ['done', 'resumed'].includes(s.status) ? 'ok' : 'muted'}">${esc(status)}</td>
      <td>${s.pages}${active ? ' / ' + s.page_limit + ' max' : ''}</td><td>${s.faculty}</td><td>${s.offices}</td>
      <td>${s.with_phone ?? '—'}</td><td>${s.with_email ?? '—'}</td><td>${s.with_any_contact ?? '—'}</td>
      <td class="muted">${clamp(active ? s.queued + ' discovered pages waiting; more may be found.' : s.note, 3)}</td></tr>`;
  }).join('');
  wrap.scrollTop = keep;
  if (resultsFrom === 'the college list batch' && (++batchPolls % 3 === 0)) loadBatchDoctors();  // refresh shown doctors
  if (j.state !== 'running' && j.state !== 'paused' && j.state !== 'stopping') {
    clearInterval(batchTimer); setBatchButtons(false);
    if (j.error) say($('b-msg'), j.error, 'bad');
    if (j.stats) {
      $('b-dl-btn').textContent = 'Download combined Excel';
      $('b-totals').textContent = `${j.stats.faculty} faculty (${j.stats.faculty_with_own_email} with own email, ${j.stats.faculty_with_own_phone} with own phone, ${j.stats.faculty_with_any_contact} with any contact), ${j.stats.student_offices} student offices.`;
    }
    if (resultsFrom === 'the college list batch') loadBatchDoctors();
  }
}

// ---- after a page reload, pick up runs that are still going --------------------------
(async () => {
  const r = await api('/api/active');
  if (!r.ok) return;
  const { job, batch } = r.data;
  if (batch) {
    attachBatch(batch.id);
    if (batch.state === 'running' || batch.state === 'paused')
      say($('b-msg'), 'Reconnected to the batch that is still running.', 'info');
  }
  if (job) {
    attachJob(job.id);
    if (job.state === 'running' || job.state === 'paused') say($('msg'), 'Reconnected to the crawl that is still running.', 'info');
  }
})();
