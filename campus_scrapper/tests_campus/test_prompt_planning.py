"""Regressions for real prompt failures, with controlled model responses."""
import pytest

from campusscraper.discovery_services import ConfigurationError, ServiceWait
from campusscraper.prompt_discovery import Plan, Limits
from campusscraper.verified_discovery import PlanReview, discover_verified, ground_plan_quotes
from test_prompt_discovery import Fetch, Search, ScriptedModel, plan, decision


PROMPT = "Find faculty directory URLs for 2 engineering colleges in Pune, Maharashtra, India."
REVIEW = PlanReview(outcome="ok", questions=[], reason="Request preserved")


def pune_plan(quote="Pune, Maharashtra, India", **kwargs):
    return plan(objective=PROMPT, requested_institutions=2,
                queries=["engineering colleges Pune official faculty directory"],
                criteria=[dict(id="location", requirement="Located in Pune, Maharashtra, India",
                               prompt_quote=quote, applies_to="institution")], **kwargs)


@pytest.mark.parametrize("quote", ["Pune, Maharashtra, India", "pune, maharashtra, india",
                                   " Pune,   Maharashtra,\nIndia "])
def test_pune_prompt_returns_two_observed_sources_after_format_normalization(quote):
    html = "<h1>Example Engineering College</h1><p>Official faculty directory in Pune, Maharashtra, India</p>"
    d = decision(staff_page_quote="Official faculty directory in Pune",
                 evidence=[dict(criterion_id="location", quote="Pune, Maharashtra, India")])
    model = ScriptedModel(pune_plan(quote), REVIEW, d, d)
    urls = ["https://first.example/faculty", "https://second.example/faculty"]
    report = discover_verified(PROMPT, model, Search(urls), Fetch(html), Limits(rounds=1))
    assert {c["url"] for c in report.candidates} == set(urls)
    assert report.plan["requested_institutions"] == 2
    assert report.plan["criteria"][0]["prompt_quote"] == "Pune, Maharashtra, India"
    assert [call[0] for call in model.calls].count(Plan) == 1


def test_original_ten_college_prompt_does_not_require_a_city():
    prompt = "I want to find the 10 engineering colleges faculty page urls ."
    p = plan(requested_institutions=10, criteria=[dict(id="type", requirement="Engineering colleges",
             prompt_quote="Engineering colleges", applies_to="institution")])
    d = decision(evidence=[dict(criterion_id="type", quote="Engineering College")])
    report = discover_verified(prompt, ScriptedModel(p, REVIEW, d), Search(), Fetch(), Limits(rounds=1))
    assert report.candidates and report.plan["requested_institutions"] == 10
    assert report.plan["criteria"][0]["prompt_quote"] == "engineering colleges"
    assert any("found 1" in issue for issue in report.issues)  # No false completeness.


def test_hinglish_exclusion_survives_newline_and_case_normalization():
    prompt = "Pune mein engineering faculty chahiye, PRIVATE\ncolleges nahi"
    p = plan(criteria=[dict(id="exclude", requirement="Exclude private colleges",
                           prompt_quote="private colleges nahi", applies_to="institution")])
    grounded, errors = ground_plan_quotes(p, prompt)
    assert not errors
    assert grounded.criteria[0].prompt_quote == "PRIVATE\ncolleges nahi"
    assert grounded.criteria[0].requirement == p.criteria[0].requirement
    assert p.criteria[0].prompt_quote == "private colleges nahi"  # Original proposal preserved.


@pytest.mark.parametrize("quote", ["Pune in Maharashtra", "Mumbai", "Pune … India", " "])
def test_changed_words_locations_and_combined_fragments_are_not_normalized(quote):
    _, errors = ground_plan_quotes(pune_plan(quote), PROMPT)
    assert errors


def test_paraphrased_quote_is_repaired_before_any_search_and_still_audited():
    rejected, corrected = pune_plan("colleges located in Pune"), pune_plan()
    search = Search([])
    class Model(ScriptedModel):
        def ask(self, *args):
            assert not search.queries  # Planner, repair and review all precede search.
            return super().ask(*args)
    model = Model(rejected, corrected, REVIEW)
    report = discover_verified(PROMPT, model, search, Fetch(), Limits(rounds=1))
    assert report.status == "no_candidates" and search.queries
    assert [call[0] for call in model.calls] == [Plan, Plan, PlanReview]
    assert model.calls[1][2]["original_prompt"] == PROMPT
    assert model.calls[1][2]["grounding_errors"][0]["criterion_id"] == "location"
    assert report.planning_attempts[0]["proposed_plan"] == rejected.model_dump()
    assert report.planning_attempts[1]["grounding_errors"] == []


def test_unrepairable_plan_stops_without_network_search_and_preserves_diagnostics():
    model = ScriptedModel(pune_plan("Mumbai"), pune_plan("Mumbai"))
    search, fetch, events = Search(), Fetch(), []
    with pytest.raises(ConfigurationError, match="after one automatic repair"):
        discover_verified(PROMPT, model, search, fetch, progress=events.append)
    report = events[-1]["report"]
    assert not search.queries and not fetch.urls
    assert report["status"] == "planning_failed" and report["issues"]
    assert report["model_calls"] == 2 and len(report["planning_attempts"]) == 2
    assert not report["plan"]  # Rejected proposal must not appear as validated.


def test_literal_quote_does_not_bypass_semantic_audit():
    # A copied location paired with a false requirement is still rejected.
    p = pune_plan()
    p.criteria[0].requirement = "Located in Mumbai"
    review = PlanReview(outcome="clarify", questions=["Please confirm the requested location."],
                        reason="Requirement contradicts Pune in the prompt")
    search = Search()
    report = discover_verified(PROMPT, ScriptedModel(p, review), search, Fetch())
    assert report.status == "clarify" and not search.queries


def test_real_missing_context_still_returns_clarification_without_search():
    p = plan(action="clarify", queries=[], questions=["Which city should I search?"])
    search = Search()
    report = discover_verified("Find colleges near me", ScriptedModel(p), search, Fetch())
    assert report.status == "clarify" and not search.queries


def test_quota_during_repair_preserves_the_rejected_attempt():
    events, search = [], Search()
    with pytest.raises(ServiceWait):
        discover_verified(PROMPT, ScriptedModel(pune_plan("Mumbai"), ServiceWait("Groq quota")),
                          search, Fetch(), progress=events.append)
    assert events[-1]["report"]["planning_attempts"][0]["grounding_errors"]
    assert not search.queries
