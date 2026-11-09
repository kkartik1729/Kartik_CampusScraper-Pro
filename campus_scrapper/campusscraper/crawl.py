"""Same-site crawler: sitemap + link discovery, prioritised toward faculty, department and student-office pages."""
from __future__ import annotations

import gzip
import hashlib
import heapq
import itertools
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .config import ScrapeConfig
from .contacts import demote_shared, demote_sitewide
from .dom import same_site
from .extract import extract_page
from .fetch import Fetcher, normalize_url
from . import relevance
from .models import OfficeRecord, Page, PersonRecord, PageResult
from .name_suffix import strip_shared_name_suffix
from .profile import add_contacts, clean_fields
from .vocab import (
    FOCUS_URL_HINTS, LINK_TEXT_HINTS, LISTING_URL_HINTS, OFFICE_URL_HINTS, PAGINATION_RE, PROFILE_URL_HINTS, SKIP_URL,
)

log = logging.getLogger(__name__)


def name_key(name: str) -> str:
    n = re.sub(r"^(?:(?:dr|prof|professor|doctor|mr|mrs|ms|miss|shri|sri|smt|kum|er)\.?\s+)+", "", (name or "").lower())
    n = re.sub(r"\((?:dr|prof|col|brig|maj|lt|gen)[^)]*\)\s*", "", n)
    n = re.sub(r"\b(?:brig|col|lt|maj|gen|capt)\.?\s+", "", n)
    n = re.sub(r"\b(?:dr|prof|professor|mr|mrs|ms|miss)\b\.?", "", n)
    return re.sub(r"[^a-z]", "", n)


def loose_key(name: str) -> str | None:
    """Name without initials: 'Dr. Ch. Mohana Vamsy' and 'Dr. Mohana Vamsy' -> 'mohanavamsy'.
    Needs two full words left, so 'Dr. A. Kumar' and 'Dr. B. Kumar' never meet."""
    n = re.sub(r"^(?:(?:dr|prof|professor|doctor|mr|mrs|ms|miss|shri|sri|smt|kum|er)\.?\s+)+", "", (name or "").lower())
    words = [w for w in re.split(r"[\s.]+", n) if len(re.sub(r"[^a-z]", "", w)) > 2]
    return "".join(re.sub(r"[^a-z]", "", w) for w in words) if len(words) >= 2 else None


def _seen_key(url: str) -> str:
    """www.site.in/x and site.in/x are the same page; crawl it once."""
    return re.sub(r"^(https?://)www\.", r"\1", url, flags=re.I)


def has_initials(name: str) -> bool:
    n = re.sub(r"^(?:(?:dr|prof|professor|doctor|mr|mrs|ms|miss|shri|sri|smt|kum|er)\.?\s+)+", "", (name or "").lower())
    n = re.sub(r"\b(?:dr|prof|professor|mr|mrs|ms|miss)\b\.?", "", n)
    return any(len(re.sub(r"[^a-z]", "", w)) <= 2 for w in re.split(r"[\s.]+", n) if re.sub(r"[^a-z]", "", w))


def url_key(url: str | None) -> str | None:
    if not url:
        return None
    p = urlsplit(url)
    host = (p.hostname or "").removeprefix("www.")
    return f"{host}{p.path.rstrip('/').lower()}" + (f"?{p.query}" if p.query else "")


class Merger:
    """Collects records and merges duplicates of the same doctor."""

    def __init__(self):
        self.records: list[PersonRecord] = []
        self._by_url: dict[str, PersonRecord] = {}
        self._by_name: dict[str, list[PersonRecord]] = {}
        self._by_loose: dict[str, list[PersonRecord]] = {}
        self.duplicates_merged: int = 0

    def add(self, rec: PersonRecord) -> PersonRecord:
        key_u = url_key(rec.profile_url)
        key_n = name_key(rec.get("name", ""))
        target = self._by_url.get(key_u) if key_u else None
        if target is not None and (name_key(target.get("name", "")) != key_n or
                                   (target.source_institution and rec.source_institution and
                                    target.source_institution != rec.source_institution)):
            target = None  # Shared/reassigned pages are not proof that two names identify one person.
        if target is None and key_n:
            loose = loose_key(rec.get("name", ""))
            exact = self._by_name.get(key_n, [])
            candidates = [(c, True) for c in exact] + [(c, False) for c in self._by_loose.get(loose, []) if c not in exact]
            for cand, is_exact in candidates:
                h1, h2 = (cand.get("institution") or "").lower(), (rec.get("institution") or "").lower()
                same_hosp = not h1 or not h2 or h1[:12] == h2[:12] or h1 in h2 or h2 in h1
                s1, s2 = cand.source_institution, rec.source_institution
                u1, u2 = url_key(cand.profile_url), key_u
                c1, c2 = cand.get("city"), rec.get("city")
                # A card and a profile on one site can label the institution differently
                # ('IIT Bombay' / 'Indian Institute of Technology Bombay'). With the same full name,
                # the same site and the same city, the label is not evidence of two people.
                host1 = (urlsplit(cand.source_url).hostname or "").lower().removeprefix("www.")
                host2 = (urlsplit(rec.source_url).hostname or "").lower().removeprefix("www.")
                same_city = bool(c1 and c2 and c1.strip().casefold() == c2.strip().casefold())
                card_profile = {cand.page_type, rec.page_type} == {"listing", "profile"}
                if is_exact and len(key_n) >= 8 and card_profile and host1 and host1 == host2 and same_city:
                    same_hosp = True
                same_hosp = same_hosp and (not s1 or not s2 or s1 == s2)
                # A link to the doctor's own off-site website is not a second profile on this site.
                urls_ok = not u1 or not u2 or u1 == u2 or u1.split("/", 1)[0] != u2.split("/", 1)[0]
                if is_exact:
                    # Repeated full names on the same college site may use different profile URLs.
                    # Preserve initials and conflicting campus/city identities.
                    same_site_name = bool(cand.source_url and rec.source_url) and urlsplit(cand.source_url).hostname == urlsplit(rec.source_url).hostname
                    email1, email2 = cand.get("email"), rec.get("email")
                    shared_email = email1 and email2 and set(email1.split("; ")) & set(email2.split("; "))
                    ok = urls_ok or bool(shared_email) or (same_site_name and len(key_n) >= 8 and
                         not has_initials(rec.get("name", "")) and not (c1 and c2 and c1 != c2))
                else:
                    # Only initials differ ('Ch. Mohana Vamsy' / 'Mohana Vamsy'): one side must have no initials,
                    # so 'K. Srinivas Rao' and 'P. Srinivas Rao' stay two doctors.
                    ok = urls_ok and (not has_initials(cand.get("name", "")) or not has_initials(rec.get("name", "")))
                if same_hosp and ok:
                    target = cand
                    break
        if target is None:
            self.records.append(rec)
            target = rec
        elif target is not rec:
            self.duplicates_merged += 1
            # Keep every attributed contact when a verified person has both a directory
            # address and a different address/number in their CV or profile.
            base, other = (rec, target) if rec.page_type == "profile" and target.page_type != "profile" else (target, rec)
            for field in ("email", "phone", "contact_email", "contact_phone"):
                if other.get(field):
                    add_contacts(base, field, str(other.get(field)).split("; "),
                                 other.layers.get(field, "patterns"), "Same person's published contact", other.source_url)
            # A profile-page record should become the base.
            if rec.page_type == "profile" and target.page_type != "profile":
                rec.absorb(target, fill_only=True)
                rec_found = target.found_on + [target.source_url]
                target.values, target.layers, target.evidence = rec.values, rec.layers, rec.evidence
                target.source_url, target.page_type = rec.source_url, "profile"
                target.profile_url = rec.profile_url or target.profile_url
                target.relevance_score = max(rec.relevance_score, target.relevance_score)
                target.relevance_terms = list(dict.fromkeys(rec.relevance_terms + target.relevance_terms))
                target.found_on = [u for u in dict.fromkeys(rec_found) if u and u != target.source_url]
            else:
                target.absorb(rec, fill_only=target.page_type == "profile" and rec.page_type != "profile")
        for k in {url_key(target.profile_url), key_u} - {None}:
            self._by_url[k] = target
        if key_n:
            bucket = self._by_name.setdefault(key_n, [])
            if target not in bucket:
                bucket.append(target)
        lk = loose_key(rec.get("name", ""))
        if lk:
            bucket = self._by_loose.setdefault(lk, [])
            if target not in bucket:
                bucket.append(target)
        return target


@dataclass
class PageLog:
    url: str
    status: int
    page_type: str
    doctors: int
    depth: int
    note: str = ""


@dataclass
class CrawlResult:
    start_urls: list[str]
    doctors: list[PersonRecord] = field(default_factory=list)   # every person found
    pages: list[PageLog] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    offices: list[OfficeRecord] = field(default_factory=list)

    @property
    def people(self) -> list[PersonRecord]:
        return self.doctors

    @property
    def faculty(self) -> list[PersonRecord]:
        return [d for d in self.doctors if d.is_relevant]


def merge_offices(existing: list[OfficeRecord], new: list[OfficeRecord]) -> None:
    by_key = {o.key(): o for o in existing}
    for o in new:
        k = o.key()
        if k in by_key:
            by_key[k].absorb(o)
        else:
            existing.append(o)
            by_key[k] = o


class Crawler:
    def __init__(self, cfg: ScrapeConfig, progress: Callable[[dict], None] | None = None,
                 stop_event: threading.Event | None = None, pause_event: threading.Event | None = None):
        self.cfg = cfg.validate()
        self.pause_event = pause_event or threading.Event()  # set = paused
        self.stop_event = stop_event or threading.Event()
        self.fetcher = Fetcher(cfg, self.stop_event)
        self.progress = progress or (lambda e: None)
        self._frontier: list[tuple[float, int, str, int, bool]] = []
        self._seq = itertools.count()
        self._queued: set[str] = set()
        self._visited: set[str] = set()
        self._content_hashes: set[str] = set()
        self.duplicate_pages: int = 0
        self.merger = Merger()
        self.offices: list[OfficeRecord] = []
        self._focus_profile_urls: set[str] = set()
        self._pdf_people: dict[str, PersonRecord | None] = {}
        self._path_scopes: dict[str, str] = {}
        self.value_pages: dict[str, set[str]] = {}
        self.result_pages: list = []  # phone/email -> pages that show it
        self.blocked: list[str] = []
        # Wall-clock limit for one run. A slow site then costs a known number of
        # seconds instead of max_pages x timeout, and what was read is kept.
        self.deadline: float | None = None

    # -- frontier -----------------------------------------------------------
    def _score(self, url: str, depth: int, from_focus: bool, hint: str = "") -> float | None:
        p = urlsplit(url)
        path = p.path + ("?" + p.query if p.query else "")
        if hint == "start":
            return 1000.0  # URLs given by the user are always fetched first, whatever their path
        if p.path.lower().endswith(".pdf") and hint in ("profile", "focus_profile"):
            return 210.0 - 4 * depth  # Only PDFs linked from an identified person's card/row.
        if re.search(r"special[-_/]placement|placement[-_/](?:history|records?|highlights?|stories)|faculty[-_/]achievements?|student[-_/]chapter[-_/]activities", path, re.I):
            return None
        if SKIP_URL.search(path) or SKIP_URL.search(url):
            return None
        s = 10.0
        if PROFILE_URL_HINTS.search(p.path):
            s += 50
        if LISTING_URL_HINTS.search(path):
            s += 35
        if re.search(r"facult(?:y|ies)(?:[-_/]?(?:new|list|members|profile|directory))?/?$|/department[s]?/[^/]+/?$|/(?:people|staff|management|academic-staff|leadership|administration)/?$", p.path, re.I):
            s += 250  # Discover all departments and directories before spending the budget on individual profiles.
        elif hint == "directory":
            s += 180
        if FOCUS_URL_HINTS.search(path):
            s += 30
        if self.cfg.include_offices and OFFICE_URL_HINTS.search(path):
            s += 30
        if PAGINATION_RE.search(path):
            s += 25
        if from_focus:
            s += 15
        if hint == "focus_profile":
            s += 80
        elif hint == "profile":
            s += 20
        elif hint == "pagination":
            s += 40
        for pat in self.cfg.extra_profile_patterns:
            if pat and pat in p.path:
                s += 50
        return s - 4 * depth

    def _push(self, url: str, depth: int, from_focus: bool = False, hint: str = "", start: str = "") -> None:
        try:
            url = normalize_url(url)
        except ValueError:
            return
        if urlsplit(url).path.lower().endswith(".pdf") and url not in self._pdf_people:
            return
        if not self._within_path_scope(url):
            return
        if _seen_key(url) in self._queued or depth > self.cfg.max_depth:
            return
        if start and not same_site(url, start, self.cfg.include_subdomains):
            return
        score = self._score(url, depth, from_focus, hint)
        if score is None:
            return
        self._queued.add(_seen_key(url))
        try:
            if not self.fetcher.robots_for(url).allowed(url):
                self.blocked.append(url)
                return
        except Exception:  # robots fetch problems are handled again at fetch time
            pass
        heapq.heappush(self._frontier, (-score, next(self._seq), url, depth, from_focus))

    def _within_path_scope(self, url: str) -> bool:
        p = urlsplit(url)
        prefix = self._path_scopes.get(p.netloc)
        return not prefix or p.path == prefix.rstrip("/") or p.path.startswith(prefix) or url in self._pdf_people

    def _set_path_scopes(self, starts: list[str]) -> None:
        # Two or more supplied pages beneath one campus folder establish its scope.
        # Generic site-wide faculty/department folders do not establish a campus boundary.
        by_host: dict[str, list[str]] = {}
        for u in starts:
            p = urlsplit(u)
            by_host.setdefault(p.netloc, []).append(p.path)
        generic = re.compile(r"^(?:departments?|facult(?:y|ies)|academics?|people|staff|schools?|"
                             r"profiles?|about|contact|en|in|index\.php)$", re.I)
        for host, paths in by_host.items():
            parts = {p.strip("/").split("/")[0] for p in paths}
            if len(set(paths)) >= 2 and len(parts) == 1 and all(len(p.strip("/").split("/")) >= 2 for p in paths):
                part = next(iter(parts))
                if part and not generic.fullmatch(part):
                    self._path_scopes[host] = "/" + part + "/"

    # -- sitemaps -----------------------------------------------------------
    def _sitemap_urls(self, start: str) -> list[str]:
        origin = f"{urlsplit(start).scheme}://{urlsplit(start).netloc}"
        robots = self.fetcher.robots_for(start)
        if robots.crawl_delay >= 10:
            self.progress({"event": "sitemap", "urls": 0, "note": "skipped sitemap prefetch: site requests slow pacing; following supplied pages and links"})
            return []
        todo = list(dict.fromkeys(robots.sitemaps + [origin + "/sitemap.xml"]))
        found: list[str] = []
        fetched = 0
        while todo and fetched < 12 and len(found) < 50000:
            sm = todo.pop(0)
            fetched += 1
            try:
                self.fetcher.check_allowed(sm)
                resp = self.fetcher._raw_get(sm)  # noqa: SLF001
            except Exception as exc:
                log.info("sitemap %s skipped: %s", sm, exc)
                continue
            if resp.status_code != 200:
                continue
            body = resp.content
            if sm.endswith(".gz") or body[:2] == b"\x1f\x8b":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    continue
            try:
                root = ElementTree.fromstring(body)
            except ElementTree.ParseError:
                continue
            locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
            if root.tag.endswith("sitemapindex"):
                # Doctor sitemaps first.
                locs.sort(key=lambda u: 0 if re.search(r"faculty|staff|people|team|profile|department|page", u, re.I) else 1)
                todo.extend(locs[:40])
            else:
                found.extend(locs)
        self.progress({"event": "sitemap", "urls": len(found)})
        return found

    # -- main loop ------------------------------------------------------------
    def run(self, start_urls: list[str]) -> CrawlResult:
        from .system_monitor import MONITOR
        starts = [normalize_url(u if re.match(r"https?://", u) else "https://" + u) for u in start_urls]
        self._set_path_scopes(starts)
        result = CrawlResult(starts)
        self.result_pages = result.pages  # live page log, read by the UI for partial downloads
        t0 = time.time()
        sitemap_origins = set()
        for s in starts:
            self._queued.discard(_seen_key(s))
            self._push(s, 0, hint="start")
            origin = (urlsplit(s).scheme, urlsplit(s).netloc)
            if self.cfg.use_sitemaps and origin not in sitemap_origins:
                sitemap_origins.add(origin)
                for u in self._sitemap_urls(s):
                    if same_site(u, s, self.cfg.include_subdomains):
                        path = urlsplit(u).path
                        if PROFILE_URL_HINTS.search(path) or LISTING_URL_HINTS.search(path) or (
                                self.cfg.include_offices and OFFICE_URL_HINTS.search(path)) or any(
                                p in path for p in self.cfg.extra_profile_patterns if p):
                            self._push(u, 1, from_focus=bool(FOCUS_URL_HINTS.search(path)), start=s)
        state = {"fetched": 0, "in_flight": 0, "last_new": 0, "patience_stop": False}
        counts = {"profile": 0, "listing": 0, "other": 0, "error": 0}
        lock = threading.Lock()

        def take():
            """Next URL to fetch, or None when this worker should stop. Called with the lock held."""
            while True:
                if self.deadline and time.time() >= self.deadline:
                    return None
                if self.stop_event.is_set() or state["fetched"] >= self.cfg.max_pages or state["patience_stop"]:
                    return None
                if not self._frontier:
                    return "" if state["in_flight"] else None  # "" = wait: a page in flight may add links
                _, _, url, depth, from_focus = heapq.heappop(self._frontier)
                if _seen_key(url) in self._visited:
                    continue
                self._visited.add(_seen_key(url))
                state["fetched"] += 1
                state["in_flight"] += 1
                return url, depth, from_focus

        def handle(url, depth, from_focus, page, pr: PageResult) -> None:
            """Record one fetched page and queue its links. Called with the lock held."""
            fetched = state["fetched"]
            if page.final_url != url:
                self._visited.add(_seen_key(page.final_url))
            for v in pr.signals.get("page_contacts", []):
                self.value_pages.setdefault(v, set()).add(page.final_url)
            counts[pr.page_type] = counts.get(pr.page_type, 0) + 1
            note = page.error or ("rendered" if page.rendered else "")
            result.pages.append(PageLog(page.final_url, page.status, pr.page_type, len(pr.doctors), depth, note))
            start = next((s for s in starts if same_site(page.final_url, s, self.cfg.include_subdomains)), starts[0])
            page_focus = bool(FOCUS_URL_HINTS.search(urlsplit(page.final_url).path)) or any(d.is_relevant for d in pr.doctors) or from_focus
            before = (len(self.merger.records), len(self.offices),
                      sum(len(r.filled_fields()) for r in self.merger.records))
            merge_offices(self.offices, pr.offices)
            for rec in pr.doctors:
                merged = self.merger.add(rec)
                if merged.is_relevant and merged.profile_url:
                    self._focus_profile_urls.add(normalize_url(merged.profile_url))
            after = (len(self.merger.records), len(self.offices),
                     sum(len(r.filled_fields()) for r in self.merger.records))
            if after != before:
                state["last_new"] = fetched
            elif self.cfg.patience and fetched - state["last_new"] >= self.cfg.patience:
                state["patience_stop"] = True  # many pages in a row without new people, offices, or populated fields
            if pr.page_type != "error":
                for d in pr.doctors:
                    if d.page_type == "listing" and d.profile_url:
                        if urlsplit(d.profile_url).path.lower().endswith(".pdf"):
                            key = normalize_url(d.profile_url)
                            if key in self._pdf_people and (self._pdf_people[key] is None or
                                    name_key(self._pdf_people[key].get("name", "")) != name_key(d.get("name", ""))):
                                self._pdf_people[key] = None  # A shared brochure is not an individual CV.
                            else:
                                self._pdf_people[key] = PersonRecord.from_full_dict(d.to_full_dict())
                        self._push(d.profile_url, depth + 1, page_focus, "focus_profile" if d.is_relevant else "profile", start)
                for u in pr.pagination_links:
                    self._push(u, depth, page_focus, "pagination", start)
                for u in pr.profile_links:
                    self._push(u, depth + 1, page_focus, "profile", start)
                for u in pr.links:
                    path = urlsplit(u).path
                    text = pr.link_texts.get(u, "")
                    directory = bool(re.search(r"facult|department|\bdept\b|teaching staff|people|management|academic staff", text, re.I))
                    if (LISTING_URL_HINTS.search(path) or FOCUS_URL_HINTS.search(path) or directory or PROFILE_URL_HINTS.search(path)
                            or (self.cfg.include_offices and OFFICE_URL_HINTS.search(path + " " + text))
                            or (pr.page_type != "profile" and (LINK_TEXT_HINTS.search(text) or depth == 0))):
                        self._push(u, depth + 1, page_focus or bool(FOCUS_URL_HINTS.search(text)), "directory" if directory else "", start)
            fac_records = [d for d in self.merger.records if d.is_relevant]
            self.progress({
                "event": "page",
                "url": page.final_url,
                "type": pr.page_type,
                "status": page.status,
                "doctors_on_page": len(pr.doctors),
                "fetched": fetched,
                "queued": len(self._frontier),
                "discovered": len(self._queued),
                "doctors_total": len(self.merger.records),
                "faculty": len(fac_records),
                "with_email": sum(1 for d in fac_records if d.get("email")),
                "with_phone": sum(1 for d in fac_records if d.get("phone")),
                "duplicates_removed": self.merger.duplicates_merged + self.duplicate_pages,
                "offices": len(self.offices),
                "error": page.error,
            })

        def worker(worker_idx: int) -> None:
            MONITOR.adjust_workers(1)
            try:
                relevance.set_targets(self.cfg.departments)
                while True:
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.3)  # paused: keep everything found so far and wait for Resume
                    if self.cfg.adaptive_concurrency and worker_idx >= 2:
                        snap = MONITOR.snapshot(record_history=False)
                        if snap.throttled and worker_idx >= snap.recommended_workers:
                            time.sleep(0.4)
                    with lock:
                        job = take()
                    if job is None:
                        return
                    if job == "":
                        time.sleep(0.05)
                        continue
                    url, depth, from_focus = job
                    try:
                        page = self.fetcher.get(url)  # network and parsing happen outside the lock
                        if not self._within_path_scope(page.final_url):
                            page = Page(url, page.final_url, page.status, "", error="redirect outside supplied campus path")
                        if not page.error and page.html and len(page.html) > 250:
                            sig = hashlib.md5(page.html.encode("utf-8", errors="ignore")).hexdigest()
                            with lock:
                                if sig in self._content_hashes and depth > 0:
                                    self.duplicate_pages += 1
                                    page = Page(url, page.final_url, page.status, "", error="duplicate page content skipped")
                                else:
                                    self._content_hashes.add(sig)
                        if page.content_type == "application/pdf":
                            from .pdf_profiles import extract_faculty_pdf
                            pr = extract_faculty_pdf(page, self._pdf_people.get(url))
                        elif page.error == "duplicate page content skipped":
                            pr = PageResult(page.final_url, "other", signals={"note": "duplicate_content"})
                        else:
                            pr = extract_page(page)
                            # A faculty or staff page that named nobody is worth one
                            # look through the browser: the list is often drawn by
                            # JavaScript into a page that already carries its menu.
                            if (not pr.doctors and not page.rendered and self.cfg.render == "auto"
                                    and LISTING_URL_HINTS.search(urlsplit(page.final_url).path)):
                                shown = self.fetcher.render_now(page)
                                if shown is not None:
                                    drawn = extract_page(shown)
                                    if drawn.doctors or drawn.offices:
                                        page, pr = shown, drawn
                    except Exception as exc:  # one bad page must not stop the site
                        log.warning("page %s failed: %s", url, exc)
                        page = Page(url, url, 0, "", error=f"{type(exc).__name__}: {exc}")
                        pr = PageResult(url, "error", signals={"error": page.error})
                    with lock:
                        try:
                            handle(url, depth, from_focus, page, pr)
                        finally:
                            state["in_flight"] -= 1
            finally:
                MONITOR.adjust_workers(-1)

        try:
            threads = [threading.Thread(target=worker, args=(i,), daemon=True, name=f"crawl-{i}") for i in range(self.cfg.workers)]
            for t in threads:
                t.start()
            for t in threads:
                while t.is_alive():
                    t.join(0.2)
        except KeyboardInterrupt:
            self.stop_event.set()
            for t in threads:
                t.join(30)
        finally:
            self.fetcher.close()
        fetched = state["fetched"]
        result.doctors = self.merger.records
        if strip_shared_name_suffix(result.doctors):
            # 'Dr. Anitha Rao PESU' (card) and 'Dr. Anitha Rao' (profile) only meet once the acronym is gone.
            remerge = Merger()
            remerge.duplicates_merged = self.merger.duplicates_merged
            for d in result.doctors:
                remerge.add(d)
            self.merger = remerge
            result.doctors = remerge.records
        result.offices = self.offices
        caller_targets = relevance.targets()  # this thread may belong to the caller: put its filter back after
        relevance.set_targets(self.cfg.departments)
        for d in result.doctors:  # merged records: check the final title and department once more
            clean_fields(d)
            relevance.rescore(d)
        relevance.set_targets(caller_targets)
        demote_sitewide(result.doctors, self.value_pages)
        demote_shared(result.doctors)
        if self.stop_event.is_set():
            stop_reason = "user_stopped"
        elif not fetched and self.blocked:
            stop_reason = "robots_blocked"
        elif fetched >= self.cfg.max_pages:
            stop_reason = "page_limit"
        elif state["patience_stop"]:
            stop_reason = "no_new_data"
        else:
            stop_reason = "queue_exhausted"
        fac_list = result.faculty
        result.stats = {
            "pages_fetched": fetched,
            "urls_discovered": len(self._queued),
            "requests": self.fetcher.requests_made,
            "page_types": counts,
            "people_found": len(result.doctors),
            "faculty": len(fac_list),
            "faculty_with_own_email": sum(1 for d in fac_list if d.get("email")),
            "faculty_with_own_phone": sum(1 for d in fac_list if d.get("phone")),
            "duplicates_removed": self.merger.duplicates_merged + self.duplicate_pages,
            "duplicate_pages_skipped": self.duplicate_pages,
            "offices": len(result.offices),
            "profiles_fetched": sum(1 for d in result.doctors if d.page_type == "profile"),
            "seconds": round(time.time() - t0, 1),
            "stopped_early": self.stop_event.is_set(),
            "frontier_left": len(self._frontier),
            "blocked_by_robots": len(self.blocked),
            "crawl_delays": {origin: max(self.cfg.delay_seconds, policy.crawl_delay)
                             for origin, policy in self.fetcher._robots.items()},
            "stopped_no_new_people": state["patience_stop"],
            "stop_reason": stop_reason,
        }
        self.progress({"event": "done", **result.stats})
        return result


def scrape_page(url: str, cfg: ScrapeConfig | None = None) -> PageResult:
    """Fetch and extract a single URL without crawling."""
    cfg = (cfg or ScrapeConfig()).validate()
    fetcher = Fetcher(cfg)
    try:
        return extract_page(fetcher.get(url))
    finally:
        fetcher.close()
