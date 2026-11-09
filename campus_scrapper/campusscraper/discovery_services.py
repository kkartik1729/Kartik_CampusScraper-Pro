"""Small HTTP adapters and durable checkpoints for prompt discovery.

No provider SDK, model download, paid search tool, or key in browser storage.
Successful steps are replayable after restart without repeating network work.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from .models import Page
from .fetch import _ssl_verify
from .search_candidates import rank_urls, unrelated_result

REVISION = 3


class ServiceWait(Exception):
    def __init__(self, message, retry_at=None):
        super().__init__(message)
        self.retry_at = retry_at or time.time() + 60


class ConfigurationError(Exception):
    pass


class ModelResponseError(ConfigurationError):
    """A completed model response failed validation; retry once, then surface it."""


class SearchQualityError(ConfigurationError):
    """This query produced unusable results; other queries may still succeed."""


class IncompleteSearch(ServiceWait):
    """A search returned no URLs with some engines down; other queries may work."""


class Paused(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DiscoveryDB:
    """One bounded-work SQLite journal. Keys are never written to it."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS steps(job TEXT, key TEXT, data TEXT, PRIMARY KEY(job,key));
            CREATE TABLE IF NOT EXISTS cooldowns(provider TEXT PRIMARY KEY, until REAL);
            CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY, job TEXT, provider TEXT, at REAL, tokens INTEGER);
            CREATE INDEX IF NOT EXISTS usage_time ON usage(provider,at);
        """)
        self.db.execute("DELETE FROM usage WHERE at < ?", (time.time() - 2 * 86400,))
        self.db.commit()

    def save(self, data):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?)", (data["id"], json.dumps(data, ensure_ascii=False)))
            self.db.commit()

    def get(self, job):
        with self.lock:
            row = self.db.execute("SELECT data FROM jobs WHERE id=?", (job,)).fetchone()
        return json.loads(row[0]) if row else None

    def jobs(self):
        with self.lock:
            rows = self.db.execute("SELECT data FROM jobs ORDER BY rowid DESC LIMIT 200").fetchall()
        return [json.loads(r[0]) for r in rows]

    def cached(self, job, key):
        with self.lock:
            row = self.db.execute("SELECT data FROM steps WHERE job=? AND key=?", (job, key)).fetchone()
        return json.loads(row[0]) if row else None

    def cache(self, job, key, data):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO steps VALUES (?,?,?)", (job, key, json.dumps(data, ensure_ascii=False)))
            self.db.commit()

    def delete(self, job):
        with self.lock:
            self.db.execute("DELETE FROM steps WHERE job=?", (job,))
            # Deleting a run must not reset this installation's daily budget.
            self.db.execute("UPDATE usage SET job='' WHERE job=?", (job,))
            self.db.execute("DELETE FROM jobs WHERE id=?", (job,))
            self.db.commit()

    def cooldown(self, provider, until=None):
        with self.lock:
            if until is not None:
                self.db.execute("INSERT OR REPLACE INTO cooldowns VALUES (?,?)", (provider, until))
                self.db.commit()
            row = self.db.execute("SELECT until FROM cooldowns WHERE provider=?", (provider,)).fetchone()
        return row[0] if row else 0

    def reserve(self, job, provider, tokens, rpm, tpm, rpd, tpd):
        """Conservative rolling budgets shared by runs in this installation.

        The provider remains authoritative for other users/apps in the account.
        Failed requests retain their reservation; retries cannot spin for free.
        """
        now = time.time()
        with self.lock:
            if min(rpm, tpm, rpd, tpd) < 1:
                raise ConfigurationError(f"{provider}: scheduling budgets must be positive integers")
            if tokens > tpm:
                raise ConfigurationError(f"{provider}: this request exceeds the configured per-minute token budget")
            if tokens > tpd:
                raise ConfigurationError(f"{provider}: this request exceeds the configured daily token budget")
            until = self.cooldown(provider)
            if until > now:
                raise ServiceWait(f"{provider}: waiting for API capacity", until)
            rows = self.db.execute("SELECT at,tokens FROM usage WHERE provider=? AND at>? ORDER BY at",
                                   (provider, now - 86400)).fetchall()
            recent = [r for r in rows if r[0] > now - 60]
            if len(rows) >= rpd or sum(r[1] for r in rows) + tokens > tpd:
                raise ServiceWait(f"{provider}: local daily budget reached", (rows[0][0] + 86401) if rows else now + 86400)
            if len(recent) >= rpm or sum(r[1] for r in recent) + tokens > tpm:
                raise ServiceWait(f"{provider}: pacing requests within the token limit", recent[0][0] + 61)
            cursor = self.db.execute("INSERT INTO usage(job,provider,at,tokens) VALUES (?,?,?,?)", (job, provider, now, tokens))
            self.db.commit()
            return cursor.lastrowid

    def actual_usage(self, reservation, tokens):
        if isinstance(tokens, int) and tokens >= 0:
            with self.lock:
                self.db.execute("UPDATE usage SET tokens=? WHERE id=?", (tokens, reservation))
                self.db.commit()

    def stats(self, job):
        with self.lock:
            rows = self.db.execute("SELECT provider,COUNT(*),SUM(tokens) FROM usage WHERE job=? GROUP BY provider", (job,)).fetchall()
        return [{"provider": r[0], "requests": r[1], "tokens": r[2]} for r in rows]


def json_schema(schema):
    """Provider subset; stricter length/value constraints are also checked locally."""
    def clean(node):
        if isinstance(node, list):
            return [clean(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {k: clean(v) for k, v in node.items() if k not in
               {"title", "default", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}}
        if out.get("type") == "object":
            out["required"] = list(out.get("properties", {}))
            out["additionalProperties"] = False
        return out
    return clean(schema.model_json_schema())


def retry_time(response):
    """Both HTTP dates and provider RetryInfo durations, without storing bodies."""
    raw = response.headers.get("retry-after", "")
    seconds = 60.0
    try:
        seconds = float(raw)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(raw).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            try:
                for item in response.json().get("error", {}).get("details", []):
                    if "retryDelay" in item:
                        seconds = float(item["retryDelay"].rstrip("s"))
            except (ValueError, TypeError, AttributeError):
                pass
    return time.time() + max(5, min(seconds if math.isfinite(seconds) else 60, 86400))


def settings():
    # Server environment only. Never serialize API keys in API responses or jobs.
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env", override=False)
    search_url = os.getenv("SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/")
    from urllib.parse import urlsplit
    parsed = urlsplit(search_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("SEARXNG_URL must be an HTTP(S) service URL without credentials or query parameters")
    providers = [p.strip().lower() for p in os.getenv("MODEL_PROVIDERS", "ollama").split(",") if p.strip()]
    if not providers or len(set(providers)) != len(providers) or any(p not in {"ollama", "groq", "gemini"} for p in providers):
        raise ConfigurationError("MODEL_PROVIDERS must be an ordered list of ollama, groq, gemini without duplicates")
    ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    local = urlsplit(ollama_url)
    if local.scheme not in {"http", "https"} or not local.hostname or local.username or local.password or local.query or local.fragment:
        raise ConfigurationError("OLLAMA_URL must be an HTTP(S) service URL without credentials or query parameters")
    return {
        "providers": providers,
        "ollama_url": ollama_url,
        "ollama_model": os.getenv("OLLAMA_MODEL", "qwen3:0.6b"),
        "ollama_configured": "ollama" in providers,
        "search_url": search_url,
        "groq_model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "groq_configured": "groq" in providers and bool(os.getenv("GROQ_API_KEY")),
        "gemini_configured": "gemini" in providers and bool(os.getenv("GEMINI_API_KEY")),
    }


class ModelRouter:
    def __init__(self, db, job, control=lambda: None, client=None):
        self.db, self.job, self.control = db, job, control
        self.client = client or httpx.Client(timeout=90, follow_redirects=False, trust_env=False, verify=_ssl_verify())
        self.owns_client = client is None
        self.config = settings()
        self.diagnostics = []

    def close(self):
        if self.owns_client:
            self.client.close()

    def ask(self, schema, instruction, payload):
        self.control()
        key = "model:" + digest([REVISION, self.config["providers"], self.config["ollama_url"], self.config["ollama_model"], self.config["groq_model"], self.config["gemini_model"],
                                  schema.model_json_schema(), instruction, payload])
        cached = self.db.cached(self.job, key)
        if cached is not None:
            return schema.model_validate_json(json.dumps(cached))
        providers = [p for p in self.config["providers"] if self.config[p + "_configured"]]
        if not providers:
            raise ConfigurationError("Configure MODEL_PROVIDERS and the selected provider in .env, then restart the portal")
        waits, errors = [], []
        for provider in providers:
            marker = key + ":invalid:" + provider
            invalid = self.db.cached(self.job, marker) or {"attempts": 0}
            while invalid["attempts"] < 2:
                self.control()
                try:
                    request_instruction = instruction
                    if invalid["attempts"]:
                        request_instruction += ("\nPrevious response failed validation. Return concise complete JSON only. "
                                                "Obey all field types, lengths and list limits. Do not guess missing evidence.")
                        if provider == "ollama":
                            request_instruction += "\nValidation feedback: " + invalid["error"]
                    result = self._ask(provider, schema, request_instruction, payload)
                    self.db.cache(self.job, key, result.model_dump())
                    return result
                except ModelResponseError as exc:
                    invalid = {"attempts": invalid["attempts"] + 1, "error": str(exc)}
                    self.db.cache(self.job, marker, invalid)
                    if invalid["attempts"] == 2:
                        errors.append(str(exc))
                except ServiceWait as exc:
                    waits.append(exc)
                    break
                except ConfigurationError as exc:
                    errors.append(str(exc))
                    break
            else:
                if invalid["error"] not in errors:
                    errors.append(invalid["error"])
        if waits:
            raise ServiceWait("; ".join([str(w) for w in waits] + errors), min(w.retry_at for w in waits))
        if errors and all("incomplete or invalid structured response" in e for e in errors):
            raise ModelResponseError("; ".join(errors))
        raise ConfigurationError("; ".join(errors))

    def _ask(self, provider, schema, instruction, payload):
        model = self.config[provider + "_model"]
        if not re.fullmatch(r"[A-Za-z0-9._/:-]+", model) or ".." in model:
            raise ConfigurationError(f"Invalid {provider} model name")
        if provider == "ollama":
            return self._ask_ollama(model, schema, instruction, payload)
        shape = json_schema(schema)
        # Providers use a schema subset; state omitted local constraints explicitly.
        constraints = []
        def visit(node, path="root"):
            if isinstance(node, dict):
                rules = {k: v for k, v in node.items() if k in
                         {"minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}}
                if rules:
                    constraints.append({"field": path, **rules})
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        visit(v, path + "." + k)
            elif isinstance(node, list):
                for v in node:
                    visit(v, path)
        visit(schema.model_json_schema())
        instruction += "\nLocal validation limits: " + json.dumps(constraints, separators=(",", ":"))
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        # A conservative scheduling estimate, not a tokenizer or a billing claim.
        output_limit = 1200 if schema.__name__ == "Decision" and "Previous response failed" not in instruction else 2000
        estimate = math.ceil(len((instruction + content + json.dumps(shape)).encode()) / 3) + output_limit
        limits = (30, 8000, 1000, 200000) if provider == "groq" else (5, 20000, 100, 200000)
        limits = tuple(int(os.getenv(f"{provider.upper()}_{name}", default)) for name, default in zip(("RPM", "TPM", "RPD", "TPD"), limits))
        reservation = self.db.reserve(self.job, provider, estimate, *limits)
        if provider == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}
            body = {"model": model, "temperature": 0, "max_completion_tokens": output_limit,
                    "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": content}],
                    "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": shape}}}
            if model.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "low"
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}
            body = {"systemInstruction": {"parts": [{"text": instruction}]},
                    "contents": [{"role": "user", "parts": [{"text": content}]}],
                    "generationConfig": {"temperature": 0, "maxOutputTokens": output_limit,
                                         "responseMimeType": "application/json", "responseJsonSchema": shape}}
            if model.startswith("gemini-2.5-flash"):
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        try:
            response = self.client.post(url, headers=headers, json=body)
        except httpx.HTTPError:
            self.db.cooldown(provider, time.time() + 60)
            raise ServiceWait(f"{provider}: connection unavailable; progress is saved") from None
        if response.status_code == 429 or response.status_code >= 500:
            until = retry_time(response)
            self.db.cooldown(provider, until)
            raise ServiceWait(f"{provider}: quota or service temporarily unavailable (HTTP {response.status_code})", until)
        if response.status_code != 200:
            # Provider bodies/exceptions can echo credentials or request content.
            raise ConfigurationError(f"{provider}: HTTP {response.status_code}; check API key, model access and free-tier settings")
        try:
            data = response.json()
            if provider == "groq":
                self.db.actual_usage(reservation, data.get("usage", {}).get("total_tokens"))
                choice = data["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("incomplete response")
                text = choice["message"]["content"]
            else:
                self.db.actual_usage(reservation, data.get("usageMetadata", {}).get("totalTokenCount"))
                choice = data["candidates"][0]
                if choice.get("finishReason") != "STOP":
                    raise ValueError("incomplete response")
                text = "".join(p.get("text", "") for p in choice["content"]["parts"] if not p.get("thought"))
            return schema.model_validate_json(text)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelResponseError(f"{provider}: incomplete or invalid structured response; no URLs or contacts were guessed") from None


    def _ask_ollama(self, model, schema, instruction, payload):
        # Local inference has no cloud token quota. Never send API credentials.
        # Schema validation and evidence verification remain mandatory.
        body = {"model": model, "stream": False, "think": False,
                "format": schema.model_json_schema(), "keep_alive": "5m",
                "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 4096},
                "messages": [{"role": "system", "content": instruction + "\nReturn only JSON matching the schema. Never invent evidence. "
                    "Use null for unknown optional counts, never 0 or an empty string. "
                    "All requested counts must be positive integers when supplied. "
                    "Copy prompt quotes exactly. Keep lists within the schema limits.\nJSON schema: "
                    + json.dumps(schema.model_json_schema(), separators=(",", ":"))},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        try:
            response = self.client.post(self.config["ollama_url"] + "/api/chat", json=body, timeout=180)
        except httpx.HTTPError:
            raise ConfigurationError("ollama: cannot reach the local model. Start Ollama, pull the configured model, and retry; check OLLAMA_URL") from None
        if response.status_code == 404:
            raise ConfigurationError(f"ollama: model unavailable; run ollama pull {model}")
        if response.status_code != 200:
            raise ConfigurationError(f"ollama: HTTP {response.status_code}; check the local Ollama server and available memory")
        diagnostic = {"schema": schema.__name__, "model": model}
        def fail(detail):
            diagnostic.update(status="invalid", detail=detail)
            self.diagnostics.append(diagnostic)
            raise ModelResponseError(
                f"ollama: incomplete or invalid structured response [{schema.__name__}: {detail}]; "
                "no URLs or contacts were guessed") from None
        try:
            data = response.json()
        except ValueError:
            fail("server response is not JSON")
        if not isinstance(data, dict):
            fail("server response must be an object")
        reason = data.get("done_reason")
        diagnostic["done_reason"] = reason if reason in ("stop", "length", "load", None) else "other"
        if data.get("done") is not True or reason != "stop":
            fail("generation incomplete; done_reason=" + str(diagnostic["done_reason"]))
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            fail("message.content is missing or empty")
        diagnostic["output_characters"] = len(content)
        try:
            result = schema.model_validate_json(content)
        except ValidationError as exc:
            # Never include response values, input, context, or arbitrary extra
            # field names in persistent errors (they can contain secrets).
            known = set()
            def fields(node):
                if isinstance(node, dict):
                    known.update(node.get("properties", {}))
                    for value in node.values(): fields(value)
                elif isinstance(node, list):
                    for value in node: fields(value)
            fields(schema.model_json_schema())
            problems = []
            for error in exc.errors(include_input=False, include_context=False, include_url=False)[:8]:
                path = ".".join(str(part) if isinstance(part, int) or part in known else "<field>"
                                for part in error["loc"]) or "root"
                problems.append(path + ": " + error["type"])
            fail("; ".join(problems))
        diagnostic["status"] = "valid"
        self.diagnostics.append(diagnostic)
        return result



class CachedSearch:
    def __init__(self, db, job, base_url, control=lambda: None, client=None):
        self.db, self.job, self.base_url, self.control = db, job, base_url.rstrip("/"), control
        self.client = client or httpx.Client(timeout=35, follow_redirects=False, trust_env=False, verify=_ssl_verify())
        self.owns_client = client is None
        self.warnings = []
        self.search_details = {}

    def close(self):
        if self.owns_client:
            self.client.close()

    def search(self, query, _fallback=False):
        self.control()
        key = "search:" + digest(["quality-v3", self.base_url, query, _fallback])
        cached = self.db.cached(self.job, key)
        if cached is not None:
            self.warnings.extend(cached["warnings"])
            self.search_details[query] = cached.get("details", {"results": [{"url": u} for u in cached["urls"]]})
            return cached["urls"]
        try:
            response = self.client.get(self.base_url + "/search", params={"q": query, "format": "json", "safesearch": 2,
                **({"engines": "google,duckduckgo,brave,startpage,yahoo,qwant"} if _fallback else {"categories": "general"})})
            if response.status_code in (401, 403):
                raise ConfigurationError("SearXNG refused JSON search. Enable search.formats: [html, json] in its settings.yml")
            if response.status_code == 429 or response.status_code >= 500:
                raise ServiceWait("SearXNG temporarily unavailable; progress is saved", retry_time(response))
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("results"), list):
                raise ValueError("missing results")
        except httpx.HTTPError:
            raise ServiceWait("Cannot reach SearXNG. Start the search service; progress is saved") from None
        except (ValueError, AttributeError):
            raise ConfigurationError("SearXNG did not return JSON search results. Check its URL and JSON format setting") from None
        results = []
        for r in data["results"][:60]:
            if not isinstance(r, dict) or not isinstance(r.get("url"), str):
                continue
            if len(r["url"]) > 3000:
                continue
            results.append({"url": r["url"], "title": str(r.get("title") or "")[:250],
                            "content": str(r.get("content") or "")[:800],
                            "engines": [str(e)[:60] for e in (r.get("engines") or [])[:8]]})
        # SearXNG categories can re-add engines; reject unexpected fallback
        # provenance as well as isolating the request. Never call Bing a recovery.
        unexpected = [r for r in results if _fallback and "bing" in r["engines"]
                      and not set(r["engines"]) & {"google", "duckduckgo", "brave", "startpage", "yahoo", "qwant"}]
        rejected = [r for r in results if r in unexpected or unrelated_result(query, r)]
        by_url = {r["url"]: r for r in results if r not in rejected}
        urls = rank_urls(list(by_url), by_url, query)[:30]
        unavailable = [str(e[0])[:60] for e in data.get("unresponsive_engines", [])
                       if isinstance(e, (list, tuple)) and e]
        details = {"results": [by_url[u] for u in urls], "unavailable_engines": unavailable,
                   "rejected_results": rejected, "fallback_used": _fallback,
                   "unexpected_engine_results": unexpected,
                   "engine_errors": [[str(x)[:200] for x in e[:2]] for e in data.get("unresponsive_engines", [])
                                     if isinstance(e, (list, tuple))]}
        self.search_details[query] = details
        warnings = []
        if rejected and not urls and not _fallback:
            # One bounded recovery request, never send off-topic pages to the LLM.
            try:
                recovered = self.search(query, _fallback=True)
                if not recovered:
                    raise IncompleteSearch("Alternative engines returned no results")
                fallback_details = self.search_details[query]
                fallback_details["initial_rejected_results"] = rejected
                self.db.cache(self.job, key, {"urls": recovered, "warnings": self.warnings,
                                             "details": fallback_details})
                return recovered
            except (ServiceWait, ConfigurationError) as exc:
                details["fallback_error"] = str(exc)
                details["fallback_used"] = True
                details["fallback_response"] = dict(self.search_details.get(query, {}))
            self.search_details[query] = details
            raise SearchQualityError("Search returned unrelated results and alternative engines could not recover. "
                                     "Check SearXNG engine availability; no unrelated pages were sent to Groq.")
        if rejected and not urls:
            raise IncompleteSearch("Alternative search engines also returned unrelated results")
        if data.get("unresponsive_engines"):
            if not urls:
                raise IncompleteSearch("Search engines returned no usable results while some were unavailable; retry later")
            warnings = ["Some search engines were unavailable; usable results from other engines were retained"]
            self.warnings.extend(warnings)
        self.db.cache(self.job, key, {"urls": urls, "warnings": warnings, "details": details})
        return urls


class CachedFetcher:
    def __init__(self, fetcher, db, job, control=lambda: None):
        self.inner, self.cfg, self.db, self.job, self.control = fetcher, fetcher.cfg, db, job, control

    def get(self, url):
        self.control()
        key = "page:" + digest(url)
        value = self.db.cached(self.job, key)
        if value is not None:
            if "page" not in value:  # Successful checkpoints from older versions.
                return Page(**value)
            if value.get("retry_at") is None or value["retry_at"] > time.time():
                return Page(**value["page"])
        page = self.inner.get(url)
        if not page.error and page.status == 200:
            self.db.cache(self.job, key, asdict(page))
        elif page.error and "stopped" not in page.error.lower():
            transient = page.status in (0, 408, 429) or page.status >= 500
            self.db.cache(self.job, key, {"page": asdict(page),
                                         "retry_at": time.time() + 300 if transient else None})
        self.control()
        return page

    def close(self):
        self.inner.close()
