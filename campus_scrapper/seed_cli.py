"""Ask any of the three scrapers' question from one command line.

    python seed_cli.py "Find 20 engineering colleges in Pune with faculty URLs" \
        --registry results/registry.db --no-resolve

    python seed_cli.py "healthcare law firms in Ohio" --no-searxng
    python seed_cli.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seedsources import parse, default_chain, Seed, SourceResult  # noqa: E402
from seedsources.base import Intent, SeedChain, SeedSource  # noqa: E402


def self_test() -> int:
    """Chain behaviour with stub sources. No network, no database."""

    class Stub(SeedSource):
        def __init__(self, name, seeds=None, ok=True, error="", resolves=None):
            self.name, self._seeds, self._ok, self._error = name, seeds or [], ok, error
            self._resolves = resolves or {}

        def supports(self, intent):
            return True

        def find(self, intent, limit=None):
            return SourceResult(self.name, seeds=list(self._seeds), ok=self._ok, error=self._error)

        def resolve(self, blanks):
            for seed in blanks:
                if seed.name in self._resolves:
                    seed.url = self._resolves[seed.name]
                    seed.note = "resolved by stub"

    checks, failures = [], 0

    # A dead first source must not stop the chain.
    chain = SeedChain([
        Stub("dead", ok=False, error="down"),
        Stub("alive", [Seed("A", "https://a.test/", "alive")]),
    ])
    seeds, trail = chain.find(Intent("colleges"))
    checks.append(("dead source skipped", len(seeds) == 1 and len(trail) == 2))

    # The first source with results wins; later ones do not append.
    chain = SeedChain([
        Stub("first", [Seed("A", "https://a.test/", "first")]),
        Stub("second", [Seed("B", "https://b.test/", "second")]),
    ])
    seeds, _ = chain.find(Intent("colleges"))
    checks.append(("first answer wins", [s.name for s in seeds] == ["A"]))

    # A later source fills a blank URL from an earlier one.
    chain = SeedChain([
        Stub("registry", [Seed("Alpha College", None, "registry"),
                          Seed("Beta College", "https://beta.test/", "registry")]),
        Stub("search", resolves={"Alpha College": "https://alpha.test/"}),
    ])
    seeds, _ = chain.find(Intent("colleges"))
    checks.append(("blank url resolved", all(s.crawlable for s in seeds)))

    # An unresolvable seed survives as a named row, never as a guessed URL.
    chain = SeedChain([
        Stub("registry", [Seed("Ghost College", None, "registry")]),
        Stub("search", resolves={}),
    ])
    seeds, _ = chain.find(Intent("colleges"))
    checks.append(("unresolved kept, not invented", len(seeds) == 1 and seeds[0].url is None))

    # A throwing resolver is recorded and does not sink the run.
    class Thrower(Stub):
        def resolve(self, blanks):
            raise RuntimeError("boom")

    chain = SeedChain([
        Stub("registry", [Seed("X", None, "registry")]),
        Thrower("broken"),
    ])
    seeds, trail = chain.find(Intent("colleges"))
    checks.append(("resolver failure contained", len(seeds) == 1 and any(not t.ok for t in trail)))

    # Prompts across all four verticals.
    prompts = {
        "Find 20 engineering colleges in Pune with faculty URLs": ("colleges", "Pune", 20),
        "oncologists from hospitals in Mumbai": ("hospitals", "Mumbai", None),
        "healthcare law firms in Ohio": ("lawyers", "Ohio", None),
        "companies actively hiring in Bengaluru": ("companies", "Bengaluru", None),
    }
    for prompt, (vertical, place, count) in prompts.items():
        intent, _ = parse(prompt)
        ok = intent.vertical == vertical and intent.location == place and intent.count == count
        checks.append((f"parse: {prompt[:34]}", ok))

    for label, passed in checks:
        if not passed:
            failures += 1
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
    print(f"\n{len(checks) - failures}/{len(checks)} passed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt to crawlable seeds")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--upload", type=Path)
    parser.add_argument("--no-searxng", action="store_true")
    parser.add_argument("--no-resolve", action="store_true",
                        help="skip the url-filling pass (no network)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.prompt:
        parser.error("a prompt is required")

    intent, unparsed = parse(args.prompt)
    print(f"Read as: {intent.describe()}  (target: {intent.target})")
    for item in unparsed:
        print(f"  could not read {item}")

    chain = default_chain(
        registry_db=args.registry, upload=args.upload,
        searxng=None if args.no_searxng else "http://127.0.0.1:8080/search",
        delay=args.delay,
    )
    if args.no_resolve:
        for source in chain.sources:
            if hasattr(source, "resolve"):
                source.resolve = lambda blanks: None

    seeds, trail = chain.find(intent, args.limit or intent.count)

    print("\nSources tried:")
    for result in trail:
        status = f"{len(result.seeds)} seeds" if result.ok else f"FAILED - {result.error}"
        print(f"  {result.source:12} {status}")

    crawlable = [s for s in seeds if s.crawlable]
    print(f"\n{len(seeds)} seeds, {len(crawlable)} crawlable\n")
    for seed in seeds[:30]:
        mark = "+" if seed.crawlable else "-"
        print(f" {mark} {seed.name[:44]:44} {str(seed.url or '')[:38]:38} {seed.source}")

    if args.csv:
        import csv as csv_module

        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv_module.writer(handle)
            writer.writerow(["name", "url", "source", "confidence", "external_id", "location", "note"])
            for seed in seeds:
                writer.writerow([seed.name, seed.url or "", seed.source, seed.confidence,
                                 seed.external_id or "", seed.location or "", seed.note])
        print(f"\nwritten to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
