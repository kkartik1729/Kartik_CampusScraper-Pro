"""Provider contracts, persistent quota handling and safe network boundaries."""
import json
import socket
import time
from dataclasses import asdict

import httpx
import pytest

from campusscraper.discovery_services import (
    CachedFetcher, CachedSearch, ConfigurationError, DiscoveryDB, ModelRouter,
    ServiceWait, json_schema, retry_time,
)
@pytest.fixture(autouse=True)
def cloud_provider_selection(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDERS", "groq,gemini")


from campusscraper.models import Page
from campusscraper.network_guard import public_destination
from campusscraper.prompt_discovery import Plan
from campusscraper.verified_discovery import PlanReview


@pytest.fixture
def db(tmp_path):
    db = DiscoveryDB(tmp_path / "discovery.db")
    yield db
    db.db.close()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-test")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-test")


def groq_ok(data):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}], "usage": {"total_tokens": 100}})


def gemini_ok(data):
    return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(data)}]}}], "usageMetadata": {"totalTokenCount": 100}})


OK = {"outcome": "ok", "questions": [], "reason": "All requirements preserved"}


@pytest.mark.parametrize("status", [429, 500, 503, 401, 403, 404])
def test_provider_failure_falls_back_without_exposing_keys(db, keys, status):
    calls = []
    def handler(req):
        calls.append(req)
        body = json.loads(req.content)
        assert "secret-" not in str(req.url) and "secret-" not in json.dumps(body)
        if "groq.com" in req.url.host:
            assert req.headers["authorization"] == "Bearer secret-groq-test"
            assert body["response_format"]["json_schema"]["strict"] is True
            return httpx.Response(status, headers={"retry-after": "65"}, json={"error": "secret-groq-test"})
        assert req.headers["x-goog-api-key"] == "secret-gemini-test"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert "responseJsonSchema" in body["generationConfig"]
        assert "tools" not in body  # no paid search tool is enabled
        return gemini_ok(OK)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = ModelRouter(db, "one", client=client)
        assert router.ask(PlanReview, "Review", {}).outcome == "ok"
        # Repeat and process restart both reuse the completed step.
        router.ask(PlanReview, "Review", {})
        other = DiscoveryDB(db.path)
        ModelRouter(other, "one", client=client).ask(PlanReview, "Review", {})
        other.db.close()
    assert len(calls) == 2
    assert sum(x["requests"] for x in db.stats("one")) == 2
    assert "secret-groq-test" not in db.path.read_bytes().decode(errors="ignore")


def test_all_quotas_wait_and_cooldown_survives_restart(db, keys):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "120"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ServiceWait) as err:
            ModelRouter(db, "one", client=client).ask(PlanReview, "Review", {})
        assert err.value.retry_at > time.time() + 110
        other = DiscoveryDB(db.path)
        with pytest.raises(ServiceWait):
            ModelRouter(other, "two", client=client).ask(PlanReview, "Review", {})
        other.db.close()
    assert len(calls) == 2  # no retry storm or key rotation


@pytest.mark.parametrize("body", [{"choices": []}, {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(OK)}}]},
                                  {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]}])
def test_invalid_response_uses_fallback(db, keys, body):
    def handler(req):
        return httpx.Response(200, json=body) if req.url.host == "api.groq.com" else gemini_ok(OK)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ModelRouter(db, "one", client=client).ask(PlanReview, "Review", {}).outcome == "ok"


def test_no_keys_fails_before_network(db, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network used"))) as client:
        with pytest.raises(ConfigurationError, match="Configure MODEL_PROVIDERS"):
            ModelRouter(db, "one", client=client).ask(PlanReview, "Review", {})


def test_schema_nullable_required_and_extra_fields():
    schema = json_schema(Plan)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert {"type": "null"} in schema["properties"]["requested_institutions"]["anyOf"]
    assert "default" not in json.dumps(schema)


def test_local_budget_is_shared_across_jobs(db):
    db.reserve("one", "groq", 5000, 30, 8000, 1000, 200000)
    with pytest.raises(ServiceWait, match="pacing"):
        db.reserve("two", "groq", 5000, 30, 8000, 1000, 200000)
    with pytest.raises(ConfigurationError, match="per-minute"):
        db.reserve("two", "gemini", 20001, 5, 20000, 100, 200000)


def test_daily_budget_waits_instead_of_charging_or_looping(db):
    db.reserve("one", "groq", 200, 30, 8000, 1, 10000)
    with pytest.raises(ServiceWait) as err:
        db.reserve("two", "groq", 200, 30, 8000, 1, 10000)
    assert err.value.retry_at > time.time() + 86000


def test_deleting_a_run_does_not_reset_provider_budget(db):
    db.reserve("one", "groq", 200, 30, 8000, 1, 10000)
    db.delete("one")
    with pytest.raises(ServiceWait, match="daily"):
        db.reserve("two", "groq", 200, 30, 8000, 1, 10000)


def test_retry_info_and_date_header():
    response = httpx.Response(429, json={"error": {"details": [{"retryDelay": "121s"}]}})
    assert 120 <= retry_time(response) - time.time() <= 122
    response = httpx.Response(429, headers={"retry-after": "NaN"})
    assert 59 <= retry_time(response) - time.time() <= 61


def test_search_keeps_usable_results_if_one_engine_fails(db):
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [{"url": "https://college.example/faculty"}], "unresponsive_engines": [["one", "CAPTCHA"]]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        search = CachedSearch(db, "one", "http://localhost:8080", client=client)
        assert len(search.search("faculty")) == 1
        assert search.warnings
        assert len(search.search("faculty")) == 1
    assert len(calls) == 1


@pytest.mark.parametrize("status,data,exc", [(403, {}, ConfigurationError), (429, {}, ServiceWait),
    (200, {"results": [], "unresponsive_engines": [["all", "blocked"]]}, ServiceWait), (200, {"wrong": []}, ConfigurationError)])
def test_search_outage_is_not_an_empty_success(db, status, data, exc):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=data))) as client:
        with pytest.raises(exc):
            CachedSearch(db, "one", "http://localhost:8080", client=client).search("faculty")


def test_page_checkpoint_replays_without_refetch(db):
    class Fetch:
        cfg = object()
        calls = 0
        def get(self, url):
            self.calls += 1
            return Page(url, url, 200, "<h1>Faculty</h1>")
    inner = Fetch()
    fetch = CachedFetcher(inner, db, "one")
    first = fetch.get("https://college.example/faculty")
    assert asdict(fetch.get(first.url)) == asdict(first)
    assert inner.calls == 1


@pytest.mark.parametrize("status,error", [(403, "Access challenge"), (404, "HTTP 404"),
                                         (200, "Site blocks automated access")])
def test_terminal_page_failures_are_cached_across_fetcher_restart(db, status, error):
    class Fetch:
        cfg = object()
        calls = 0
        def get(self, url):
            self.calls += 1
            return Page(url, url, status, "", error=error)
    inner = Fetch()
    url = "https://college.example/faculty"
    assert CachedFetcher(inner, db, "one").get(url).error == error
    assert CachedFetcher(inner, db, "one").get(url).error == error
    assert inner.calls == 1


def test_transient_page_failures_can_retry_after_backoff(db, monkeypatch):
    import campusscraper.discovery_services as services
    clock = [1000.0]
    monkeypatch.setattr(services.time, "time", lambda: clock[0])
    class Fetch:
        cfg = object()
        calls = 0
        def get(self, url):
            self.calls += 1
            return Page(url, url, 503, "", error="HTTP 503") if self.calls == 1 else Page(url, url, 200, "Faculty")
    inner = Fetch()
    fetch = CachedFetcher(inner, db, "one")
    url = "https://college.example/faculty"
    assert fetch.get(url).error
    clock[0] += 60
    assert fetch.get(url).error and inner.calls == 1
    clock[0] += 300
    assert not fetch.get(url).error and inner.calls == 2


def test_cancelled_fetch_is_not_cached(db):
    class Fetch:
        cfg = object()
        calls = 0
        def get(self, url):
            self.calls += 1
            return Page(url, url, 0, "", error="stopped") if self.calls == 1 else Page(url, url, 200, "Faculty")
    inner = Fetch()
    fetch = CachedFetcher(inner, db, "one")
    assert fetch.get("https://college.example/faculty").error == "stopped"
    assert fetch.get("https://college.example/faculty").status == 200
    assert inner.calls == 2


def test_completed_fetch_is_saved_when_pause_arrives_during_request(db):
    from campusscraper.discovery_services import Paused
    controls = []
    def control():
        controls.append(True)
        if len(controls) == 2:
            raise Paused()
    class Fetch:
        cfg = object()
        calls = 0
        def get(self, url):
            self.calls += 1
            return Page(url, url, 200, "Faculty")
    inner = Fetch()
    with pytest.raises(Paused):
        CachedFetcher(inner, db, "one", control).get("https://college.example/faculty")
    assert CachedFetcher(inner, db, "one").get("https://college.example/faculty").status == 200
    assert inner.calls == 1


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_dns_private_answers_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))])
    with pytest.raises(httpx.RequestError, match="non-public"):
        public_destination("https://malicious.example/faculty")


def test_redirect_checked_before_target_request(monkeypatch):
    from campusscraper.fetch import Fetcher
    from campusscraper.config import ScrapeConfig
    def dns(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8" if host == "public.example" else "127.0.0.1", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    fetcher = Fetcher(ScrapeConfig(strict_public_network=True, allow_private_hosts=False, render="never", delay_seconds=0))
    fetcher.client.close()
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://private.example/secret"})
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, event_hooks={"request": [fetcher._guard_request]})
    try:
        with pytest.raises(httpx.RequestError, match="non-public"):
            fetcher._raw_get("https://public.example/")
    finally:
        fetcher.close()
    assert seen == ["https://public.example/"]


def test_invalid_model_response_repairs_once_and_caches(db, keys, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    calls = []
    def handler(req):
        calls.append(json.loads(req.content))
        return groq_ok({'unexpected': 'value'}) if len(calls) == 1 else groq_ok(OK)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = ModelRouter(db, 'repair', client=client)
        assert router.ask(PlanReview, 'Review', {}).outcome == 'ok'
        assert router.ask(PlanReview, 'Review', {}).outcome == 'ok'
    assert len(calls) == 2
    assert 'Previous response failed validation' in calls[1]['messages'][0]['content']
    assert 'maxItems' in calls[0]['messages'][0]['content']


def test_invalid_model_response_retry_budget_survives_restart(db, keys, monkeypatch):
    from campusscraper.discovery_services import ModelResponseError
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    calls = []
    def handler(req):
        calls.append(req)
        return groq_ok({'unexpected': 'secret-body-that-must-not-be-saved'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            with pytest.raises(ModelResponseError):
                ModelRouter(db, 'repair', client=client).ask(PlanReview, 'Review', {})
    assert len(calls) == 2
    rows = db.db.execute('SELECT data FROM steps').fetchall()
    assert 'secret-body' not in str(rows)


def test_invalid_model_wait_resumes_repair_not_original(db, keys, monkeypatch):
    from campusscraper.discovery_services import ModelResponseError
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    class Router(ModelRouter):
        instructions = []
        def _ask(self, provider, schema, instruction, payload):
            self.instructions.append(instruction)
            if len(self.instructions) == 1:
                raise ModelResponseError('groq: incomplete or invalid structured response')
            if len(self.instructions) == 2:
                raise ServiceWait('quota')
            return PlanReview(**OK)
    with pytest.raises(ServiceWait):
        Router(db, 'repair').ask(PlanReview, 'Review', {})
    assert Router(db, 'repair').ask(PlanReview, 'Review', {}).outcome == 'ok'
    assert all('Previous response failed' in s for s in Router.instructions[1:])
