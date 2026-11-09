"""Connection checks without exposing keys. --live-model tests structured output."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from pydantic import BaseModel, ConfigDict
from campusscraper.discovery_services import CachedSearch, DiscoveryDB, ModelRouter, settings
from campusscraper.fetch import _ssl_verify


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: bool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-model", action="store_true", help="also make one small structured-output request to each configured provider")
    parser.add_argument("--query", default="Mumbai engineering college faculty directory", help="Search relevance probe")
    args = parser.parse_args()
    config = settings()
    failures = 0
    with tempfile.TemporaryDirectory() as temp, httpx.Client(timeout=30, trust_env=False, verify=_ssl_verify()) as client:
        db = DiscoveryDB(Path(temp) / "check.db")
        search = CachedSearch(db, "check", config["search_url"], client=client)
        try:
            urls = search.search(args.query)
            print(f"SearXNG: {len(urls)} candidate URLs (not yet page-verified)")
            for url in urls[:5]:
                print("  " + url)
            for warning in search.warnings:
                print("  " + warning)
        except Exception as exc:
            print(f"SearXNG: {type(exc).__name__}: {exc}")
            failures += 1
        configured = [p for p in config["providers"] if config[p + "_configured"]]
        if not configured:
            print("Models: no selected providers configured. Edit .env, then run this check again.")
            failures += 1
        router = ModelRouter(db, "check", client=client)
        for provider in configured:
            try:
                if args.live_model:
                    result = router._ask(provider, Check, "Return JSON with ok set to true.", {"task": "connection check"})
                    if not result.ok:
                        raise ValueError("invalid check response")
                else:
                    if provider == "ollama":
                        response = client.get(config["ollama_url"] + "/api/tags")
                    elif provider == "groq":
                        response = client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]})
                    else:
                        response = client.get("https://generativelanguage.googleapis.com/v1beta/models/" + config["gemini_model"], headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
                    response.raise_for_status()
                    if provider == "ollama" and config["ollama_model"] not in {m.get("name") for m in response.json().get("models", [])}:
                        raise ValueError("Configured Ollama model is not installed")
                    if provider == "groq" and config["groq_model"] not in {m.get("id") for m in response.json().get("data", [])}:
                        raise ValueError("Configured Groq model is unavailable")
                print(f"{provider}: OK ({config[provider + '_model']})")
            except Exception as exc:
                print(f"{provider}: {type(exc).__name__}. Check server availability, installed model, or cloud key/quota.")
                failures += 1
        db.db.close()
    print("These checks confirm connectivity, not faculty coverage or semantic accuracy.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
