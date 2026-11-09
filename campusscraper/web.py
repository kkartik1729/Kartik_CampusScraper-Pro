"""Local web UI: paste a college URL or upload a college list, watch the crawl, download the Excel file."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .config import ScrapeConfig
from .crawl import Crawler, CrawlResult
from .export import ExportManager, to_csv, to_json, to_xlsx, validate_xlsx
from .extract import extract_page
from .fetch import Fetcher
from .models import OfficeRecord, PersonRecord
from .quality import audit_records, compute_confidence
from .system_monitor import MONITOR
from .vocab import STATES

STATIC = Path(__file__).parent / "static"
OUT_DIR = Path.cwd() / "results"
app = FastAPI(title="CampusScraper", docs_url=None, redoc_url=None)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_STORE = None
_STORE_LOCK = threading.RLock()
_EXPORT_MANAGER: ExportManager | None = None
_SETTINGS_Lock = threading.Lock()
_DEFAULT_SETTINGS = {
    "max_pages": 200,
    "pages_per_site": 150,
    "max_depth": 4,
    "delay_seconds": 1.5,
    "timeout_seconds": 25.0,
    "workers": 4,
    "colleges_at_once": 4,
    "patience": 80,
    "render": "auto",
    "use_sitemaps": True,
    "include_subdomains": False,
    "include_offices": True,
    "adaptive_concurrency": True,
    "respect_robots": True,
}
_RUNTIME_SETTINGS = dict(_DEFAULT_SETTINGS)


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def store():
    """The local database (results/campusscraper.db next to the Excel files)."""
    global _STORE
    from .store import Store
    path = Path(os.environ.get("CAMPUSSCRAPER_DB") or OUT_DIR / "campusscraper.db")
    with _STORE_LOCK:
        if _STORE is None or _STORE.path != path:
            _STORE = Store(path)
        return _STORE


def export_manager() -> ExportManager:
    global _EXPORT_MANAGER
    with _STORE_LOCK:
        if _EXPORT_MANAGER is None or _EXPORT_MANAGER.out_dir != OUT_DIR:
            _EXPORT_MANAGER = ExportManager(OUT_DIR)
        return _EXPORT_MANAGER


def _safe(fn, *a, **k):
    """Saving history must never break a crawl."""
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        print(f"[history] could not save: {exc}")


def _reconcile_site_counts(sites, docs, offices=()):
    """Make the college report match the rows that are actually saved and exported."""
    reconciled = []
    for saved in sites or []:
        site = dict(saved)
        college = site.get("institution") or ""
        people = [d for d in docs if d.source_institution == college]
        faculty = [d for d in people if d.is_relevant]
        site.update(
            people=len(people),
            faculty=len(faculty),
            offices=sum(1 for o in offices or () if o.source_institution == college),
            with_phone=sum(bool(d.get("phone")) for d in faculty),
            with_email=sum(bool(d.get("email")) for d in faculty),
            with_any_contact=sum(any(d.get(f) for f in ("phone", "email", "contact_phone", "contact_email"))
                                 for d in faculty),
        )
        reconciled.append(site)
    return sorted(reconciled, key=lambda s: s.get("row", 0))


def _host(u: str) -> str:
    return (urlsplit(u if "://" in u else "https://" + u).hostname or u).removeprefix("www.")


class JobIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    max_pages: int = Field(200, ge=1, le=20000)
    max_depth: int = Field(4, ge=0, le=10)
    delay_seconds: float = Field(1.5, ge=0, le=30)
    render: str = "auto"
    all_people: bool = False
    use_sitemaps: bool = True
    include_subdomains: bool = False
    departments: str = Field("", max_length=300)
    include_offices: bool = True
    workers: int = Field(3, ge=1, le=32)
    patience: int = Field(80, ge=0, le=5000)


class PageIn(BaseModel):
    url: str
    render: str = "auto"


class Job:
    def __init__(self, body: JobIn):
        self.id = uuid.uuid4().hex[:10]
        self.body = body
        self.state = "running"
        self.log: deque[dict] = deque(maxlen=400)
        self.console: deque[str] = deque(maxlen=300)
        self.progress: dict = {
            "fetched": 0, "doctors_total": 0, "faculty": 0, "offices": 0, "queued": 0,
            "discovered": len(body.urls), "with_email": 0, "with_phone": 0, "duplicates_removed": 0,
        }
        self.result: CrawlResult | None = None
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.crawler: Crawler | None = None
        self.xlsx: Path | None = None
        self.site_name = _host(body.urls[0]) if body.urls else ""
        self._emit_console(f"Starting website scraping for {self.site_name or 'target site'}.")

    def _emit_console(self, msg: str) -> None:
        line = f"[{_ts()}] {msg}"
        self.console.append(line)

    def save_history(self, res: CrawlResult) -> None:
        store().save_people(self.id, res.doctors, self.site_name)
        store().save_offices(self.id, res.offices, self.site_name)

    def on_event(self, e: dict) -> None:
        if e["event"] == "page":
            self.progress = {
                k: e.get(k, 0)
                for k in (
                    "fetched", "doctors_total", "faculty", "offices", "queued",
                    "discovered", "with_email", "with_phone", "duplicates_removed",
                )
            }
            if e["fetched"] % 25 == 0 and self.crawler:  # keep the history up to date during long crawls
                _safe(lambda: self.save_history(self.snapshot()))
            self.log.append({"url": e["url"], "type": e["type"], "status": e["status"],
                             "doctors": e["doctors_on_page"], "error": e.get("error"), "ts": _ts()})
            if e.get("doctors_on_page", 0) > 0:
                self._emit_console(
                    f"Faculty page identified ({e['type']}): extracted {e['doctors_on_page']} records from {e['url']}."
                )
            elif e.get("error"):
                self._emit_console(f"Page failed ({e['error']}): {e['url']}")
            elif e["fetched"] == 1 or e["fetched"] % 10 == 0:
                self._emit_console(
                    f"Processed {e['fetched']} pages (discovered {e.get('discovered', 0)} internal URLs, {e.get('faculty', 0)} faculty found)."
                )
        elif e["event"] == "sitemap":
            self.log.append({"url": f"sitemap: {e['urls']} URLs", "type": "info", "status": "", "doctors": 0, "ts": _ts()})
            self._emit_console(f"Discovered {e['urls']} URLs from sitemap.")

    def run(self) -> None:
        b = self.body
        try:
            cfg = ScrapeConfig(max_pages=b.max_pages, max_depth=b.max_depth, delay_seconds=b.delay_seconds,
                               render=b.render, relevant_only=not b.all_people, use_sitemaps=b.use_sitemaps,
                               include_subdomains=b.include_subdomains, departments=_depts(b.departments),
                               include_offices=b.include_offices, workers=b.workers, patience=b.patience)
            self.crawler = Crawler(cfg, self.on_event, self.stop, self.pause)
            self.result = self.crawler.run([u.strip() for u in b.urls if u.strip()])
            OUT_DIR.mkdir(exist_ok=True)
            self.xlsx = to_xlsx(self.result, OUT_DIR / f"faculty-{time.strftime('%Y%m%d-%H%M%S')}-{self.id}.xlsx")
            self.state = "stopped" if self.stop.is_set() else "done"
            self._emit_console(
                f"Crawl {self.state}: extracted {len(self.result.faculty)} faculty records across {len(self.result.pages)} pages."
            )
            _safe(self.save_history, self.result)
        except Exception as exc:  # surface any crash to the UI
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
            self._emit_console(f"Crawl failed: {self.error}")
        self.finished = time.time()
        _safe(store().update_run, self.id, state=self.state, finished=self.finished, error=self.error,
              stats=(self.result.stats if self.result else {}), xlsx=str(self.xlsx) if self.xlsx else None)

    def snapshot(self) -> CrawlResult:
        """What has been found so far, while the crawl is still running (copies; the crawl is not touched)."""
        from . import relevance
        from .contacts import demote_shared, demote_sitewide
        from .models import OfficeRecord, PersonRecord
        from .profile import clean_fields
        c = self.crawler
        docs = [PersonRecord.from_full_dict(d.to_full_dict()) for d in list(c.merger.records)] if c else []
        relevance.set_targets(_depts(self.body.departments))
        for d in docs:
            clean_fields(d)
            relevance.rescore(d)
        if c:
            demote_sitewide(docs, {k: set(v) for k, v in list(c.value_pages.items())})
        demote_shared(docs)
        offices = [OfficeRecord.from_full_dict(o.to_full_dict()) for o in list(c.offices)] if c else []
        res = CrawlResult([u.strip() for u in self.body.urls if u.strip()], docs, list(c.result_pages) if c else [],
                          offices=offices)
        fac = [d for d in docs if d.is_relevant]
        res.stats = {"partial": "yes - downloaded while the crawl was running", "pages_fetched": self.progress.get("fetched", 0),
                     "people_found": len(docs), "faculty": len(fac), "offices": len(offices),
                     "seconds": round(time.time() - self.started, 1)}
        return res

    def doctors(self):
        if self.result:
            docs = self.result.doctors
        elif self.crawler:
            docs = list(self.crawler.merger.records)
        else:
            docs = []
        if not self.body.all_people:
            docs = [d for d in docs if d.is_relevant]
        return docs

    def offices(self):
        if self.result:
            return self.result.offices
        return list(self.crawler.offices) if self.crawler else []


def _depts(text: str) -> list[str]:
    return [d.strip() for d in (text or "").split(",") if d.strip()]


JOBS: dict[str, Job] = {}


ROW_KEYS = ("name", "designation", "department", "qualifications", "experience_years", "institution", "city",
            "phone", "email", "contact_phone", "contact_email", "research_areas", "google_scholar", "orcid",
            "profile_url", "source_url", "is_relevant", "page_type")


def _office_rows(offices, limit: int = 1000) -> list[dict]:
    return [o.to_dict() for o in list(offices)[:limit]]


def _rows(docs, limit: int = 3000) -> list[dict]:
    out = []
    for d in docs[:limit]:
        x = d.to_dict()
        conf = compute_confidence(d)
        out.append(
            {k: x.get(k) for k in ROW_KEYS}
            | {
                "fields": len(d.filled_fields()),
                "source_institution": d.source_institution,
                "confidence": conf["score"],
                "confidence_grade": conf["grade"],
            }
        )
    return out


def _state(job) -> str:
    if job.state == "running" and job.stop.is_set():
        return "stopping"  # Stop was pressed; pages already in flight are finishing
    return "paused" if job.state == "running" and job.pause.is_set() else job.state


def _pause(job, paused: bool) -> dict:
    if job.state != "running":
        raise HTTPException(409, f"cannot pause or resume: the run is {job.state}")
    (job.pause.set if paused else job.pause.clear)()
    if hasattr(job, "_emit_console"):
        job._emit_console("Scraping paused by user." if paused else "Scraping resumed by user.")
    _safe(store().update_run, job.id, state=_state(job))
    return {"ok": True, "state": _state(job)}


@app.get("/static/{name}.js")
def static_js(name: str):
    if name not in ("app", "portal", "prompt"):
        raise HTTPException(404, "not found")
    return Response((STATIC / f"{name}.js").read_text(encoding="utf-8"), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/api/jobs")
def start(body: JobIn):
    if body.render not in ("never", "auto", "always"):
        raise HTTPException(400, "render must be never, auto or always")
    job = Job(body)
    JOBS[job.id] = job
    _safe(store().create_run, job.id, "crawl", ", ".join(_host(u) for u in body.urls[:3]), body.model_dump())
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
def status(job_id: str, since: int = 0):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    rows = _rows(job.doctors())
    elapsed = max(0.1, (job.finished or time.time()) - job.started)
    fetched = job.progress.get("fetched", 0)
    pages_per_min = round((fetched / elapsed) * 60.0, 1)
    return {
        "id": job.id,
        "state": _state(job),
        "error": job.error,
        "progress": job.progress,
        "max_pages": job.body.max_pages,
        "elapsed": round(elapsed),
        "pages_per_min": pages_per_min,
        "stats": job.result.stats if job.result else None,
        "log": list(job.log)[-60:],
        "console": list(job.console)[-80:],
        "doctors": rows,
        "offices": _office_rows(job.offices()),
    }


@app.post("/api/jobs/{job_id}/stop")
def stop(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job.stop.set()
    job._emit_console("Stop requested by user.")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    job = JOBS.get(job_id) or _missing("job")
    return _pause(job, True)


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    job = JOBS.get(job_id) or _missing("job")
    return _pause(job, False)


def _missing(what: str):
    raise HTTPException(404, f"{what} not found")


@app.get("/api/jobs/{job_id}/download.{fmt}")
def download(job_id: str, fmt: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if fmt not in ("xlsx", "csv", "json"):
        raise HTTPException(400, "format must be xlsx, csv or json")
    partial = job.result is None
    if partial and not job.crawler:
        raise HTTPException(404, "nothing found yet")
    try:
        result = job.snapshot() if partial else job.result
    except Exception as exc:  # the crawl changed under us; a second click works
        raise HTTPException(503, f"could not copy results, try again: {exc}") from None
    tag = "-partial-" + time.strftime("%H%M%S") if partial else ""
    if fmt == "xlsx":
        if not partial and job.xlsx:
            path = job.xlsx
        else:
            OUT_DIR.mkdir(exist_ok=True)
            path = to_xlsx(result, OUT_DIR / f"faculty-{job.id}{tag}.xlsx")
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "csv":
        docs = result.doctors if job.body.all_people else [d for d in result.doctors if d.is_relevant]
        return Response(to_csv(docs).encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="faculty-{job.id}{tag}.csv"'})
    return Response(to_json(result, not job.body.all_people), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="faculty-{job.id}{tag}.json"'})


@app.post("/api/page")
def test_page(body: PageIn):
    cfg = ScrapeConfig(render=body.render, delay_seconds=0)
    fetcher = Fetcher(cfg)
    try:
        page = fetcher.get(body.url)
    finally:
        fetcher.close()
    pr = extract_page(page)
    return {"status": page.status, "final_url": page.final_url, "error": page.error, "rendered": page.rendered,
            "page_type": pr.page_type, "signals": {k: v for k, v in pr.signals.items() if k != "page_contacts"},
            "doctors": [{k: v for k, v in d.to_dict().items() if v not in (None, [], {}, "")} for d in pr.doctors],
            "offices": [{k: v for k, v in o.to_dict().items() if v not in (None, [], {}, "", 0)} for o in pr.offices],
            "profile_links": pr.profile_links[:50], "pagination_links": pr.pagination_links}


# ---------------------------------------------------------------------------
# Batch: upload a college list (standard template) and crawl every college.
class UploadIn(BaseModel):
    filename: str = Field(max_length=200)
    content_base64: str = Field(max_length=20_000_000)


class BatchIn(UploadIn):
    pages_per_site: int = Field(150, ge=1, le=5000)
    max_depth: int = Field(4, ge=0, le=10)
    delay_seconds: float = Field(1.5, ge=0, le=30)
    render: str = "auto"
    all_people: bool = False
    use_sitemaps: bool = True
    departments: str = Field("", max_length=300)
    include_offices: bool = True
    workers: int = Field(3, ge=1, le=32)
    patience: int = Field(80, ge=0, le=5000)
    colleges_at_once: int = Field(3, ge=1, le=32)


def _load_upload(body: UploadIn):
    import base64
    import tempfile

    from .sources import HeaderError, read_sources
    suffix = Path(body.filename).suffix.lower()
    if suffix not in (".xlsx", ".xlsm", ".csv"):
        raise HTTPException(400, "Upload an .xlsx or .csv file made from the template")
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except ValueError:
        raise HTTPException(400, "File could not be decoded") from None
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(data)
        tmp = Path(fh.name)
    try:
        return read_sources(tmp)
    except HeaderError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        raise HTTPException(400, f"Could not read the file. Save it as .xlsx or .csv from the template and try again ({exc})") from None
    finally:
        tmp.unlink(missing_ok=True)


class BatchJob:
    def __init__(self, body: BatchIn, sources, run_id: str | None = None, resume: bool = False,
                 seconds_already_spent: float = 0.0):
        self.id = run_id or uuid.uuid4().hex[:10]
        self.resume = resume
        # A resumed batch continues the same piece of work, so the clock
        # continues from where it stopped instead of starting again at zero.
        self.seconds_already_spent = max(0.0, float(seconds_already_spent or 0))
        self.body = body
        self.sources = sources
        self.state = "running"
        self.current = ""
        self.index = 0
        self.sites: list[dict] = []
        self.progress_lock = threading.RLock()
        self.rows = {
            src.row: {
                "row": src.row,
                "sequence": i,
                "institution": src.institution,
                "website": src.urls[0] if src.urls else "",
                "status": "queued",
                "stage": "Queued",
                "pages": 0,
                "discovered": len(src.urls),
                "faculty": 0,
                "with_email": 0,
                "with_phone": 0,
                "duplicates_removed": 0,
                "offices": 0,
                "queued": 0,
                "started_at": None,
                "duration": 0,
                "note": "",
                "page_limit": src.max_pages or body.pages_per_site,
            }
            for i, src in enumerate(sources, 1)
        }
        self.finished: float | None = None
        self.phase = "crawling"
        self.log: deque[dict] = deque(maxlen=400)
        self.console: deque[str] = deque(maxlen=400)
        self.progress: dict = {}
        self.stats: dict | None = None
        self.error: str | None = None
        self.started = time.time()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.xlsx: Path | None = None
        self.live: dict = {}
        self._emit_console(
            f"{'Resuming' if resume else 'Starting'} {body.filename or 'batch'} scraping ({len(sources)} colleges)."
        )

    def _emit_console(self, msg: str) -> None:
        line = f"[{_ts()}] {msg}"
        self.console.append(line)

    def elapsed(self) -> float:
        """Crawling time for this batch, including earlier sessions of it."""
        return self.seconds_already_spent + ((self.finished or time.time()) - self.started)

    def on_event(self, e: dict) -> None:
        with self.progress_lock:
            self._on_event(e)

    def _on_event(self, e: dict) -> None:
        row = self.rows.get(e.get("row"))
        now = time.time()
        if row is not None:
            if e["event"] == "site_start":
                row.update(status="crawling", stage="Discovering URLs & Sitemaps", started_at=now)
            elif e["event"] == "sitemap":
                disc = max(row.get("discovered", 0), e.get("urls", 0))
                row.update(discovered=disc, stage=f"Sitemap discovered ({disc} URLs)")
            elif e["event"] == "page":
                ptype = e.get("type", "other")
                stage_label = (
                    "Extracting Faculty Profile"
                    if ptype == "profile"
                    else ("Scanning Faculty Directory" if ptype == "listing" else "Crawling Internal Pages")
                )
                started_at = row.get("started_at") or now
                row.update(
                    pages=e.get("fetched", 0),
                    faculty=e.get("faculty", 0),
                    with_email=e.get("with_email", row.get("with_email", 0)),
                    with_phone=e.get("with_phone", row.get("with_phone", 0)),
                    duplicates_removed=e.get("duplicates_removed", row.get("duplicates_removed", 0)),
                    offices=e.get("offices", 0),
                    queued=e.get("queued", 0),
                    discovered=max(row.get("discovered", 0), e.get("discovered", 0), e.get("fetched", 0) + e.get("queued", 0)),
                    stage=stage_label,
                    website=e.get("url") or row.get("website", ""),
                    duration=round(max(0.0, now - started_at), 1),
                )
            elif e["event"] == "site_done":
                started_at = row.get("started_at") or now
                row.update({k: v for k, v in e.items() if k != "event"})
                row["queued"] = 0
                row["stage"] = "Completed" if e.get("status") in ("done", "resumed") else e.get("status", "Finished").title()
                row["duration"] = round(max(0.0, now - started_at), 1)
        if e["event"] == "site_start":
            self.index = max(self.index, e["index"])
            running = [src.institution for _, src in list((self.live.get("active") or {}).values())]
            self.current = ", ".join(dict.fromkeys(running + [e["institution"]]))
            self.log.append({"url": f"=== {e['institution']}", "type": "info", "status": "", "doctors": 0, "ts": _ts()})
            self._emit_console(f"Processing College {e['index']:02d}: {e['institution']}.")
        elif e["event"] == "sitemap":
            if e.get("urls", 0) > 0:
                self._emit_console(f"Discovered {e['urls']} sitemap URLs for {e.get('institution', 'college')}.")
        elif e["event"] == "site_done":
            site = {k: v for k, v in e.items() if k != "event"}
            self.sites.append(site)
            running = [src.institution for _, src in list((self.live.get("active") or {}).values())]
            self.current = ", ".join(running)
            self._emit_console(
                f"Finished {site['institution']} ({site['status']}): {site.get('faculty', 0)} faculty from {site.get('pages', 0)} pages."
            )
            _safe(store().save_site, self.id, site)
            _safe(self._save_college, site["institution"])
        elif e["event"] == "page":
            self.progress = {k: e.get(k, 0) for k in ("fetched", "doctors_total", "faculty", "offices", "queued")}
            self.log.append({"url": e["url"], "type": e["type"], "status": e["status"],
                             "doctors": e["doctors_on_page"], "error": e.get("error"), "ts": _ts(),
                             "institution": e.get("institution", "")})
            if e.get("doctors_on_page", 0) > 0:
                self._emit_console(
                    f"[{e.get('institution', 'College')}] Extracted {e['doctors_on_page']} faculty records from {e['url']}."
                )

    def run(self) -> None:
        from .batch import run_batch
        b = self.body
        try:
            base = ScrapeConfig(max_depth=b.max_depth, delay_seconds=b.delay_seconds, render=b.render,
                                relevant_only=not b.all_people, use_sitemaps=b.use_sitemaps,
                                departments=_depts(b.departments), include_offices=b.include_offices,
                                workers=b.workers, patience=b.patience)
            OUT_DIR.mkdir(exist_ok=True)
            out = OUT_DIR / f"batch-{self.id}.xlsx"  # fixed name: a resumed batch continues in the same files
            self.xlsx = run_batch(self.sources, out, base, b.pages_per_site, resume=self.resume,
                                  progress=self._capture, stop_event=self.stop, live=self.live,
                                  pause_event=self.pause, parallel_sites=b.colleges_at_once,
                                  export_result=False)
            self.state = "stopped" if self.stop.is_set() else "done"
            if self.live.get("merger") is not None:
                _safe(store().save_people, self.id, list(self.live["merger"].records))
                _safe(store().save_offices, self.id, list(self.live.get("offices") or []))
                docs = store().run_people(self.id)
                from .batch import normalize_legacy_affiliation_records, normalize_shared_source_records
                docs, remapped, deduplicated, unparsed = normalize_legacy_affiliation_records(docs, self.sources)
                docs, shared_duplicates, unmapped_shared = normalize_shared_source_records(docs, self.sources)
                store().replace_run_people(self.id, docs)
                docs = store().run_people(self.id)
                offices = store().run_offices(self.id)
                sites = store().sites(self.id)
                pages = list(self.live.get("pages") or [])
                result = CrawlResult([u for s in self.sources for u in s.urls], docs, pages, offices=offices)
                faculty = result.faculty
                result.stats = {
                    "colleges": len(sites), "pages_fetched": len(pages), "people_found": len(docs),
                    "faculty": len(faculty), "student_offices": len(offices),
                    "faculty_with_own_phone": sum(bool(d.get("phone")) for d in faculty),
                    "faculty_with_own_email": sum(bool(d.get("email")) for d in faculty),
                    "faculty_with_any_contact": sum(any(d.get(f) for f in ("phone", "email", "contact_phone", "contact_email"))
                                                     for d in faculty),
                    "affiliation_rows_recovered": remapped,
                    "duplicate_rows_removed": deduplicated + shared_duplicates,
                    "unmapped_legacy_affiliation_rows": unparsed,
                    "shared_source_duplicates_merged": shared_duplicates,
                    "unmapped_shared_source_records": unmapped_shared,
                    "seconds": round(self.elapsed()), "stopped_early": self.stop.is_set(),
                }
                sites = _reconcile_site_counts(sites, docs, offices)
                for site in sites:
                    _safe(store().save_site, self.id, site)
                    if site.get("row") in self.rows:
                        self.rows[site["row"]].update(site)
                self.sites = sites
                self.stats = result.stats
                self._emit_console(f"Generating Excel workbook for {len(faculty)} faculty records...")
                self.xlsx = to_xlsx(result, out, sites=sites)
                self._emit_console(f"Batch completed ({self.state}). Workbook saved to {self.xlsx.name}.")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
            self._emit_console(f"Batch failed: {self.error}")
        self.finished = time.time()
        _safe(store().update_run, self.id, state=self.state, finished=self.finished, error=self.error,
              stats={**(self.stats or {}), "seconds": round(self.elapsed())},
              xlsx=str(self.xlsx) if self.xlsx else None)

    def _save_college(self, college: str) -> None:
        """This college's people and offices go into the history as soon as it finishes."""
        _safe(store().update_run, self.id, stats={**(self.stats or {}), "seconds": round(self.elapsed())})
        merger = self.live.get("merger")
        if merger is not None:
            store().save_people(self.id, [d for d in list(merger.records) if d.source_institution == college])
        store().save_offices(self.id, [o for o in list(self.live.get("offices") or []) if o.source_institution == college])

    def _capture(self, e: dict) -> None:
        if e["event"] == "batch_done":
            self.phase = "finalizing"
            self.stats = {k: v for k, v in e.items() if k != "event"}
        else:
            self.on_event(e)


BATCHES: dict[str, BatchJob] = {}


@app.get("/api/template.xlsx")
def template_download():
    import tempfile

    from .sources import write_template
    tmp = Path(tempfile.gettempdir()) / "campusscraper_college_list_template.xlsx"
    write_template(tmp)
    return FileResponse(tmp, filename="college_list_template.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/sources/check")
def sources_check(body: UploadIn):
    sources, problems = _load_upload(body)
    return {"usable": len(sources), "sources": [s.as_dict() for s in sources],
            "problems": [p.__dict__ for p in problems]}


@app.post("/api/batch")
def batch_start(body: BatchIn):
    if body.render not in ("never", "auto", "always"):
        raise HTTPException(400, "render must be never, auto or always")
    sources, problems = _load_upload(body)
    if not sources:
        raise HTTPException(400, "No usable colleges in the file: " + "; ".join(p.problem for p in problems[:5]))
    job = BatchJob(body, sources)
    BATCHES[job.id] = job
    import base64
    _safe(store().create_run, job.id, "batch", f"{body.filename} ({len(sources)} colleges)",
          body.model_dump(exclude={"content_base64"}), body.filename, base64.b64decode(body.content_base64))
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": job.id, "colleges": len(sources), "problems": [p.__dict__ for p in problems]}


@app.get("/api/active")
def active():
    """The latest single crawl and batch, so a reloaded page can pick them up again."""
    def last(d):
        if not d:
            return None
        job = list(d.values())[-1]
        return {"id": job.id, "state": _state(job)}
    return {"job": last(JOBS), "batch": last(BATCHES)}


@app.get("/api/batch/{job_id}/doctors")
def batch_doctors(job_id: str):
    """People and offices found so far in a batch (finished colleges plus the one being crawled)."""
    job = BATCHES.get(job_id) or _missing("batch")
    live = job.live
    docs = list(live["merger"].records) if live.get("merger") else []
    running = list((live.get("active") or {}).values())
    offices = list(live.get("offices") or [])
    from .batch import _map_affiliation_record
    for crawler, src in running:
        for d in list(crawler.merger.records):
            _map_affiliation_record(d, src, job.sources)
            docs.append(d)
        offices += list(crawler.offices)
    if not job.body.all_people:
        docs = [d for d in docs if d.is_relevant]
    return {"doctors": _rows(docs), "offices": _office_rows(offices)}


@app.get("/api/batch/{job_id}")
def batch_status(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    now = time.time()
    with job.progress_lock:
        rows = []
        for r in job.rows.values():
            rc = dict(r)
            if rc.get("status") == "crawling" and rc.get("started_at"):
                rc["duration"] = round(max(0.0, now - rc["started_at"]), 1)
            rows.append(rc)
        sites = sorted((dict(s) for s in job.sites), key=lambda s: s["row"])
    state = _state(job)
    terminal = state in {"done", "stopped", "failed"}
    if terminal:
        for row in rows:
            if row["status"] in {"queued", "crawling"}:
                row["status"] = "not started" if row["status"] == "queued" else "interrupted"
    completed = sum(r["status"] in {"done", "resumed", "failed", "error"} for r in rows)
    active_rows = [r for r in rows if r["status"] == "crawling"]
    queued = sum(r["status"] == "queued" for r in rows)
    failed = sum(r["status"] in {"failed", "error"} for r in rows)
    total_colleges = len(rows)
    elapsed = max(0.0, job.elapsed())
    total_pages = sum(r.get("pages", 0) for r in rows)
    total_discovered = sum(max(r.get("discovered", 0), r.get("pages", 0) + r.get("queued", 0)) for r in rows)
    total_faculty = sum(r.get("faculty", 0) for r in rows)
    with_email = sum(r.get("with_email", 0) for r in rows)
    with_phone = sum(r.get("with_phone", 0) for r in rows)
    dups_removed = sum(r.get("duplicates_removed", 0) for r in rows)
    speed_pages_min = round((total_pages / elapsed) * 60.0, 1) if elapsed >= 1 else 0.0
    avg_college_sec = round(elapsed / completed, 1) if completed > 0 else 0.0
    remaining_colleges = max(0, total_colleges - completed)
    eta_seconds = round(remaining_colleges * (avg_college_sec / max(1, len(active_rows) or 1))) if completed > 0 else None
    progress_pct = round((completed / total_colleges) * 100.0, 1) if total_colleges > 0 else 0.0

    return {
        "id": job.id,
        "state": state,
        "phase": job.phase,
        "error": job.error,
        "current": ", ".join(r["institution"] for r in active_rows),
        "index": job.index,
        "total": total_colleges,
        "elapsed": round(elapsed),
        "progress": {"fetched": total_pages, "faculty": total_faculty},
        "completed": completed,
        "active": len(active_rows),
        "queued": queued,
        "failed": failed,
        "urls_discovered": total_discovered,
        "urls_processed": total_pages,
        "valid_emails": with_email,
        "valid_phones": with_phone,
        "duplicates_removed": dups_removed,
        "speed_pages_per_min": speed_pages_min,
        "avg_time_per_college": avg_college_sec,
        "progress_pct": progress_pct,
        "eta_seconds": eta_seconds,
        "running_colleges": active_rows,
        "rows": rows,
        "sites": sites,
        "stats": job.stats,
        "log": list(job.log)[-60:],
        "console": list(job.console)[-80:],
    }


@app.post("/api/batch/{job_id}/stop")
def batch_stop(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    job.stop.set()
    job._emit_console("Batch stop requested by user.")
    return {"ok": True}


@app.post("/api/batch/{job_id}/pause")
def pause_batch(job_id: str):
    job = BATCHES.get(job_id) or _missing("batch")
    return _pause(job, True)


@app.post("/api/batch/{job_id}/resume")
def resume_batch(job_id: str):
    job = BATCHES.get(job_id) or _missing("batch")
    return _pause(job, False)


@app.post("/api/batch/{job_id}/retry-failed")
def batch_retry_failed(job_id: str):
    """Retry only failed/interrupted/error colleges in a batch without re-scraping completed ones."""
    if _live_state(job_id) in ("running", "paused"):
        raise HTTPException(409, "Stop or wait for the current run before retrying failed colleges")
    return run_resume(job_id)


@app.get("/api/batch/{job_id}/download.xlsx")
def batch_download(job_id: str):
    job = BATCHES.get(job_id)
    if not job:
        raise HTTPException(404, "batch not found")
    if not job.xlsx:  # still running: write what has been found so far
        if not job.live.get("merger"):
            raise HTTPException(404, "nothing found yet")
        from .batch import snapshot_xlsx
        OUT_DIR.mkdir(exist_ok=True)
        part = OUT_DIR / f"batch-{job.id}-partial-{time.strftime('%H%M%S')}.xlsx"
        try:
            snapshot_xlsx(job.live, part)
        except Exception as exc:
            raise HTTPException(500, f"could not write partial Excel: {exc}") from None
        return FileResponse(part, filename=part.name,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return FileResponse(job.xlsx, filename=job.xlsx.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Portal: run history, faculty directory, student-office directory, dashboard (all from the local database)
def _live_state(run_id: str) -> str | None:
    job = JOBS.get(run_id) or BATCHES.get(run_id)
    if job:
        return _state(job)
    if _PROMPT_MANAGER is not None:
        saved = _PROMPT_MANAGER.db.get(run_id)
        if saved:
            return saved["state"]
    return None


@app.get("/api/runs")
def runs_list():
    runs = store().list_runs()
    for r in runs:
        r["state"] = _live_state(r["id"]) or r["state"]
        r["live"] = r["id"] in JOBS or r["id"] in BATCHES
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    r = store().get_run(run_id) or _missing("run")
    r["state"] = _live_state(run_id) or r["state"]
    r["sites"] = store().sites(run_id)
    return r


@app.delete("/api/runs/{run_id}")
def run_delete(run_id: str):
    if (store().get_run(run_id) or {}).get("kind") == "prompt":
        manager = prompt_manager()
        saved = manager.db.get(run_id)
        from .prompt_jobs import ACTIVE
        if saved and saved["state"] in ACTIVE:
            raise HTTPException(409, "Stop the prompt run before deleting it")
        manager.db.delete(run_id)
        # Exact job-owned files only; never follow a filename supplied by a client.
        import shutil
        shutil.rmtree(OUT_DIR / f"prompt-{run_id}-raw_sites", ignore_errors=True)
        for suffix in (".xlsx", "-raw.xlsx", "-sources.xlsx"):
            (OUT_DIR / f"prompt-{run_id}{suffix}").unlink(missing_ok=True)
    if _live_state(run_id) in ("running", "paused"):
        raise HTTPException(409, "Stop the run before deleting it")
    store().delete_run(run_id)
    return {"ok": True}


@app.get("/api/runs/{run_id}/download.{fmt}")
def run_download(run_id: str, fmt: str):
    if (store().get_run(run_id) or {}).get("kind") == "prompt":
        if fmt != "xlsx":
            raise HTTPException(400, "Use the prompt Excel or evidence report")
        return prompt_download(run_id)
    if run_id in JOBS:
        return download(run_id, fmt)
    if run_id in BATCHES and fmt == "xlsx":
        return batch_download(run_id)
    r = store().get_run(run_id) or _missing("run")
    if fmt == "xlsx" and r.get("kind") != "batch" and r.get("xlsx") and Path(r["xlsx"]).is_file():
        p = Path(r["xlsx"])
        return FileResponse(p, filename=p.name, media_type=XLSX)
    docs, offices = store().run_people(run_id), store().run_offices(run_id)
    if not docs and not offices:
        raise HTTPException(404, "nothing saved for this run")
    return _export(docs, offices, fmt, f"run-{run_id}", sites=store().sites(run_id) or None)


def _export(docs: list[PersonRecord], offices: list[OfficeRecord], fmt: str, name: str, sites=None,
            only_fac: bool = True):
    if fmt == "csv":
        return Response(to_csv([d for d in docs if d.is_relevant or not only_fac]).encode("utf-8-sig"),
                        media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    res = CrawlResult([], docs, [], offices=offices)
    fac = res.faculty
    res.stats = {"people_found": len(docs), "faculty": len(fac), "student_offices": len(offices),
                 "faculty_with_own_email": sum(1 for d in fac if d.get("email")),
                 "faculty_with_own_phone": sum(1 for d in fac if d.get("phone"))}
    if fmt == "json":
        return Response(to_json(res, only_fac), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if fmt != "xlsx":
        raise HTTPException(400, "format must be xlsx, csv or json")
    OUT_DIR.mkdir(exist_ok=True)
    path = to_xlsx(res, OUT_DIR / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.xlsx",
                   sites=_reconcile_site_counts(sites, docs, offices) if sites else sites)
    return FileResponse(path, filename=path.name, media_type=XLSX)


@app.post("/api/runs/{run_id}/resume")
def run_resume(run_id: str):
    """Carry on a batch that was cut off when the app closed: finished colleges are not crawled again."""
    import base64
    if _live_state(run_id) in ("running", "paused"):
        raise HTTPException(409, "This run is still going")
    r = store().get_run(run_id) or _missing("run")
    if r["kind"] == "prompt":
        return prompt_action(run_id, "resume")
    if r["kind"] != "batch":
        raise HTTPException(400, "Only college-list batches can be resumed; use Run again for a single website")
    name, data = store().upload_of(run_id)
    if not data:
        raise HTTPException(400, "The college list of this run was not saved; start a new batch")
    settings = {k: v for k, v in r["settings"].items() if k not in ("filename", "content_base64")}
    body = BatchIn(filename=name or "list.xlsx", content_base64=base64.b64encode(data).decode(), **settings)
    sources, _ = _load_upload(body)
    job = BatchJob(body, sources, run_id=run_id, resume=True,
                   seconds_already_spent=(r.get("stats") or {}).get("seconds", 0))
    BATCHES[run_id] = job
    store().update_run(run_id, state="running", finished=None, error=None)
    threading.Thread(target=job.run, daemon=True).start()
    return {"id": run_id, "colleges": len(sources)}


@app.post("/api/runs/{run_id}/rerun")
def run_again(run_id: str):
    r = store().get_run(run_id) or _missing("run")
    if r["kind"] != "crawl":
        raise HTTPException(400, "Use Resume for a batch")
    return start(JobIn(**r["settings"]))


def _dir_filters(q: str = "", city: str = "", college: str = "", department: str = "", has_email: bool = False,
                 has_phone: bool = False, has_contact: bool = False, has_profile: bool = False,
                 all_people: bool = False) -> dict:
    return dict(q=q.strip(), city=city, college=college, department=department, has_email=has_email,
                has_phone=has_phone, has_contact=has_contact, has_profile=has_profile, faculty_only=not all_people)


@app.get("/api/directory")
def directory(q: str = "", city: str = "", college: str = "", department: str = "", has_email: bool = False,
              has_phone: bool = False, has_contact: bool = False, has_profile: bool = False,
              all_people: bool = False, page: int = 1):
    page = max(1, page)
    f = _dir_filters(q, city, college, department, has_email, has_phone, has_contact, has_profile, all_people)
    total, rows = store().directory(**f, limit=100, offset=(page - 1) * 100)
    docs = [PersonRecord.from_full_dict(r) for r in rows]
    return {"total": total, "page": page, "pages": max(1, -(-total // 100)), "doctors": _rows(docs, 100),
            "facets": store().facets()}


@app.get("/api/directory/download.{fmt}")
def directory_download(fmt: str, q: str = "", city: str = "", college: str = "", department: str = "",
                       has_email: bool = False, has_phone: bool = False, has_contact: bool = False,
                       has_profile: bool = False, all_people: bool = False):
    f = _dir_filters(q, city, college, department, has_email, has_phone, has_contact, has_profile, all_people)
    docs = store().directory_records(**f)
    if not docs:
        raise HTTPException(404, "No faculty match these filters")
    return _export(docs, [], fmt, "faculty-directory", only_fac=not all_people)


def _office_filters(q: str = "", college: str = "", office_type: str = "", has_email: bool = False,
                    has_phone: bool = False, has_figures: bool = False) -> dict:
    return dict(q=q.strip(), college=college, office_type=office_type, has_email=has_email, has_phone=has_phone,
                has_figures=has_figures)


@app.get("/api/offices")
def office_directory(q: str = "", college: str = "", office_type: str = "", has_email: bool = False,
                     has_phone: bool = False, has_figures: bool = False):
    rows = store().office_directory(**_office_filters(q, college, office_type, has_email, has_phone, has_figures))
    return {"total": len(rows), "offices": rows[:1000], "facets": store().facets()}


@app.get("/api/offices/download.{fmt}")
def office_download(fmt: str, q: str = "", college: str = "", office_type: str = "", has_email: bool = False,
                    has_phone: bool = False, has_figures: bool = False):
    offices = store().office_records(**_office_filters(q, college, office_type, has_email, has_phone, has_figures))
    if not offices:
        raise HTTPException(404, "No student offices match these filters")
    if fmt == "csv":
        import csv
        import io
        from .export import OFFICE_COLUMNS, OFFICE_HEADERS, _csv_safe, office_row
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([OFFICE_HEADERS.get(c) or c for c in OFFICE_COLUMNS])
        for o in offices:
            w.writerow([_csv_safe(v) for v in office_row(o)])
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="student-offices.csv"'})
    return _export([], offices, fmt, "student-offices")


@app.get("/api/dashboard")
def dashboard():
    return store().dashboard()


# ---------------------------------------------------------------------------
# New Platform Endpoints: Live Monitor, Failed Websites, URL Discovery,
# Quality Control, Background Excel Export Center, System Performance, Settings
# ---------------------------------------------------------------------------
@app.get("/api/monitor/live")
def monitor_live():
    """Unified real-time telemetry across active batch/crawl jobs and historical runs."""
    batch = list(BATCHES.values())[-1] if BATCHES else None
    job = list(JOBS.values())[-1] if JOBS else None

    if batch is not None and (_state(batch) in ("running", "paused") or job is None):
        st = batch_status(batch.id)
        return {
            "mode": "batch",
            "active_id": batch.id,
            "state": st["state"],
            "phase": st["phase"],
            "total_colleges": st["total"],
            "completed_colleges": st["completed"],
            "running_colleges_count": st["active"],
            "pending_colleges": st["queued"],
            "failed_colleges": st["failed"],
            "urls_discovered": st["urls_discovered"],
            "urls_processed": st["urls_processed"],
            "total_faculty": st["progress"]["faculty"],
            "valid_emails": st["valid_emails"],
            "valid_phones": st["valid_phones"],
            "duplicates_removed": st["duplicates_removed"],
            "scraping_speed": st["speed_pages_per_min"],
            "avg_time_per_college": st["avg_time_per_college"],
            "progress_pct": st["progress_pct"],
            "eta_seconds": st["eta_seconds"],
            "elapsed": st["elapsed"],
            "running_colleges": st["running_colleges"],
            "rows": st["rows"],
            "console": st["console"],
            "log": st["log"],
        }

    if job is not None:
        st = status(job.id)
        prog = st["progress"]
        done = st["state"] in ("done", "stopped", "failed")
        pct = 100.0 if done else min(99.0, round((prog.get("fetched", 0) / max(1, st["max_pages"])) * 100.0, 1))
        running_item = {
            "row": 1,
            "sequence": 1,
            "institution": job.site_name or "Website Crawl",
            "website": job.body.urls[0] if job.body.urls else "",
            "stage": "Completed" if done else "Crawling & Extracting",
            "discovered": prog.get("discovered", prog.get("fetched", 0) + prog.get("queued", 0)),
            "pages": prog.get("fetched", 0),
            "faculty": prog.get("faculty", 0),
            "status": st["state"],
            "duration": st["elapsed"],
        }
        return {
            "mode": "crawl",
            "active_id": job.id,
            "state": st["state"],
            "phase": st["state"],
            "total_colleges": 1,
            "completed_colleges": 1 if done and st["state"] != "failed" else 0,
            "running_colleges_count": 0 if done else 1,
            "pending_colleges": 0,
            "failed_colleges": 1 if st["state"] == "failed" else 0,
            "urls_discovered": running_item["discovered"],
            "urls_processed": prog.get("fetched", 0),
            "total_faculty": prog.get("faculty", 0),
            "valid_emails": prog.get("with_email", 0),
            "valid_phones": prog.get("with_phone", 0),
            "duplicates_removed": prog.get("duplicates_removed", 0),
            "scraping_speed": st["pages_per_min"],
            "avg_time_per_college": st["elapsed"],
            "progress_pct": pct,
            "eta_seconds": 0 if done else max(5, round((st["max_pages"] - prog.get("fetched", 0)) * 1.5)),
            "elapsed": st["elapsed"],
            "running_colleges": [] if done else [running_item],
            "rows": [running_item],
            "console": st["console"],
            "log": st["log"],
        }

    # Fallback: summarize latest stored batch run from DB so the monitor always shows rich telemetry
    runs = store().list_runs(limit=10)
    latest_batch = next((r for r in runs if r.get("kind") == "batch"), runs[0] if runs else None)
    if latest_batch:
        sites = store().sites(latest_batch["id"])
        stats = latest_batch.get("stats") or {}
        total_c = len(sites) or stats.get("colleges", 0)
        failed_c = sum(1 for s in sites if s.get("status") in ("failed", "error"))
        done_c = sum(1 for s in sites if s.get("status") in ("done", "resumed"))
        pages_p = sum(s.get("pages", 0) for s in sites) or stats.get("pages_fetched", 0)
        fac_t = sum(s.get("faculty", 0) for s in sites) or stats.get("faculty", 0)
        emails_t = sum(s.get("with_email", 0) for s in sites) or stats.get("faculty_with_own_email", 0)
        phones_t = sum(s.get("with_phone", 0) for s in sites) or stats.get("faculty_with_own_phone", 0)
        dups_t = stats.get("duplicate_rows_removed", 0) or stats.get("shared_source_duplicates_merged", 0)
        secs = max(1, stats.get("seconds", 1))
        return {
            "mode": "history",
            "active_id": latest_batch["id"],
            "state": latest_batch["state"],
            "phase": "idle",
            "total_colleges": total_c,
            "completed_colleges": done_c,
            "running_colleges_count": 0,
            "pending_colleges": max(0, total_c - done_c - failed_c),
            "failed_colleges": failed_c,
            "urls_discovered": pages_p,
            "urls_processed": pages_p,
            "total_faculty": fac_t,
            "valid_emails": emails_t,
            "valid_phones": phones_t,
            "duplicates_removed": dups_t,
            "scraping_speed": round((pages_p / secs) * 60.0, 1),
            "avg_time_per_college": round(secs / max(1, total_c), 1),
            "progress_pct": round(((done_c + failed_c) / max(1, total_c)) * 100.0, 1) if total_c else 100.0,
            "eta_seconds": 0,
            "elapsed": secs,
            "running_colleges": [],
            "rows": [
                {
                    "row": s.get("row", i + 1),
                    "sequence": i + 1,
                    "institution": s.get("institution", ""),
                    "website": s.get("start_urls", ""),
                    "stage": s.get("status", "done").title(),
                    "discovered": s.get("pages", 0),
                    "pages": s.get("pages", 0),
                    "faculty": s.get("faculty", 0),
                    "status": s.get("status", "done"),
                    "duration": round(secs / max(1, total_c), 1),
                    "note": s.get("note", ""),
                }
                for i, s in enumerate(sites[:250])
            ],
            "console": [f"[{_ts()}] Monitoring ready. Showing last run {latest_batch['id']} ({latest_batch.get('title', '')})."],
            "log": [],
        }

    return {
        "mode": "idle",
        "active_id": None,
        "state": "idle",
        "phase": "idle",
        "total_colleges": 0,
        "completed_colleges": 0,
        "running_colleges_count": 0,
        "pending_colleges": 0,
        "failed_colleges": 0,
        "urls_discovered": 0,
        "urls_processed": 0,
        "total_faculty": 0,
        "valid_emails": 0,
        "valid_phones": 0,
        "duplicates_removed": 0,
        "scraping_speed": 0.0,
        "avg_time_per_college": 0.0,
        "progress_pct": 0.0,
        "eta_seconds": None,
        "elapsed": 0,
        "running_colleges": [],
        "rows": [],
        "console": [f"[{_ts()}] Live monitor ready. Start a scrape or batch to view real-time telemetry."],
        "log": [],
    }


@app.get("/api/colleges")
def colleges_management(q: str = "", status_filter: str = ""):
    """Comprehensive college management view combining dashboard stats and batch site statuses."""
    dash = store().dashboard()
    all_sites = store().all_sites(limit=2500)
    site_by_inst: dict[str, dict] = {}
    for s in all_sites:
        inst = (s.get("institution") or "").strip()
        if inst and inst not in site_by_inst:
            site_by_inst[inst] = s

    merged: list[dict] = []
    seen: set[str] = set()
    for c in dash["colleges"]:
        name = c["c"]
        seen.add(name)
        s = site_by_inst.get(name, {})
        st = s.get("status") or "done"
        item = {
            "college": name,
            "faculty": c.get("fac", 0),
            "with_email": c.get("email", 0),
            "with_phone": c.get("phone", 0),
            "with_contact": c.get("contact", 0),
            "with_profile": c.get("profile", 0),
            "offices": c.get("offices", 0),
            "pages": s.get("pages", 0),
            "status": st,
            "start_urls": s.get("start_urls", ""),
            "note": s.get("note", ""),
            "run_id": s.get("run_id", ""),
            "last_updated": c.get("last"),
        }
        merged.append(item)

    for inst, s in site_by_inst.items():
        if inst not in seen:
            merged.append({
                "college": inst,
                "faculty": s.get("faculty", 0),
                "with_email": s.get("with_email", 0),
                "with_phone": s.get("with_phone", 0),
                "with_contact": s.get("with_any_contact", 0),
                "with_profile": 0,
                "offices": s.get("offices", 0),
                "pages": s.get("pages", 0),
                "status": s.get("status", "unknown"),
                "start_urls": s.get("start_urls", ""),
                "note": s.get("note", ""),
                "run_id": s.get("run_id", ""),
                "last_updated": s.get("run_started"),
            })

    if q.strip():
        ql = q.strip().lower()
        merged = [m for m in merged if ql in m["college"].lower() or ql in (m["start_urls"] or "").lower() or ql in (m["note"] or "").lower()]
    if status_filter.strip():
        sf = status_filter.strip().lower()
        merged = [m for m in merged if (m["status"] or "").lower() == sf]

    return {
        "total": len(merged),
        "colleges": merged[:1000],
        "states": sorted(STATES.keys()),
    }


@app.get("/api/failed-websites")
def failed_websites():
    """List all failed/partial/blocked college websites and URLs across active and historical runs."""
    all_sites = store().all_sites(limit=3000)
    failed_sites = []
    for s in all_sites:
        st = (s.get("status") or "").lower()
        note = s.get("note") or ""
        if st in ("failed", "error", "interrupted", "stopped") or (s.get("faculty", 0) == 0 and note):
            failed_sites.append({
                "run_id": s.get("run_id", ""),
                "run_title": s.get("run_title", ""),
                "row": s.get("row", 0),
                "institution": s.get("institution", ""),
                "status": s.get("status", "failed"),
                "pages": s.get("pages", 0),
                "faculty": s.get("faculty", 0),
                "start_urls": s.get("start_urls", ""),
                "note": note or "No faculty extracted or connection error",
            })

    # Also include live page errors from active jobs
    live_errors = []
    for job in list(JOBS.values()) + list(BATCHES.values()):
        for entry in list(job.log):
            if entry.get("error") or (isinstance(entry.get("status"), int) and entry.get("status", 0) >= 400):
                live_errors.append({
                    "job_id": job.id,
                    "url": entry.get("url", ""),
                    "status": entry.get("status", 0),
                    "error": entry.get("error") or f"HTTP {entry.get('status')}",
                    "institution": entry.get("institution", getattr(job, "site_name", "")),
                })

    return {
        "total_failed_colleges": len(failed_sites),
        "failed_colleges": failed_sites[:500],
        "live_url_errors": live_errors[-200:],
    }


class RetryWebsitesIn(BaseModel):
    urls: list[str] = Field(default_factory=list)
    run_id: str | None = None
    render: str = "auto"


@app.post("/api/failed-websites/retry")
def retry_failed_websites(body: RetryWebsitesIn):
    """Retry a specific run's failed colleges or launch a new recovery crawl for selected failed URLs."""
    if body.run_id:
        r = store().get_run(body.run_id)
        if r and r.get("kind") == "batch":
            return run_resume(body.run_id)
        if r and r.get("kind") == "crawl":
            return run_again(body.run_id)
    clean_urls = [u.strip() for u in body.urls if u and u.strip()][:20]
    if not clean_urls:
        # Collect up to 15 failed start URLs from failed_websites
        fw = failed_websites()["failed_colleges"]
        for item in fw:
            for u in (item.get("start_urls") or "").split():
                if u.startswith("http") and u not in clean_urls:
                    clean_urls.append(u)
                    if len(clean_urls) >= 15:
                        break
            if len(clean_urls) >= 15:
                break
    if not clean_urls:
        raise HTTPException(400, "No failed website URLs found to retry")
    return start(JobIn(urls=clean_urls, render=body.render, max_pages=150))


@app.get("/api/urls/discovered")
def discovered_urls(q: str = "", limit: int = 300):
    """Return discovered internal URLs, source URLs, and profile URLs across live and stored runs."""
    items: list[dict] = []
    seen: set[str] = set()

    for job in list(BATCHES.values()) + list(JOBS.values()):
        for entry in reversed(list(job.log)):
            u = entry.get("url") or ""
            if u.startswith("http") and u not in seen:
                seen.add(u)
                items.append({
                    "url": u,
                    "type": entry.get("type", "page"),
                    "status": entry.get("status", 200),
                    "doctors": entry.get("doctors", 0),
                    "college": entry.get("institution", getattr(job, "site_name", "")),
                    "error": entry.get("error", ""),
                })

    if len(items) < limit:
        _, rows = store().directory(faculty_only=True, limit=min(500, limit * 2), offset=0)
        for r in rows:
            su = r.get("source_url") or ""
            pu = r.get("profile_url") or ""
            col = r.get("source_institution") or r.get("institution") or ""
            for u, utype in ((su, "listing"), (pu, "profile")):
                if u and u.startswith("http") and u not in seen:
                    seen.add(u)
                    items.append({
                        "url": u,
                        "type": utype,
                        "status": 200,
                        "doctors": 1,
                        "college": col,
                        "error": "",
                    })
                    if len(items) >= limit * 2:
                        break

    if q.strip():
        ql = q.strip().lower()
        items = [i for i in items if ql in i["url"].lower() or ql in (i["college"] or "").lower() or ql in (i["type"] or "").lower()]

    return {"total": len(items), "urls": items[:limit]}


@app.get("/api/quality/summary")
def quality_summary(college: str = "", department: str = "", city: str = "", state: str = "", run_id: str = ""):
    """Run the data-quality validation and confidence audit before Excel export."""
    if run_id:
        docs = store().run_people(run_id)
    else:
        _, raw_rows = store().directory(
            college=college, department=department, city=city, faculty_only=True, limit=50000, offset=0
        )
        if state and state in STATES:
            state_cities = {c.lower() for c in STATES[state]}
            state_l = state.lower()
            raw_rows = [
                r for r in raw_rows
                if (r.get("city") or "").lower() in state_cities
                or state_l in (r.get("source_institution") or "").lower()
                or state_l in (r.get("institution") or "").lower()
            ]
        docs = raw_rows
    return audit_records(docs)


class ExportStartIn(BaseModel):
    scope: str = "master"  # master | state | college | department | run
    state: str = ""
    college: str = ""
    department: str = ""
    city: str = ""
    q: str = ""
    run_id: str = ""
    has_email: bool = False
    has_phone: bool = False
    has_contact: bool = False
    has_profile: bool = False
    all_people: bool = False


@app.post("/api/exports/start")
def start_async_export(body: ExportStartIn):
    """Start a non-blocking background Excel export job with deduplication and automatic validation."""
    sig = json.dumps(body.model_dump(), sort_keys=True)
    title_parts = [body.scope.title()]
    if body.state:
        title_parts.append(body.state)
    if body.college:
        title_parts.append(body.college)
    if body.department:
        title_parts.append(body.department)
    if body.run_id:
        title_parts.append(f"Run {body.run_id}")
    title = " - ".join(title_parts)

    def _loader():
        if body.scope == "run" and body.run_id:
            docs = store().run_people(body.run_id)
            offs = store().run_offices(body.run_id)
            sites = store().sites(body.run_id) or None
            if sites:
                sites = _reconcile_site_counts(sites, docs, offs)
            return docs, offs, sites

        f = _dir_filters(
            body.q, body.city, body.college, body.department,
            body.has_email, body.has_phone, body.has_contact, body.has_profile, body.all_people,
        )
        docs = store().directory_records(**f)
        offs = store().office_records(college=body.college)
        if body.state and body.state in STATES:
            state_cities = {c.lower() for c in STATES[body.state]}
            state_l = body.state.lower()
            docs = [
                d for d in docs
                if (d.get("city") or "").lower() in state_cities
                or state_l in (d.source_institution or "").lower()
                or state_l in (d.get("institution") or "").lower()
            ]
            offs = [
                o for o in offs
                if (o.city or "").lower() in state_cities
                or state_l in (o.source_institution or "").lower()
                or state_l in (o.institution or "").lower()
            ]
        return docs, offs, None

    prefix = f"campus-{body.scope}-{body.state or body.college or body.run_id or 'all'}".lower()
    prefix = "".join(c if c.isalnum() or c == "-" else "-" for c in prefix).strip("-")[:45] or "campus-export"
    job = export_manager().start_export(sig, title, _loader, filename_prefix=prefix)
    return job


@app.get("/api/exports")
def list_async_exports():
    return {"jobs": export_manager().list_jobs(), "states": sorted(STATES.keys())}


@app.get("/api/exports/{job_id}")
def get_async_export(job_id: str):
    job = export_manager().get_job(job_id) or _missing("export job")
    return job


@app.get("/api/exports/{job_id}/download.xlsx")
def download_async_export(job_id: str):
    job = export_manager().get_job(job_id) or _missing("export job")
    if job["state"] != "done" or not job.get("path") or not Path(job["path"]).is_file():
        raise HTTPException(409, "Export file is still being generated or failed")
    p = Path(job["path"])
    return FileResponse(p, filename=p.name, media_type=XLSX)


@app.get("/api/system/performance")
def system_performance():
    snap = MONITOR.snapshot(record_history=True)
    db_path = store().path
    db_mb = round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.is_file() else 0.0
    return {
        "current": snap.to_dict(),
        "history": MONITOR.history(limit=60),
        "database_mb": db_mb,
        "active_batches": sum(1 for b in BATCHES.values() if _state(b) in ("running", "paused")),
        "active_crawls": sum(1 for j in JOBS.values() if _state(j) in ("running", "paused")),
    }


@app.get("/api/settings")
def get_settings():
    with _SETTINGS_Lock:
        return {"settings": dict(_RUNTIME_SETTINGS), "defaults": dict(_DEFAULT_SETTINGS)}


class SettingsIn(BaseModel):
    max_pages: int = Field(200, ge=1, le=20000)
    pages_per_site: int = Field(150, ge=1, le=5000)
    max_depth: int = Field(4, ge=0, le=10)
    delay_seconds: float = Field(1.5, ge=0, le=30)
    timeout_seconds: float = Field(25.0, ge=5, le=120)
    workers: int = Field(4, ge=1, le=32)
    colleges_at_once: int = Field(4, ge=1, le=32)
    patience: int = Field(80, ge=0, le=5000)
    render: str = "auto"
    use_sitemaps: bool = True
    include_subdomains: bool = False
    include_offices: bool = True
    adaptive_concurrency: bool = True
    respect_robots: bool = True


@app.post("/api/settings")
def update_settings(body: SettingsIn):
    if body.render not in ("never", "auto", "always"):
        raise HTTPException(400, "render must be never, auto or always")
    with _SETTINGS_Lock:
        _RUNTIME_SETTINGS.update(body.model_dump())
        return {"ok": True, "settings": dict(_RUNTIME_SETTINGS)}


# Prompt discovery is isolated from upload/single-site jobs, and shares their
# extraction engine and final history only after filtering is complete.
_PROMPT_MANAGER = None
_PROMPT_LOCK = threading.RLock()


def prompt_manager():
    global _PROMPT_MANAGER
    from .discovery_services import DiscoveryDB
    from .prompt_jobs import PromptManager
    with _PROMPT_LOCK:
        history = store()
        path = history.path.with_name("prompt-discovery.db")
        if _PROMPT_MANAGER is None or _PROMPT_MANAGER.db.path != path:
            if _PROMPT_MANAGER is not None:
                _PROMPT_MANAGER.close()
            _PROMPT_MANAGER = PromptManager(DiscoveryDB(path), history, OUT_DIR)
            _PROMPT_MANAGER.start_worker()
        return _PROMPT_MANAGER


from .prompt_jobs import PromptIn  # noqa: E402 - keep the workflow separate from legacy endpoints


@app.get("/api/discovery/config")
def prompt_config():
    from .discovery_services import settings
    config = settings()
    config["ready"] = config["ollama_configured"] or config["groq_configured"] or config["gemini_configured"]
    # This is configuration presence, not a claim that the remote APIs are healthy.
    return config


@app.post("/api/discovery")
def prompt_start(body: PromptIn):
    from .discovery_services import ConfigurationError
    try:
        job = prompt_manager().create(body)
    except ConfigurationError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"id": job["id"], "state": job["state"]}


@app.get("/api/discovery")
def prompt_list():
    return {"jobs": [{k: j.get(k) for k in ("id", "state", "phase", "started", "finished", "body")}
                     for j in prompt_manager().db.jobs()]}


@app.get("/api/discovery/{job_id}")
def prompt_status(job_id: str):
    manager = prompt_manager()
    job = manager.db.get(job_id) or _missing("prompt run")
    job["download_ready"] = bool(job.get("xlsx") and Path(job["xlsx"]).is_file())
    job.pop("xlsx", None)
    job["usage"] = manager.db.stats(job_id) or job.get("usage", [])
    return job


@app.get("/api/discovery/{job_id}/download.xlsx")
def prompt_download(job_id: str):
    job = prompt_manager().db.get(job_id) or _missing("prompt run")
    if not job.get("xlsx") or not Path(job["xlsx"]).is_file():
        raise HTTPException(409, "The filtered Excel is not ready. Check the run stage; progress is saved")
    path = Path(job["xlsx"])
    return FileResponse(path, filename=path.name, media_type=XLSX)


@app.get("/api/discovery/{job_id}/sources.xlsx")
def prompt_sources(job_id: str):
    from .prompt_jobs import export_sources
    job = prompt_manager().db.get(job_id) or _missing("prompt run")
    if not job.get("report", {}).get("candidates"):
        raise HTTPException(409, "No source pages found yet")
    path = export_sources(job, OUT_DIR / f"prompt-{job_id}-sources.xlsx")
    return FileResponse(path, filename=path.name, media_type=XLSX)


@app.get("/api/discovery/{job_id}/evidence.json")
def prompt_evidence(job_id: str):
    manager = prompt_manager()
    job = manager.db.get(job_id) or _missing("prompt run")
    return Response(json.dumps({"job": {key: job.get(key) for key in ("id", "state", "phase", "error", "retry_at", "progress")},
                                "report": job["report"], "filter_decisions": manager.db.cached(job_id, "filter_audit") or [],
                                "usage": manager.db.stats(job_id) or job.get("usage", [])}, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="prompt-{job_id}-evidence.json"'})


@app.post("/api/discovery/{job_id}/{action}")
def prompt_action(job_id: str, action: str):
    try:
        job = prompt_manager().action(job_id, action)
    except KeyError:
        raise HTTPException(404, "Prompt run not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": job_id, "state": job["state"], "kind": "prompt"}


# Registry-first seeding, beside the existing prompt-discovery workflow.
# Guarded, so the portal starts unchanged when seedsources is absent.
try:
    from .seed_discovery import register as _register_seeds

    _register_seeds(app, BatchIn, BatchJob, BATCHES, store, _safe, _load_upload)
except Exception as _seed_error:  # pragma: no cover - import-time guard
    app.state.seed_error = str(_seed_error)
