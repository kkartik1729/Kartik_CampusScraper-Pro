"""The seed-source contract.

Every scraper asks the same question — give me URLs to crawl — and the answer
should not depend on where the URLs came from. A registry, a search engine and
an uploaded spreadsheet all satisfy the same interface, so the crawler never
learns which one answered, and a new source is a new class rather than a change
to the pipeline.

Three things make this worth the indirection:

  Provenance. Every seed records the source that produced it and why it was
  accepted, so a bad batch can be traced to its origin rather than guessed at.

  Fallback. Sources are tried in order and a dead one is skipped, which is the
  failure that has cost the most so far: a search service answering from one
  engine looked like success and poisoned the whole run.

  Honesty. A source that cannot answer returns nothing and says so. It never
  invents a URL, and an unresolved row stays unresolved.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Intent:
    """What the user asked for, after parsing. Fields are None when unstated."""

    vertical: str                      # colleges, hospitals, lawyers, companies
    location: str | None = None        # city or district as the user wrote it
    discipline: str | None = None      # engineering, oncology, healthcare...
    target: str = "organisation"       # organisation | people
    count: int | None = None           # how many the user asked for
    raw: str = ""                      # the original prompt, kept for the record

    def describe(self) -> str:
        parts = [f"{self.count}" if self.count else "", self.discipline or "", self.vertical]
        if self.location:
            parts.append(f"in {self.location}")
        return " ".join(p for p in parts if p)


@dataclass
class Seed:
    """One crawlable thing, with enough provenance to audit it later."""

    name: str
    url: str | None                    # None means identified but not yet locatable
    source: str                        # which adapter produced this
    confidence: float = 1.0            # 1.0 for statutory lists, lower for inference
    external_id: str | None = None     # AICTE id, NABH id, bar number
    location: str | None = None
    note: str = ""                     # why it was accepted, or why the url is missing
    extra: dict = field(default_factory=dict)

    @property
    def crawlable(self) -> bool:
        return bool(self.url)


@dataclass
class SourceResult:
    """What one adapter returned, including its own failure."""

    source: str
    seeds: list[Seed] = field(default_factory=list)
    ok: bool = True
    error: str = ""

    @property
    def crawlable(self) -> list[Seed]:
        return [s for s in self.seeds if s.crawlable]


class SeedSource(ABC):
    """Base class. Implementations answer an Intent with Seeds."""

    name: str = "unnamed"

    @abstractmethod
    def supports(self, intent: Intent) -> bool:
        """True when this source has anything to say about that intent."""

    @abstractmethod
    def find(self, intent: Intent, limit: int | None = None) -> SourceResult:
        """Return seeds. Raise nothing: report failure in the result."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name}>"


class SeedChain:
    """Tries sources in order, then uses later ones to fill earlier gaps.

    Two passes, because the two jobs are different. The first pass asks each
    source for candidates and stops at the first that produces any, so an
    authoritative list wins over a search. The second pass hands every seed
    that still has no URL to the remaining sources, which is how a registry
    name becomes a crawlable site.
    """

    def __init__(self, sources: Iterable[SeedSource]):
        self.sources = list(sources)

    def find(self, intent: Intent, limit: int | None = None) -> tuple[list[Seed], list[SourceResult]]:
        trail: list[SourceResult] = []
        seeds: list[Seed] = []

        for source in self.sources:
            if not source.supports(intent):
                continue
            result = source.find(intent, limit)
            trail.append(result)
            if result.ok and result.seeds:
                seeds = result.seeds
                break

        if not seeds:
            return [], trail

        # Second pass: resolve URLs for seeds the first source could not locate.
        blank = [s for s in seeds if not s.crawlable]
        if blank:
            for source in self.sources:
                if not blank:
                    break
                resolver = getattr(source, "resolve", None)
                if resolver is None:
                    continue
                # URLs already held, so a resolver cannot hand one out twice.
                taken = {s.url: s.name for s in seeds if s.crawlable}
                try:
                    try:
                        resolver(blank, taken)
                    except TypeError:
                        resolver(blank)  # older resolvers take the list alone
                except Exception as error:  # a failed resolver must not sink the run
                    trail.append(SourceResult(source=source.name, ok=False,
                                              error=f"resolve failed: {type(error).__name__}"))
                blank = [s for s in seeds if not s.crawlable]

        return seeds, trail
