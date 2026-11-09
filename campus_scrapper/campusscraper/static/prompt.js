// Prompt workflow. Uses the portal's escaped rendering and download helpers.
let promptId = null, promptTimer = null, promptJob = null;
const promptActive = new Set(['queued', 'running', 'waiting', 'pausing', 'stopping']);
const promptLabels = {queued:'Queued', running:'Working', waiting:'Waiting for service capacity', paused:'Paused',
  pausing:'Finishing the current operation before pausing', stopping:'Stopping', stopped:'Stopped', interrupted:'Interrupted — progress saved',
  failed:'Run failed', done:'Finished', partial:'Finished with coverage limits', clarify:'Clarification needed',
  unsupported:'Outside the campus scraper’s scope', no_candidates:'No matching sources found', sources_ready:'Source list ready'};

async function promptSetup() {
  const r = await api('/api/discovery/config');
  if (!r.ok) { say($('p-config'), r.error, 'bad'); return; }
  const c = r.data;
  const providers = (c.providers || ['groq', 'gemini']).filter(p => c[p + '_configured']).map(p => p === 'ollama' ? `Ollama (${c.ollama_model})` : p);
  say($('p-config'), c.ready ? `${providers.join(' + ')} configured. Search service: ${c.search_url}. Availability is checked when a run starts.`
    : 'Setup needed: configure Ollama in your server’s .env file and start SearXNG. See docs/prompt-setup.md. Excel and URL runs still work.', c.ready ? 'info' : 'bad');
  const runs = await api('/api/discovery');
  if (runs.ok && runs.data.jobs.length && !promptId) attachPrompt(runs.data.jobs[0].id);
}

function promptBody(text) {
  return {prompt:text, max_colleges:num('p-colleges','Maximum colleges',1,100), discovery_pages:num('p-pages','Discovery pages',1,100),
    searches:num('p-searches','Search queries',1,30), pages_per_college:num('p-crawl-pages','Pages per college',1,1000),
    render:$('p-render').value, auto_scrape:$('p-auto').checked,
    engine:$('p-engine').value, site_pages:num('p-site-pages','Pages read per site',2,60),
    site_seconds:num('p-site-seconds','Seconds per site',10,300),
    site_workers:num('p-site-workers','Sites at the same time',1,12)};
}

async function startPrompt(body) {
  $('p-start').disabled = true;
  const r = await postJSON('/api/discovery', body);
  $('p-start').disabled = false;
  if (!r.ok) { say($('p-msg'), r.error, 'bad'); return; }
  say($('p-msg'), 'Run saved. Discovery starts automatically.');
  attachPrompt(r.data.id);
}
$('p-start').onclick = () => {
  try { const text = $('p-text').value.trim(); if (!text) throw new Error('Describe the faculty, colleges or offices you need.'); startPrompt(promptBody(text)); }
  catch (e) { say($('p-msg'), e.message, 'bad'); }
};
$('p-auto').onchange = () => { $('p-start').textContent = $('p-auto').checked ? 'Find and scrape' : 'Find source URLs'; };

function attachPrompt(id) {
  promptId = id;
  clearTimeout(promptTimer);
  $('p-run').hidden = false;
  pollPrompt(id);
}

async function pollPrompt(id) {
  const r = await api(`/api/discovery/${id}`);
  if (id !== promptId) return;
  if (!r.ok) {
    say($('p-msg'), r.error, 'bad');
    if (r.status !== 404) promptTimer = setTimeout(() => pollPrompt(id), 4000);
    return;
  }
  const j = promptJob = r.data, report = j.report || {}, p = j.progress || {}, plan = report.plan || {};
  if (j.state === 'failed') say($('p-msg'), (report.candidates || []).length ? 'Run stopped. Verified source URLs are available below; contact extraction may be incomplete.' : 'Run stopped. No verified faculty URLs or contacts were produced. The evidence report is a diagnostic log.', 'bad');
  else if (j.state !== 'queued') say($('p-msg'), '');
  $('p-status').innerHTML = `<b>${esc(promptLabels[j.state] || j.state)}</b> <span class="clamp" title="${esc(j.body.prompt)}">${esc(j.body.prompt)}</span>`;
  const active = promptActive.has(j.state);
  $('p-start').disabled = active;
  $('p-pause').disabled = !['queued','running','waiting'].includes(j.state);
  $('p-stop').disabled = !['queued','running','waiting','pausing'].includes(j.state);
  $('p-resume').hidden = !['paused','stopped','interrupted','failed'].includes(j.state);
  $('p-download').hidden = !j.download_ready;
  $('p-source-download').hidden = !(report.candidates || []).length;
  const stage = ['checking_pages','sources_ready'].includes(j.phase) ? 'searching' : j.phase;
  document.querySelectorAll('#p-stages span').forEach(el => {
    el.hidden = j.body.auto_scrape === false && ['scraping','filtering','exporting'].includes(el.dataset.phase);
    el.classList.toggle('current', active && el.dataset.phase === stage);
  });
  const institutes = new Set((report.candidates || []).map(c => c.institution));
  // While colleges are being crawled, show the crawl. The discovery counters
  // have stopped moving by then, which reads as if nothing is happening.
  const crawling = j.phase === 'scraping' && (p.total || 0) > 0;
  const stats = crawling
    ? [['Colleges crawled', `${p.completed || 0} of ${p.total || institutes.size}`],
       ['Pages read', p.pages || 0], ['Faculty so far', p.faculty || 0], ['Elapsed', fmtDur(j.started, j.finished)]]
    : [['Source colleges found', institutes.size], ['Discovery pages checked', report.pages || 0],
       ['Search queries used', report.searches || 0], ['Elapsed', fmtDur(j.started, j.finished)]];
  $('p-stats').innerHTML = stats.map(([k, v]) => `<div class="stat"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
  const share = crawling && p.total ? Math.round(100 * (p.completed || 0) / p.total) : 0;
  $('p-bar').hidden = !crawling;
  $('p-bar-fill').style.width = share + '%';
  let work = '';
  if (j.state === 'waiting') work = `${j.error || 'Waiting for capacity'}. Next attempt: ${fmtTime(j.retry_at)}. Progress is saved; keep the server running for automatic retry.`;
  else if (j.state === 'failed') work = j.error || 'Check setup, then resume this saved run.';
  else if (j.state === 'sources_ready') work = `${institutes.size}${plan.requested_institutions ? ' of ' + plan.requested_institutions + ' requested' : ''} colleges have verified source pages below. Download source URLs to save them. Contact scraping was not requested.${plan.requested_institutions > institutes.size ? ' The requested college count was not reached; see coverage notes.' : ''}`;
  else if (j.state === 'no_candidates') work = 'Search finished without verified faculty sources. No results file was produced. See coverage notes for search or access failures.';
  else if (j.phase === 'scraping') work = `Reading college websites: ${p.completed || 0} of ${p.total || (j.rows || []).length} finished`
    + (p.current ? `, now on ${p.current}` : '') + `. ${p.pages || 0} pages read, ${p.faculty || 0} faculty found so far. `
    + 'Each college below shows its own count as it goes. Filtering and the Excel file follow.';
  else if (j.phase === 'filtering') work = `Checking extracted records against your criteria: ${p.checked || 0}/${p.total || '…'}; ${p.matched || 0} matched so far.`;
  else if (j.phase === 'finished') work = `${j.stats.people_found || 0} matching people (${j.stats.faculty || 0} faculty) and ${j.stats.student_offices || 0} offices saved across ${j.stats.colleges_with_matching_records || 0} colleges. ${j.stats.filtered_out || 0} extracted records did not meet the prompt’s criteria.`;
  else if (j.phase === 'exporting') work = 'Writing the Excel file. The run will finish when the file is ready.';
  else work = p.current ? `Checking: ${p.current}` : 'Time remaining depends on site speed, evidence found and API capacity. No reliable estimate yet.';
  $('p-work').textContent = work;
  $('p-usage').textContent = (j.usage || []).map(u => `${u.provider}: ${u.requests} API requests, ${u.tokens.toLocaleString()} tokens used or reserved`).join(' · ');
  const planMessage = plan.objective || (j.state === 'running' && j.phase === 'planning'
    ? 'Interpreting your request…' : j.state === 'failed'
    ? 'No validated search plan. This run has stopped; no search results are available.'
    : 'No validated search plan yet.');
  $('p-plan').innerHTML = `<p>${esc(planMessage)}</p><ul>${(plan.criteria || []).map(c => `<li>${esc(c.requirement)}</li>`).join('')}</ul>`;
  $('p-clarify').hidden = j.state !== 'clarify';
  $('p-questions').textContent = (plan.questions || []).join(' ') || plan.explanation || '';
  if (j.state === 'unsupported') $('p-work').textContent = plan.explanation || (report.issues || []).join(' ') || 'This portal supports campus faculty/staff and office contacts. It excludes individual students.';
  const groups = new Map();
  for (const c of report.candidates || []) {
    if (!groups.has(c.institution)) groups.set(c.institution, []);
    groups.get(c.institution).push(c);
  }
  $('p-sources').innerHTML = [...groups].map(([name, cs], idx) => {
    const row = (j.rows || []).find(x => x.institution === name);
    const words = {queued: 'Waiting its turn', scraping: 'Reading this website now', crawling: 'Reading this website now',
      done: 'Crawl ended', resumed: 'Crawl ended (saved)', failed: 'Could not be read', error: 'Could not be read',
      stopped: 'Stopped', interrupted: 'Interrupted'};
    const state = row ? `${words[row.status] || row.status} · ${row.pages} pages · ${row.faculty} extracted faculty before filtering` : 'Source pages found';
    return `<article class="prompt-source"><b>${idx+1}. ${esc(name)}</b><p class="hint">${esc(state)}</p><ul>${cs.map(c => `<li><a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener">${esc(c.url)}</a>${typeof c.extracted_staff === 'number' ? ` <span class="hint">· ${c.extracted_staff} staff read from this page</span>` : ''}</li>`).join('')}</ul>${row?.note ? `<p class="hint">${esc(row.note)}</p>` : ''}</article>`;
  }).join('');
  $('p-notes-wrap').hidden = !(report.issues || []).length;
  $('p-notes').innerHTML = (report.issues || []).map(x => `<li>${esc(x)}</li>`).join('');
  if (active) promptTimer = setTimeout(() => pollPrompt(id), 2000);
}

for (const action of ['pause','stop','resume']) $('p-'+action).onclick = async () => {
  if (!promptId) return;
  const r = await postJSON(`/api/discovery/${promptId}/${action}`);
  if (!r.ok) say($('p-msg'), r.error, 'bad');
  else { say($('p-msg'), action === 'resume' ? 'Resuming saved work.' : 'Request saved. The current network operation may need to finish.'); attachPrompt(promptId); }
};
$('p-answer-btn').onclick = async () => {
  if (!promptJob) return;
  const answer = $('p-answer').value.trim();
  if (!answer) { say($('p-msg'),'Add the missing detail first.','bad'); return; }
  const body = {...promptJob.body, prompt:promptJob.body.prompt + '\nClarification: ' + answer};
  if (body.prompt.length > 6000) { say($('p-msg'),'Please shorten the combined request to 6,000 characters.','bad'); return; }
  $('p-text').value = body.prompt;
  await startPrompt(body);
};
$('p-download').onclick = () => download(`/api/discovery/${promptId}/download.xlsx`, $('p-msg'), $('p-download'));
$('p-source-download').onclick = () => download(`/api/discovery/${promptId}/sources.xlsx`, $('p-msg'), $('p-source-download'));
$('p-audit').onclick = () => download(`/api/discovery/${promptId}/evidence.json`, $('p-msg'), $('p-audit'));
promptSetup();
