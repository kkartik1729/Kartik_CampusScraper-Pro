"""Transport and workflow regression tests; not real-model quality measurements."""
import json
import time
import httpx
import pytest
from campusscraper.discovery_services import DiscoveryDB, ModelRouter, settings, ConfigurationError, ModelResponseError
from campusscraper.verified_discovery import PlanReview, discover_verified
from campusscraper.prompt_discovery import Limits
from test_prompt_discovery import Fetch, Search, ScriptedModel, plan, decision
from test_prompt_workflow import REVIEW

OK = {"outcome": "ok", "questions": [], "reason": "Matches the prompt"}

@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PROVIDERS", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:0.6b")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("GROQ_API_KEY", "secret-must-not-be-sent")
    db = DiscoveryDB(tmp_path / 'test.db')
    yield db
    db.db.close()

def response(data=OK, **kw):
    return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": json.dumps(data)}, **kw})

def test_default_is_local_even_with_cloud_key(local, monkeypatch):
    monkeypatch.delenv('MODEL_PROVIDERS')
    config = settings()
    assert config['providers'] == ['ollama']
    assert config['ollama_configured'] and not config['groq_configured']

def test_local_ignores_cloud_budget_and_never_sends_secrets(local):
    local.cooldown('groq', time.time() + 86400)
    local.reserve('old', 'gemini', 200000, 100, 200000, 100, 200000)
    calls = []
    def handler(req):
        calls.append(req)
        assert str(req.url) == 'http://127.0.0.1:11434/api/chat'
        assert 'authorization' not in req.headers
        body = json.loads(req.content)
        assert body['model'] == 'qwen3:0.6b'
        assert body['think'] is False and body['stream'] is False
        assert body['format']['additionalProperties'] is False
        return response()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = ModelRouter(local, 'job', client=client)
        assert router.ask(PlanReview, 'Review', {}).outcome == 'ok'
        router.ask(PlanReview, 'Review', {})
    assert len(calls) == 1 and local.stats('job') == []

def test_changed_model_does_not_reuse_decisions(local, monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req)
        return response()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ModelRouter(local, 'job', client=client).ask(PlanReview,'Review',{})
        monkeypatch.setenv('OLLAMA_MODEL','qwen3:1.7b')
        ModelRouter(local, 'job', client=client).ask(PlanReview,'Review',{})
    assert len(calls)==2

@pytest.mark.parametrize('done_reason', ['length', 'load', None])
def test_incomplete_local_output_is_bounded_and_never_accepted(local, done_reason):
    calls=[]
    def handler(req):
        calls.append(req)
        return response(done_reason=done_reason)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router=ModelRouter(local,'job',client=client)
        for _ in range(2):
            with pytest.raises(ModelResponseError): router.ask(PlanReview,'Review',{})
    assert len(calls)==2

def test_missing_model_actionable_no_cloud_fallback(local):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(404))) as client:
        with pytest.raises(ConfigurationError, match='ollama pull qwen3:0.6b'):
            ModelRouter(local,'job',client=client).ask(PlanReview,'Review',{})

def test_cloud_fallback_requires_explicit_selection(local,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDERS','ollama,groq')
    calls=[]
    def handler(req):
        calls.append(req.url.host)
        if req.url.host=='127.0.0.1': return httpx.Response(404)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(OK)}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ModelRouter(local,'job',client=client).ask(PlanReview,'Review',{}).outcome=='ok'
    assert calls==['127.0.0.1','api.groq.com']

@pytest.mark.parametrize('value',['unknown','ollama,ollama',''])
def test_bad_provider_config_fails(local,monkeypatch,value):
    monkeypatch.setenv('MODEL_PROVIDERS',value)
    with pytest.raises(ConfigurationError): settings()

def test_navigation_survives_invalid_small_model_assessment():
    from campusscraper.models import Page
    class Pages(Fetch):
        def get(self,url):
            if url.endswith('/home'):
                self.urls.append(url)
                return Page(url,url,200,'<h1>Example Engineering College</h1><a href="/faculty">Faculty</a>')
            return super().get(url)
    fetch=Pages()
    model=ScriptedModel(plan(requested_institutions=1),REVIEW,
        ModelResponseError('bad response'),decision())
    report=discover_verified('Find one college',model,Search(['https://college.example/home']),fetch,limits=Limits(rounds=1))
    assert len(report.candidates)==1
    assert report.candidates[0]['url']=='https://college.example/faculty'
    assert any('unverified' in issue for issue in report.issues)


def test_validation_feedback_identifies_fields_and_repairs(local):
    calls=[]
    def handler(req):
        body=json.loads(req.content)
        calls.append(body)
        if len(calls)==1:
            return response({'outcome':'ok','questions':'wrong type','reason':'Check'})
        assert 'questions: list_type' in body['messages'][0]['content']
        assert 'JSON schema:' in body['messages'][0]['content']
        return response()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router=ModelRouter(local,'job',client=client)
        assert router.ask(PlanReview,'Review',{}).outcome=='ok'
        assert router.diagnostics[0]['schema']=='PlanReview'
        assert router.diagnostics[0]['detail']=='questions: list_type'
        assert router.diagnostics[1]['status']=='valid'


def test_invalid_output_diagnostics_never_persist_response_secrets(local):
    def handler(req):
        return response({'outcome':'secret-content', 'questions':[], 'reason':'ok', 'secret-extra-key':'secret-value'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router=ModelRouter(local,'job',client=client)
        with pytest.raises(ModelResponseError) as exc:
            router.ask(PlanReview,'Review',{})
        assert 'outcome: literal_error' in str(exc.value)
        assert 'secret-' not in str(exc.value)
        assert 'secret-' not in json.dumps(router.diagnostics)
    assert 'secret-content' not in local.path.read_bytes().decode(errors='ignore')


@pytest.mark.parametrize('body,detail', [
    ({'done':True,'done_reason':'length'},'generation incomplete'),
    ({'done':True,'done_reason':'stop','message':{'content':''}},'missing or empty'),
    ([], 'must be an object'),
    ({'done':True,'done_reason':'stop','message':{'content':'{broken'}},'json_invalid'),
])
def test_local_diagnostics_distinguish_failure_modes(local,body,detail):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body))) as client:
        router=ModelRouter(local,'job',client=client)
        with pytest.raises(ModelResponseError,match=detail): router.ask(PlanReview,'Review',{})
        assert len(router.diagnostics)==2
