"""The four seed sources.

Each one satisfies the same contract and knows nothing about the others. The
differences that matter:

  RegistrySource is authoritative and offline. A statutory approval list is the
  complete population, so when one exists it wins and no search happens.

  SearchSource infers. It is the only way to answer a vertical with no register
  — law firms, hiring companies — and it is also the resolver that turns a
  registry name into a crawlable site. Its scoring refuses rather than guesses.

  MetaSearchSource is the existing SearXNG service, kept as a fallback because
  it queries several engines at once when it is healthy. It is last because its
  quiet failure mode — answering from one engine and looking fine — is what has
  cost the most.

  UploadSource is the user handing over a list. Highest confidence of all,
  because a person chose every row.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

from .base import Intent, Seed, SeedSource, SourceResult


class RegistrySource(SeedSource):
    """A statutory list held locally. Offline, complete, authoritative."""

    name = "registry"

    # Which registers cover which verticals. Hospitals and the rest are listed
    # so the gap is visible in code rather than discovered at runtime.
    COVERAGE = {"colleges": "aicte"}

    def __init__(self, db_path: Path, registry_module=None):
        self.db_path = Path(db_path)
        if registry_module is None:
            import registry_aicte as registry_module
        self.registry = registry_module

    def supports(self, intent: Intent) -> bool:
        return intent.vertical in self.COVERAGE and self.db_path.exists()

    def find(self, intent: Intent, limit: int | None = None) -> SourceResult:
        try:
            conn = self.registry.connect(self.db_path)
            rows = self.registry.query(
                conn,
                district=intent.location,
                discipline=intent.discipline,
                limit=limit or intent.count,
                # Ranked institutions first when a ranking file has been
                # loaded, because "10 colleges in Pune" means the ones worth
                # contacting, not the first ten alphabetically. Unranked rows
                # still follow, so nothing is lost.
                order="rank",
            )
        except sqlite3.Error as error:
            return SourceResult(self.name, ok=False, error=f"registry read failed: {error}")

        seeds = [
            Seed(
                name=row["name"],
                url=row.get("website") or None,
                source=f"{self.name}:{self.COVERAGE[intent.vertical]}",
                confidence=1.0,
                external_id=row["aicte_id"],
                location=row.get("district"),
                note=row.get("discipline_match", "statutory list"),
                extra={"faculty_api": row.get("faculty_api"),
                       "inst_type": row.get("inst_type"),
                       "rank": row.get("rank")},
            )
            for row in rows
        ]
        return SourceResult(self.name, seeds=seeds)


class SearchSource(SeedSource):
    """Web search with scored acceptance. Finds sites, and resolves blanks."""

    name = "search"

    # Verticals with no register, so search is the only route to candidates.
    DISCOVERS = {"lawyers", "companies", "hospitals"}

    def __init__(self, searcher=None, scorer=None, threshold: float = 2.0,
                 delay: float = 3.0, registry_db: Path | None = None):
        if searcher is None or scorer is None:
            import resolve_websites as resolver
            searcher = searcher or resolver.ddgs_search
            scorer = scorer or resolver.pick
        self.searcher = searcher
        self.scorer = scorer
        self.threshold = threshold
        self.delay = delay
        self.registry_db = Path(registry_db) if registry_db else None

    def supports(self, intent: Intent) -> bool:
        return True  # always available, as discoverer or as resolver

    def _query(self, intent: Intent) -> str:
        words = [intent.discipline or "", intent.vertical]
        if intent.location:
            words.append(intent.location)
        return " ".join(w for w in words if w).strip()

    def find(self, intent: Intent, limit: int | None = None) -> SourceResult:
        if intent.vertical not in self.DISCOVERS:
            # A register covers this vertical; this source only fills gaps.
            return SourceResult(self.name, seeds=[])
        try:
            urls = self.searcher(self._query(intent), max_results=(limit or 10) * 2)
        except Exception as error:
            return SourceResult(self.name, ok=False, error=f"search failed: {type(error).__name__}")

        # The blocklist applies here too. Discovery is where aggregators do the
        # most damage: a directory page lists hundreds of firms, so crawling it
        # produces a plausible-looking haul that belongs to no real
        # organisation and cannot be attributed to one.
        try:
            import resolve_websites as scorer_module
            blocked = scorer_module.is_blocked
        except ImportError:
            blocked = lambda host: False  # noqa: E731

        seen, seeds, refused = set(), [], []
        for url in urls:
            host = urllib.parse.urlsplit(url).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            if not host or host in seen:
                continue
            if blocked(host):
                refused.append(host)
                continue
            seen.add(host)
            seeds.append(Seed(
                name=host, url=url, source=self.name,
                confidence=0.5, location=intent.location,
                note="search result, unverified until crawled",
            ))
            if limit and len(seeds) >= limit:
                break

        if not seeds and refused:
            return SourceResult(
                self.name, ok=False,
                error=f"every result was a directory or aggregator: {', '.join(refused[:5])}",
            )
        return SourceResult(self.name, seeds=seeds)

    def resolve(self, blanks: list[Seed], taken: dict[str, str] | None = None) -> None:
        """Fill in URLs for seeds that have a name but no site.

        A trust that runs several colleges usually outranks each of them in
        search, so two sibling institutions resolve to the same parent domain.
        Accepting both would crawl one site twice and attribute the same staff
        to two colleges, which is a worse outcome than leaving a row blank. The
        first claim on a URL stands; a later one is refused and told why.
        """
        import time

        conn = None
        if self.registry_db and self.registry_db.exists():
            import registry_aicte as registry
            conn = registry.connect(self.registry_db)

        claimed: dict[str, str] = dict(taken or {})

        for seed in blanks:
            place = f" {seed.location.title()}" if seed.location else ""
            try:
                results = self.searcher(f"{seed.name}{place} official website")
            except Exception as error:
                seed.note = f"resolve failed: {type(error).__name__}"
                time.sleep(self.delay)
                continue

            best, _scored, why = self.scorer(results, seed.name, self.threshold)
            if not best:
                seed.note = f"url unresolved: {why}"
                time.sleep(self.delay)
                continue

            owner = claimed.get(best.url)
            if owner and owner != seed.name:
                seed.note = f"shared domain, already claimed by {owner[:40]}"
                seed.extra["shared_url"] = best.url
                time.sleep(self.delay)
                continue

            claimed[best.url] = seed.name
            seed.url = best.url
            seed.confidence = min(seed.confidence, 0.8)
            seed.note = f"url from search: {'; '.join(best.reasons)}"
            if conn is not None and seed.external_id:
                import registry_aicte as registry
                registry.set_website(conn, seed.external_id, best.url, self.name)
            time.sleep(self.delay)


class MetaSearchSource(SeedSource):
    """The local SearXNG instance. Several engines at once, when healthy.

    Kept last. It reports which engines answered, so a run can tell a real
    empty result from a service that is quietly down to one engine.
    """

    name = "searxng"

    def __init__(self, endpoint: str = "http://127.0.0.1:8080/search", timeout: float = 20.0):
        self.endpoint = endpoint
        self.timeout = timeout

    def supports(self, intent: Intent) -> bool:
        return True

    def find(self, intent: Intent, limit: int | None = None) -> SourceResult:
        query = " ".join(w for w in [intent.discipline or "", intent.vertical,
                                     intent.location or ""] if w)
        url = f"{self.endpoint}?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                import json
                payload = json.load(response)
        except Exception as error:
            return SourceResult(self.name, ok=False,
                                error=f"searxng unreachable: {type(error).__name__}")

        results = payload.get("results", [])
        engines = sorted({e for r in results for e in r.get("engines", [])})
        if len(engines) <= 1:
            # The failure that looks like success: one engine answering alone.
            return SourceResult(
                self.name, ok=False,
                error=f"only {engines or 'no'} engine(s) answered; treat as degraded",
            )

        seeds = [
            Seed(name=r.get("title", r["url"]), url=r["url"], source=self.name,
                 confidence=0.5, location=intent.location,
                 note=f"engines: {','.join(r.get('engines', []))}")
            for r in results[: (limit or 20)] if r.get("url")
        ]
        return SourceResult(self.name, seeds=seeds)


class UploadSource(SeedSource):
    """A list the user supplied. Nothing outranks a person choosing rows."""

    name = "upload"

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None

    def supports(self, intent: Intent) -> bool:
        return self.path is not None and self.path.exists()

    def find(self, intent: Intent, limit: int | None = None) -> SourceResult:
        import csv

        try:
            if self.path.suffix.lower() in {".xlsx", ".xls"}:
                import pandas as pd
                records = pd.read_excel(self.path).to_dict("records")
            else:
                with self.path.open(encoding="utf-8-sig", newline="") as handle:
                    records = list(csv.DictReader(handle))
        except Exception as error:
            return SourceResult(self.name, ok=False, error=f"cannot read file: {error}")

        def pick_field(row: dict, *names: str) -> str | None:
            for key, value in row.items():
                if str(key).strip().lower() in names and str(value).strip():
                    return str(value).strip()
            return None

        seeds = []
        for row in records[: limit or len(records)]:
            url = pick_field(row, "url", "website", "site", "link")
            name = pick_field(row, "name", "institution", "hospital", "firm", "organisation")
            if not (url or name):
                continue
            seeds.append(Seed(
                name=name or url, url=url, source=self.name, confidence=1.0,
                location=pick_field(row, "city", "district", "location"),
                note="supplied by the user",
            ))
        return SourceResult(self.name, seeds=seeds)
