"""Shared seed sources for the campus, onco and attorney scrapers.

One prompt in, crawlable seeds out, whichever source can answer. The scraper
that imports this keeps its own fetcher, extractor, journal and export; only
the question "which URLs" moves here.

    from seedsources import seeds_for_prompt

    seeds, trail = seeds_for_prompt(
        "Find 20 engineering colleges in Pune with faculty directory URLs",
        registry_db="results/registry.db",
    )

`trail` holds what every source did, including the ones that failed, so a thin
result can be explained instead of guessed at.
"""

from __future__ import annotations

from pathlib import Path

from .base import Intent, Seed, SeedChain, SeedSource, SourceResult
from .intent import parse
from .adapters import MetaSearchSource, RegistrySource, SearchSource, UploadSource

__all__ = [
    "Intent", "Seed", "SeedChain", "SeedSource", "SourceResult",
    "parse", "RegistrySource", "SearchSource", "MetaSearchSource",
    "UploadSource", "default_chain", "seeds_for_prompt",
]


def default_chain(
    registry_db: str | Path | None = None,
    upload: str | Path | None = None,
    searxng: str | None = "http://127.0.0.1:8080/search",
    delay: float = 3.0,
) -> SeedChain:
    """The agreed order: what the user gave, then a register, then search.

    SearXNG sits last because its degraded state is silent; the sources above
    it either answer completely or say nothing.
    """
    sources: list[SeedSource] = []
    if upload:
        sources.append(UploadSource(Path(upload)))
    if registry_db:
        sources.append(RegistrySource(Path(registry_db)))
    sources.append(SearchSource(registry_db=Path(registry_db) if registry_db else None, delay=delay))
    if searxng:
        sources.append(MetaSearchSource(searxng))
    return SeedChain(sources)


def seeds_for_prompt(
    prompt: str,
    registry_db: str | Path | None = None,
    upload: str | Path | None = None,
    searxng: str | None = "http://127.0.0.1:8080/search",
    limit: int | None = None,
    delay: float = 3.0,
) -> tuple[list[Seed], list[SourceResult]]:
    """Parse a prompt and answer it with seeds."""
    intent, _unparsed = parse(prompt)
    chain = default_chain(registry_db, upload, searxng, delay)
    return chain.find(intent, limit or intent.count)
