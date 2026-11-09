"""Prompt to a batch run, using the shared seed sources.

This sits beside the existing prompt-discovery workflow rather than replacing
it. That one searches with SearXNG and judges pages with a local model, which
is the right tool when nothing authoritative exists. This one asks a register
first and search second, and hands the result to the batch path that is
already in use for uploaded sheets.

The portal already crawls a list of colleges well, and already resumes a batch
across a restart. What it lacked was a way to produce that list without a
person assembling the spreadsheet by hand. That work lives in the shared
`seedsources` package.

Seeds become a CSV in the portal's own upload template and go through the
existing batch path, so history, resume, pause and the Excel export all behave
exactly as they do for a file a person uploaded. Nothing in the crawl changes.

    POST /api/seeds/preview   what would be crawled, and where each URL came from
    POST /api/seeds/run       the same, then start the normal batch
"""

from __future__ import annotations

import base64
import csv
import io
import sys
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

# The shared package sits at the repository root, beside this module's package.
for candidate in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[2]):
    if (candidate / "seedsources").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class SeedIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=20, ge=1, le=200)
    registry_db: str | None = None
    use_searxng: bool = False
    resolve_websites: bool = True
    delay: float = Field(default=3.0, ge=0.0, le=30.0)
    # Crawl settings, handed straight to the batch that already exists.
    pages_per_site: int = Field(default=60, ge=1, le=5000)
    max_depth: int = Field(default=3, ge=0, le=10)
    delay_seconds: float = Field(default=1.5, ge=0, le=30)
    render: str = "auto"
    all_people: bool = False
    departments: str = Field(default="", max_length=300)
    include_offices: bool = True
    workers: int = Field(default=3, ge=1, le=8)
    patience: int = Field(default=80, ge=0, le=5000)
    colleges_at_once: int = Field(default=3, ge=1, le=8)


def _plan(body: SeedIn) -> dict:
    try:
        from seedsources import default_chain, parse
    except ImportError as error:
        raise HTTPException(
            500,
            "The shared seedsources package is not importable. Put it at the "
            f"repository root beside campusscraper ({error}).",
        ) from error

    intent, unparsed = parse(body.prompt)
    chain = default_chain(
        registry_db=body.registry_db,
        searxng="http://127.0.0.1:8080/search" if body.use_searxng else None,
        delay=body.delay,
    )
    if not body.resolve_websites:
        for source in chain.sources:
            if hasattr(source, "resolve"):
                source.resolve = lambda blanks, taken=None: None

    seeds, trail = chain.find(intent, body.limit)
    return {
        "understood": {
            "vertical": intent.vertical, "location": intent.location,
            "discipline": intent.discipline, "target": intent.target,
            "count": intent.count, "summary": intent.describe(),
        },
        "unparsed": unparsed,
        "sources_tried": [
            {"source": r.source, "ok": r.ok, "seeds": len(r.seeds), "error": r.error}
            for r in trail
        ],
        "seeds": [
            {"name": s.name, "url": s.url, "source": s.source,
             "confidence": s.confidence, "location": s.location, "note": s.note}
            for s in seeds
        ],
        "crawlable": sum(1 for s in seeds if s.crawlable),
        "unresolved": [
            {"name": s.name, "reason": s.note} for s in seeds if not s.crawlable
        ],
    }


def seeds_to_csv(seeds: list[dict]) -> str:
    """Write seeds into the portal's upload template.

    One row per site. A seed that resolved to a host another seed already holds
    is left out, because a chain's parent domain outranks each of its colleges
    in search and crawling one site twice attributes the same people twice.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["College Name", "Official Website", "City", "Enabled"])

    seen: set[str] = set()
    for seed in seeds:
        if not seed.get("url"):
            continue
        from urllib.parse import urlsplit

        host = urlsplit(seed["url"]).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if not host or host in seen:
            continue
        seen.add(host)
        writer.writerow([seed["name"][:200], seed["url"], seed.get("location") or "", "yes"])
    return buffer.getvalue()


def register(app, batch_in_model, batch_job_cls, batches: dict, store_getter, safe,
             load_upload) -> None:
    """Attach the prompt endpoints to the existing portal.

    The portal's own pieces are passed in rather than imported, so this module
    does not create a circular import with web.py.
    """
    import threading

    @app.post("/api/seeds/preview")
    def seeds_preview(body: SeedIn):
        return _plan(body)

    @app.post("/api/seeds/run")
    def seeds_run(body: SeedIn):
        plan = _plan(body)
        if not plan["crawlable"]:
            last = plan["sources_tried"][-1] if plan["sources_tried"] else None
            raise HTTPException(
                422,
                "No seed produced a URL. " + (
                    last["error"] if last and not last["ok"]
                    else "Widen the prompt or upload a list."
                ),
            )

        csv_text = seeds_to_csv(plan["seeds"])
        payload = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
        upload = batch_in_model(
            filename=f"seeds-{abs(hash(body.prompt)) % 10**8}.csv",
            content_base64=payload,
            pages_per_site=body.pages_per_site,
            max_depth=body.max_depth,
            delay_seconds=body.delay_seconds,
            render=body.render,
            all_people=body.all_people,
            departments=body.departments,
            include_offices=body.include_offices,
            workers=body.workers,
            patience=body.patience,
            colleges_at_once=body.colleges_at_once,
        )

        sources, problems = load_upload(upload)
        if not sources:
            raise HTTPException(
                422,
                "The resolved sites were rejected by the uploader: "
                + "; ".join(p.problem for p in problems[:5]),
            )

        job = batch_job_cls(upload, sources)
        batches[job.id] = job
        settings = upload.model_dump(exclude={"content_base64"})
        settings["prompt"] = body.prompt
        safe(store_getter().create_run, job.id, "batch",
             f"Seeds: {body.prompt[:80]} ({len(sources)} sites)",
             settings, upload.filename, csv_text.encode("utf-8"))
        threading.Thread(target=job.run, daemon=True).start()

        return {
            "id": job.id,
            "colleges": len(sources),
            "problems": [p.__dict__ for p in problems],
            "prompt_plan": plan,
        }
