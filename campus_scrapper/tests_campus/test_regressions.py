"""Regression cases from the Pune audit, using fictional faculty only."""
import json
import pytest
from campusscraper.config import ScrapeConfig
from campusscraper.crawl import Crawler, Merger
from campusscraper.extract import extract_page, is_student_record
from campusscraper.fetch import _is_dns_failure
from campusscraper.models import Page, PersonRecord
from campusscraper.pdf_profiles import extract_faculty_pdf
from campusscraper.profile import clean_fields

URL = 'https://college.example/faculty/'

def person(name='Dr. Anitha Rao', url=URL+'anitha', college='Example College'):
    r = PersonRecord(source_url=URL, profile_url=url, source_institution=college)
    r.set('name', name, 'h1')
    r.set('designation', 'Professor', 'labels')
    return r

def page(html):
    return extract_page(Page(URL, URL, 200, html))

def test_shared_coordinator_url_does_not_merge_different_people():
    m=Merger()
    first=person('Dr. Anitha Rao')
    second=person('Dr. Priya Sharma', first.profile_url)
    second.set('email','priya.sharma@college.example','labels')
    m.add(first);m.add(second)
    assert len(m.records)==2
    assert first.get('email') is None

def test_same_full_name_two_profiles_deduplicates_but_not_different_colleges():
    m=Merger()
    m.add(person('Dr. Anitha Rao',URL+'old'))
    m.add(person('Dr.Anitha Rao',URL+'new'))
    assert len(m.records)==1
    m.add(person('Dr. Anitha Rao',URL+'elsewhere','Other College'))
    assert len(m.records)==2

def test_teaching_assistant_excluded_but_dean_student_affairs_kept():
    r=person();r.values['designation']='Teaching Assistant'
    assert is_student_record(r)
    r.values['designation']='Professor and Dean Student Affairs'
    assert not is_student_record(r)

def test_stale_title_does_not_assign_predecessors_name_to_contact():
    pr=page('<html><head><title>Dr. Anitha Rao</title></head><body><h1>Coordinator Desk</h1>'
            '<p>Dr. Priya Sharma</p><p>Professor</p><p>Email: priya.sharma@college.example</p></body></html>')
    assert pr.doctors
    assert all('Anitha' not in d.get('name','') for d in pr.doctors)
    assert any(d.get('name')=='Dr. Priya Sharma' and d.get('email')=='priya.sharma@college.example' for d in pr.doctors)

def test_research_topics_not_a_department():
    r=person();r.set('department','Cloud Computing, Cyber Security, Machine Learning','patterns')
    clean_fields(r)
    assert not r.get('department')

def test_malformed_port_in_link_does_not_lose_faculty_page():
    pr=page('<h1>Faculty</h1><a href="https://bad.example: https://other.example">bad link</a>'
            '<div><h3>Dr. Anitha Rao</h3><p>Professor</p></div>')
    assert any(d.get('name')=='Dr. Anitha Rao' for d in pr.doctors)

def test_hidden_faculty_details_are_retained():
    pr=page('<h1>Faculty</h1><div class="modal" style="display:none"><h3>Dr. Anitha Rao</h3>'
            '<p>Professor</p><p>Email: anitha.rao@college.example</p></div>')
    assert any(d.get('email')=='anitha.rao@college.example' for d in pr.doctors)

def test_roster_outranks_individual_profiles_and_generic_pdfs_skipped():
    c=Crawler(ScrapeConfig(render='never'))
    try:
        assert c._score(URL,1,True)>c._score(URL+'dr-anitha-rao',1,True,'focus_profile')
        assert c._score('https://college.example/report.pdf',1,True) is None
        assert c._score('https://college.example/anitha.pdf',1,True,'focus_profile') is not None
    finally:
        c.fetcher.close()

def test_temporary_dns_error_is_not_permanent_host_failure():
    assert not _is_dns_failure('[Errno -3] Temporary failure in name resolution: getaddrinfo failed')
    assert _is_dns_failure('[Errno 11001] getaddrinfo failed')

def test_pdf_only_enriches_verified_linked_faculty():
    p=Page(URL+'anitha.pdf',URL+'anitha.pdf',200,
           'Dr. Anitha Rao\nProfessor\nEmail: anitha.rao@college.example\nPublications\nEmail: other@college.example',
           content_type='application/pdf')
    pr=extract_faculty_pdf(p,person())
    assert len(pr.doctors)==1
    assert pr.doctors[0].get('email')=='anitha.rao@college.example'
    assert not extract_faculty_pdf(p,None).doctors
    assert not extract_faculty_pdf(p,person('Dr. Priya Sharma')).doctors

def test_pdf_link_in_separate_table_cell_is_found():
    pr=page('<h1>Faculty</h1><table><tr><th>Name</th><th>Designation</th><th>Profile</th></tr>'
            '<tr><td>Dr. Anitha Rao</td><td>Professor</td><td><a href="anitha.pdf">View Profile</a></td></tr></table>')
    assert any(d.profile_url==URL+'anitha.pdf' for d in pr.doctors)

def test_dns_failure_after_success_does_not_poison_later_pages(monkeypatch):
    import httpx
    from campusscraper.fetch import Fetcher
    f=Fetcher(ScrapeConfig(render='never',respect_robots=False,retries=0,delay_seconds=0))
    responses=iter([httpx.Response(200,text='<h1>Faculty</h1>',request=httpx.Request('GET',URL)),
                    httpx.ConnectError('[Errno 11001] getaddrinfo failed'),
                    httpx.Response(200,text='<h1>Faculty</h1>',request=httpx.Request('GET',URL))])
    def get(url):
        r=next(responses)
        if isinstance(r,Exception): raise r
        return r
    monkeypatch.setattr(f,'_raw_get',get)
    monkeypatch.setattr(f,'_sleep',lambda _:None)
    try:
        assert not f.get(URL).error
        assert f.get(URL).error
        assert not f.get(URL).error
    finally: f.close()

def test_pdf_fetch_reads_text_and_rejects_corrupt_pdf(monkeypatch):
    import httpx
    from io import BytesIO
    from pypdf import PdfWriter
    from campusscraper.fetch import Fetcher
    w=PdfWriter();w.add_blank_page(width=100,height=100);buf=BytesIO();w.write(buf)
    f=Fetcher(ScrapeConfig(render='never',respect_robots=False,retries=0))
    def response(content):
        return httpx.Response(200,content=content,headers={'content-type':'application/pdf'},request=httpx.Request('GET',URL+'a.pdf'))
    try:
        monkeypatch.setattr(f,'_raw_get',lambda _:response(buf.getvalue()))
        p=f.get(URL+'a.pdf')
        assert p.content_type=='application/pdf' and not p.error
        monkeypatch.setattr(f,'_raw_get',lambda _:response(b'not a pdf'))
        assert f.get(URL+'a.pdf').error.startswith('PDF could not be read')
    finally: f.close()

def test_crawl_reports_page_limit(site_url):
    result=Crawler(ScrapeConfig(max_pages=1,workers=1,delay_seconds=0,render='never',use_sitemaps=False,
                               allow_private_hosts=True)).run([site_url])
    assert result.stats['pages_fetched']==1
    assert result.stats['stop_reason']=='page_limit'

def test_named_pdf_is_fetched_and_contact_merged_in_crawl(monkeypatch):
    url='https://college.example/faculty/'
    html=('<h1>Faculty</h1><table><tr><th>Name</th><th>Designation</th><th>Profile</th></tr>'
          '<tr><td>Dr. Anitha Rao</td><td>Professor</td><td><a href="anitha.pdf">View Profile</a></td></tr>'
          '<tr><td>Dr. Priya Sharma</td><td>Professor</td><td></td></tr></table>'
          '<a href="prospectus.pdf">Prospectus</a>')
    c=Crawler(ScrapeConfig(max_pages=3,workers=1,render='never',use_sitemaps=False,respect_robots=False))
    visited=[]
    def get(u):
        visited.append(u)
        if u.endswith('.pdf'):
            return Page(u,u,200,'Dr. Anitha Rao\nProfessor\nEmail: anitha.rao@college.example','application/pdf')
        return Page(u,u,200,html)
    monkeypatch.setattr(c.fetcher,'get',get)
    result=c.run([url])
    assert url+'anitha.pdf' in visited and url+'prospectus.pdf' not in visited
    people=[d for d in result.faculty if d.get('name')=='Dr. Anitha Rao']
    assert len(people)==1 and people[0].get('email')=='anitha.rao@college.example'

def test_sitemaps_fetched_once_per_origin(monkeypatch):
    c=Crawler(ScrapeConfig(max_pages=2,workers=1,render='never',respect_robots=False))
    called=[]
    monkeypatch.setattr(c,'_sitemap_urls',lambda u:called.append(u) or [])
    monkeypatch.setattr(c.fetcher,'get',lambda u:Page(u,u,200,'<h1>Welcome</h1>'))
    c.run([URL,URL+'second'])
    assert called==[URL]

@pytest.mark.parametrize('text,expected',[
    ('MRS. SUDHA A. BHUMARE','Mrs. Sudha A. Bhumare'),
    ('DR. YOGESHRAO YASHWANTRAO MORE','Dr. Yogeshrao Yashwantrao More'),
    ('Professor Anitha Rao','Prof. Anitha Rao'),
    ('Dr. Anitha Rao Read More','Dr. Anitha Rao'),
])
def test_complete_honorifics_and_more_surname(text,expected):
    from campusscraper.profile import find_name_in_text
    assert find_name_in_text(text)==expected

def test_degree_is_not_a_department():
    r=person();r.set('department','M.E.(Manufacturing Engineering)','patterns')
    clean_fields(r)
    assert not r.get('department')

def test_contact_enrichment_resets_patience(monkeypatch):
    names=['Anitha Rao','Priya Sharma','Farah Siddiqui','Sneha Kulkarni','Geetha Rani']
    rows=''.join(f'<tr><td>Dr. {n}</td><td>Professor</td><td><a href="p{i}.pdf">View Profile</a></td></tr>' for i,n in enumerate(names))
    html='<h1>Faculty</h1><table><tr><th>Name</th><th>Designation</th><th>Profile</th></tr>'+rows+'</table>'
    c=Crawler(ScrapeConfig(max_pages=10,patience=2,workers=1,render='never',use_sitemaps=False,respect_robots=False))
    def get(u):
        if u.endswith('.pdf'):
            i=int(u.rsplit('/p',1)[1].split('.')[0]);name=names[i]
            return Page(u,u,200,f'Dr. {name}\nProfessor\nEmail: {name.lower().replace(" ",".")}@college.example','application/pdf')
        return Page(u,u,200,html)
    monkeypatch.setattr(c.fetcher,'get',get)
    result=c.run([URL])
    assert len(result.faculty)==5
    assert all(d.get('email') for d in result.faculty)
    assert result.stats['stop_reason']=='queue_exhausted'

def test_campus_path_does_not_collect_other_university_colleges(monkeypatch):
    c=Crawler(ScrapeConfig(max_pages=10,workers=1,render='never',use_sitemaps=False,respect_robots=False))
    visited=[]
    def get(u):
        visited.append(u)
        return Page(u,u,200,'<h1>Welcome</h1><a href="/medicine/faculty">Faculty</a>'
                    '<a href="/pune-engineering/department/cse/faculty">Department Faculty</a>')
    monkeypatch.setattr(c.fetcher,'get',get)
    c.run(['https://university.example/pune-engineering/faculty','https://university.example/pune-engineering/placement'])
    assert 'https://university.example/pune-engineering/department/cse/faculty' in visited
    assert not any('/medicine/' in u for u in visited)

def test_generic_department_folder_does_not_restrict_other_departments():
    c=Crawler(ScrapeConfig(render='never'))
    try:
        c._set_path_scopes(['https://college.example/departments/cse','https://college.example/departments/mech'])
        assert c._within_path_scope('https://college.example/faculty/dr-anitha-rao')
    finally: c.fetcher.close()

@pytest.mark.parametrize('text,expected',[
    ('MR. CHANDRAKANT .V. PATIL','Mr. Chandrakant V. Patil'),
    ('MS KRITI SACHDEVA','Ms. Kriti Sachdeva'),
    ('DR.MRS. VIDULA .S. JAPE','Dr. Vidula S. Jape'),
    ('DR.MRS.VRISHALI U. EDLABADKAR','Dr. Vrishali U. Edlabadkar'),
])
def test_compound_titles_and_leading_dot_initials(text,expected):
    from campusscraper.profile import find_name_in_text
    assert find_name_in_text(text)==expected

def test_robots_blocked_batch_is_failed_and_not_cached(monkeypatch,tmp_path):
    from campusscraper import batch
    from campusscraper.crawl import CrawlResult
    from campusscraper.sources import Source
    class BlockedCrawler:
        def __init__(self,*args,**kwargs): pass
        def run(self,urls):
            return CrawlResult(urls,stats={'blocked_by_robots':1,'stop_reason':'robots_blocked'})
    monkeypatch.setattr(batch,'Crawler',BlockedCrawler)
    events=[]
    batch.run_batch([Source(row=2,institution='Example College',urls=[URL])],tmp_path/'out.xlsx',
                    ScrapeConfig(),10,progress=events.append)
    done=next(e for e in events if e.get('event')=='site_done')
    assert done['status']=='failed' and 'no pages read' in done['note']
    assert not list((tmp_path/'out_sites').glob('*.json'))

def test_profile_enrichment_preserves_directory_and_cv_contacts():
    listing=person();listing.page_type='listing';listing.set('email','anitha.rao@college.example','listing_card')
    profile=person();profile.set('email','anitha.rao@example.net','pdf')
    m=Merger();m.add(listing);m.add(profile)
    assert set(m.records[0].get('email').split('; '))=={'anitha.rao@college.example','anitha.rao@example.net'}
    later=person();later.page_type='listing';later.set('email','anitha.rao@new.example','listing_card')
    m.add(later)
    assert 'anitha.rao@new.example' in m.records[0].get('email')

def test_site_requested_delay_is_not_capped(monkeypatch):
    from campusscraper.fetch import Fetcher
    from campusscraper.robots import RobotsPolicy
    f=Fetcher(ScrapeConfig(delay_seconds=1.5))
    waits=[]
    f._robots['https://college.example']=RobotsPolicy('User-agent: *\nCrawl-delay: 60','CampusScraper')
    monkeypatch.setattr(f,'_sleep',waits.append)
    try:
        f._pace(URL);f._pace(URL)
        assert waits and 59 <= waits[0] <= 60
    finally: f.close()

def test_slow_sites_start_with_supplied_pages_instead_of_bulk_sitemaps(monkeypatch):
    from campusscraper.robots import RobotsPolicy
    c=Crawler(ScrapeConfig(render='never'))
    policy=RobotsPolicy('User-agent: *\nCrawl-delay: 60\nSitemap: https://college.example/sitemap.xml','CampusScraper')
    monkeypatch.setattr(c.fetcher,'robots_for',lambda _:policy)
    monkeypatch.setattr(c.fetcher,'_raw_get',lambda _:pytest.fail('slow sitemap should not be fetched'))
    try: assert c._sitemap_urls(URL)==[]
    finally: c.fetcher.close()

@pytest.mark.parametrize('expected,published,accepted',[
    ('Dr. Anitha Priya Rao','Name Anitha P. Rao',True),
    ('Dr. Anitha Priya Rao','Name Anitha Rao',True),
    ('Dr. Anitha Priya Rao','Name Rao Anitha Priya',True),
    ('Dr. Anitha Priya Rao','Name Anitha Kavya Rao',False),
    ('Dr. A. Rao','Name Anitha Rao',False),
])
def test_pdf_labelled_name_variants_require_full_identity(expected,published,accepted):
    p=Page(URL+'a.pdf',URL+'a.pdf',200,published+'\nProfessor\nEmail: anitha.rao@college.example','application/pdf')
    assert bool(extract_faculty_pdf(p,person(expected)).doctors)==accepted

def test_department_head_repeated_in_faculty_cards_keeps_identity_and_contact():
    html=('<h1>Mechanical Engineering</h1><div><h3>Prof. Anitha Rao</h3><h4>Head of Department</h4></div>'
          '<section><div><h4>Prof. Anitha Rao</h4><p>Assistant Professor</p><p>anitha.rao@college.example</p></div>'
          '<div><h4>Prof. Priya Sharma</h4><p>Assistant Professor</p></div>'
          '<div><h4>Prof. Farah Siddiqui</h4><p>Assistant Professor</p></div></section>')
    result=page(html)
    people=[d for d in result.doctors if d.get('name')=='Prof. Anitha Rao']
    assert len(people)==1 and people[0].is_relevant
    assert people[0].get('email')=='anitha.rao@college.example'


def test_merged_headers_preserve_email_and_research_columns():
    pr=page('<h1>Faculty</h1><table><tr><th rowspan="2">Name</th><th rowspan="2">Designation</th>'
            '<th colspan="2">Experience</th><th rowspan="2">Research Interests</th><th rowspan="2">Email</th></tr>'
            '<tr><th>Industry</th><th>Academic</th></tr>'
            '<tr><td>Dr. Anitha Rao</td><td>Professor</td><td>2</td><td>7.5</td>'
            '<td>Power Systems</td><td>anitha.rao@college.example</td></tr></table>')
    r=next(r for r in pr.doctors if r.get('name')=='Dr. Anitha Rao')
    assert r.get('email')=='anitha.rao@college.example'
    assert r.get('research_areas')==['Power Systems']


def test_person_row_email_without_recognized_header():
    pr=page('<h1>Faculty</h1><table><tr><th>Name</th><th>Designation</th><th>Reach me</th></tr>'
            '<tr><td>Dr. Anitha Rao</td><td>Professor</td><td>anitha.rao@college.example</td></tr>'
            '<tr><td>Dr. Priya Sharma</td><td>Professor</td><td>priya.sharma@college.example</td></tr></table>')
    assert {r.get('email') for r in pr.doctors}=={'anitha.rao@college.example','priya.sharma@college.example'}


@pytest.mark.parametrize('path',['special-placement.aspx','placement-history','placement-records','student-chapter-activities-mech/'])
def test_outcomes_and_external_speakers_not_people_even_under_faculty(path):
    u=URL+path
    pr=extract_page(Page(u,u,200,'<h1>Faculty activities</h1><table><tr><th>Name</th><th>Designation</th></tr>'
                        '<tr><td>Dr. Anitha Rao</td><td>Professor</td></tr></table>'))
    assert not pr.doctors


def test_repeated_named_email_kept_but_footer_contact_demoted():
    from campusscraper.contacts import demote_sitewide, demote_shared
    from campusscraper.profile import assign_emails
    r=person();assign_emails(r,['anitha.rao@college.example'],'table_row','Dr. Anitha Rao | anitha.rao@college.example',URL)
    r.set('phone','+91 2023456789','patterns')
    vp={v:{URL+'a',URL+'b',URL+'c'} for v in [r.get('email'),r.get('phone')]}
    demote_sitewide([r],vp)
    assert r.get('email')=='anitha.rao@college.example'
    assert not r.get('phone') and r.get('contact_phone')=='+91 2023456789'
    # Independently shared attribution still requires caution.
    other=person('Dr. Priya Sharma');other.set('email',r.get('email'),'labels')
    demote_shared([r,other])
    assert not r.get('email') and not other.get('email')


def test_abbreviated_professor_roles_survive_cleaning():
    from campusscraper.profile import split_designation
    from campusscraper.relevance import score
    assert split_designation('Asst. Prof.')[0]=='Assistant Professor'
    assert split_designation('Assoc. Prof.')[0]=='Associate Professor'
    r=person();r.values['designation']='Asst. Prof.';clean_fields(r)
    assert r.get('designation')
    r=person('Prof. Anitha Rao');r.values.pop('designation');score(r)
    assert r.is_relevant


def test_directory_api_uses_observed_ids_and_paginates():
    from bs4 import BeautifulSoup
    from campusscraper.discovery import directory_links, directory_form, directory_next
    soup=BeautifulSoup('<select id="fac_school"><option value="">All</option><option value="2">Engineering</option></select>','html.parser')
    links=directory_links(soup,'https://mitwpu.edu.in/faculty-members')
    assert len(links)==1 and directory_form(links[0])['school_id']=='2'
    assert directory_next(links[0],'<div>Faculty</div>###156').endswith('page=1')
    assert directory_next(links[0].replace('page=0','page=1'),'<div>Faculty</div>###156') is None
    assert directory_links(soup,'https://other.example/faculty-members')==[]
    assert directory_form('https://other.example/faculty/showFacultyDataAjax?school_id=2') is None


def test_resume_cache_invalidated_by_extractor_and_settings(monkeypatch):
    from campusscraper import batch
    from campusscraper.sources import Source
    src=Source(2,'Example',[URL]);cfg=ScrapeConfig()
    key=batch._cache_key(src,cfg,150)
    assert key!=batch._cache_key(src,cfg,300)
    src.urls.append(URL+'other')
    assert key!=batch._cache_key(src,cfg,150)
    src.urls.pop();monkeypatch.setattr(batch,'EXTRACTION_REVISION',99)
    assert key!=batch._cache_key(src,cfg,150)


def test_directory_post_and_profiles_flow_through_crawler(monkeypatch):
    import httpx
    from campusscraper.discovery import directory_form
    c=Crawler(ScrapeConfig(max_pages=4,workers=1,render='never',use_sitemaps=False,
                           respect_robots=False,delay_seconds=0))
    seen=[]
    def handler(req):
        seen.append((req.method,str(req.url),req.content.decode()))
        if req.url.path=='/faculty-members':
            html='<select id="fac_school"><option value="2">Engineering</option></select>'
        elif req.url.path=='/faculty/showFacultyDataAjax':
            assert req.method=='POST' and 'school_id=2' in req.content.decode()
            html='<a href="/faculty/anitha-rao">Dr. Anitha Rao</a>###1'
        else:
            html='<h1>Dr. Anitha Rao</h1><p>Professor</p><p>Email: anitha.rao@college.example</p>'
        return httpx.Response(200,text=html,headers={'content-type':'text/html'},request=req)
    c.fetcher.client.close();c.fetcher.client=httpx.Client(transport=httpx.MockTransport(handler))
    result=c.run(['https://mitwpu.edu.in/faculty-members'])
    assert any(method=='POST' for method,_,_ in seen)
    assert any(r.get('email')=='anitha.rao@college.example' for r in result.faculty)
    assert len(result.pages)==3


def test_grouped_experience_does_not_export_industry_as_total():
    pr=page('<h1>Faculty</h1><table><tr><th>Name</th><th>Designation</th><th colspan="2">Experience</th></tr>'
            '<tr><td>Dr. Anitha Rao</td><td>Professor</td><td>2</td><td>7.5</td></tr></table>')
    r=next(r for r in pr.doctors if r.get('name')=='Dr. Anitha Rao')
    assert not r.get('experience_years')


def test_batch_reruns_when_cached_settings_change(monkeypatch,tmp_path):
    from campusscraper import batch
    from campusscraper.crawl import CrawlResult, PageLog
    from campusscraper.sources import Source
    calls=[]
    class FakeCrawler:
        def __init__(self,*args,**kwargs): pass
        def run(self,urls):
            calls.append(urls)
            return CrawlResult(urls,pages=[PageLog(URL,200,'other',0,0,'')],stats={'stop_reason':'queue_exhausted'})
    monkeypatch.setattr(batch,'Crawler',FakeCrawler)
    src=[Source(2,'Example',[URL])];out=tmp_path/'out.xlsx';cfg=ScrapeConfig()
    batch.run_batch(src,out,cfg,150)
    batch.run_batch(src,out,cfg,150)
    assert len(calls)==1
    batch.run_batch(src,out,cfg,300)
    assert len(calls)==2
    monkeypatch.setattr(batch,'EXTRACTION_REVISION',99)
    batch.run_batch(src,out,cfg,300)
    assert len(calls)==3


# ---- the employer is the site, not the university someone studied at --------------
PROFILE_WITH_DEGREES = """<!doctype html><html><head>
<meta property="og:site_name" content="Thadomal Shahani Engineering College">
<title>Prof. Mridul Saxena | Thadomal Shahani Engineering College</title></head><body>
<h1>Prof. Mridul Saxena</h1>
<p>Assistant Professor, Department of Artificial Intelligence and Data Science</p>
<p>Email: mridul.saxena@thadomal.org</p>
<table><tr><th>Degree</th><th>Year</th><th>University</th></tr>
<tr><td>Ph.D</td><td>2022</td><td>A Haryana State Public University (Formerly YMCA University)</td></tr>
<tr><td>M.Tech</td><td>2014</td><td>Amity University</td></tr></table>
<p>Conference: WET-WAR 2025, NIT Patna, India</p>
</body></html>"""


def test_a_persons_alma_mater_is_never_recorded_as_their_institution():
    """142 of 785 rows in a live run named the university someone studied at."""
    result = extract_page(Page("https://tsec.edu/faculty/mridul", "https://tsec.edu/faculty/mridul",
                               200, PROFILE_WITH_DEGREES))
    assert result.doctors, "the faculty member was still read"
    person = result.doctors[0]
    assert person.get("institution") == "Thadomal Shahani Engineering College"
    assert "Amity" not in str(person.get("institution"))
    assert "YMCA" not in str(person.get("institution"))


def test_a_degree_or_conference_line_is_never_an_institution():
    for text in ["Ph.D ,2022, UoM", "M. Tech., 2007, IITB", "Conference: WET-WAR 2025, NIT Patna, India",
                 "M.Tech. ,1990 NIT, Kurukshetra"]:
        rec = PersonRecord(source_url="https://x.ac.in/f/1")
        rec.set("name", "Dr. A B", "h1")
        rec.set("institution", text, "labels")
        clean_fields(rec)
        assert not rec.get("institution"), f"{text!r} was kept as an institution"


def test_a_site_whose_banner_is_an_advertisement_does_not_name_its_people():
    html = PROFILE_WITH_DEGREES.replace("Thadomal Shahani Engineering College",
                                        "Best Engineering College in Mumbai")
    result = extract_page(Page("https://x.ac.in/f/1", "https://x.ac.in/f/1", 200, html))
    assert result.doctors
    assert not result.doctors[0].get("institution"), "a search headline is not a college name"


# ---- headings and subjects are not people ----------------------------------------
@pytest.mark.parametrize("text", [
    "Professional Summary", "Educational Qualifications", "Research Interests",
    "Awards and Honours", "Prof. Dean-Student", "Prof. Biotechnology, Professor",
])
def test_a_heading_or_a_subject_is_never_read_as_a_person(text):
    from campusscraper.profile import find_name_in_text
    assert find_name_in_text(text) is None


@pytest.mark.parametrize("text,expected", [
    ("Dr. Anil Mehta", "Dr. Anil Mehta"),
    ("Ms. Khushboo", "Ms. Khushboo"),
    ("Dr. N. C. Shivprakash", "Dr. N. C. Shivprakash"),
    ("Prof. Shanthi Therese", "Prof. Shanthi Therese"),
    ("Dr. Mohan Krishna, Training and Placement Officer", "Dr. Mohan Krishna"),
])
def test_real_names_are_still_read(text, expected):
    from campusscraper.profile import find_name_in_text
    assert find_name_in_text(text) == expected


# ---- a faculty list drawn by JavaScript ------------------------------------------
SHELL = ("<!doctype html><html><head><title>Faculty | Example College</title>"
         + "<script src='/static/bundle.js'></script>" * 9 +
         "</head><body><div id='app'></div><nav>Home About Admissions Departments Contact Us</nav>"
         "<footer>Example College, Mumbai</footer></body></html>")
FILLED = ("<!doctype html><html><head><title>Faculty | Example College</title></head><body>"
          "<h1>Faculty</h1><table><tr><th>Name</th><th>Designation</th><th>E-mail</th></tr>"
          "<tr><td>Dr. M. Suresh</td><td>Professor</td><td>suresh@example.ac.in</td></tr>"
          "<tr><td>Mr. P. Kiran</td><td>Assistant Professor</td><td>kiran@example.ac.in</td></tr>"
          "</table></body></html>")


def test_an_empty_faculty_page_is_given_one_look_through_the_browser(monkeypatch):
    """119 pages produced 6 faculty on a site that draws its list with JavaScript."""
    cfg = ScrapeConfig(max_pages=2, delay_seconds=0, render="auto", allow_private_hosts=True,
                       retries=0, use_sitemaps=False, respect_robots=False)
    crawler = Crawler(cfg)
    url = "https://example.ac.in/faculty"
    monkeypatch.setattr(crawler.fetcher, "get",
                        lambda u, **k: Page(u, u, 200, SHELL))
    monkeypatch.setattr(crawler.fetcher, "render_now",
                        lambda page: Page(page.url, page.final_url, 200, FILLED, rendered=True))
    result = crawler.run([url])
    names = {d.get("name") for d in result.doctors}
    assert {"Dr. M. Suresh", "Mr. P. Kiran"} <= names, f"the rendered list was missed: {names}"


def test_a_page_that_already_has_its_people_is_not_rendered_again(monkeypatch):
    cfg = ScrapeConfig(max_pages=2, delay_seconds=0, render="auto", allow_private_hosts=True,
                       retries=0, use_sitemaps=False, respect_robots=False)
    crawler = Crawler(cfg)
    calls = []
    monkeypatch.setattr(crawler.fetcher, "get", lambda u, **k: Page(u, u, 200, FILLED))
    monkeypatch.setattr(crawler.fetcher, "render_now", lambda page: calls.append(page) or None)
    crawler.run(["https://example.ac.in/faculty"])
    assert calls == [], "the browser was started for a page that was already complete"


def _labelled(name, institution, url, page_type, city="Mumbai"):
    r = PersonRecord(source_url=url, profile_url=url if page_type == "profile" else None, page_type=page_type)
    r.set("name", name, "h1", name, url)
    r.set("institution", institution, "patterns", institution, url)
    r.set("city", city, "patterns", city, url)
    r.set("designation", "Professor", "labels")
    return r


def test_card_and_profile_merge_when_institution_is_written_differently():
    # The card says 'IIT Bombay', the profile says the full name; the first 12 characters differ.
    for reverse in (False, True):
        card = _labelled("Dr. Anitha Rao", "IIT Bombay", "https://www.iitb.ac.in/cse/faculty", "listing")
        card.set("email", "anitha@iitb.ac.in", "listing_card")
        profile = _labelled("Dr. Anitha Rao", "Indian Institute of Technology Bombay", "https://iitb.ac.in/people/anitha", "profile")
        m = Merger()
        for r in ((profile, card) if reverse else (card, profile)):
            m.add(r)
        assert len(m.records) == 1, reverse
        merged = m.records[0]
        assert merged.page_type == "profile"
        assert merged.get("institution") == "Indian Institute of Technology Bombay"
        assert "anitha@iitb.ac.in" in merged.get("email")


def test_institution_variant_merge_needs_same_site_city_and_full_name():
    for change in ("city", "missing_city", "host", "name", "two_profiles", "source_institution"):
        card = _labelled("Dr. Anitha Rao", "IIT Bombay", "https://iitb.ac.in/cse/faculty", "listing")
        profile = _labelled("Dr. Anitha Rao", "Indian Institute of Technology Bombay", "https://iitb.ac.in/people/anitha", "profile")
        if change == "city":
            profile.values["city"] = "Delhi"
        elif change == "missing_city":
            profile.values.pop("city")
        elif change == "host":
            profile.source_url = "https://other.ac.in/people/anitha"
        elif change == "name":
            profile.values["name"] = "Dr. Anita Rao"
        elif change == "two_profiles":
            card.page_type = "profile"
            card.profile_url = card.source_url
        else:
            card.source_institution, profile.source_institution = "College A", "College B"
        m = Merger()
        m.add(card)
        m.add(profile)
        assert len(m.records) == 2, change


def test_acronym_glued_to_several_names_is_stripped_sitewide():
    from campusscraper.name_suffix import strip_shared_name_suffix, trailing_acronym
    assert trailing_acronym("Dr. Anitha Rao PESU") == "PESU"
    assert trailing_acronym("Mrs. Lakshmi DEVI") is None       # surname in caps
    assert trailing_acronym("DR. ANITHA RAO PESU") is None     # whole name in caps
    assert trailing_acronym("Dr. Anitha Rao PhD") is None      # a degree
    recs = [_labelled("Dr. Anitha Rao PESU", "PES University", "https://pes.edu/faculty", "listing"),
            _labelled("Prof. K. Srinivas PESU", "PES University", "https://pes.edu/faculty", "listing")]
    assert strip_shared_name_suffix(recs) == {"PESU": 2}
    assert [r.get("name") for r in recs] == ["Dr. Anitha Rao", "Prof. K. Srinivas"]
    only = [_labelled("Dr. Anitha Rao PESU", "PES University", "https://pes.edu/faculty", "listing")]
    assert strip_shared_name_suffix(only) == {} and only[0].get("name") == "Dr. Anitha Rao PESU"


def test_crawl_strips_shared_acronym_and_merges_card_with_profile(monkeypatch):
    import campusscraper.crawl as crawl_mod
    base = "https://acr.example"
    pages = {
        f"{base}/faculty": "<h1>Faculty</h1>" + "".join(
            f'<div class="card"><h3>Dr. {n} PESU</h3><p>Assistant Professor, Computer Science and Engineering</p></div>'
            for n in ("Anitha Rao", "Priya Sharma", "Rajesh Kumar")) + '<a href="/people/anitha-rao">Profile</a>',
        f"{base}/people/anitha-rao": "<h1>Dr. Anitha Rao</h1><p>Assistant Professor, Computer Science and Engineering</p>"
                                     "<p>Email: anitha.rao@acr.example</p>",
    }

    class FakeFetcher:
        requests_made = 0
        _robots = {}
        def __init__(self, cfg, *a, **k): pass
        def get(self, url, *a, **kw):
            html = pages.get(url.rstrip("/"))
            return Page(url, url, 200 if html else 404, f"<html><body>{html}</body></html>" if html else "")
        def close(self): pass
        def robots_for(self, url):
            from campusscraper.robots import AllowAll
            return AllowAll()

    monkeypatch.setattr(crawl_mod, "Fetcher", FakeFetcher)
    result = Crawler(ScrapeConfig(delay_seconds=0, render="never", use_sitemaps=False, max_pages=10)).run([f"{base}/faculty"])
    names = sorted(d.get("name") for d in result.doctors)
    assert names == ["Dr. Anitha Rao", "Dr. Priya Sharma", "Dr. Rajesh Kumar"], names
    anitha = next(d for d in result.doctors if d.get("name") == "Dr. Anitha Rao")
    assert anitha.page_type == "profile" and anitha.get("email") == "anitha.rao@acr.example"
