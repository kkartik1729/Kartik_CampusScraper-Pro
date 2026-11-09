"""Registry-first seeding for the campus batch path.

Search responses are stubbed, so these check portal behaviour rather than live
search quality. The cases that matter are the refusals: a college-listing
aggregator must never become a source, a prompt that resolves nothing must fail
rather than start an empty batch, and two seeds on one host must not both be
crawled.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("seedsources", reason="shared seedsources package not installed")

from campusscraper.seed_discovery import seeds_to_csv  # noqa: E402
from campusscraper.web import app  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    import resolve_websites
    import seedsources.adapters as adapters

    original = adapters.SearchSource.__init__
    monkeypatch.setattr(
        adapters.SearchSource,
        "__init__",
        lambda self, searcher=None, scorer=None, **kw: original(
            self, searcher=resolve_websites.ddgs_search, scorer=resolve_websites.pick, **kw
        ),
    )

    http = TestClient(app)

    def stub(urls):
        monkeypatch.setattr(
            resolve_websites, "ddgs_search", lambda q, max_results=8, attempts=3: list(urls)
        )

    http.stub = stub
    return http


def test_endpoints_registered(client):
    assert getattr(app.state, "seed_error", None) is None


def test_prompt_is_read_without_a_model(client):
    client.stub(["https://www.coeptech.ac.in/"])
    body = client.post(
        "/api/seeds/preview",
        json={"prompt": "Find 20 engineering colleges in Pune with faculty URLs", "delay": 0},
    ).json()
    assert body["understood"]["vertical"] == "colleges"
    assert body["understood"]["location"] == "Pune"
    assert body["understood"]["discipline"] == "engineering"
    assert body["understood"]["target"] == "people"
    assert body["understood"]["count"] == 20


def test_aggregators_are_never_sources(client):
    # Colleges are covered by the registry, so the search adapter only resolves
    # blanks; with no registry configured the run must still refuse directories
    # rather than return them as sources.
    client.stub(
        [
            "https://collegedunia.com/pune",
            "https://www.shiksha.com/pune",
            "https://www.getmyuni.com/pune",
        ]
    )
    response = client.post(
        "/api/seeds/run",
        json={"prompt": "engineering colleges in Pune", "delay": 0},
    )
    assert response.status_code == 422
    assert "No seed produced a URL" in response.json()["detail"]


def test_csv_matches_the_upload_template():
    csv_text = seeds_to_csv(
        [
            {
                "name": "COEP Technological University",
                "url": "https://www.coeptech.ac.in/",
                "location": "Pune",
            },
            {"name": "No Website College", "url": None, "location": "Pune"},
        ]
    )
    lines = csv_text.strip().splitlines()
    assert lines[0] == "College Name,Official Website,City,Enabled"
    assert lines[1].startswith("COEP Technological University,https://www.coeptech.ac.in/,Pune")
    assert "No Website College" not in csv_text


def test_one_row_per_host():
    csv_text = seeds_to_csv(
        [
            {"name": "Bharati Vidyapeeth A", "url": "https://www.bharatividyapeeth.edu/", "location": "Pune"},
            {"name": "Bharati Vidyapeeth B", "url": "https://bharatividyapeeth.edu/colleges", "location": "Pune"},
            {"name": "COEP", "url": "https://www.coeptech.ac.in/", "location": "Pune"},
        ]
    )
    assert len(csv_text.strip().splitlines()) == 3  # header plus two sites
    assert "Bharati Vidyapeeth B" not in csv_text


def test_generated_sheet_is_readable_by_the_uploader(tmp_path):
    # The point of writing the portal's own template is that the portal's own
    # parser accepts it; assert that against the real parser, not a fixture.
    from campusscraper.sources import read_sources

    csv_text = seeds_to_csv(
        [{"name": "COEP Technological University",
          "url": "https://www.coeptech.ac.in/", "location": "Pune"}]
    )
    path = tmp_path / "seeds.csv"
    path.write_text(csv_text, encoding="utf-8")

    sources, problems = read_sources(path)
    assert len(sources) == 1
    assert not [p for p in problems if getattr(p, "problem", "")]
    assert sources[0].as_dict()["urls"] == ["https://www.coeptech.ac.in/"]
