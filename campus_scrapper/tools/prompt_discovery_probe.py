"""Opt-in local discovery experiment. Does not alter or start portal jobs."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from campusscraper.config import ScrapeConfig
from campusscraper.fetch import Fetcher
from campusscraper.prompt_discovery import Limits, OllamaModel, SearxSearch, discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("--model", required=True, help="An installed local Ollama model; no automatic download")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--search-url", required=True, help="Your SearXNG instance with JSON enabled")
    parser.add_argument("--out", default="discovery-report.json")
    parser.add_argument("--pages", type=int, default=16)
    parser.add_argument("--searches", type=int, default=8)
    parser.add_argument("--institution-cap", type=int, default=10)
    args = parser.parse_args()
    limits = Limits(pages=args.pages, searches=args.searches, institutions=args.institution_cap)
    model = OllamaModel(args.model, args.ollama_url)
    search = SearxSearch(args.search_url)
    fetcher = Fetcher(ScrapeConfig(render="never", workers=1, retries=1))
    try:
        report = discover(args.prompt, model, search, fetcher, limits,
                          progress=lambda event: print(json.dumps(event), flush=True))
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{report.status}: {len(report.candidates)} candidate pages; report: {path}")
        print("Experimental: model-assessed candidates are not a validated faculty coverage result.")
        return 0 if report.status == "candidates_found" else 2
    finally:
        fetcher.close()
        search.close()
        model.close()


if __name__ == "__main__":
    raise SystemExit(main())
