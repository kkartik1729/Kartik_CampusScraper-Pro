"""Contract and pipeline simulations, NOT an evaluation of a real language model."""
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from openpyxl import load_workbook

from campusscraper.batch import run_batch
from campusscraper.config import ScrapeConfig
from campusscraper.fetch import Fetcher
from campusscraper.models import Page
from campusscraper.prompt_discovery import (
    Plan, Decision, Refinement, Limits, OllamaModel, SearxSearch,
    discover, safe_url, validate_decision,
)

URL = "https://college.example/faculty"
HTML = """<h1>Example Engineering College</h1><p>Official faculty directory in Mumbai</p>
<table><tr><th>Name</th><th>Designation</th><th>Email</th></tr>
<tr><td>Dr. Anitha Rao</td><td>Professor</td><td>anitha.rao@college.example</td></tr></table>"""
TEXT = "Example Engineering College Official faculty directory in Mumbai"


def plan(**kw):
    data = dict(action="search", objective="Find campus faculty", scope="campus_staff",
                criteria=[], queries=["engineering faculty Mumbai"])
    data.update(kw)
    return Plan(**data)


def decision(**kw):
    data = dict(accepted=True, institution="Example Engineering College",
                official_site_quote="Example Engineering College",
                staff_page_quote="Official faculty directory in Mumbai",
                evidence=[], follow_link_ids=[], reason="Official faculty page")
    data.update(kw)
    return Decision(**data)


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def ask(self, schema, instruction, payload):
        self.calls.append((schema, instruction, payload))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, schema)
        return response


class Search:
    def __init__(self, urls=None, error=None):
        self.urls, self.error, self.queries = urls if urls is not None else [URL], error, []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.urls


class Fetch:
    cfg = SimpleNamespace(allow_private_hosts=False)

    def __init__(self, html=HTML, error=None, final_url=None):
        self.html, self.error, self.final_url, self.urls = html, error, final_url, []

    def get(self, url):
        self.urls.append(url)
        return Page(url, self.final_url or url, 200, self.html, error=self.error)


def run(model=None, search=None, fetch=None, **kw):
    return discover("Find engineering faculty in Mumbai", model or ScriptedModel(plan(), decision()),
                    search or Search(), fetch or Fetch(), limits=kw.pop("limits", Limits(rounds=1)), **kw)


@pytest.mark.parametrize("prompt", [
    "Engineering teachers around Mumbai; leave Navi Mumbai out",
    "Robotics pe kaam karne wale professors ki official profiles chahiye",
    "Faculty pages for COEP and PICT",
    "Universities with marine biology research teams, anywhere",
    "Find placement office contacts at autonomous colleges",
])
def test_arbitrary_prompt_passed_intact_not_parsed_by_regex(prompt):
    model = ScriptedModel(plan(), decision())
    result = discover(prompt, model, Search(), Fetch(), Limits(rounds=1))
    assert result.status == "candidates_found"
    assert all(call[2]["original_prompt"] == prompt for call in model.calls)


def test_missing_context_stops_before_any_search():
    model = ScriptedModel(plan(action="clarify", queries=[], questions=["Which city is near you?"]))
    search = Search()
    result = run(model, search)
    assert result.status == "clarify" and not search.queries
    assert result.plan["questions"] == ["Which city is near you?"]


@pytest.mark.parametrize("scope", ["students", "mixed", "other"])
def test_unsupported_scope_cannot_be_search(scope):
    with pytest.raises(ValidationError):
        plan(scope=scope)
    search = Search()
    result = run(ScriptedModel(plan(action="unsupported", scope=scope)), search)
    assert result.status == "unsupported" and not search.queries


@pytest.mark.parametrize("bad", ["http://127.0.0.1/admin", "http://169.254.169.254/", "http://localhost/",
                                  "file:///etc/passwd", "javascript:alert(1)", "https://user:pass@college.example/",
                                  "https://college.example:bad/", "http://[::1]/"])
def test_invalid_urls_never_fetched(bad):
    fetch = Fetch()
    result = run(search=Search([bad]), fetch=fetch)
    assert not fetch.urls and result.status == "no_candidates"
    assert safe_url(bad) is None


def test_normalized_url_dedup_and_observed_links_only():
    fetch = Fetch()
    result = run(search=Search([URL, URL + "#faculty", URL + "?utm_source=test"]), fetch=fetch)
    assert fetch.urls == [URL]
    assert len(result.sources()) == 1
    assert result.sources()[0].urls == [URL]


def test_invented_link_id_rejects_page():
    result = run(ScriptedModel(plan(), decision(follow_link_ids=[999])))
    assert not result.candidates
    assert "unobserved link" in result.issues[0]


def test_invented_quote_rejects_page():
    result = run(ScriptedModel(plan(), decision(staff_page_quote="This text is not on the page")))
    assert not result.candidates
    assert "not found" in result.issues[0]


def test_every_constraint_including_exclusion_needs_evidence():
    p = plan(criteria=[dict(id="exclude", requirement="Outside Navi Mumbai", prompt_quote="Mumbai",
                           applies_to="institution")])
    result = run(ScriptedModel(p, decision()))
    assert not result.candidates and "every criterion" in result.issues[0]


def test_unanchored_plan_stops_before_search():
    p = plan(criteria=[dict(id="invented", requirement="Only Pune", prompt_quote="Pune",
                           applies_to="institution")])
    search = Search()
    result = run(ScriptedModel(p), search)
    assert result.status == "planning_failed" and not search.queries


def test_person_filter_cannot_silently_export_entire_college():
    p = plan(criteria=[dict(id="person", requirement="Faculty in Mumbai", prompt_quote="faculty in Mumbai",
                           applies_to="person")])
    d = decision(evidence=[dict(criterion_id="person", quote="Official faculty directory in Mumbai")])
    result = run(ScriptedModel(p, d))
    assert result.candidates
    with pytest.raises(ValueError, match="Person-level"):
        result.sources()


def test_model_offline_does_not_fallback_to_guessed_urls():
    result = run(ScriptedModel(httpx.ConnectError("Ollama offline")))
    assert result.status == "planning_failed"
    assert result.searches == result.pages == 0


def test_search_outage_reported_separately_from_no_results():
    result = run(search=Search(error=httpx.HTTPStatusError("429", request=httpx.Request("GET", URL),
                                                         response=httpx.Response(429))))
    assert result.status == "no_candidates" and "Search failed" in result.issues[0]


def test_page_failure_does_not_invoke_assessor():
    model = ScriptedModel(plan())
    result = run(model, fetch=Fetch(error="robots.txt disallowed"))
    assert len(model.calls) == 1 and not result.candidates
    assert "robots.txt" in result.issues[0]


def test_long_page_evidence_cap_is_reported():
    result = run(fetch=Fetch(HTML + "<p>" + "unexamined text " * 2000 + "</p>"))
    assert result.candidates
    assert any("evidence capped" in issue for issue in result.issues)


def test_budget_and_requested_count_shortfall_are_explicit():
    p = plan(requested_institutions=10, queries=["one", "two", "three"])
    result = run(ScriptedModel(p, decision()), limits=Limits(pages=1, searches=1, rounds=1))
    assert result.pages == result.searches == 1
    assert any("incomplete" in i for i in result.issues)
    assert any("Requested 10" in i for i in result.issues)


def test_cancel_before_planning_does_no_work():
    stop = threading.Event()
    stop.set()
    result = run(ScriptedModel(), stop=stop)
    assert result.status == "cancelled" and result.model_calls == 0


def test_refinement_queries_are_bounded():
    model = ScriptedModel(plan(), Refinement(queries=["new faculty query"], unresolved=["Directory missing"]))
    search = Search([])
    result = run(model, search, limits=Limits(rounds=2))
    assert search.queries == ["engineering faculty Mumbai", "new faculty query"]
    assert result.model_calls == 2 and "Directory missing" in result.issues


def test_observed_link_can_discover_directory_from_homepage():
    homepage = HTML + '<a href="/faculty">Faculty</a><a href="/students">Students</a>'
    model = ScriptedModel(plan(), decision(accepted=False, follow_link_ids=[0]), decision())
    fetch = Fetch(homepage)
    result = run(model, Search(["https://college.example/"]), fetch)
    assert fetch.urls == ["https://college.example/", URL]
    assert [c["url"] for c in result.candidates] == [URL]


def test_ollama_adapter_schema_and_malformed_json():
    requests = []

    def handler(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": plan().model_dump_json()}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OllamaModel("test-local", client=client)
        assert model.ask(Plan, "Interpret", {"prompt": "anything"}).action == "search"
    assert requests[0]["format"]["additionalProperties"] is False
    assert requests[0]["options"]["temperature"] == 0
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"message": {"content": '{"action":"search","invented":1}'}}))) as client:
        with pytest.raises(ValidationError):
            OllamaModel("test-local", client=client).ask(Plan, "Interpret", {})


def test_searx_adapter_parameters_and_engine_outage():
    def handler(req):
        assert req.url.params["format"] == "json" and req.url.params["q"] == "faculty"
        return httpx.Response(200, json={"results": [{"url": URL}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert SearxSearch("http://127.0.0.1:8080", client).search("faculty") == [URL]
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"results": [], "unresponsive_engines": [["engine", "CAPTCHA"]]}))) as client:
        with pytest.raises(ValueError, match="unavailable"):
            SearxSearch("http://127.0.0.1:8080", client).search("faculty")


def test_discovery_to_real_crawler_and_excel_integration(site_url, tmp_path):
    # Only the semantic services are scripted. HTTP, robots, extraction, batch
    # orchestration, no-student rules and Excel export run for real on our fixture.
    cfg = ScrapeConfig(render="never", allow_private_hosts=True, delay_seconds=0,
                       retries=0, workers=1, max_pages=60)
    fetcher = Fetcher(cfg)
    try:
        page = fetcher.get(site_url)
        from campusscraper.prompt_discovery import text_and_links
        text, links = text_and_links(page, allow_local=True)
        institution = "Sunrise Institute of Technology"
        assert institution in text
        faculty_link = next(l for l in links if l["url"].endswith("/faculty"))
        staff_page = fetcher.get(faculty_link["url"])
        staff_text, _ = text_and_links(staff_page, allow_local=True)
        # Exact fixture evidence, not an assumption that the model found it.
        assert institution in staff_text
        quote = staff_text[:100]
        model = ScriptedModel(plan(), decision(accepted=False, follow_link_ids=[faculty_link["id"]]),
                              decision(institution=institution, official_site_quote=institution,
                                       staff_page_quote=quote))
        result = discover("Find Sunrise faculty", model, Search([site_url]), fetcher, Limits(rounds=1))
    finally:
        fetcher.close()
    assert result.status == "candidates_found", result.issues
    out = run_batch(result.sources(), tmp_path / "result.xlsx", cfg, 60, resume=False, parallel_sites=1)
    wb = load_workbook(out, read_only=True, data_only=True)
    try:
        rows = list(wb["Faculty"].values)
        all_data = json.dumps(rows)
        assert "anitha.rao@sunrise-it.ac.in" in all_data
        assert "Dr. Anitha Rao" in all_data
        assert "rahul.v21" not in all_data
        assert "Rahul Verma" not in all_data
    finally:
        wb.close()


def test_a_state_is_read_as_a_place_and_its_cities():
    from campusscraper.site_discovery import read_request

    r = read_request("top 10 engineering colleges in Tamil Nadu")
    assert r.state == "Tamil Nadu" and not r.unread_place   # was: "Tamil" not recognised
    assert {"Chennai", "Coimbatore", "Vellore"} <= set(r.places)
    assert read_request("colleges in tamilnadu").state == "Tamil Nadu"
    assert read_request("engineering colleges in UP").state == "Uttar Pradesh"
    assert read_request("private engineering colleges in Hyderabad").location == "Hyderabad"


def test_an_excluded_city_is_not_mistaken_for_the_place_asked_for():
    from campusscraper.site_discovery import read_request

    r = read_request("engineering colleges in Karnataka except Bengaluru")
    assert r.state == "Karnataka" and r.excluded_locations == ["Bengaluru"]
    assert "Bengaluru" not in r.places and "Mysuru" in r.places
    r = read_request("colleges in Maharashtra except Mumbai and Pune")
    assert r.excluded_locations == ["Mumbai", "Pune"]
    assert "Nagpur" in r.places and "Mumbai" not in r.places


def test_a_foreign_university_is_never_returned_for_an_indian_run():
    from campusscraper.site_discovery import Rejected, _asked_for, read_request
    from campusscraper.vocab import FOREIGN_HOST

    assert FOREIGN_HOST.search("www.imperial.ac.uk")      # the London result that appeared
    assert not FOREIGN_HOST.search("vit.ac.in")
    assert not FOREIGN_HOST.search("www.nitt.edu")
    # Named explicitly, a foreign site is allowed through.
    from campusscraper.site_discovery import Request

    assert _asked_for(Request(names=["Imperial College London"]), "www.imperial.ac.uk")
    assert not _asked_for(read_request("top 10 engineering colleges in Tamil Nadu"), "www.imperial.ac.uk")
    assert not _asked_for(Request(names=["VIT Vellore"]), "www.imperial.ac.uk")
    assert isinstance(Rejected("h", "r"), Rejected)
