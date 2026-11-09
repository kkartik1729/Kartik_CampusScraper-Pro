"""Search must reject unrelated SERPs before fetching or model assessment."""
import httpx
import pytest
from campusscraper.discovery_services import CachedSearch, ConfigurationError, DiscoveryDB
from campusscraper.search_candidates import unrelated_result

QUERY = 'engineering colleges Mumbai faculty directory site:.ac.in'
BAD = {'url': 'https://www.bath.ac.uk/campaigns/what-is-engineering/',
       'title': 'What is engineering? - University of Bath',
       'content': 'Engineering is designing and building machines using maths and science.',
       'engines': ['bing']}
GOOD = {'url': 'https://college.example/faculty', 'title': 'Mumbai engineering college faculty', 'engines': ['google']}


def test_quality_gate_preserves_sparse_acronym_and_relevant_sources():
    assert unrelated_result(QUERY, BAD)
    assert not unrelated_result(QUERY, GOOD)
    assert not unrelated_result('VJTI', {'url': 'https://vjti.ac.in/', 'title': 'Home'})
    assert not unrelated_result(QUERY, {'url': 'https://college.example/'})


def test_bad_search_recovers_once_and_caches_good_urls(tmp_path):
    db = DiscoveryDB(tmp_path / 'db')
    calls = []
    def handle(req):
        calls.append(req)
        assert req.url.params['q'] == QUERY
        assert req.url.params['safesearch'] == '2'
        result = GOOD if 'engines' in req.url.params else BAD
        return httpx.Response(200, json={'results': [result]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://localhost:8080', client=client)
        assert search.search(QUERY) == [GOOD['url']]
        assert search.search(QUERY) == [GOOD['url']]
        assert search.search_details[QUERY]['initial_rejected_results'] == [BAD | {'content': BAD['content']}]
    assert len(calls) == 2
    assert 'bing' not in calls[-1].url.params['engines']
    db.db.close()


@pytest.mark.parametrize('fallback', [[], [BAD]])
def test_bad_search_never_becomes_success_or_infinite_retry(tmp_path, fallback):
    db = DiscoveryDB(tmp_path / 'db')
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(200, json={'results': fallback if len(calls) > 1 else [BAD]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://localhost:8080', client=client)
        with pytest.raises(ConfigurationError, match='unrelated'):
            search.search(QUERY)
        assert search.search_details[QUERY]['rejected_results'] == [BAD]
    assert len(calls) == 2
    db.db.close()


def test_fallback_outage_evidence_is_serializable(tmp_path):
    import json
    db = DiscoveryDB(tmp_path / 'db')
    calls = []
    def handle(req):
        calls.append(req)
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={'results': [BAD]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://localhost:8080', client=client)
        with pytest.raises(ConfigurationError):
            search.search(QUERY)
        json.dumps(search.search_details)
        assert search.search_details[QUERY]['fallback_used']
    db.db.close()


def test_discovery_continues_after_bad_first_query_to_five_sources(tmp_path):
    from campusscraper.verified_discovery import discover_verified
    from campusscraper.prompt_discovery import Limits
    from test_prompt_discovery import Fetch, ScriptedModel, plan, decision
    from test_prompt_workflow import REVIEW
    urls = [f'https://college{i}.example/faculty' for i in range(5)]
    calls = []
    def handle(req):
        calls.append(str(req.url))
        if req.url.params['q'] == QUERY:
            results = [BAD] if 'engines' not in req.url.params else []
        else:
            results = [dict(GOOD, url=u) for u in urls]
        return httpx.Response(200, json={'results': results})
    db = DiscoveryDB(tmp_path / 'db')
    class CollegePages(Fetch):
        def get(self, url):
            page = super().get(url)
            index = urls.index(url)
            page.html = page.html.replace('Example Engineering College', f'Example Engineering College {index}')
            return page
    fetch = CollegePages()
    events = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://search.test', client=client)
        model = ScriptedModel(plan(queries=[QUERY, 'Mumbai engineering college faculty'], requested_institutions=5,
            criteria=[dict(id='location', requirement='Located in Mumbai', prompt_quote='Mumbai', applies_to='institution')]),
            REVIEW, *[decision(institution=f'Example Engineering College {i}',
                official_site_quote=f'Example Engineering College {i}',
                evidence=[dict(criterion_id='location', quote='directory in Mumbai')]) for i in range(5)])
        report = discover_verified('Find 5 engineering colleges in Mumbai', model, search, fetch,
                                   Limits(rounds=1, searches=5), progress=events.append)
    assert report.status == 'candidates_found'
    assert {c['url'] for c in report.candidates} == set(urls)
    assert report.search_attempts[0]['status'] == 'unrelated'
    assert report.search_attempts[0]['rejected_results'] == [BAD]
    assert len(fetch.urls) == 5
    assert BAD['url'] not in fetch.urls
    assert report.model_calls == 7  # plan/review plus five verified page decisions
    db.db.close()


def test_all_unrelated_queries_finish_with_no_fabricated_sources(tmp_path):
    from campusscraper.verified_discovery import discover_verified
    from campusscraper.prompt_discovery import Limits
    from test_prompt_discovery import Fetch, ScriptedModel, plan
    from test_prompt_workflow import REVIEW
    db = DiscoveryDB(tmp_path / 'db')
    calls = []
    def handle(req):
        calls.append(req)
        return httpx.Response(200, json={'results': [BAD]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://search.test', client=client)
        fetch = Fetch()
        report = discover_verified('Find engineering colleges in Mumbai',
            ScriptedModel(plan(queries=[QUERY]), REVIEW), search, fetch, Limits(rounds=1, searches=2))
    assert report.status == 'no_candidates' and not report.candidates
    assert report.pages == 0 and report.model_calls == 2
    assert report.searches == 2 and len(calls) == 4
    assert not fetch.urls
    db.db.close()


def test_real_configuration_errors_still_stop_and_keep_evidence(tmp_path):
    from campusscraper.verified_discovery import discover_verified
    from test_prompt_discovery import Fetch, ScriptedModel, plan
    from test_prompt_workflow import REVIEW
    db = DiscoveryDB(tmp_path / 'db')
    events = []
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(403))) as client:
        search = CachedSearch(db, 'job', 'http://search.test', client=client)
        with pytest.raises(ConfigurationError, match='JSON'):
            discover_verified('Find engineering colleges', ScriptedModel(plan(), REVIEW), search, Fetch(), progress=events.append)
    assert events[-1]['report']['search_attempts'][0]['status'] == 'failed'
    db.db.close()


def test_faculty_link_survives_large_about_navigation():
    from campusscraper.verified_discovery import page_content
    from campusscraper.models import Page
    html = ''.join(f'<a href="/about/{i}">About {i}</a>' for i in range(30))
    html += '<a href="/zz/faculty">Our faculty</a><a href="http://127.0.0.1/admin">Staff</a>'
    _, _, links = page_content(Page('https://college.example/', 'https://college.example/', 200, html))
    assert links[0]['url'] == 'https://college.example/zz/faculty'
    assert len(links) == 20
    assert all('127.0.0.1' not in l['url'] for l in links)


def test_alternative_engines_do_not_reenable_bing_via_general_category(tmp_path):
    db = DiscoveryDB(tmp_path / 'db')
    calls = []
    def handle(req):
        calls.append(req)
        # Match SearXNG's additive categories/engines contract.
        is_isolated = 'engines' in req.url.params and 'categories' not in req.url.params
        return httpx.Response(200, json={'results': [GOOD if is_isolated else BAD]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://search.test', client=client)
        assert search.search(QUERY) == [GOOD['url']]
    assert len(calls) == 2
    assert 'startpage' in calls[-1].url.params['engines']
    assert 'categories' not in calls[-1].url.params
    db.db.close()


def test_bing_cannot_masquerade_as_alternative_engine_recovery(tmp_path):
    db = DiscoveryDB(tmp_path / 'db')
    def handle(req):
        return httpx.Response(200, json={'results': [dict(GOOD, engines=['bing'])]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        search = CachedSearch(db, 'job', 'http://search.test', client=client)
        from campusscraper.discovery_services import IncompleteSearch
        with pytest.raises(IncompleteSearch):
            search.search(QUERY, _fallback=True)
        assert search.search_details[QUERY]['unexpected_engine_results']
    db.db.close()


def test_invalid_optional_refinement_retains_verified_source():
    from campusscraper.discovery_services import ModelResponseError
    from campusscraper.verified_discovery import discover_verified
    from campusscraper.prompt_discovery import Limits
    from test_prompt_discovery import Fetch, Search, ScriptedModel, plan, decision
    from test_prompt_workflow import REVIEW
    model = ScriptedModel(plan(requested_institutions=2), REVIEW, decision(),
                          ModelResponseError('groq: incomplete or invalid structured response'))
    report = discover_verified('Find two colleges', model, Search(), Fetch(), Limits(rounds=2))
    assert len(report.candidates) == 1 and report.status == 'candidates_found'
    assert any('retained' in i for i in report.issues)
