import sys,json,hashlib,re
from pathlib import Path
from campusscraper.extract import extract_page
from campusscraper.models import Page
from campusscraper.crawl import Merger
from campusscraper.contacts import demote_sitewide,demote_shared
from campusscraper.relevance import set_targets
# Run with python -m tools.replay_faculty_snapshots SNAPSHOT_DIR OUTPUT_JSON.
# Inputs are local research snapshots; no network requests are made.
D=Path(sys.argv[1]);ref=json.loads((D/'final-contacts.json').read_text());tasks=set()
for i in range(10):
 for p in json.loads((D/f'{i}.json').read_text())['pages']:tasks.add((i,p['url']))
for p in json.loads((D/'extra-pages.json').read_text()):tasks.add((p['i'],p['url']))
for r in ref['records']:
 for u in r['source_url'].splitlines():tasks.add((r['i'],u))
mergers=[Merger() for _ in range(10)];vp=[{} for _ in range(10)];n=0
for i,u in sorted(tasks):
 if 'indiacustomercare' in u:continue
 p=D/(str(i)+'-'+hashlib.sha1(u.encode()).hexdigest()[:12]+'.html')
 if not p.exists():continue
 pr=extract_page(Page(u,u,200,p.read_text()));n+=1
 for r in pr.doctors:r.source_institution=ref['colleges'][i];mergers[i].add(r)
 for v in pr.signals.get('page_contacts',[]):vp[i].setdefault(v,set()).add(u)
 if n%100==0:print('pages',n,flush=True)
def nk(s):return re.sub('[^a-z]','',re.sub(r'\b(professor|prof|dr|mrs|mr|ms|miss)\b','',s.lower()))
def es(s):return set(re.findall(r'[\w.+%-]+@[\w.-]+\.[a-z]{2,}',(s or '').lower()))
metrics=[]
for i,m in enumerate(mergers):
 demote_sitewide(m.records,vp[i]);demote_shared(m.records)
 rr=[r for r in ref['records'] if r['i']==i];names=em=0
 for r in rr:
  hits=[p for p in m.records if p.is_relevant and nk(p.get('name',''))==nk(r['name'])]
  names+=bool(hits);em+=any(es(r['email'])&(es(p.get('email'))|es(p.get('contact_email'))) for p in hits)
 metrics.append({'college':ref['colleges'][i],'names':names,'emails':em,'faculty':sum(p.is_relevant for p in m.records)})
print(json.dumps(metrics),flush=True)
Path(sys.argv[2]).write_text(json.dumps({'pages':n,'metrics':metrics,'records':[[r.to_full_dict() for r in m.records] for m in mergers]},indent=2))
