"""Bound discovery work without accepting unverified institutions."""
from types import SimpleNamespace
from campusscraper.models import Page
from campusscraper.prompt_discovery import Limits, Decision
from campusscraper.verified_discovery import discover_verified, page_content, compact_evidence, _check_page
from campusscraper.search_candidates import exclusion_reason
from test_prompt_discovery import Fetch, Search, ScriptedModel, plan, decision, HTML, URL
from test_prompt_workflow import REVIEW
import pytest


def test_long_rejected_page_costs_one_assessment_not_six():
    fetch = Fetch(HTML + '<p>' + 'Generic campus history. ' * 2000 + '</p>')
    model = ScriptedModel(plan(requested_institutions=1), REVIEW, decision(accepted=False))
    report = discover_verified('Find faculty', model, Search(), fetch, Limits(rounds=1))
    calls = [c for c in model.calls if c[0] is Decision]
    assert len(calls) == 1 and report.pages == 1
    assert len(calls[0][2]['current_text']) <= 6000
    assert not report.candidates


def test_logo_identity_navigation_cleanup_and_location_excerpt():
    html = '<nav><img alt="Example Engineering College" src="logo.png">' + 'Menu ' * 4000 + '</nav>'
    html += '<h1>Faculty</h1><p>Dr. Rao Professor</p>' + '<p>History</p>' * 3000
    html += '<footer>Address: Mumbai 400001</footer>'
    text, chunks, _ = page_content(Page(URL, URL, 200, html))
    evidence = compact_evidence(text, 'Mumbai engineering colleges')
    assert 'Example Engineering College' in evidence
    assert 'Dr. Rao Professor' in evidence and 'Mumbai 400001' in evidence
    assert 'Menu Menu' not in evidence
    assert len(evidence) <= 6000 and len(chunks) == 1


def test_identity_link_supplies_location_without_extra_model_call():
    about = 'https://college.example/contact'
    p = plan(requested_institutions=1, criteria=[dict(id='city', requirement='In Mumbai', prompt_quote='Mumbai', applies_to='institution')])
    class Pages(Fetch):
        def get(self, url):
            self.urls.append(url)
            html = ('<h1>Example Engineering College</h1><p>Address: Mumbai 400001</p>' if url == about else
                    '<h1>Faculty Members</h1><p>Dr. Rao Professor</p><a href="/contact">Contact us</a>')
            return Page(url,url,200,html)
    d = decision(staff_page_quote='Faculty Members', evidence=[dict(criterion_id='city',quote='Mumbai 400001')])
    model, fetch = ScriptedModel(p, REVIEW, d), Pages()
    report = discover_verified('Find faculty in Mumbai',model,Search(),fetch,Limits(rounds=1))
    assert report.candidates and report.pages == 2 and report.model_calls == 3
    assert report.candidates[0]['evidence_sources'][0]['source_url'] == about
    assert fetch.urls == [URL,about]


def test_identity_redirect_to_other_host_cannot_supply_evidence():
    p = plan(criteria=[dict(id='city',requirement='Mumbai',prompt_quote='Mumbai',applies_to='institution')])
    class Pages(Fetch):
        def get(self,url):
            if url.endswith('/contact'):
                return Page(url,'https://other.example/contact',200,'Mumbai 400001')
            return Page(url,url,200,'<h1>Faculty Members</h1><a href="/contact">Contact</a>')
    model=ScriptedModel(p,REVIEW,decision(accepted=False))
    report=discover_verified('Mumbai faculty',model,Search(),Pages(),Limits(rounds=1))
    assert not report.candidates
    assert model.calls[-1][2]['related_documents'] == []


def test_quote_formatting_restores_literal_source_but_never_paraphrases():
    d=decision(institution='example engineering college',staff_page_quote='Official   faculty directory in Mumbai')
    _check_page(d,plan(),'Example Engineering College Official faculty directory in Mumbai',[],[])
    assert d.institution == 'Example Engineering College'
    assert d.staff_page_quote == 'Official faculty directory in Mumbai'
    with pytest.raises(ValueError,match='staff evidence'):
        _check_page(decision(staff_page_quote='List of teachers in Mumbai'),plan(),
                    'Example Engineering College Official faculty directory in Mumbai',[],[])


def test_aggregators_are_skipped_without_excluding_unrelated_hosts():
    assert exclusion_reason('https://www.scribd.com/document/1')
    assert exclusion_reason('https://facultyplus.com/jobs/')
    assert not exclusion_reason('https://scribd.com.college.example/faculty')


def test_short_literal_heading_and_location_are_valid_evidence():
    p=plan(criteria=[dict(id='city',requirement='Mumbai',prompt_quote='Mumbai',applies_to='institution')])
    d=decision(staff_page_quote='Faculty',evidence=[dict(criterion_id='city',quote='Mumbai')])
    evidence=_check_page(d,p,'Example Engineering College Faculty Dr Rao Professor Mumbai',[],[])
    assert evidence[0]['quote'] == 'Mumbai'
