// Portal pages: Dashboard, Start New Scraping, College Management, Live Monitor,
// Faculty Records, URL Discovery, Failed Websites, Excel Export Center,
// Scraping History, Settings, System Performance, and Student Offices.
// Uses helpers from app.js.
const VIEWS = [
  'dashboard', 'new', 'colleges', 'monitor', 'directory',
  'urls', 'failed', 'exports', 'runs', 'settings', 'performance', 'offices'
];
let viewTimer = null;

function fmtTime(t) {
  if (!t) return '';
  const d = new Date(t * 1000);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function timeCell(t) {
  if (!t) return '';
  const d = new Date(t * 1000);
  return `${d.toLocaleDateString()}<span class="sub">${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>`;
}
function fmtDur(a, b, live = true) {
  if (!a) return '';
  if (!b && !live) return '\u2014';
  const s = Math.max(0, Math.round(((b || Date.now() / 1000) - a)));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
}
function fmtSeconds(sec) {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return '\u2014';
  const s = Math.max(0, Math.round(sec));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
}

const STATE_CLASS = {
  done: 'ok', finished: 'ok', completed: 'ok', resumed: 'ok', running: 'ok', crawling: 'ok',
  paused: 'warn', pausing: 'warn', waiting: 'warn', partial: 'warn', clarify: 'warn',
  stopping: 'warn', interrupted: 'warn', stopped: 'warn', queued: 'warn',
  failed: 'bad', error: 'bad'
};
const STATE_WORDS = {
  queued: 'Queued', running: 'Running', crawling: 'Crawling', paused: 'Paused',
  pausing: 'Pausing', stopping: 'Stopping', stopped: 'Stopped', waiting: 'Waiting',
  done: 'Finished', completed: 'Completed', resumed: 'Finished (saved)',
  partial: 'Finished with gaps', failed: 'Failed', error: 'Error',
  interrupted: 'Interrupted', clarify: 'Needs a detail', sources_ready: 'Source URLs ready',
  no_candidates: 'No colleges verified', planning_failed: 'Could not read the prompt',
  unsupported: 'Not supported', idle: 'Idle'
};
function stateWord(s) {
  return STATE_WORDS[s] || String(s || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
}
function stateCell(s) {
  return `<span class="${STATE_CLASS[s] || ''}"><b>${esc(stateWord(s))}</b></span>`;
}

function show(view) {
  if (!VIEWS.includes(view)) view = 'dashboard';
  for (const v of VIEWS) {
    const el = $('view-' + v);
    if (el) el.hidden = v !== view;
  }
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + view));
  clearInterval(viewTimer);
  if (view === 'dashboard') { loadDashboard(); viewTimer = setInterval(loadDashboard, 5000); }
  if (view === 'colleges') { loadColleges(); }
  if (view === 'monitor') { loadMonitor(); viewTimer = setInterval(loadMonitor, 2000); }
  if (view === 'directory') { loadDirectory(); }
  if (view === 'urls') { loadUrls(); }
  if (view === 'failed') { loadFailed(); }
  if (view === 'exports') { loadExports(); viewTimer = setInterval(pollExportJobsOnly, 2500); }
  if (view === 'runs') { loadRuns(); viewTimer = setInterval(loadRuns, 4000); }
  if (view === 'settings') { loadSettings(); }
  if (view === 'performance') { loadPerformance(); viewTimer = setInterval(loadPerformance, 2500); }
  if (view === 'offices') { loadOffices(); }
}
window.addEventListener('hashchange', () => show(location.hash.slice(1)));

// ---- 1. Dashboard ----------------------------------------------------------------
async function loadDashboard() {
  const [d, r] = await Promise.all([api('/api/dashboard'), api('/api/runs')]);
  if (!d.ok) { $('d-totals').innerHTML = `<p class="msg bad">${esc(d.error)}</p>`; return; }
  const t = d.data.totals;
  const pct = n => t.faculty ? ` <small>(${Math.round(100 * n / t.faculty)}%)</small>` : '';
  $('d-totals').innerHTML = [
    ['Faculty', t.faculty], ['Colleges', t.colleges], ['Departments', t.departments],
    ['With own email', t.with_email + pct(t.with_email)], ['With any contact', t.with_contact + pct(t.with_contact)],
    ['With Scholar / ORCID', t.with_profile + pct(t.with_profile)],
    ['Student offices', t.offices + (t.offices ? ` <small>(${t.offices_with_email} with email)</small>` : '')],
    ['Placement cells', t.placement_cells], ['Higher-studies cells', t.higher_studies_cells],
    ['Runs', t.runs + (t.active_runs ? ` <small>(${t.active_runs} active)</small>` : '')],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join('');
  const cs = d.data.colleges;
  $('d-coll').innerHTML = cs.length ? cs.map(c => {
    const n = Math.max(1, c.fac), e = 100 * c.email / n, p = 100 * Math.max(0, c.phone - c.email) / n, o = 100 * Math.max(0, c.contact - Math.max(c.email, c.phone)) / n;
    const types = (c.office_types || []).map(x => OFFICE_NAMES[x] || x).join(', ');
    return `<tr><td><a href="#directory" data-coll="${esc(c.c)}" class="clamp l3" title="${esc(c.c)}">${esc(c.c)}</a></td><td>${c.fac}</td><td>${c.email}</td><td>${c.phone}</td><td>${c.contact}</td><td>${c.profile}</td>
      <td>${c.offices ? `<a href="#offices" data-ocoll="${esc(c.c)}">${c.offices}</a> <span class="muted">${esc(types)}</span>` : '0'}</td>
      <td><div class="cov" title="${Math.round(100 * c.contact / n)}% have some contact"><i class="e" style="width:${e}%"></i><i class="p" style="width:${p}%"></i><i class="o" style="width:${o}%"></i></div></td></tr>`;
  }).join('') + `<tr><td colspan="8" class="legend">Coverage:<b style="background:#10b981"></b>own email<b style="background:#38bdf8"></b>own phone<b style="background:#64748b"></b>department / office contact only</td></tr>`
    : `<tr><td colspan="8" class="empty">Nothing saved yet. Start a run under <a href="#new">New run</a>.</td></tr>`;
  document.querySelectorAll('#d-coll a[data-coll]').forEach(a => a.onclick = () => { $('x-coll').dataset.want = a.dataset.coll; });
  document.querySelectorAll('#d-coll a[data-ocoll]').forEach(a => a.onclick = () => { $('o-coll').dataset.want = a.dataset.ocoll; });
  if (r.ok) $('d-runs').innerHTML = r.data.runs.slice(0, 8).map(x => `<tr><td class="nowrap">${timeCell(x.started)}</td><td>${clamp(x.title, 2)}</td><td>${stateCell(x.state)}</td><td>${x.faculty_saved}</td><td>${x.offices_saved}</td></tr>`).join('')
    || `<tr><td colspan="5" class="empty">No runs yet.</td></tr>`;
}

// ---- 3. College Management -------------------------------------------------------
let allCollegesCache = [];
async function loadColleges() {
  const r = await api('/api/colleges');
  if (!r.ok) { say($('cm-msg'), r.error, 'bad'); return; }
  say($('cm-msg'), '');
  allCollegesCache = r.data.colleges || [];
  renderColleges();
}
function renderColleges() {
  if (!$('cm-rows')) return;
  const q = ($('cm-q')?.value || '').trim().toLowerCase();
  const st = ($('cm-status')?.value || '').trim().toLowerCase();
  const filtered = allCollegesCache.filter(c => {
    if (st && String(c.status || '').toLowerCase() !== st) return false;
    if (q && !`${c.college} ${c.urls?.join(' ') || ''}`.toLowerCase().includes(q)) return false;
    return true;
  });
  $('cm-count').textContent = `${filtered.length} college${filtered.length === 1 ? '' : 's'}`;
  if (!filtered.length) {
    $('cm-rows').innerHTML = `<tr><td colspan="9" class="empty">No colleges match the current filters.</td></tr>`;
    return;
  }
  $('cm-rows').innerHTML = filtered.slice(0, 300).map(c => {
    const firstUrl = (c.urls && c.urls[0]) || (c.start_urls ? String(c.start_urls).split(/\s+/)[0] : '');
    return `<tr>
      <td><b>${clamp(c.college, 2)}</b></td>
      <td>${stateCell(c.status || 'done')}</td>
      <td>${c.faculty || 0}</td>
      <td>${c.with_email || 0}</td>
      <td>${c.with_phone || 0}</td>
      <td>${c.offices || 0}</td>
      <td>${c.pages || 0}</td>
      <td>${firstUrl ? `<a href="${esc(safeUrl(firstUrl))}" target="_blank" rel="noopener" class="clamp l1" title="${esc(firstUrl)}">${esc(firstUrl)}</a>` : '<span class="muted">\u2014</span>'}</td>
      <td class="actions"><div class="btns">
        <button class="secondary" data-cm-view="${esc(c.college)}">Faculty</button>
        ${firstUrl ? `<button data-cm-scrape="${esc(firstUrl)}" data-cm-name="${esc(c.college)}">Rescrape</button>` : ''}
      </div></td>
    </tr>`;
  }).join('');
}
if ($('cm-q')) $('cm-q').oninput = renderColleges;
if ($('cm-status')) $('cm-status').onchange = renderColleges;
if ($('cm-refresh')) $('cm-refresh').onclick = loadColleges;
if ($('cm-clear')) $('cm-clear').onclick = () => {
  $('cm-q').value = '';
  $('cm-status').value = '';
  renderColleges();
};
if ($('cm-rows')) $('cm-rows').onclick = async e => {
  const vb = e.target.closest('button[data-cm-view]');
  if (vb) {
    $('x-coll').dataset.want = vb.dataset.cmView;
    location.hash = '#directory';
    return;
  }
  const sb = e.target.closest('button[data-cm-scrape]');
  if (sb) {
    sb.disabled = true;
    const r = await postJSON('/api/failed-websites/retry', { urls: [sb.dataset.cmScrape] });
    sb.disabled = false;
    if (!r.ok) { say($('cm-msg'), r.error, 'bad'); return; }
    say($('cm-msg'), `Started scraping ${sb.dataset.cmName}. Switching to Live Monitor…`, 'ok');
    attachJob(r.data.id);
    setTimeout(() => { location.hash = '#monitor'; }, 600);
  }
};

// ---- 4. Live Scraping Monitor ----------------------------------------------------
let lastMonitorData = null;
async function loadMonitor() {
  const r = await api('/api/monitor/live');
  if (!r.ok) { if ($('m-msg')) say($('m-msg'), r.error, 'bad'); return; }
  const m = lastMonitorData = r.data;
  const runCount = m.running_colleges_count ?? (Array.isArray(m.running_colleges) ? m.running_colleges.length : m.running_colleges ?? 0);
  const discUrls = m.urls_discovered ?? m.total_urls_discovered ?? 0;
  const procUrls = m.urls_processed ?? m.total_urls_processed ?? 0;
  const totFac = m.total_faculty ?? m.total_faculty_extracted ?? 0;
  const valEmails = m.valid_emails ?? m.valid_email_count ?? 0;
  const valPhones = m.valid_phones ?? m.valid_phone_count ?? 0;
  const dups = m.duplicates_removed ?? m.duplicate_records_removed ?? 0;
  const speed = m.scraping_speed ?? m.scraping_speed_pages_per_min ?? 0;
  const avgTime = m.avg_time_per_college ?? m.avg_time_per_college_sec ?? 0;
  const pct = m.progress_pct ?? m.overall_progress_pct ?? 0;
  const eta = m.eta_seconds ?? m.estimated_remaining_sec;

  if ($('m-total-colleges')) $('m-total-colleges').textContent = m.total_colleges ?? 0;
  if ($('m-completed-colleges')) $('m-completed-colleges').textContent = m.completed_colleges ?? 0;
  if ($('m-running-colleges')) $('m-running-colleges').textContent = runCount;
  if ($('m-pending-colleges')) $('m-pending-colleges').textContent = m.pending_colleges ?? 0;
  if ($('m-failed-colleges')) $('m-failed-colleges').textContent = m.failed_colleges ?? 0;
  if ($('m-urls-discovered')) $('m-urls-discovered').textContent = discUrls;
  if ($('m-urls-processed')) $('m-urls-processed').textContent = procUrls;
  if ($('m-total-faculty')) $('m-total-faculty').textContent = totFac;
  if ($('m-valid-emails')) $('m-valid-emails').textContent = valEmails;
  if ($('m-valid-phones')) $('m-valid-phones').textContent = valPhones;
  if ($('m-duplicates')) $('m-duplicates').textContent = dups;
  if ($('m-speed')) $('m-speed').textContent = `${speed} pg/m`;
  if ($('m-avg-time')) $('m-avg-time').textContent = `${avgTime}s`;
  if ($('m-progress-pct')) $('m-progress-pct').textContent = `${pct}%`;
  if ($('m-eta')) $('m-eta').textContent = eta !== null && eta !== undefined ? fmtSeconds(eta) : '\u2014';
  if ($('m-bar-fill')) $('m-bar-fill').style.width = `${Math.min(100, pct)}%`;

  const isRunning = m.state === 'running' || m.state === 'crawling';
  const isPaused = m.state === 'paused';
  if ($('m-pause-btn')) $('m-pause-btn').disabled = !isRunning;
  if ($('m-resume-btn')) $('m-resume-btn').disabled = !isPaused;
  if ($('m-stop-btn')) $('m-stop-btn').disabled = !(isRunning || isPaused);
  if ($('m-retry-btn')) $('m-retry-btn').disabled = !(m.failed_colleges > 0 && !isRunning);

  if ($('global-live-text')) {
    $('global-live-text').textContent = isRunning
      ? `Scraping: ${m.completed_colleges}/${m.total_colleges} colleges · ${totFac} faculty`
      : isPaused ? 'Scraping Paused' : 'Engine Ready';
  }

  // Running colleges table
  if ($('m-running-rows')) {
    const rc = m.running_colleges || m.running_college_details || [];
    $('m-running-rows').innerHTML = rc.length ? rc.map(c => `<tr>
      <td><b>${clamp(c.institution || c.college_name, 1)}</b></td>
      <td><a href="${esc(safeUrl(c.website))}" target="_blank" rel="noopener" class="clamp l1">${esc(c.website || '\u2014')}</a></td>
      <td><span class="pill">${esc(c.stage || 'crawling')}</span></td>
      <td>${c.discovered ?? c.pages_discovered ?? 0}</td>
      <td>${c.pages ?? c.pages_processed ?? 0}</td>
      <td><b>${c.faculty ?? c.faculty_extracted ?? 0}</b></td>
      <td>${stateCell(c.status || 'running')}</td>
      <td>${fmtSeconds(c.duration ?? c.duration_sec)}</td>
    </tr>`).join('') : `<tr><td colspan="8" class="empty">${isRunning ? 'Starting next college worker…' : 'No active colleges running right now.'}</td></tr>`;
  }

  // Live activity console
  if ($('m-console')) {
    const logs = m.console || m.activity_console || [];
    const atBottom = $('m-console').scrollHeight - $('m-console').scrollTop - $('m-console').clientHeight < 40;
    $('m-console').innerHTML = logs.length ? logs.map(line => {
      const cls = /failed|error/i.test(line) ? 'x' : (/extracted|completed|finished|ready/i.test(line) ? 'p' : (/discovered|faculty page/i.test(line) ? 'l' : ''));
      return `<div class="${cls}">${esc(line)}</div>`;
    }).join('') : `<div class="muted">[--:--:--] Waiting for scraping activity…</div>`;
    if (atBottom) $('m-console').scrollTop = $('m-console').scrollHeight;
  }

  // All colleges in current job
  if ($('m-all-rows')) {
    const all = m.rows || m.all_college_rows || [];
    $('m-all-rows').innerHTML = all.length ? all.map(c => `<tr>
      <td>${c.sequence || c.row || ''}</td>
      <td><b>${clamp(c.institution, 1)}</b></td>
      <td><span class="pill">${esc(c.stage || c.status || '')}</span></td>
      <td>${stateCell(c.status)}</td>
      <td>${c.discovered ?? c.pages ?? 0}</td>
      <td>${c.pages ?? 0}</td>
      <td><b>${c.faculty ?? 0}</b></td>
      <td>${c.with_email ?? 0}</td>
      <td>${c.with_phone ?? 0}</td>
      <td>${fmtSeconds(c.duration)}</td>
    </tr>`).join('') : `<tr><td colspan="10" class="empty">No batch or job loaded yet.</td></tr>`;
  }
}

async function monitorControl(action) {
  if (!lastMonitorData || !lastMonitorData.active_id) {
    say($('m-msg'), 'No active job to control.', 'bad');
    return;
  }
  const kind = lastMonitorData.mode || lastMonitorData.active_kind;
  const base = kind === 'batch'
    ? `/api/batch/${lastMonitorData.active_id}`
    : `/api/jobs/${lastMonitorData.active_id}`;
  const r = await postJSON(`${base}/${action}`);
  if (!r.ok) say($('m-msg'), r.error, 'bad');
  else {
    say($('m-msg'), `Action '${action}' applied.`, 'ok');
    loadMonitor();
  }
}
if ($('m-pause-btn')) $('m-pause-btn').onclick = () => monitorControl('pause');
if ($('m-resume-btn')) $('m-resume-btn').onclick = () => monitorControl('resume');
if ($('m-stop-btn')) $('m-stop-btn').onclick = () => monitorControl('stop');
if ($('m-retry-btn')) $('m-retry-btn').onclick = async () => {
  const kind = lastMonitorData && (lastMonitorData.mode || lastMonitorData.active_kind);
  if (lastMonitorData && kind === 'batch' && lastMonitorData.active_id) {
    const r = await postJSON(`/api/batch/${lastMonitorData.active_id}/retry-failed`);
    if (!r.ok) say($('m-msg'), r.error, 'bad');
    else { say($('m-msg'), `Retrying ${r.data.retrying || 'failed'} colleges…`, 'ok'); loadMonitor(); }
  } else {
    location.hash = '#failed';
  }
};

// ---- 6. URL Discovery ------------------------------------------------------------
async function loadUrls() {
  const q = ($('u-q')?.value || '').trim();
  const r = await api('/api/urls/discovered?limit=300' + (q ? '&q=' + encodeURIComponent(q) : ''));
  if (!r.ok) { say($('u-msg'), r.error, 'bad'); return; }
  say($('u-msg'), '');
  const urls = r.data.urls || [];
  if ($('u-count')) $('u-count').textContent = `${r.data.total || urls.length} URLs`;
  if ($('u-rows')) {
    $('u-rows').innerHTML = urls.length ? urls.map(u => `<tr>
      <td><a href="${esc(safeUrl(u.url))}" target="_blank" rel="noopener" class="clamp l1" title="${esc(u.url)}">${esc(u.url)}</a></td>
      <td><span class="pill">${esc(u.type || u.page_type || 'page')}</span></td>
      <td>${esc(String(u.status ?? '200'))}</td>
      <td>${u.doctors ?? u.faculty_count ?? 0}</td>
      <td>${clamp(u.college || u.institution || '\u2014', 1)}</td>
    </tr>`).join('') : `<tr><td colspan="5" class="empty">No discovered URLs match your filter.</td></tr>`;
  }
}
if ($('u-q')) {
  let uTimer = null;
  $('u-q').oninput = () => { clearTimeout(uTimer); uTimer = setTimeout(loadUrls, 250); };
}
if ($('u-refresh')) $('u-refresh').onclick = loadUrls;
if ($('u-clear')) $('u-clear').onclick = () => { $('u-q').value = ''; loadUrls(); };

// ---- 7. Failed Websites ----------------------------------------------------------
let failedSitesCache = [];
async function loadFailed() {
  const r = await api('/api/failed-websites');
  if (!r.ok) { say($('fw-msg'), r.error, 'bad'); return; }
  say($('fw-msg'), '');
  failedSitesCache = r.data.failed_colleges || r.data.failed || [];
  if ($('fw-count')) $('fw-count').textContent = `${failedSitesCache.length} failed`;
  if ($('fw-rows')) {
    $('fw-rows').innerHTML = failedSitesCache.length ? failedSitesCache.map((f, idx) => {
      const firstUrl = f.url || (f.start_urls ? String(f.start_urls).split(/\s+/)[0] : '');
      return `<tr>
        <td><b>${clamp(f.institution || 'Unknown college', 1)}</b></td>
        <td>${firstUrl ? `<a href="${esc(safeUrl(firstUrl))}" target="_blank" rel="noopener" class="clamp l1">${esc(firstUrl)}</a>` : '<span class="muted">\u2014</span>'}</td>
        <td>${stateCell(f.status || 'failed')}</td>
        <td>${f.pages || 0}</td>
        <td class="bad">${clamp(f.note || f.reason || 'Failed during crawl', 2)}</td>
        <td class="actions"><button data-fw-idx="${idx}">Retry</button></td>
      </tr>`;
    }).join('') : `<tr><td colspan="6" class="empty">No failed websites recorded. All scraped colleges succeeded!</td></tr>`;
  }
}
if ($('fw-refresh')) $('fw-refresh').onclick = loadFailed;
if ($('fw-rows')) $('fw-rows').onclick = async e => {
  const b = e.target.closest('button[data-fw-idx]');
  if (!b) return;
  const item = failedSitesCache[Number(b.dataset.fwIdx)];
  if (!item) return;
  const firstUrl = item.url || (item.start_urls ? String(item.start_urls).split(/\s+/)[0] : '');
  b.disabled = true;
  const r = await postJSON('/api/failed-websites/retry', {
    run_id: item.run_id || null,
    urls: firstUrl ? [firstUrl] : []
  });
  b.disabled = false;
  if (!r.ok) { say($('fw-msg'), r.error, 'bad'); return; }
  say($('fw-msg'), `Retry started for ${item.institution || firstUrl}.`, 'ok');
  if (r.data.kind === 'batch') attachBatch(r.data.id);
  else attachJob(r.data.id);
};
if ($('fw-retry-all')) $('fw-retry-all').onclick = async () => {
  if (!failedSitesCache.length) { say($('fw-msg'), 'No failed websites to retry.', 'info'); return; }
  const urls = failedSitesCache.map(x => x.url || (x.start_urls ? String(x.start_urls).split(/\s+/)[0] : '')).filter(Boolean);
  const firstWithRun = failedSitesCache.find(x => x.run_id);
  $('fw-retry-all').disabled = true;
  const r = await postJSON('/api/failed-websites/retry', {
    run_id: !urls.length && firstWithRun ? firstWithRun.run_id : null,
    urls
  });
  $('fw-retry-all').disabled = false;
  if (!r.ok) { say($('fw-msg'), r.error, 'bad'); return; }
  say($('fw-msg'), 'Retry job started! Open Live Monitor to watch progress.', 'ok');
  if (r.data.kind === 'batch') attachBatch(r.data.id);
  else attachJob(r.data.id);
};

// ---- 8. Excel Export Center & Data Quality ---------------------------------------
async function loadExports() {
  const [dirRes, expRes] = await Promise.all([api('/api/directory?page=1'), api('/api/exports')]);
  if (dirRes.ok && dirRes.data.facets) {
    fillSelect($('ex-state'), dirRes.data.facets.cities || [], 'All cities / states');
    fillSelect($('ex-college'), dirRes.data.facets.colleges || [], 'All colleges');
    fillSelect($('ex-dept'), dirRes.data.facets.departments || [], 'All departments');
  }
  if (expRes.ok) renderExportJobs(expRes.data.jobs || expRes.data.exports || []);
  loadExportQuality();
}
async function pollExportJobsOnly() {
  const expRes = await api('/api/exports');
  if (expRes.ok) renderExportJobs(expRes.data.jobs || expRes.data.exports || []);
}
function exportFilterParams() {
  return {
    city: $('ex-state')?.value || '',
    college: $('ex-college')?.value || '',
    department: $('ex-dept')?.value || '',
    q: $('ex-q')?.value.trim() || '',
    has_email: !!$('ex-email')?.checked,
    has_phone: !!$('ex-phone')?.checked,
    has_contact: !!$('ex-contact')?.checked,
    all_people: !!$('ex-all')?.checked
  };
}
async function loadExportQuality() {
  const f = exportFilterParams();
  const p = new URLSearchParams();
  if (f.college) p.set('college', f.college);
  if (f.department) p.set('department', f.department);
  if (f.city) p.set('city', f.city);
  if (f.all_people) p.set('all_people', 'true');
  const r = await api('/api/quality/summary?' + p.toString());
  if (!r.ok) return;
  const q = r.data;
  if ($('q-stats')) {
    $('q-stats').innerHTML = [
      ['Total Records', q.total_records],
      ['Quality Score', `${q.quality_score}%`],
      ['Avg Confidence', `${q.average_confidence}/100`],
      ['Valid Emails', q.valid_emails],
      ['Valid Phones', q.valid_phones],
      ['High Confidence', q.high_confidence_records],
      ['Duplicate Candidates', q.duplicate_records],
      ['Missing Designation', q.missing_designations],
      ['Suspicious Records', q.suspicious_records],
      ['Broken URLs', q.broken_source_urls]
    ].map(([k, v]) => `<div class="stat"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
  }
  if ($('q-flagged-rows')) {
    const fl = q.flagged_samples || [];
    $('q-flagged-rows').innerHTML = fl.length ? fl.map(item => `<tr>
      <td><b>${clamp(item.name || '(empty)', 1)}</b></td>
      <td>${clamp(item.institution || '\u2014', 1)}</td>
      <td>${item.confidence}/100</td>
      <td class="warn">${esc((item.issues || []).join(', '))}</td>
      <td>${item.source_url ? `<a href="${esc(safeUrl(item.source_url))}" target="_blank" rel="noopener">open</a>` : '\u2014'}</td>
    </tr>`).join('') : `<tr><td colspan="5" class="empty">Zero quality issues detected in selected records.</td></tr>`;
  }
}
function renderExportJobs(jobs) {
  if (!$('ex-job-rows')) return;
  $('ex-job-rows').innerHTML = jobs.length ? jobs.map(j => {
    const v = j.validation || {};
    const validText = j.state === 'done'
      ? (v.ok ? `Valid (${v.faculty_rows || j.total_records} rows, ${Math.round((v.size_bytes || 0) / 1024)} KB)` : `Invalid: ${v.error || 'error'}`)
      : (j.error || '\u2014');
    return `<tr>
      <td><code>${esc(j.id)}</code></td>
      <td><b>${clamp(j.title, 1)}</b></td>
      <td>${stateCell(j.state)} <span class="muted">(${esc(j.stage || '')} ${j.progress || 0}%)</span></td>
      <td>${j.total_records || 0}</td>
      <td>${j.elapsed ? j.elapsed + 's' : '\u2014'}</td>
      <td class="${v.ok ? 'ok' : j.error ? 'bad' : 'muted'}">${esc(validText)}</td>
      <td class="actions">${j.state === 'done' ? `<button data-ex-dl="${esc(j.id)}">Download .xlsx</button>` : '<span class="muted">Working…</span>'}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="7" class="empty">No background export jobs started yet. Click "Generate Master Excel (Async)" above.</td></tr>`;
}
if ($('ex-start-btn')) $('ex-start-btn').onclick = async () => {
  const f = exportFilterParams();
  const titleParts = ['Master Export'];
  if (f.city) titleParts.push(f.city);
  if (f.college) titleParts.push(f.college);
  if (f.department) titleParts.push(f.department);
  $('ex-start-btn').disabled = true;
  const r = await postJSON('/api/exports/start', { ...f, title: titleParts.join(' - ') });
  $('ex-start-btn').disabled = false;
  if (!r.ok) { say($('ex-msg'), r.error, 'bad'); return; }
  say($('ex-msg'), `Background Excel export job ${r.data.id} started!`, 'ok');
  pollExportJobsOnly();
};
if ($('ex-direct-btn')) $('ex-direct-btn').onclick = () => {
  const f = exportFilterParams();
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v === true) p.set(k, 'true');
    else if (v) p.set(k, String(v));
  }
  download('/api/directory/download.xlsx?' + p.toString(), $('ex-msg'), $('ex-direct-btn'));
};
if ($('ex-quality-btn')) $('ex-quality-btn').onclick = loadExportQuality;
if ($('ex-clear-btn')) $('ex-clear-btn').onclick = () => {
  for (const id of ['ex-state', 'ex-college', 'ex-dept', 'ex-q']) if ($(id)) $(id).value = '';
  for (const id of ['ex-email', 'ex-phone', 'ex-contact', 'ex-all']) if ($(id)) $(id).checked = false;
  loadExportQuality();
};
if ($('ex-job-rows')) $('ex-job-rows').onclick = e => {
  const b = e.target.closest('button[data-ex-dl]');
  if (!b) return;
  download(`/api/exports/${b.dataset.exDl}/download.xlsx`, $('ex-msg'), b);
};

// ---- 9. Scraping History (Runs) --------------------------------------------------
const armed = new Set();
async function loadRuns() {
  const r = await api('/api/runs');
  if (!r.ok) { say($('r-msg'), r.error, 'bad'); return; }
  $('r-rows').innerHTML = r.data.runs.map(x => {
    const live = ['queued','running','paused','pausing','waiting','stopping'].includes(x.state);
    const acts = [];
    if (live || x.kind === 'prompt') acts.push(`<button data-act="open" data-id="${x.id}" data-kind="${x.kind}">Open</button>`);
    acts.push(`<button class="secondary" data-act="dl" data-id="${x.id}">Excel</button>`);
    if (!live && x.kind === 'batch' && x.has_upload && x.state !== 'done') acts.push(`<button data-act="resume" data-id="${x.id}">Resume</button>`);
    if (x.kind === 'prompt' && ['paused','stopped','interrupted','failed'].includes(x.state)) acts.push(`<button data-act="resume" data-id="${x.id}" data-kind="prompt">Resume</button>`);
    if (!live && x.kind === 'crawl') acts.push(`<button class="secondary" data-act="rerun" data-id="${x.id}">Run again</button>`);
    if (!live) acts.push(`<button class="secondary" data-act="del" data-id="${x.id}">${armed.has(x.id) ? 'Click again to delete' : 'Delete'}</button>`);
    return `<tr><td class="nowrap">${timeCell(x.started)}</td><td class="nowrap">${x.kind === 'prompt' ? 'Prompt' : x.kind === 'batch' ? 'College list' : 'One website'}</td><td>${clamp(x.title, 3)}</td>
      <td>${stateCell(x.state)}${x.error ? `<div class="muted clamp" title="${esc(x.error)}">${esc(x.error)}</div>` : ''}</td><td class="nowrap">${fmtDur(x.started, live ? null : x.finished, live)}</td>
      <td>${x.faculty_saved}</td><td>${x.offices_saved}</td><td class="actions"><div class="btns">${acts.join('')}</div></td></tr>`;
  }).join('') || `<tr><td colspan="8" class="empty">No runs yet. Start one under <a href="#new">New run</a>.</td></tr>`;
}
$('r-rows').onclick = async e => {
  const b = e.target.closest('button[data-act]'); if (!b) return;
  const id = b.dataset.id, act = b.dataset.act;
  if (act === 'open') { location.hash = '#new'; if (b.dataset.kind === 'prompt') { attachPrompt(id); $('prompt-card').scrollIntoView(); } else b.dataset.kind === 'batch' ? attachBatch(id) : attachJob(id); return; }
  if (act === 'dl') { download(`/api/runs/${id}/download.xlsx`, $('r-msg'), b); return; }
  if (act === 'del') {
    if (!armed.has(id)) { armed.add(id); b.textContent = 'Click again to delete'; setTimeout(() => armed.delete(id), 5000); return; }
    const r = await api(`/api/runs/${id}`, { method: 'DELETE' });
    say($('r-msg'), r.ok ? 'Run deleted.' : r.error, r.ok ? 'ok' : 'bad'); armed.delete(id); loadRuns(); return;
  }
  b.disabled = true;
  const r = await postJSON(`/api/runs/${id}/${act}`);
  b.disabled = false;
  if (!r.ok) { say($('r-msg'), r.error, 'bad'); return; }
  location.hash = '#new';
  if (r.data.kind === 'prompt') { attachPrompt(r.data.id); return; }
  if (act === 'resume') { say($('b-msg'), `Resumed: finished colleges are kept, ${r.data.colleges} in the list.`, 'ok'); attachBatch(r.data.id); }
  else { showRows([], 'this website crawl', []); attachJob(r.data.id); }
};

function fillSelect(sel, values, label, names) {
  if (!sel) return;
  const want = sel.dataset.want || sel.value;
  sel.innerHTML = `<option value="">${label}</option>` + values.map(v => `<option value="${esc(v)}"${v === want ? ' selected' : ''}>${esc((names && names[v]) || v)}</option>`).join('');
  delete sel.dataset.want;
}

// ---- 5. Faculty Directory --------------------------------------------------------
const dirState = { page: 1 };
function dirQuery() {
  const p = new URLSearchParams();
  const v = { q: $('x-q').value, college: $('x-coll').dataset.want || $('x-coll').value, department: $('x-dept').value, city: $('x-city').value };
  for (const [k, x] of Object.entries(v)) if (x) p.set(k, x);
  for (const [k, id] of [['has_email', 'x-email'], ['has_phone', 'x-phone'], ['has_contact', 'x-contact'], ['has_profile', 'x-profile'], ['all_people', 'x-all']])
    if ($(id).checked) p.set(k, 'true');
  return p;
}
async function loadDirectory() {
  const p = dirQuery(); p.set('page', dirState.page);
  const r = await api('/api/directory?' + p);
  if (!r.ok) { say($('x-msg'), r.error, 'bad'); return; }
  const j = r.data; say($('x-msg'), '');
  fillSelect($('x-coll'), j.facets.colleges, 'All colleges');
  fillSelect($('x-dept'), j.facets.departments, 'All departments');
  fillSelect($('x-city'), j.facets.cities, 'All cities');
  $('x-count').textContent = `${j.total} ${j.total === 1 ? 'person' : 'people'}`;
  $('x-page').textContent = `Page ${j.page} of ${j.pages}`;
  $('x-prev').disabled = j.page <= 1; $('x-next').disabled = j.page >= j.pages;
  $('x-rows').innerHTML = j.doctors.map(d => `<tr><td>${clamp(d.name)}</td><td>${clamp(d.designation)}</td><td>${clamp(d.department)}</td>
    <td class="contact">${clamp(d.email, 2)}</td><td class="contact">${clamp(d.phone, 1)}</td><td class="muted">${clamp([d.contact_email, d.contact_phone].filter(Boolean).join('; '))}</td>
    <td>${clamp(d.source_institution || d.institution)}</td><td>${clamp(d.qualifications)}</td><td>${clamp(d.research_areas)}</td>
    <td>${d.google_scholar ? `<a href="${esc(safeUrl(d.google_scholar))}" target="_blank" rel="noopener">Scholar</a>` : ''}${d.orcid ? ` ORCID ${esc(d.orcid)}` : ''}</td>
    <td><a href="${esc(safeUrl(d.profile_url || d.source_url))}" target="_blank" rel="noopener">open</a></td></tr>`).join('')
    || `<tr><td colspan="11" class="empty">${j.total ? '' : 'Nobody matches. Clear the filters or run a crawl first.'}</td></tr>`;
}
let dirDebounce = null;
const dirChanged = () => { dirState.page = 1; clearTimeout(dirDebounce); dirDebounce = setTimeout(loadDirectory, 300); };
$('x-q').oninput = dirChanged;
for (const id of ['x-coll', 'x-dept', 'x-city', 'x-email', 'x-phone', 'x-contact', 'x-profile', 'x-all']) $(id).onchange = dirChanged;
$('x-prev').onclick = () => { dirState.page--; loadDirectory(); };
$('x-next').onclick = () => { dirState.page++; loadDirectory(); };
$('x-reset').onclick = () => {
  for (const id of ['x-q', 'x-coll', 'x-dept', 'x-city']) $(id).value = '';
  for (const id of ['x-email', 'x-phone', 'x-contact', 'x-profile', 'x-all']) $(id).checked = false;
  dirChanged();
};
$('x-xlsx').onclick = () => download('/api/directory/download.xlsx?' + dirQuery(), $('x-msg'), $('x-xlsx'));
$('x-csv').onclick = () => download('/api/directory/download.csv?' + dirQuery(), $('x-msg'), $('x-csv'));

// ---- 10. Settings ----------------------------------------------------------------
async function loadSettings() {
  const r = await api('/api/settings');
  if (!r.ok) { say($('st-msg'), r.error, 'bad'); return; }
  const s = r.data.settings || r.data;
  if ($('st-workers')) $('st-workers').value = s.workers ?? 6;
  if ($('st-colleges')) $('st-colleges').value = s.colleges_at_once ?? 4;
  if ($('st-pages')) $('st-pages').value = s.max_pages ?? 120;
  if ($('st-depth')) $('st-depth').value = s.max_depth ?? 3;
  if ($('st-delay')) $('st-delay').value = s.delay_seconds ?? 0.35;
  if ($('st-timeout')) $('st-timeout').value = s.timeout_seconds ?? 20;
  if ($('st-render')) $('st-render').value = s.render ?? 'auto';
  if ($('st-adaptive')) $('st-adaptive').checked = !!s.adaptive_concurrency;
  if ($('st-cache')) $('st-cache').checked = !!s.use_url_cache;
  if ($('st-sitemaps')) $('st-sitemaps').checked = !!s.use_sitemaps;
  if ($('st-offices')) $('st-offices').checked = !!s.include_offices;
}
if ($('st-save')) $('st-save').onclick = async () => {
  const body = {
    workers: Number($('st-workers').value || 6),
    colleges_at_once: Number($('st-colleges').value || 4),
    max_pages: Number($('st-pages').value || 120),
    pages_per_site: Number($('st-pages').value || 120),
    max_depth: Number($('st-depth').value || 3),
    delay_seconds: Number($('st-delay').value || 0.35),
    timeout_seconds: Number($('st-timeout').value || 20),
    render: $('st-render').value,
    adaptive_concurrency: $('st-adaptive').checked,
    use_sitemaps: $('st-sitemaps').checked,
    include_offices: $('st-offices').checked
  };
  const r = await postJSON('/api/settings', body);
  if (!r.ok) { say($('st-msg'), r.error, 'bad'); return; }
  const saved = r.data.settings || r.data;
  // Sync defaults into New Run inputs
  if ($('workers')) $('workers').value = saved.workers;
  if ($('b-workers')) $('b-workers').value = saved.workers;
  if ($('b-at-once')) $('b-at-once').value = saved.colleges_at_once;
  if ($('max_pages')) $('max_pages').value = saved.max_pages;
  if ($('b-pages')) $('b-pages').value = saved.max_pages;
  if ($('depth')) $('depth').value = saved.max_depth;
  if ($('delay')) $('delay').value = saved.delay_seconds;
  if ($('b-delay')) $('b-delay').value = saved.delay_seconds;
  say($('st-msg'), 'Engine settings saved and applied to scraping forms.', 'ok');
};
if ($('st-reset')) $('st-reset').onclick = async () => {
  const defaults = {
    workers: 6, colleges_at_once: 4, max_pages: 120, pages_per_site: 120, max_depth: 3,
    delay_seconds: 0.35, timeout_seconds: 20, render: 'auto',
    adaptive_concurrency: true, use_sitemaps: true, include_offices: true
  };
  await postJSON('/api/settings', defaults);
  await loadSettings();
  say($('st-msg'), 'Restored default engine settings.', 'info');
};

// ---- 11. System Performance ------------------------------------------------------
async function loadPerformance() {
  const r = await api('/api/system/performance');
  if (!r.ok) return;
  const cur = r.data.current || {};
  const dbMb = r.data.database_mb ?? (r.data.database && r.data.database.size_mb) ?? 0;
  if ($('pf-cpu')) $('pf-cpu').textContent = `${cur.cpu_percent ?? 0}%`;
  if ($('pf-cpu-bar')) $('pf-cpu-bar').style.width = `${Math.min(100, cur.cpu_percent || 0)}%`;
  if ($('pf-ram')) $('pf-ram').textContent = `${cur.ram_percent ?? 0}% (${Math.round(cur.ram_used_mb || 0)} / ${Math.round(cur.ram_total_mb || 0)} MB)`;
  if ($('pf-ram-bar')) $('pf-ram-bar').style.width = `${Math.min(100, cur.ram_percent || 0)}%`;
  if ($('pf-ram-avail')) $('pf-ram-avail').textContent = `${Math.round(cur.ram_available_mb || 0)} MB`;
  if ($('pf-proc-ram')) $('pf-proc-ram').textContent = `${cur.process_ram_mb ?? 0} MB`;
  if ($('pf-workers')) $('pf-workers').textContent = cur.active_workers ?? 0;
  if ($('pf-colleges')) $('pf-colleges').textContent = cur.active_colleges ?? 0;
  if ($('pf-browsers')) $('pf-browsers').textContent = cur.browser_instances ?? 0;
  if ($('pf-net-rate')) $('pf-net-rate').textContent = `${cur.requests_per_min ?? 0} req/m`;
  if ($('pf-net-total')) $('pf-net-total').textContent = cur.network_requests_total ?? 0;
  if ($('pf-net-mb')) $('pf-net-mb').textContent = `${((cur.network_bytes_total || 0) / (1024 * 1024)).toFixed(2)} MB`;
  if ($('pf-rec-workers')) $('pf-rec-workers').textContent = `${cur.recommended_workers ?? 8} workers / ${cur.recommended_colleges ?? 4} colleges`;
  if ($('pf-db-size')) $('pf-db-size').textContent = `${dbMb} MB`;
  if ($('pf-history-rows')) {
    const hist = (r.data.history || []).slice().reverse().slice(0, 20);
    $('pf-history-rows').innerHTML = hist.length ? hist.map(h => `<tr>
      <td>${new Date(h.timestamp * 1000).toLocaleTimeString()}</td>
      <td>${h.cpu_percent}%</td>
      <td>${h.ram_percent}%</td>
      <td>${h.process_ram_mb} MB</td>
      <td>${h.active_workers}</td>
      <td>${h.requests_per_min} req/m</td>
      <td>${h.throttled ? `<span class="warn">Throttled (${esc(h.throttle_reason)})</span>` : '<span class="ok">Optimal</span>'}</td>
    </tr>`).join('') : `<tr><td colspan="7" class="empty">Collecting telemetry samples…</td></tr>`;
  }
}

// ---- 12. Student Offices ---------------------------------------------------------
function officeQuery() {
  const p = new URLSearchParams();
  const v = { q: $('o-q').value, college: $('o-coll').dataset.want || $('o-coll').value, office_type: $('o-type').value };
  for (const [k, x] of Object.entries(v)) if (x) p.set(k, x);
  for (const [k, id] of [['has_email', 'o-email'], ['has_phone', 'o-phone'], ['has_figures', 'o-figures']])
    if ($(id).checked) p.set(k, 'true');
  return p;
}
async function loadOffices() {
  const r = await api('/api/offices?' + officeQuery());
  if (!r.ok) { say($('o-msg'), r.error, 'bad'); return; }
  const j = r.data; say($('o-msg'), '');
  fillSelect($('o-coll'), j.facets.colleges, 'All colleges');
  fillSelect($('o-type'), j.facets.office_types, 'All types', OFFICE_NAMES);
  $('o-count').textContent = `${j.total} office${j.total === 1 ? '' : 's'}`;
  $('o-rows').innerHTML = j.offices.map(o => `<tr><td><span class="pill">${esc(OFFICE_NAMES[o.office_type] || o.office_type)}</span></td>
    <td>${clamp(o.office_name)}</td><td>${clamp(o.college || o.source_institution || o.institution)}</td>
    <td class="contact">${clamp(o.emails, 2)}</td><td class="contact">${clamp(o.phones, 2)}</td>
    <td>${clamp((o.coordinators || []).join('; '))}</td><td class="muted">${clamp((o.higher_studies_lines || []).join(' | '))}</td>
    <td><a href="${esc(safeUrl(o.page_url))}" target="_blank" rel="noopener">open</a></td></tr>`).join('')
    || `<tr><td colspan="8" class="empty">No student offices match. Clear the filters or run a crawl first.</td></tr>`;
}
let offDebounce = null;
const offChanged = () => { clearTimeout(offDebounce); offDebounce = setTimeout(loadOffices, 300); };
$('o-q').oninput = offChanged;
for (const id of ['o-coll', 'o-type', 'o-email', 'o-phone', 'o-figures']) $(id).onchange = offChanged;
$('o-reset').onclick = () => {
  for (const id of ['o-q', 'o-coll', 'o-type']) $(id).value = '';
  for (const id of ['o-email', 'o-phone', 'o-figures']) $(id).checked = false;
  offChanged();
};
$('o-xlsx').onclick = () => download('/api/offices/download.xlsx?' + officeQuery(), $('o-msg'), $('o-xlsx'));
$('o-csv').onclick = () => download('/api/offices/download.csv?' + officeQuery(), $('o-msg'), $('o-csv'));

show(location.hash.slice(1) || 'dashboard');
