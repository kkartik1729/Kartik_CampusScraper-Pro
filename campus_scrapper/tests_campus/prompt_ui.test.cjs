// Browser-free DOM integration tests. Optional dev dependency: jsdom@26.1.0.
// NODE_PATH=/path/to/node_modules node --test tests_campus/prompt_ui.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');

function portal(job) {
  const html = fs.readFileSync(path.join(root,'campusscraper/static/index.html'),'utf8');
  const dom = new JSDOM(html,{url:'http://localhost:8001/#new',runScripts:'outside-only'});
  const w=dom.window, calls=[];
  w.fetch=async (url,opts={}) => {
    calls.push({url,opts});
    let data={}, status=200;
    if (url==='/api/active') data={job:null,batch:null};
    else if (url==='/api/discovery/config') data={ready:false,search_url:'http://127.0.0.1:8080'};
    else if (url==='/api/discovery' && opts.method==='POST') data={id:'new-id',state:'queued'};
    else if (url==='/api/discovery') data={jobs:[]};
    else if (url.endsWith('/pause')) {job.state='paused';data={id:job.id,state:'paused'};}
    else if (url.startsWith('/api/discovery/')) data=job;
    return {ok:status===200,status,json:async()=>JSON.parse(JSON.stringify(data))};
  };
  const code=['app','portal','prompt'].map(n=>fs.readFileSync(path.join(root,`campusscraper/static/${n}.js`),'utf8')).join('\n');
  w.eval(code+'\nwindow.testPrompt={open:async id=>{promptId=id;return pollPrompt(id);}};');
  return {dom,w,calls,$:id=>w.document.getElementById(id)};
}

function saved(overrides={}) {
  return {id:'run1',state:'running',phase:'filtering',started:Date.now()/1000,finished:null,
    body:{prompt:'Find robotics faculty in Mumbai',max_colleges:10,auto_scrape:true},
    progress:{checked:2,total:8,matched:1},report:{plan:{objective:'Find robotics faculty',criteria:[]},candidates:[],issues:[],pages:4,searches:2},
    rows:[{institution:'Example College',status:'done',pages:10,faculty:8}],download_ready:false,stats:{},usage:[],...overrides};
}

test('completed college crawls do not mark filtering complete or enable export',async()=>{
  const p=portal(saved());
  try {
    await p.w.testPrompt.open('run1');
    assert.match(p.$('p-status').textContent,/Working/);
    assert.equal(p.$('p-download').hidden,true);
    assert.match(p.$('p-work').textContent,/2\/8/);
    assert.equal(p.w.document.querySelector('#p-stages .current').dataset.phase,'filtering');
    assert.equal(p.$('p-start').disabled,true);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('quota wait shows saved progress and controls without a fake percentage',async()=>{
  const p=portal(saved({state:'waiting',retry_at:Date.now()/1000+120,error:'Groq quota reached'}));
  try {
    await p.w.testPrompt.open('run1');
    assert.match(p.$('p-work').textContent,/Progress is saved/);
    assert.equal(p.$('p-pause').disabled,false);
    assert.equal(p.$('p-download').hidden,true);
    // A bar may exist for the crawl, but nothing may show a made-up percentage here.
    assert.equal([...p.$('prompt-card').querySelectorAll('.bar')].filter(b=>!b.hidden).length,0);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('source names and model text are escaped and unsafe links are not clickable',async()=>{
  const job=saved();
  job.report.candidates=[{institution:'<img src=x onerror=alert(1)>',url:'javascript:alert(1)'}];
  job.report.issues=['<script>alert(1)</script>'];
  const p=portal(job);
  try {
    await p.w.testPrompt.open('run1');
    assert.equal(p.$('p-sources').querySelectorAll('img,script').length,0);
    assert.equal(p.$('p-sources').querySelector('a').getAttribute('href'),'#');
    assert.match(p.$('p-notes').textContent,/<script>/);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('finished export is available and identifies partial coverage',async()=>{
  const p=portal(saved({state:'partial',phase:'finished',finished:Date.now()/1000,download_ready:true,
    stats:{people_found:3,faculty:3,student_offices:0,colleges_with_matching_records:1,filtered_out:4}}));
  try {
    await p.w.testPrompt.open('run1');
    assert.equal(p.$('p-download').hidden,false);
    assert.match(p.$('p-status').textContent,/coverage limits/);
    assert.match(p.$('p-work').textContent,/3 matching people/);
    assert.equal(p.$('p-start').disabled,false);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('clarification preserves original wording and options',async()=>{
  const job=saved({state:'clarify',phase:'clarify'});
  job.report.plan.questions=['Which Mumbai area?'];
  const p=portal(job);
  try {
    await p.w.testPrompt.open('run1');
    assert.equal(p.$('p-clarify').hidden,false);
    p.$('p-answer').value='Only central Mumbai';
    await p.$('p-answer-btn').onclick();
    const request=p.calls.find(c=>c.url==='/api/discovery' && c.opts.method==='POST');
    const body=JSON.parse(request.opts.body);
    assert.equal(body.prompt,job.body.prompt+'\nClarification: Only central Mumbai');
    assert.equal(body.max_colleges,10);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('empty prompt is rejected before sending a request',async()=>{
  const p=portal(saved());
  try {
    p.$('p-text').value='  ';
    p.$('p-start').onclick();
    assert.match(p.$('p-msg').textContent,/Describe/);
    assert.equal(p.calls.filter(c=>c.opts.method==='POST').length,0);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('failed planning is visibly stopped with no stale interpreting or saved-start message',async()=>{
  const p=portal(saved({state:'failed',phase:'planning_failed',finished:Date.now()/1000,
    error:'The AI search plan could not be validated after one automatic repair.',
    report:{plan:{},candidates:[],issues:[],searches:0,pages:0}}));
  try {
    p.$('p-msg').textContent='Run saved. Discovery starts automatically.';
    await p.w.testPrompt.open('run1');
    assert.match(p.$('p-status').textContent,/Run failed/);
    assert.match(p.$('p-plan').textContent,/run has stopped/);
    assert.doesNotMatch(p.$('p-plan').textContent,/Interpreting/);
    assert.match(p.$('p-msg').textContent,/Run stopped/);
    assert.match(p.$('p-work').textContent,/automatic repair/);
    assert.equal(p.$('p-download').hidden,true);
    assert.equal(p.$('p-start').disabled,false);
    assert.equal(p.w.document.querySelectorAll('#p-stages .current').length,0);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('waiting on plan repair does not claim to be actively interpreting',async()=>{
  const p=portal(saved({state:'waiting',phase:'planning',retry_at:Date.now()/1000+120,
    error:'Groq quota reached',report:{plan:{},candidates:[],issues:[],searches:0,pages:0}}));
  try {
    await p.w.testPrompt.open('run1');
    assert.match(p.$('p-work').textContent,/Groq quota/);
    assert.doesNotMatch(p.$('p-plan').textContent,/Interpreting/);
    assert.match(p.$('p-plan').textContent,/No validated search plan yet/);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('URL-only completion clearly identifies results and download',async()=>{
  const j=saved({state:'sources_ready',phase:'sources_ready'});
  j.body.auto_scrape=false;
  j.report.plan.requested_institutions=5;
  j.report.candidates=[{institution:'College A',url:'https://college.example/faculty'}];
  const p=portal(j);
  try {
    await p.w.testPrompt.open('run1');
    assert.match(p.$('p-work').textContent,/Download source URLs/);
    assert.equal(p.$('p-source-download').hidden,false);
    assert.equal(p.$('p-download').hidden,true);
    assert.equal(p.$('p-sources').querySelector('a').href,'https://college.example/faculty');
    assert.equal(p.$('p-auto').closest('details'),null);
    assert.match(p.$('p-work').textContent,/1 of 5 requested/);
    assert.equal(p.w.document.querySelector('#p-stages [data-phase="scraping"]').hidden,true);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

test('failed discovery explicitly says no results and labels evidence as diagnostic',async()=>{
  const p=portal(saved({state:'failed',phase:'searching',error:'Search unavailable'}));
  try {
    await p.w.testPrompt.open('run1');
    assert.match(p.$('p-msg').textContent,/No verified faculty URLs or contacts/);
    assert.match(p.$('p-msg').textContent,/diagnostic log/);
    assert.equal(p.$('p-source-download').hidden,true);
    assert.equal(p.$('p-download').hidden,true);
  } finally {await new Promise(resolve=>setImmediate(resolve));p.dom.window.close();}
});

// ---- layout rules the tables depend on ------------------------------------------
test('every table states a width for each of its columns', async () => {
  const html = fs.readFileSync(path.join(root, 'campusscraper/static/index.html'), 'utf8');
  const dom = new JSDOM(html);
  const tables = [...dom.window.document.querySelectorAll('.tablewrap table')];
  assert.ok(tables.length >= 6, 'the portal still has its tables');
  for (const t of tables) {
    const heads = t.querySelectorAll('thead th').length;
    const cols = t.querySelectorAll('colgroup col').length;
    const first = t.querySelector('thead th').textContent.trim();
    assert.equal(cols, heads, `table starting "${first}" has ${heads} headers but ${cols} column widths`);
  }
});

test('run states are shown as words, and long text is clamped with the full value kept', async () => {
  const long = 'Find 20 engineering colleges in Mumbai and return their official faculty directory URLs';
  const p = portal(saved());
  try {
    await p.w.testPrompt.open('run1');
    const row = p.w.eval(`(() => {
      const x = {id:'r1', kind:'prompt', state:'sources_ready', title:${JSON.stringify(long)},
                 started: Date.now()/1000, finished: Date.now()/1000, faculty_saved:0, offices_saved:0, error:''};
      return stateCell(x.state) + clamp(x.title, 3);
    })()`);
    assert.ok(row.includes('Source URLs ready'), 'the state reads as words: ' + row);
    assert.ok(!row.includes('sources_ready'), 'the code word is not shown');
    assert.ok(row.includes('class="clamp l3"'), 'long titles are clamped');
    assert.ok(row.includes(`title="${long}"`), 'the whole title stays in the tooltip');
    await new Promise(r => setTimeout(r, 50));
  } finally { p.dom.window.close(); }
});

test('while colleges are being crawled the counters show the crawl, not the search', async () => {
  const job = saved({state: 'running', phase: 'scraping',
    progress: {completed: 3, total: 8, pages: 182, faculty: 247, current: 'Example College'},
    rows: [{institution: 'Example College', status: 'scraping', pages: 40, faculty: 12}]});
  const p = portal(job);
  try {
    await p.w.testPrompt.open('run1');
    const stats = p.$('p-stats').textContent;
    assert.match(stats, /Colleges crawled/);
    assert.match(stats, /3 of 8/);
    assert.match(stats, /182/, 'pages read are shown');
    assert.match(stats, /247/, 'faculty found so far are shown');
    assert.match(p.$('p-work').textContent, /Reading college websites/);
    assert.match(p.$('p-work').textContent, /now on Example College/);
    assert.equal(p.$('p-bar').hidden, false, 'the crawl shows how far along it is');
  } finally { await new Promise(r => setImmediate(r)); p.dom.window.close(); }
});
