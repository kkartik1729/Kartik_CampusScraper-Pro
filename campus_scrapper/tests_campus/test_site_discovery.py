"""Prompt criteria to verified college names and faculty URLs, with no model.

Four small college sites are served locally. Only the site's own pages decide
whether it qualifies, so the wrong city, the wrong kind of college and a site
with no faculty page are all rejected for stated reasons.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from campusscraper.config import ScrapeConfig
from campusscraper.crawl import Crawler
from campusscraper.site_discovery import (
    candidate_hosts, discover_colleges, names_from_results, site_queries, sources_from_colleges,
    survey_site,
)


def page(site, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta property='og:site_name' content='{site}'><title>{site}</title></head>"
            f"<body><nav><a href='/'>Home</a> <a href='/about'>About</a> "
            f"<a href='/faculty'>Faculty</a></nav><main>{body}</main></body></html>")


def faculty_table(rows):
    cells = "".join(f"<tr><td>{i}</td><td>{n}</td><td>{d}</td><td>Ph.D</td>"
                    f"<td>{e}</td></tr>" for i, (n, d, e) in enumerate(rows, 1))
    return ("<h1>Faculty</h1><table><tr><th>S.No</th><th>Name of the Faculty</th>"
            "<th>Designation</th><th>Qualification</th><th>E-mail</th></tr>" + cells + "</table>")


SITES = {
    # host key -> (site name, city line, faculty rows or None)
    "sunrise": ("Sunrise Institute of Technology", "Gachibowli, Hyderabad, Telangana",
                [("Dr. M. Suresh", "Professor &amp; Head", "suresh@sunrise.ac.in"),
                 ("Mr. P. Kiran", "Assistant Professor", "kiran@sunrise.ac.in"),
                 ("Mrs. S. Rekha", "Assistant Professor", "rekha@sunrise.ac.in")]),
    "coastal": ("Coastal College of Engineering", "Shivaji Nagar, Pune, Maharashtra",
                [("Dr. A. Joshi", "Professor", "joshi@coastal.ac.in"),
                 ("Mr. V. Patil", "Assistant Professor", "patil@coastal.ac.in")]),
    "medico": ("Medico Medical College", "Banjara Hills, Hyderabad, Telangana",
               [("Dr. R. Iyer", "Professor of Medicine", "iyer@medico.ac.in"),
                ("Dr. S. Nair", "Associate Professor", "nair@medico.ac.in")]),
    "cityarts": ("City Arts College", "Ameerpet, Hyderabad, Telangana", None),
}
ORDER = ["sunrise", "coastal", "medico", "cityarts"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        key = self.server.site_key
        name, address, rows = SITES[key]
        kind = "Engineering" if key in ("sunrise", "coastal") else (
            "Medical" if key == "medico" else "Arts")
        if self.path.startswith("/faculty") and rows:
            body = faculty_table(rows) + f"<footer>{name}, {address}</footer>"
        elif self.path.startswith("/about"):
            body = f"<h1>About {name}</h1><p>{name} is located at {address}.</p>"
        elif self.path in ("/", "/index.html"):
            body = (f"<h1>{name}</h1><p>{kind} education at {address}.</p>"
                    f"<p><a href='/faculty'>Our Faculty</a> <a href='/about'>About us</a></p>")
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = page(name, body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def campus_web():
    """Each college on its own host, as on the real web."""
    urls = {}
    for index, key in enumerate(ORDER):
        server = ThreadingHTTPServer((f"127.0.0.{index + 1}", 0), Handler)
        server.site_key = key
        threading.Thread(target=server.serve_forever, daemon=True).start()
        urls[key] = f"http://127.0.0.{index + 1}:{server.server_address[1]}/"
    yield urls


class Search:
    """A search engine that returns every site, as a real one would."""

    def __init__(self, urls, extra=()):
        self.urls, self.extra, self.queries = urls, list(extra), []

    def search(self, query):
        self.queries.append(query)
        return [self.urls[k] for k in ORDER] + self.extra


def crawler_factory(pages=12):
    def make():
        return Crawler(ScrapeConfig(max_pages=pages, delay_seconds=0, allow_private_hosts=True,
                                    render="never", retries=0, use_sitemaps=False, patience=0))
    return make


def test_queries_read_like_a_person_typed_them():
    queries = site_queries("engineering", "Mumbai")
    assert "engineering colleges in Mumbai" in queries
    assert any("site:ac.in" in q for q in queries)
    assert all(len(q) < 200 for q in queries)


def test_college_names_are_lifted_from_titles_as_leads_only():
    results = [{"title": "Sardar Patel Institute of Technology, Andheri - Mumbai"},
               {"title": "Top 20 Engineering Colleges in Mumbai 2026 | Collegedunia"},
               {"title": "K. J. Somaiya College of Engineering - Admissions"}]
    names = names_from_results(results)
    assert "Sardar Patel Institute of Technology" in names
    assert "K. J. Somaiya College of Engineering" in names


def test_aggregators_are_never_candidate_sites():
    results = [{"url": "https://collegedunia.com/engineering-colleges-mumbai", "title": "Top colleges"},
               {"url": "https://www.shiksha.com/list", "title": "Colleges"},
               {"url": "https://www.vjti.ac.in/", "title": "VJTI College of Engineering Mumbai"}]
    hosts = [host for host, _ in candidate_hosts(results)]
    assert hosts == ["vjti.ac.in"]


def test_official_academic_sites_outrank_the_rest():
    results = [{"url": "https://randomblog.com/colleges", "title": "colleges"},
               {"url": "https://spit.ac.in/", "title": "Sardar Patel Institute of Technology"}]
    assert [h for h, _ in candidate_hosts(results)][0] == "spit.ac.in"


def test_one_site_is_proved_by_its_own_faculty_page(campus_web):
    college, rejected = survey_site(campus_web["sunrise"], crawler_factory(),
                                    kind="engineering", location="Hyderabad")
    assert rejected is None
    assert college.name == "Sunrise Institute of Technology"
    assert college.city == "Hyderabad"
    assert college.staff_found == 3
    assert college.faculty_urls and college.faculty_urls[0].endswith("/faculty")
    assert "3 staff read from" in college.evidence["staff"]


@pytest.mark.parametrize("key,kind,location,reason", [
    ("coastal", "engineering", "Hyderabad", "Pune"),          # right kind, wrong city
    ("medico", "engineering", "Hyderabad", "engineering"),    # right city, wrong kind
    ("cityarts", "", "Hyderabad", "No page on this site listed faculty"),
])
def test_sites_are_rejected_for_a_stated_reason(campus_web, key, kind, location, reason):
    college, rejected = survey_site(campus_web[key], crawler_factory(), kind=kind, location=location)
    assert college is None
    assert reason in rejected.reason


def test_prompt_criteria_end_to_end_return_names_and_faculty_urls(campus_web):
    """Engineering colleges in Hyderabad: one of the four sites qualifies."""
    colleges, rejected, notes = discover_colleges(
        Search(campus_web), crawler_factory(), want=5, kind="engineering", location="Hyderabad",
        max_searches=2, workers=2)
    assert [c.name for c in colleges] == ["Sunrise Institute of Technology"]
    assert colleges[0].faculty_urls[0].endswith("/faculty")
    assert {r.host.split(":")[0] for r in rejected} >= {"127.0.0.2", "127.0.0.3", "127.0.0.4"}
    assert any("Requested 5 colleges; 1 had faculty pages" in n for n in notes)


def test_a_wider_request_returns_every_qualifying_college(campus_web):
    colleges, _, _ = discover_colleges(Search(campus_web), crawler_factory(), want=5,
                                       kind="engineering", location="", max_searches=2, workers=2)
    assert {c.name for c in colleges} == {"Sunrise Institute of Technology",
                                          "Coastal College of Engineering"}
    assert all(c.staff_found >= 2 for c in colleges)


def test_verified_colleges_become_crawler_source_rows(campus_web):
    colleges, _, _ = discover_colleges(Search(campus_web), crawler_factory(), want=5,
                                       kind="engineering", location="Hyderabad",
                                       max_searches=2, workers=2)
    rows = sources_from_colleges(colleges)
    assert [r.institution for r in rows] == ["Sunrise Institute of Technology"]
    assert rows[0].urls == colleges[0].faculty_urls
    assert rows[0].city == "Hyderabad"


class SlowHandler(Handler):
    """A site that answers, but far too slowly to be worth waiting for."""

    def do_GET(self):  # noqa: N802
        import time as _t
        _t.sleep(3)
        super().do_GET()


@pytest.fixture(scope="module")
def slow_site():
    server = ThreadingHTTPServer(("127.0.0.5", 0), SlowHandler)
    server.site_key = "sunrise"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.5:{server.server_address[1]}/"


def test_a_slow_site_is_abandoned_within_its_budget(slow_site):
    import time as _t
    started = _t.monotonic()
    college, rejected = survey_site(slow_site, crawler_factory(), kind="engineering",
                                    location="Hyderabad", budget=4)
    spent = _t.monotonic() - started
    assert spent < 20, f"a slow site held the run for {spent:.0f}s"
    assert college is None and "too slowly" in rejected.reason


def test_one_slow_site_does_not_stop_the_others(campus_web, slow_site):
    search = Search(campus_web, extra=[slow_site])
    colleges, rejected, _ = discover_colleges(search, crawler_factory(), want=5,
                                              kind="engineering", location="Hyderabad",
                                              max_searches=2, workers=4, site_seconds=4)
    assert [c.name for c in colleges] == ["Sunrise Institute of Technology"]
    assert any("too slowly" in r.reason for r in rejected)


# ---- a search headline is not a college name -------------------------------------
MARKETING = ("Best Engineering College in Mumbai", "Top 10 Engineering College in Mumbai",
             "Best University in Mumbai | Admission 2026", "No.1 College in Pune",
             "List of Engineering Colleges in Mumbai")
REAL = ("Veermata Jijabai Technological Institute", "Sardar Patel Institute of Technology",
        "Fr. Conceicao Rodrigues College of Engineering", "St. Francis Institute of Technology")


@pytest.mark.parametrize("name", MARKETING)
def test_advertising_headlines_are_not_treated_as_names(name):
    from campusscraper.site_discovery import is_marketing
    assert is_marketing(name)


@pytest.mark.parametrize("name", REAL)
def test_real_college_names_are_kept(name):
    from campusscraper.site_discovery import is_marketing
    assert not is_marketing(name)


def test_headline_titles_are_never_lifted_as_college_names():
    results = [{"title": "Best Engineering College in Mumbai - Admissions Open"},
               {"title": "Top 10 Engineering Colleges in Mumbai 2026 | Collegedunia"},
               {"title": "Sardar Patel Institute of Technology, Andheri - Mumbai"}]
    assert names_from_results(results) == ["Sardar Patel Institute of Technology"]


def test_a_site_whose_title_is_an_advertisement_is_named_by_its_address():
    """A real college whose home page is titled for search engines, not for people."""
    from campusscraper.models import Page
    from campusscraper.site_discovery import read_identity
    html = ("<html><head><meta property='og:site_name' content='Best Engineering College in Mumbai'>"
            "<title>Top 10 Engineering College in Mumbai | Admission 2026</title></head>"
            "<body><h1>Best Engineering College in Mumbai</h1><p>Andheri, Mumbai</p></body></html>")
    name, city, _ = read_identity(Page("https://x.ac.in/", "https://x.ac.in/", 200, html))
    assert name == ""          # nothing on the page is usable as a name
    assert city == "Mumbai"


# ---- a place typed in lower case is still a place ---------------------------------
@pytest.mark.parametrize("prompt,city", [
    ("we want the college website urls of faculty pages from pune", "Pune"),
    ("engineering colleges in bengaluru with faculty emails", "Bengaluru"),
    ("faculty directory urls for colleges in NEW DELHI", "New Delhi"),
    ("Find 20 engineering colleges in Mumbai", "Mumbai"),
])
def test_the_city_is_read_whatever_the_capitalisation(prompt, city):
    from campusscraper.site_discovery import read_request
    assert read_request(prompt).location == city


def test_a_place_this_reader_cannot_match_is_reported_rather_than_ignored():
    """Silently dropping the place returned colleges in Texas for a Pune request."""
    from campusscraper.site_discovery import read_request
    request = read_request("find faculty page urls for colleges in the Konkan region")
    assert request.location == ""
    assert request.unread_place == "Konkan"


def test_naming_colleges_is_not_mistaken_for_naming_a_place():
    from campusscraper.site_discovery import read_request
    request = read_request("I want the faculty URLs from Veermata Jijabai Technological Institute")
    assert request.unread_place == ""
