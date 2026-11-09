"""The page a model accepts must list the population the user asked for.

A literal quote only proves some words exist on the page. These cases cover the
observed 0.6B failure (a student directory accepted as faculty) and the wider
class around it, using the same extractor that builds the final directory.
"""
import pytest

from campusscraper.models import Page
from campusscraper.prompt_discovery import Limits
from campusscraper.verified_discovery import PlanReview, discover_verified, staff_page_signal
from test_prompt_discovery import Fetch, Search, ScriptedModel, decision, plan

PROMPT = "Find faculty page URLs for engineering colleges in Mumbai."
REVIEW = PlanReview(outcome="ok", questions=[], reason="Request preserved")
URL = "https://example-college.edu/faculty"

HEAD = "<h1>Example Engineering College</h1><p>Mumbai</p>"

# What the 0.6B model accepted: literal text, real institution, right city, wrong people.
STUDENT_DIRECTORY = HEAD + """
<h2>Student Directory</h2>
<table><tr><th>Name</th><th>Roll No</th><th>Branch</th><th>Year</th></tr>
<tr><td>Anitha Rao</td><td>CS21B041</td><td>CSE</td><td>II Year</td></tr>
<tr><td>Rahul Verma</td><td>CS21B042</td><td>CSE</td><td>III Year</td></tr>
<tr><td>Sneha Kulkarni</td><td>ME21B018</td><td>Mechanical</td><td>II Year</td></tr>
</table>"""

ALUMNI_PAGE = HEAD + """
<h2>Notable Alumni</h2><div class="alumni-list">
<div class="c"><h4>Dr. Vivek Kulkarni</h4><p>Professor, University of Michigan</p></div>
<div class="c"><h4>Dr. Asha Bhide</h4><p>Associate Professor, NIT Nagpur</p></div></div>"""

FACULTY_PAGE = HEAD + """
<h2>Faculty</h2><table>
<tr><th>Name of the Faculty</th><th>Designation</th><th>Qualification</th><th>E-mail</th></tr>
<tr><td>Dr. M. Suresh</td><td>Professor &amp; Head</td><td>Ph.D</td><td>suresh.m@example-college.edu</td></tr>
<tr><td>Mr. P. Kiran</td><td>Assistant Professor</td><td>M.Tech</td><td>kiran.p@example-college.edu</td></tr>
</table>"""


def faculty_plan():
    return plan(objective=PROMPT, requested_institutions=1, target="faculty",
                criteria=[dict(id="location", requirement="Located in Mumbai",
                               prompt_quote="Mumbai", applies_to="institution")],
                queries=["engineering colleges Mumbai official faculty"])


def run(html, staff_quote):
    """One accepted model decision against one fetched page."""
    model = ScriptedModel(faculty_plan(), REVIEW,
                          decision(staff_page_quote=staff_quote,
                                   evidence=[dict(criterion_id="location", quote="Mumbai")]))
    return discover_verified(PROMPT, model, Search([URL]), Fetch(html), Limits(rounds=1))


def test_signal_reads_the_population_of_each_page():
    def signal(html):
        return staff_page_signal(Page(URL, URL, 200, html))
    students = signal(STUDENT_DIRECTORY)
    assert students["available"] and students["people"] == 0 and students["students"] >= 3
    assert signal(ALUMNI_PAGE)["people"] == 0 and signal(ALUMNI_PAGE)["alumni"] >= 2
    assert signal(FACULTY_PAGE)["people"] >= 2


def test_student_directory_is_rejected_even_when_the_model_accepts_it():
    """The exact live failure: quote is literal, institution and city match."""
    report = run(STUDENT_DIRECTORY, "Name Roll No Branch Year")
    assert report.candidates == []
    assert any("students" in a["reason"] for a in report.attempts if not a["accepted"])


def test_alumni_page_is_rejected_even_when_the_quote_names_a_professor():
    """The quote carries a staff word, so only the population check can catch this."""
    report = run(ALUMNI_PAGE, "Dr. Vivek Kulkarni")
    assert report.candidates == []
    assert any("alumni" in a["reason"] for a in report.attempts if not a["accepted"])


def test_student_directory_quote_without_the_word_student_is_still_rejected():
    """'Name Roll No Branch Year' contains no population word at all."""
    report = run(STUDENT_DIRECTORY, "Anitha Rao CS21B041 CSE II Year")
    assert report.candidates == []


def test_real_faculty_page_is_accepted_and_reports_the_people_found():
    report = run(FACULTY_PAGE, "Name of the Faculty Designation")
    assert [c["url"] for c in report.candidates] == [URL]
    assert report.attempts[-1]["extracted_staff"] >= 2


def test_thin_staff_page_stays_inconclusive_rather_than_rejected():
    """Hard-to-parse pages are judged on evidence; they are not punished."""
    thin = HEAD + "<p>Official faculty directory in Mumbai. Contact the department office.</p>"
    assert staff_page_signal(Page(URL, URL, 200, thin))["people"] == 0
    assert [c["url"] for c in run(thin, "Official faculty directory in Mumbai").candidates] == [URL]


@pytest.mark.parametrize("target", ["offices", "both"])
def test_office_targets_are_not_judged_by_the_faculty_population_rule(target):
    office = HEAD + """<h2>Training &amp; Placement Cell</h2>
    <p>Dr. Mohan Krishna, Training and Placement Officer</p><p>Email: tpo@example-college.edu</p>
    <h3>Student Placement Coordinators</h3>
    <table><tr><th>Name</th><th>Branch</th><th>Year</th></tr>
    <tr><td>Rahul Verma</td><td>CSE</td><td>IV Year</td></tr>
    <tr><td>Sravani Reddy</td><td>ECE</td><td>IV Year</td></tr>
    <tr><td>Kiran Teja</td><td>CSE</td><td>III Year</td></tr></table>"""
    model = ScriptedModel(faculty_plan().model_copy(update={"target": target}), REVIEW,
                          decision(staff_page_quote="Training and Placement Officer",
                                   evidence=[dict(criterion_id="location", quote="Mumbai")]))
    report = discover_verified(PROMPT, model, Search([URL]), Fetch(office), Limits(rounds=1))
    assert [c["url"] for c in report.candidates] == [URL]


def test_requested_count_above_the_run_limit_is_stated_before_results():
    model = ScriptedModel(faculty_plan().model_copy(update={"requested_institutions": 20}), REVIEW,
                          decision(staff_page_quote="Name of the Faculty Designation",
                                   evidence=[dict(criterion_id="location", quote="Mumbai")]))
    report = discover_verified(PROMPT, model, Search([URL]), Fetch(FACULTY_PAGE),
                               Limits(searches=2, pages=4, institutions=10, rounds=1))
    assert any("limited to 10" in issue and "20" in issue for issue in report.issues)
