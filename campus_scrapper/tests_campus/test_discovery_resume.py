"""Interrupted discovery resumes its queue/chunk, including previously failed pages."""
import json
from types import SimpleNamespace

import httpx
import pytest

from campusscraper.discovery_services import CachedSearch, DiscoveryDB, ServiceWait, Paused
from campusscraper.models import Page
from campusscraper.prompt_discovery import Plan, Decision, Refinement, Limits
from campusscraper.search_candidates import exclusion_reason, rank_urls, relaxed_queries
from campusscraper.verified_discovery import discover_verified
from test_prompt_discovery import Fetch, Search, ScriptedModel, plan, decision, HTML
from test_prompt_workflow import REVIEW


class Journal:
    def __init__(self, path):
        self.path = path

    def load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, value):
        self.path.write_text(json.dumps(value))


def run_saved(journal, model, search, fetch, events=None, **kwargs):
    return discover_verified("Find two engineering colleges", model, search, fetch,
        limits=kwargs.pop("limits", Limits(rounds=1)), load_state=journal.load, save_state=journal.save,
        progress=(events.append if events is not None else lambda e: None), **kwargs)


def test_resume_retains_counts_candidates_and_does_not_repeat_failed_pages(tmp_path):
    journal = Journal(tmp_path / "state.json")
    bad, first, second = [f"https://{h}.example/faculty" for h in ("failed", "first", "second")]
    class Pages(Fetch):
        def get(self, url):
            if url == bad:
                self.urls.append(url)
                return Page(url, url, 403, "", error="Access challenge")
            return super().get(url)
    fetch, search, events = Pages(), Search([bad, first, second]), []
    model = ScriptedModel(plan(requested_institutions=2), REVIEW, decision(), ServiceWait("quota"))
    with pytest.raises(ServiceWait):
        run_saved(journal, model, search, fetch, events)
    snapshot = journal.load()
    assert snapshot["report"]["pages"] == 3 and snapshot["report"]["searches"] == 1
    assert snapshot["report"]["status"] == "checking_pages"
    assert len(snapshot["report"]["candidates"]) == 1
    assert snapshot["work"]["pending"]["url"] == second

    class NoNetwork:
        cfg = SimpleNamespace(allow_private_hosts=False)
        def get(self, url): pytest.fail("Page was fetched again during resume")
        def search(self, query): pytest.fail("Search was repeated during resume")
    resumed_events = []
    remaining = ScriptedModel(decision())
    result = run_saved(Journal(journal.path), remaining, NoNetwork(), NoNetwork(), resumed_events)
    assert {c["url"] for c in result.candidates} == {first, second}
    assert result.pages == 3 and result.searches == 1
    assert [c[0] for c in remaining.calls] == [Decision]
    assert fetch.urls.count(bad) == 1
    assert all(e["report"]["pages"] >= 3 for e in resumed_events)
    assert all(e["phase"] != "planning" for e in resumed_events)


def test_resume_pending_compact_page_without_refetching(tmp_path):
    journal = Journal(tmp_path / "state.json")
    fetch = Fetch(HTML + "<p>" + "Long campus history. " * 700 + "</p>")
    model = ScriptedModel(plan(requested_institutions=1), REVIEW, ServiceWait("quota"))
    with pytest.raises(ServiceWait):
        run_saved(journal, model, Search(), fetch)
    snapshot = journal.load()
    assert snapshot["work"]["pending"]["chunk_index"] == 0
    assert len(snapshot["work"]["pending"]["chunks"]) == 1
    next_chunk = snapshot["work"]["pending"]["chunks"][0]
    remaining = ScriptedModel(decision())
    report = run_saved(journal, remaining, Search(), fetch)
    assert report.candidates and len(fetch.urls) == 1
    assert remaining.calls[0][2]["current_text"] == next_chunk


def test_search_wait_resumes_only_pending_query(tmp_path):
    journal = Journal(tmp_path / "state.json")
    class SearchWait(Search):
        def search(self, query):
            self.queries.append(query)
            if query == "second": raise ServiceWait("search down")
            return ["https://first.example/faculty"]
    search = SearchWait()
    with pytest.raises(ServiceWait):
        run_saved(journal, ScriptedModel(plan(queries=["first", "second"], requested_institutions=2), REVIEW), search, Fetch())
    assert journal.load()["report"]["searches"] == 1
    restored = Search(["https://second.example/faculty"])
    result = run_saved(journal, ScriptedModel(decision(), decision()), restored, Fetch())
    assert restored.queries == ["second"]
    assert result.searches == 2 and len(result.candidates) == 2


def test_completed_page_survives_pause_in_progress_callback(tmp_path):
    journal = Journal(tmp_path / "state.json")
    def pause_after_match(event):
        if event["report"]["candidates"]:
            raise Paused()
    model = ScriptedModel(plan(requested_institutions=1), REVIEW, decision())
    with pytest.raises(Paused):
        discover_verified("Find two engineering colleges", model, Search(), Fetch(), Limits(rounds=1),
                          progress=pause_after_match, load_state=journal.load, save_state=journal.save)
    # No model responses available: even the accepted chunk must not be replayed.
    report = run_saved(journal, ScriptedModel(), Search(), Fetch())
    assert len(report.candidates) == 1 and report.status == "candidates_found"


def test_refinement_wait_does_not_restart_page_assessment(tmp_path):
    journal = Journal(tmp_path / "state.json")
    model = ScriptedModel(plan(requested_institutions=1), REVIEW, decision(accepted=False), ServiceWait("quota"))
    limits = Limits(rounds=2)
    fetch = Fetch()
    with pytest.raises(ServiceWait):
        run_saved(journal, model, Search(), fetch, limits=limits)
    assert journal.load()["work"]["stage"] == "refine"
    remaining = ScriptedModel(Refinement(queries=[], unresolved=[]))
    report = run_saved(journal, remaining, Search(), fetch, limits=limits)
    assert not report.candidates
    assert [c[0] for c in remaining.calls] == [Refinement]
    assert report.pages == 1  # No new evidence: rejected page is not reassessed.


def test_incomplete_search_retries_without_replaying_other_queries(tmp_path):
    from campusscraper.discovery_services import IncompleteSearch
    journal = Journal(tmp_path / "state.json")
    class Partial(Search):
        def search(self, query):
            if query == "broken": raise IncompleteSearch("engines down")
            return []
    with pytest.raises(ServiceWait):
        run_saved(journal, ScriptedModel(plan(queries=["empty", "broken"], requested_institutions=1), REVIEW), Partial(), Fetch())
    restored = Search()
    report = run_saved(journal, ScriptedModel(decision()), restored, Fetch())
    assert report.candidates and restored.queries == ["broken"]
    assert report.searches == 2
    assert next(a for a in report.search_attempts if a["query"] == "broken")["attempts"] == 2


def test_real_pune_queries_skip_dictionary_and_keep_search_evidence(tmp_path):
    prompt = "Find faculty directory URLs for 2 engineering colleges in Pune, Maharashtra, India."
    queries = ['site:.ac.in "faculty directory" "engineering college" Pune Maharashtra',
               '"faculty directory" "engineering college" Pune India',
               'Pune engineering college faculty staff directory']
    p = plan(objective=prompt, requested_institutions=2, queries=queries, criteria=[
        dict(id="type", requirement="Engineering colleges", prompt_quote="engineering colleges", applies_to="institution"),
        dict(id="city", requirement="Located in Pune", prompt_quote="Pune", applies_to="institution")])
    urls = ["https://first.example/faculty", "https://second.example/faculty"]
    dictionary = "https://dictionary.cambridge.org/dictionary/english/faculty"
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"url": dictionary, "title": "Meaning of faculty", "content": "English dictionary"},
            *[{"url": u, "title": "Example Engineering College faculty", "content": "Pune, Maharashtra, India", "engines": ["test"]} for u in urls]],
            "unresponsive_engines": [["offline-engine", "CAPTCHA"]]})
    db = DiscoveryDB(tmp_path / "search.db")
    fetch = Fetch("<h1>Example Engineering College</h1><p>Official faculty directory in Pune, Maharashtra, India</p>")
    d = decision(staff_page_quote="Official faculty directory in Pune", evidence=[
        dict(criterion_id="type", quote="Engineering College"), dict(criterion_id="city", quote="Pune, Maharashtra, India")])
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            search = CachedSearch(db, "pune", "http://search.test", client=client)
            report = discover_verified(prompt, ScriptedModel(p, REVIEW, d, d), search, fetch, Limits(rounds=1))
            assert {c["url"] for c in report.candidates} == set(urls)
            assert dictionary not in fetch.urls
            assert report.search_attempts[0]["rejected_results"][0]["url"] == dictionary
            assert [a["query"] for a in report.search_attempts] == queries
            assert all(a["unavailable_engines"] == ["offline-engine"] for a in report.search_attempts)
            assert report.pages == 2 and report.model_calls == 4
    finally:
        db.db.close()


def test_ranking_is_not_a_domain_allowlist_and_rejects_malformed_urls():
    relevant, generic = "https://college.example.com/faculty", "https://unrelated.edu/faculty"
    details = {relevant: {"title": "Engineering college in Pune", "content": "Pune Maharashtra"}}
    ranked = rank_urls([generic, "http://[bad", "javascript:alert(1)", relevant], details, "Pune engineering colleges")
    assert ranked == [relevant, generic]
    assert not exclusion_reason("https://www.cam.ac.uk/people")
    assert not exclusion_reason("https://college.com/research/dictionary")
    assert not exclusion_reason("https://dictionary.cambridge.org.university.example/faculty")
    assert exclusion_reason("https://en.wiktionary.org/wiki/faculty")


def test_relaxation_preserves_negative_site_filters_and_does_not_edit_the_plan():
    queries = ['site:.ac.in "faculty directory" Pune -site:aggregator.example']
    assert relaxed_queries(queries, queries) == ["faculty directory Pune -site:aggregator.example"]
    assert queries[0].startswith("site:.ac.in")


def test_only_dictionary_results_trigger_broader_search_without_page_calls(tmp_path):
    journal = Journal(tmp_path / "state.json")
    q = 'site:.ac.in "faculty directory" Pune'
    class BroaderSearch(Search):
        def search(self, query):
            self.queries.append(query)
            return (["https://dictionary.cambridge.org/dictionary/english/faculty"] if query == q
                    else ["https://college.example/faculty"])
    search, fetch = BroaderSearch(), Fetch()
    report = run_saved(journal, ScriptedModel(plan(queries=[q], requested_institutions=1), REVIEW, decision()),
                       search, fetch, limits=Limits(rounds=2))
    assert search.queries == [q, "faculty directory Pune"]
    assert report.candidates and len(fetch.urls) == 1
