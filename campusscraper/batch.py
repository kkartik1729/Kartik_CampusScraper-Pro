"""Crawl a list of colleges one by one and combine the results.

Input: a college list in the standard template (.xlsx or .csv, see sources.py).
Each college gets its own page budget and settings. Results are saved per
college as they finish, so an interrupted run resumes without re-crawling
finished sites.
"""
from __future__ import annotations

import json
import hashlib
from collections import Counter
from difflib import SequenceMatcher
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import Callable

from .config import ScrapeConfig
from .contacts import demote_shared, demote_sitewide
from .crawl import Crawler, CrawlResult, Merger, PageLog, merge_offices, name_key
from .export import to_xlsx
from .models import OfficeRecord, PersonRecord
from .sources import Source


EXTRACTION_REVISION = 2
AFFILIATION_TABLE_REVISION = 1

_AFFILIATION_MANAGEMENT_SUBJECT = re.compile(
    r"\b(?:mba|management|business|commerce|account(?:ancy|ing)?|finance|banking|marketing|"
    r"human\s+resources?|hrm|organizational?\s+behavio[u]r|operations?|quantitative\s+techniques?|"
    r"business\s+analytics|economics|econometrics|entrepreneurship|strategy|supply\s+chain|"
    r"international\s+business|industrial\s+relations|business\s+law|business\s+communication|"
    r"information\s+systems|retail|hospitality)\b", re.I,
)
_COLLEGE_STOPWORDS = {"a", "an", "and", "of", "the", "at", "in", "to", "for", "college", "institute",
                     "institution", "university", "school", "faculty", "department", "mba", "bba",
                     "management", "business", "studies", "gujarat", "north", "hemchandracharya", "shri",
                     "sri", "smt", "arts", "science", "commerce", "education", "patel", "shah", "desai",
                     "mehta", "parikh", "parekh", "bhatt", "sharma", "modi", "trivedi", "jain"}


def _teaching_role(designation: str) -> bool:
    """Recognize teaching ranks without treating an administrative post as faculty."""
    return bool(re.search(r"prof(?:essor)?|lecturer|adhyapak|reader|faculty|assistant|associate", designation or "", re.I))


def _administrative_only_role(designation: str) -> bool:
    """Identify administrative titles when no teaching rank is also stated."""
    return bool(re.search(r"\b(?:principal|director|dean|vice[- ]?chancellor|chancellor|registrar|controller|librarian)\b",
                          designation or "", re.I) and not _teaching_role(designation))


def _college_tokens(value: str, *, keep_common: bool = False) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (value or "").casefold())
    filler = {"of", "the", "and", "at", "in", "to", "for"}
    if keep_common:
        return [w for w in words if w not in filler]
    return [w for w in words if w not in _COLLEGE_STOPWORDS]


def _college_match_score(page_name: str, target_name: str) -> float:
    """Conservative match for an official affiliation name against the uploaded college list."""
    raw_a = _college_tokens(page_name, keep_common=True)
    raw_b = _college_tokens(target_name, keep_common=True)
    if not raw_a or not raw_b:
        return 0.0
    full_a, full_b = "".join(raw_a), "".join(raw_b)
    if full_a == full_b:
        return 1.0
    if min(len(full_a), len(full_b)) >= 12 and (full_a in full_b or full_b in full_a):
        return 0.98
    initials_a = "".join(w[0] for w in raw_a if w not in {"of", "the", "and", "to", "for", "at", "in", "shri", "sri", "smt"})
    initials_b = "".join(w[0] for w in raw_b if w not in {"of", "the", "and", "to", "for", "at", "in", "shri", "sri", "smt"})
    if initials_a and any(len(token) >= 4 and (initials_a.startswith(token) or token.startswith(initials_a))
                          for token in raw_b):
        return 0.95
    if initials_b and any(len(token) >= 4 and (initials_b.startswith(token) or token.startswith(initials_b))
                          for token in raw_a):
        return 0.95
    a, b = _college_tokens(page_name), _college_tokens(target_name)
    shared = set(a) & set(b)
    if len(shared) >= 2:
        return 0.9 + min(0.08, len(shared) / max(len(set(a)), len(set(b))) * 0.08)
    if min(len(full_a), len(full_b)) / max(len(full_a), len(full_b)) < 0.92:
        return 0.0
    ratio = SequenceMatcher(None, full_a, full_b).ratio()
    return ratio if ratio >= 0.96 else 0.0


def _map_affiliation_record(record: PersonRecord, source: Source, sources: list[Source]) -> None:
    """Keep central affiliation rows, but count only named colleges and management subjects."""
    affiliation_evidence = next((e for e in reversed(record.evidence)
                                if e.field == "institution" and e.layer == "affiliation_table"), None)
    if record.layers.get("institution") != "affiliation_table" and affiliation_evidence is None:
        record.source_institution = source.institution
        return
    listed_college = str((affiliation_evidence.value if affiliation_evidence else record.get("institution")) or "").strip()
    matches = [(score, candidate) for candidate in sources
               if (score := _college_match_score(listed_college, candidate.institution)) > 0]
    target = max(matches, key=lambda pair: pair[0])[1] if matches else None
    subjects = record.get("subjects") or []
    subject_text = "; ".join(map(str, subjects)) if isinstance(subjects, (list, tuple)) else str(subjects)
    designation = str(record.get("designation") or "")
    faculty_role = _teaching_role(designation)
    if target is None:
        record.source_institution = listed_college or "HNGU affiliation directory (unmapped college)"
        record.relevance_score = 0
        record.relevance_terms = ["college name is outside the uploaded Gujarat MBA college list"]
    else:
        record.source_institution = target.institution
        # Canonicalize the college value so spelling variants for the same registered college dedupe.
        record.values["institution"] = target.institution
        if not subject_text or not _AFFILIATION_MANAGEMENT_SUBJECT.search(subject_text) or not faculty_role:
            record.relevance_score = 0
            record.relevance_terms = ["college matched; affiliation subject or teaching role not MBA-relevant"]
        else:
            record.relevance_score = max(1.0, record.relevance_score)
            record.relevance_terms = list(dict.fromkeys(record.relevance_terms + [
                "official affiliation row matches this college and a management subject"
            ]))


def normalize_legacy_affiliation_records(records: list[PersonRecord], sources: list[Source]) -> tuple[list[PersonRecord], int, int, int]:
    """Recover College Name and Subject from stored HNGU row evidence and dedupe repeated copies."""
    from .store import person_key

    match_cache: dict[str, Source | None] = {}
    normalized: dict[tuple[str, str], PersonRecord] = {}
    remapped = 0
    unparsed = 0
    for record in records:
        legacy_affiliation = ("ngu.ac.in/Affiliation-Teachers.aspx" in (record.source_url or "")
                              and record.layers.get("institution") != "affiliation_table")
        mapped_legacy = False
        if legacy_affiliation:
            for evidence in record.evidence:
                if evidence.field not in {"name", "designation"} or "|" not in evidence.snippet:
                    continue
                cells = [" ".join(part.split()) for part in evidence.snippet.split("|")]
                if len(cells) < 6 or name_key(cells[0]) != name_key(record.get("name") or ""):
                    continue
                listed_college, subject = cells[3], cells[5]
                if not listed_college:
                    continue
                if listed_college not in match_cache:
                    matches = [(score, src) for src in sources
                               if (score := _college_match_score(listed_college, src.institution)) > 0]
                    match_cache[listed_college] = max(matches, key=lambda item: item[0])[1] if matches else None
                target = match_cache[listed_college]
                mapped_college = target.institution if target else listed_college
                record.source_institution = mapped_college
                record.layers.pop("institution", None)
                record.set("institution", mapped_college, "affiliation_table", evidence.snippet, record.source_url)
                if subject:
                    record.set("subjects", [subject], "table_row", evidence.snippet, record.source_url)
                    record.set("department", subject, "table_row", evidence.snippet, record.source_url)
                designation = str(record.get("designation") or "")
                faculty_role = _teaching_role(designation)
                if (target and subject and _AFFILIATION_MANAGEMENT_SUBJECT.search(subject) and faculty_role):
                    record.relevance_score = max(1.0, record.relevance_score)
                    record.relevance_terms = list(dict.fromkeys(record.relevance_terms + [
                        "official affiliation row matches this college and a management subject"
                    ]))
                else:
                    record.relevance_score = 0
                    record.relevance_terms = ["official affiliation row does not verify MBA faculty at a listed college"]
                remapped += 1
                mapped_legacy = True
                break
            if not mapped_legacy:
                record.source_institution = "HNGU affiliation directory (unmapped legacy row)"
                record.values.pop("institution", None)
                record.layers.pop("institution", None)
                record.relevance_score = 0
                record.relevance_terms = ["stored row evidence could not verify the affiliated college or subject"]
                unparsed += 1
        key = person_key(record)
        if not name_key(record.get("name") or ""):
            continue
        existing = normalized.get(key)
        if existing is None:
            normalized[key] = record
        else:
            existing.absorb(record)
    deduplicated = len(records) - len(normalized)
    return list(normalized.values()), remapped, deduplicated, unparsed


def normalize_shared_source_records(records: list[PersonRecord], sources: list[Source]) -> tuple[list[PersonRecord], int, int]:
    """Collapse people repeated from shared university pages and verify college mapping.

    A batch may crawl the same university or regulator page from many affiliated-college
    rows. Shared email, profile, or page evidence identifies repeats; a person is counted
    as target-college faculty only when their published institution maps to one target.
    """
    from .crawl import name_key

    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    token_owner: dict[tuple[str, str], int] = {}

    def canonical_url(value: str) -> str:
        parts = urlsplit(value or "")
        host = (parts.hostname or "").lower().removeprefix("www.")
        path = re.sub(r"/+", "/", parts.path or "").rstrip("/").casefold()
        return f"{host}{path}"

    host_owners: dict[str, set[str]] = {}
    host_paths: dict[str, dict[str, set[str]]] = {}
    source_by_name = {source.institution: source for source in sources}
    for source in sources:
        for value in source.urls:
            parts = urlsplit(value)
            host = (parts.hostname or "").lower().removeprefix("www.")
            path = re.sub(r"/+", "/", parts.path or "/").rstrip("/").casefold()
            if not host:
                continue
            host_owners.setdefault(host, set()).add(source.institution)
            if path:
                host_paths.setdefault(host, {}).setdefault(path, set()).add(source.institution)

    def resolved_target(record: PersonRecord) -> Source | None:
        listed = str(record.get("institution") or "").strip()
        scores = [(score, candidate) for candidate in sources
                  if listed and (score := _college_match_score(listed, candidate.institution)) > 0]
        if scores:
            highest = max(score for score, _ in scores)
            winners = [candidate for score, candidate in scores if score == highest]
            if len(winners) == 1:
                return winners[0]
        official = set()
        for value in [record.source_url, *record.found_on]:
            parts = urlsplit(value or "")
            host = (parts.hostname or "").lower().removeprefix("www.")
            if not host:
                continue
            owners = host_owners.get(host, set())
            if len(owners) == 1:
                official.update(owners)
            path = re.sub(r"/+", "/", parts.path or "/").rstrip("/").casefold()
            for start_path, owners in host_paths.get(host, {}).items():
                if start_path and (path == start_path or path.startswith(start_path + "/")):
                    official.update(owners)
        if len(official) == 1:
            return source_by_name.get(next(iter(official)))
        return None

    for i, record in enumerate(records):
        name = name_key(record.get("name") or "")
        if not name:
            continue
        tokens = set()
        for field, value in (("profile", record.profile_url), ("page", record.source_url)):
            normalized = canonical_url(value or "")
            if normalized:
                tokens.add((field, normalized))
        email_value = record.get("email") or ""
        email_items = email_value if isinstance(email_value, (list, tuple)) else re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(email_value), re.I)
        for email in email_items:
            normalized = str(email).strip().casefold()
            if normalized:
                tokens.add(("email", normalized))
        for field, value in tokens:
            key = (name, f"{field}:{value}")
            if key in token_owner:
                union(i, token_owner[key])
            else:
                token_owner[key] = i

    clusters: dict[int, list[PersonRecord]] = {}
    for i, record in enumerate(records):
        clusters.setdefault(find(i), []).append(record)

    output: list[PersonRecord] = []
    merged_count = 0
    unmapped_shared = 0
    for cluster in clusters.values():
        if len(cluster) == 1:
            output.append(cluster[0])
            continue
        if len({record.source_institution for record in cluster if record.source_institution}) <= 1:
            # Same-college duplicates are already handled by person_key; retain separate
            # records here if they do not represent a shared cross-college source.
            output.extend(cluster)
            continue

        targets = {target.institution: target for record in cluster if (target := resolved_target(record))}
        target = next(iter(targets.values())) if len(targets) == 1 else None

        representative = max(cluster, key=lambda d: (d.is_relevant, len(d.filled_fields()), len(d.evidence)))
        for record in cluster:
            if record is not representative:
                representative.absorb(record)
                for evidence in record.evidence:
                    if evidence not in representative.evidence:
                        representative.evidence.append(evidence)
        merged_count += len(cluster) - 1
        if target:
            representative.source_institution = target.institution
            representative.values["institution"] = target.institution
            if _administrative_only_role(str(representative.get("designation") or "")):
                representative.relevance_score = 0
                representative.relevance_terms = ["administrative title; excluded from Faculty"]
        else:
            actual = str(representative.get("institution") or "").strip()
            representative.source_institution = actual or "Unmapped shared source"
            representative.relevance_score = 0
            representative.relevance_terms = ["shared source record does not verify faculty at a listed MBA college"]
            unmapped_shared += 1
        if _administrative_only_role(str(representative.get("designation") or "")):
            representative.relevance_score = 0
            representative.relevance_terms = ["administrative title; excluded from Faculty"]
        output.append(representative)

    # Catch duplicate captures where the college published the same person on more than
    # one page (for example, www and non-www URLs), while retaining conflicting profiles.
    by_name_institution: dict[tuple[str, str], list[PersonRecord]] = {}
    for record in output:
        name = name_key(record.get("name") or "")
        institution = name_key(str(record.get("institution") or ""))
        if name and institution:
            by_name_institution.setdefault((name, institution), []).append(record)
    removed = set()
    for group in by_name_institution.values():
        if len(group) < 2 or len({record.source_institution for record in group}) < 2:
            continue
        profiles = {canonical_url(record.profile_url or "") for record in group if record.profile_url}
        emails = {str(email).strip().casefold() for record in group
                  for email in (record.get("email") if isinstance(record.get("email"), (list, tuple))
                                else re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(record.get("email") or ""), re.I))
                  if email}
        if len(profiles) > 1 or len(emails) > 1:
            continue
        representative = max(group, key=lambda d: (d.is_relevant, len(d.filled_fields()), len(d.evidence)))
        for record in group:
            if record is representative:
                continue
            representative.absorb(record)
            for evidence in record.evidence:
                if evidence not in representative.evidence:
                    representative.evidence.append(evidence)
            removed.add(id(record))
        merged_count += len(group) - 1
    output = [record for record in output if id(record) not in removed]

    # A university can link to external departments and affiliated campuses. Keep those
    # records in All people, but count them as Faculty only when the institution field or
    # a target-specific official page verifies the MBA college.
    for record in output:
        target = resolved_target(record)
        if target:
            record.source_institution = target.institution
            record.values["institution"] = target.institution
        elif record.is_relevant:
            actual = str(record.get("institution") or "").strip()
            record.source_institution = actual or record.source_institution or "Unverified source"
            record.relevance_score = 0
            record.relevance_terms = ["source evidence does not verify this person belongs to a listed MBA college"]
            unmapped_shared += 1
        if _administrative_only_role(str(record.get("designation") or "")):
            record.relevance_score = 0
            record.relevance_terms = ["administrative title; excluded from Faculty"]

    return output, merged_count, unmapped_shared


def _cache_key(src, base, per_site_pages):
    settings = {**base.__dict__, "max_pages": src.max_pages or per_site_pages}
    if not settings.get("strict_public_network"):
        settings.pop("strict_public_network", None)  # retain ordinary pre-discovery resume fingerprints
    ngu_source = any((urlsplit(url).hostname or "").lower().removeprefix("www.").endswith("ngu.ac.in")
                     for url in src.urls)
    payload = {"revision": EXTRACTION_REVISION,
               "affiliation_table_revision": AFFILIATION_TABLE_REVISION if ngu_source else 0,
               "source": src.as_dict(), "settings": settings}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "site"


def _reason(note: str) -> str:
    """Short, readable reason for a failed page."""
    n = note.lower()
    if "robots.txt" in n:
        return "blocked by robots.txt"
    if "certificate" in n or "ssl" in n:
        return "SSL certificate error"
    if "proxy" in n:
        return "proxy error"
    if "timeout" in n or "timed out" in n:
        return "timeout"
    if "connecterror" in n or "getaddrinfo" in n or "name resolution" in n:
        return "cannot connect"
    m = re.search(r"http (\d{3})", n)
    if m:
        return f"HTTP {m.group(1)}" + (" even in browser" if "real browser" in n else "")
    return note[:80] or "unknown"


def _failure_note(failed: list[dict], total: int) -> str:
    """Readable summary. Dead links (404/410) are links on the college's own site to pages
    that no longer exist; they are reported separately from real failures."""
    reasons = Counter(_reason(p.get("note") or "") for p in failed)
    dead = reasons.pop("HTTP 404", 0) + reasons.pop("HTTP 410", 0)
    parts = []
    if dead:
        parts.append(f"{dead} dead link{'s' if dead > 1 else ''} on the site (404, page removed)")
    for r, n in reasons.most_common(3):
        label = {"HTTP 500": "server error (500)", "HTTP 502": "server error (502)", "HTTP 503": "server busy (503)"}.get(r, r)
        parts.append(f"{n} {label}")
    if not dead or reasons:
        return f"{sum(reasons.values())} of {total} pages could not be read: " + "; ".join(parts)
    return "; ".join(parts)


def snapshot_xlsx(live: dict, out: str | Path) -> Path:
    """Write what has been found so far (finished colleges + the one in progress)."""
    sources = live.get("sources") or []
    merger = Merger()
    for d in list(live.get("merger").records if live.get("merger") else []):
        merger.add(PersonRecord.from_full_dict(d.to_full_dict()))
    pages = list(live.get("pages") or [])
    summary = [dict(r) for r in live.get("summary") or []]
    offices = [OfficeRecord.from_full_dict(o.to_full_dict()) for o in list(live.get("offices") or [])]
    running = list((live.get("active") or {}).values())
    if not running and live.get("crawler") is not None and live.get("src") is not None:
        running = [(live["crawler"], live["src"])]
    for crawler, src in running:
        current = [PersonRecord.from_full_dict(d.to_full_dict()) for d in list(crawler.merger.records)]
        demote_sitewide(current, {k: set(v) for k, v in list(crawler.value_pages.items())})
        for c in current:
            _map_affiliation_record(c, src, sources)
            if c.layers.get("institution") != "affiliation_table" and not c.get("institution"):
                c.set("institution", src.institution, "college list", "College Name column of the uploaded list", c.source_url)
            merger.add(c)
        cur_offices = [OfficeRecord.from_full_dict(o.to_full_dict()) for o in list(crawler.offices)]
        for o in cur_offices:
            o.source_institution = src.institution
        merge_offices(offices, cur_offices)
    demote_shared(merger.records)
    for crawler, src in running:  # counted after shared desk numbers are moved
        mine = [d for d in merger.records if d.source_institution == src.institution]
        fac = [d for d in mine if d.is_relevant]
        summary.append({"row": src.row, "institution": src.institution, "status": "in progress",
                        "pages": "", "people": len(mine), "faculty": len(fac),
                        "offices": sum(1 for o in offices if o.source_institution == src.institution),
                        "with_phone": sum(1 for d in fac if d.get("phone")),
                        "with_email": sum(1 for d in fac if d.get("email")),
                        "with_any_contact": sum(1 for d in fac if any(d.get(f) for f in CONTACT_FIELDS)),
                        "start_urls": " ".join(src.urls), "note": "partial: crawl still running"})
    merger.records, shared_duplicates, unmapped_shared = normalize_shared_source_records(merger.records, sources)
    for site in summary:
        mine = [d for d in merger.records if d.source_institution == site.get("institution")]
        faculty_here = [d for d in mine if d.is_relevant]
        site.update({
            "people": len(mine), "faculty": len(faculty_here),
            "with_phone": sum(bool(d.get("phone")) for d in faculty_here),
            "with_email": sum(bool(d.get("email")) for d in faculty_here),
            "with_any_contact": sum(any(d.get(f) for f in CONTACT_FIELDS) for d in faculty_here),
        })
    result = CrawlResult([u for s in sources for u in s.urls], merger.records, pages, offices=offices)
    fac = result.faculty
    result.stats = {
        "partial": "yes - downloaded while the batch was running",
        "colleges_processed": len(live.get("summary") or []),
        "colleges_failed": sum(s["status"] in {"failed", "error"} for s in summary), "colleges_total": len(sources),
        "pages_fetched": len(pages), "people_found": len(result.doctors), "faculty": len(fac),
        "student_offices": len(offices),
        "faculty_with_own_phone": sum(1 for d in fac if d.get("phone")),
        "faculty_with_own_email": sum(1 for d in fac if d.get("email")),
        "faculty_with_any_contact": sum(1 for d in fac if any(d.get(f) for f in CONTACT_FIELDS)),
        "shared_source_duplicates_merged": shared_duplicates,
        "unmapped_shared_source_records": unmapped_shared,
        "seconds": round(time.time() - live.get("t0", time.time()), 1),
    }
    return to_xlsx(result, out, sites=sorted(summary, key=lambda s: s["row"]))


CONTACT_FIELDS = ("phone", "email", "contact_phone", "contact_email")


def _hosts(src: Source) -> set[str]:
    return {(urlsplit(u).hostname or "").removeprefix("www.") for u in src.urls}


def run_batch(sources: list[Source], out: str | Path, base: ScrapeConfig, per_site_pages: int,
              resume: bool = True, progress: Callable[[dict], None] | None = None,
              stop_event: threading.Event | None = None, live: dict | None = None,
              pause_event: threading.Event | None = None, parallel_sites: int = 3,
              export_result: bool = True) -> Path:
    """Crawl every college. Up to ``parallel_sites`` colleges run at the same time; two rows on the same
    website never run together, so each site still gets one request per delay_seconds."""
    progress = progress or (lambda e: None)
    stop_event = stop_event or threading.Event()
    out = Path(out)
    site_dir = out.with_name(out.stem + "_sites")
    site_dir.mkdir(parents=True, exist_ok=True)
    merger = Merger()
    pages: list[PageLog] = []
    summary: list[dict] = []
    offices: list[OfficeRecord] = []
    active: dict[int, tuple] = {}          # row -> (crawler, source) being crawled now
    t0 = time.time()
    lock = threading.Lock()
    if live is not None:  # lets the web UI write a partial Excel at any time
        live.update(merger=merger, pages=pages, summary=summary, sources=sources, crawler=None, src=None, t0=t0,
                    offices=offices, active=active)
    pending = list(enumerate(sources, 1))
    busy_hosts: set[str] = set()

    def next_source():
        """The next college whose website is not being crawled by another worker. Lock held."""
        for k, (i, src) in enumerate(pending):
            if not (_hosts(src) & busy_hosts):
                busy_hosts.update(_hosts(src))
                return pending.pop(k)
        return None

    def crawl_one(i: int, src: Source) -> None:
        slug = f"{_slug(src.institution)}-r{src.row}"
        jpath = site_dir / f"{slug}.json"
        progress({"event": "site_start", "row": src.row, "index": i, "total": len(sources), "institution": src.institution, "urls": src.urls})
        cache_key = _cache_key(src, base, per_site_pages)
        cached = None
        if resume and jpath.is_file():
            try:
                cached = json.loads(jpath.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        if cached and cached.get("cache_key") == cache_key:
            data = cached
            status = "resumed"
        else:
            cfg = ScrapeConfig(**{
                **base.__dict__,
                "max_pages": src.max_pages or per_site_pages,
                "extra_profile_patterns": list(base.extra_profile_patterns) + src.patterns,
                "include_subdomains": base.include_subdomains or src.include_subdomains,
                "render": src.render or base.render,
                "departments": list(src.departments or base.departments),
            })
            try:
                crawler = Crawler(cfg, lambda e: progress({**e, "row": src.row, "institution": src.institution}), stop_event, pause_event)
                with lock:
                    active[src.row] = (crawler, src)
                    if live is not None:
                        live.update(crawler=crawler, src=src)
                res = crawler.run(src.urls)
                data = {"institution": src.institution, "urls": src.urls, "stats": res.stats,
                        "doctors": [d.to_full_dict() for d in res.doctors],
                        "offices": [o.to_full_dict() for o in res.offices],
                        "pages": [p.__dict__ for p in res.pages], "error": None}
            except Exception as exc:  # one bad site must not stop the batch
                data = {"institution": src.institution, "urls": src.urls, "stats": {}, "doctors": [], "offices": [], "pages": [],
                        "error": f"{type(exc).__name__}: {exc}"}
            stopped = bool(data["stats"].get("stopped_early"))
            all_failed = (bool(data["pages"]) and all(p["page_type"] == "error" for p in data["pages"])) or (
                not data["pages"] and bool(data["stats"].get("blocked_by_robots")))
            if not stopped and not data["error"] and not all_failed:  # a college cut short by Stop is crawled again next time
                data["cache_key"] = cache_key
                jpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            status = "error" if data["error"] else ("stopped" if stopped else ("failed" if all_failed else "done"))
        docs = [PersonRecord.from_full_dict(d) for d in data["doctors"]]
        for d in docs:
            _map_affiliation_record(d, src, sources)
            if d.layers.get("institution") != "affiliation_table" and not d.get("institution"):
                d.set("institution", src.institution, "college list", "College Name column of the uploaded list", d.source_url)
        site_offices = [OfficeRecord.from_full_dict(o) for o in data.get("offices") or []]
        for o in site_offices:
            o.source_institution = src.institution
            o.institution = o.institution or src.institution
        site_docs = [d for d in docs if d.source_institution == src.institution]
        fac = [d for d in site_docs if d.is_relevant]
        failed = [p for p in data["pages"] if p["page_type"] == "error"]
        note = data.get("error") or ""
        if not note and failed:
            note = _failure_note(failed, len(data["pages"]))
        if data["stats"].get("stopped_no_new_people"):
            note = (note + "; " if note else "") + "stopped early: many pages in a row added no new people or fields"
        if not data["pages"] and data["stats"].get("blocked_by_robots"):
            note = (note + "; " if note else "") + "no pages read: blocked by robots policy or unavailable robots.txt"
        if data["stats"].get("stop_reason") == "page_limit":
            note = (note + "; " if note else "") + "page limit reached; additional pages may remain"
        slowest = max(data["stats"].get("crawl_delays", {}).values(), default=0)
        if slowest >= 10:
            note = (note + "; " if note else "") + f"site requires {slowest:g}s between requests"
        if src.warnings:
            note = (note + "; " if note else "") + "; ".join(src.warnings)
        site = {
            "row": src.row, "institution": src.institution, "status": status, "pages": len(data["pages"]),
            "people": len(site_docs), "faculty": len(fac),
            "offices": sum(1 for o in site_offices if o.source_institution == src.institution),
            "with_phone": sum(1 for d in fac if d.get("phone")),
            "with_email": sum(1 for d in fac if d.get("email")),
            "with_any_contact": sum(1 for d in fac if any(d.get(f) for f in CONTACT_FIELDS)),
            "start_urls": " ".join(src.urls), "note": note,
        }
        with lock:
            for d in docs:
                merger.add(d)
            merge_offices(offices, site_offices)
            pages.extend(PageLog(**p) for p in data["pages"])
            summary.append(site)
            active.pop(src.row, None)
            if live is not None and live.get("src") is src:
                nxt = next(iter(active.values()), (None, None))
                live.update(crawler=nxt[0], src=nxt[1])
        progress({"event": "site_done", **site})

    def worker() -> None:
        while True:
            while pause_event is not None and pause_event.is_set() and not stop_event.is_set():
                time.sleep(0.3)
            if stop_event.is_set():
                return
            with lock:
                if not pending:
                    return
                job = next_source()
            if job is None:  # every remaining college shares a website with one being crawled
                time.sleep(0.2)
                continue
            i, src = job
            try:
                crawl_one(i, src)
            finally:
                with lock:
                    busy_hosts.difference_update(_hosts(src))

    n = max(1, min(parallel_sites, len(sources)))
    threads = [threading.Thread(target=worker, daemon=True, name=f"college-{k}") for k in range(n)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.2)
    except KeyboardInterrupt:
        stop_event.set()
        for t in threads:
            t.join(60)
    summary.sort(key=lambda r: r["row"])
    demote_shared(merger.records)
    merger.records, shared_duplicates, unmapped_shared = normalize_shared_source_records(merger.records, sources)
    for site in summary:
        mine = [d for d in merger.records if d.source_institution == site["institution"]]
        faculty_here = [d for d in mine if d.is_relevant]
        site.update({
            "people": len(mine), "faculty": len(faculty_here),
            "with_phone": sum(bool(d.get("phone")) for d in faculty_here),
            "with_email": sum(bool(d.get("email")) for d in faculty_here),
            "with_any_contact": sum(any(d.get(f) for f in CONTACT_FIELDS) for d in faculty_here),
        })
    result = CrawlResult([u for s in sources for u in s.urls], merger.records, pages, offices=offices)
    fac = result.faculty
    result.stats = {
        "colleges": len(summary), "pages_fetched": len(pages), "people_found": len(result.doctors),
        "faculty": len(fac), "student_offices": len(offices),
        "faculty_with_own_phone": sum(1 for d in fac if d.get("phone")),
        "faculty_with_own_email": sum(1 for d in fac if d.get("email")),
        "faculty_with_any_contact": sum(1 for d in fac if any(d.get(f) for f in CONTACT_FIELDS)),
        "seconds": round(time.time() - t0, 1), "stopped_early": stop_event.is_set(),
        "colleges_at_once": n, "shared_source_duplicates_merged": shared_duplicates,
        "unmapped_shared_source_records": unmapped_shared,
    }
    progress({"event": "batch_done", **result.stats})
    if export_result:
        return to_xlsx(result, out, sites=sorted(summary, key=lambda s: s["row"]))
    return out
