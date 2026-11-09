"""Find colleges and their faculty pages without asking a model to judge pages.

Search only suggests where to look. Every question that decides a result is
answered by crawling the site itself with the same extractor that builds the
final directory:

    Is this a college of the requested kind?   its own pages say so
    Is it in the requested place?              its own pages say so
    Which page lists its faculty?              the page our extractor reads
                                               the most faculty from

So a college is returned because its staff were actually read from the page,
not because a model agreed with a sentence. Aggregator sites are never a
source: at most they suggest a college NAME, which is then resolved to an
official site and verified there, exactly as a person would check it.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .profile import city_from
from .search_candidates import exclusion_reason
from .vocab import CITY_CANON, FOREIGN_HOST, INSTITUTION_WORDS, STATES, is_marketing_name, state_from

ACADEMIC_HOST = re.compile(r"\.(?:ac\.[a-z]{2}|edu(?:\.[a-z]{2})?)$", re.I)
# "Sardar Patel Institute of Technology", "K. J. Somaiya College of Engineering"
NAME_IN_TITLE = re.compile(
    r"\b((?:[A-Z][\w.&'-]*\s+){0,6}?"
    r"(?:College|Institute|University|Academy|Vidyapeeth|Vidyalaya|Mahavidyalaya|Polytechnic)"
    r"(?:\s+of\s+(?:[A-Z][\w.&'-]*\s*){1,4})?)")
TYPE_WORDS = {
    "engineering": r"engineering|technolog|b\.?\s?tech|b\.?\s?e\b|polytechnic",
    "medical": r"medical|medicine|mbbs|hospital",
    "dental": r"dental|dentistry|bds",
    "pharmacy": r"pharmac",
    "nursing": r"nursing",
    "law": r"\blaw\b|legal|llb",
    "management": r"management|business|mba|commerce",
    "business": r"management|business|mba|commerce",
    "arts": r"\barts\b|humanities",
    "science": r"science|b\.?\s?sc\b",
}


COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
               "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "fifty": 50}
OFFICE_WORDS = re.compile(r"placement|training\s*(?:and|&)?\s*placement|\btpo\b|higher\s*stud|study\s*abroad|"
                          r"alumni|student\s*(?:chapter|club|office|cell|affairs)|international\s*office", re.I)
FACULTY_WORDS = re.compile(r"facult|professor|teaching|staff|hod|department\s*head|people", re.I)


# Conditions about a PERSON that this reader cannot filter on by itself.
PERSON_CONDITIONS = re.compile(
    r"\bwith (?:an? )?(?:email|e-mail|phone|mobile|contact)|only (?:those|people|faculty|professors)|"
    r"\bresearch(?:ing|ers?)?\b|\bspeciali[sz]|\bph\.?\s?d\b|\byears? of experience\b|"
    r"\bexperience of\b|\bworking on\b|\bwho (?:have|has|work)", re.I)


# "colleges in Pune", "universities near Nagpur", "faculty pages from Pune"
PLACE_PHRASE = re.compile(r"\b(?:in|from|near|around|across)\s+(?:the\s+)?([A-Za-z][\w.\'-]{2,})\b")
_NOT_A_PLACE = {"the", "a", "an", "faculty", "faculties", "college", "colleges", "university", "universities",
                "institute", "institutes", "page", "pages", "website", "websites", "url", "urls", "contact",
                "contacts", "email", "emails", "information", "india", "them", "it", "which", "each", "their",
                "these", "those", "all", "any", "this", "that", "me", "us", "you", "list", "data", "details"}


EXCLUSION = re.compile(
    r"\s+(?:except|excluding|exclude|other than|apart from|but not|leave out|skip|avoid|not)\s+", re.I)


def _place_word(text):
    """A place named in the request that this reader does not know."""
    for match in PLACE_PHRASE.finditer(text):
        word = (match.group(1) or "").strip(" .,")
        if word and word.casefold() not in _NOT_A_PLACE and not word.isdigit():
            return word
    return ""


@dataclass
class Request:
    """What the user asked for, read from the prompt without a model."""
    count: int = 10
    count_stated: bool = False   # True only when the request itself names a number
    kind: str = ""
    location: str = ""
    target: str = "faculty"
    names: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    state: str = ""            # an Indian state the request names; stands for all of its cities
    excluded_locations: list[str] = field(default_factory=list)
    unfiltered_conditions: bool = False
    unread_place: str = ""     # a place the request names that this reader could not match

    @property
    def places(self) -> list[str]:
        """Every city this request accepts: the city named, or the state's cities."""
        if self.location:
            places = [CITY_CANON.get(self.location, self.location)]
        else:
            places = list(STATES.get(self.state, []))
        left_out = {c.casefold() for c in self.excluded_locations}
        return [p for p in places if p.casefold() not in left_out]

    def as_dict(self):
        return {"count": self.count, "count_stated": self.count_stated,
                "kind": self.kind, "location": self.location, "state": self.state,
                "excluded_locations": list(self.excluded_locations),
                "target": self.target, "names": list(self.names),
                "departments": list(self.departments),
                "unfiltered_conditions": self.unfiltered_conditions,
                "unread_place": self.unread_place}

    def summary(self):
        what = " ".join(w for w in [self.kind, "colleges"] if w)
        where = f" in {self.location or self.state}" if (self.location or self.state) else ""
        named = f" named: {', '.join(self.names)}" if self.names else ""
        left_out = f", not {', '.join(self.excluded_locations)}" if self.excluded_locations else ""
        depts = f", {', '.join(self.departments)} only" if self.departments else ""
        want = {"offices": "student office", "both": "faculty and student office"}.get(self.target, "faculty")
        return f"{self.count} {what}{where}{left_out}{named}{depts} -> official {want} pages"


def read_request(prompt):
    """Read count, college type, city, target and named colleges from plain words.

    Deliberately literal: it never invents a city or a type. Anything it cannot
    read stays empty and simply widens the search instead of narrowing it wrongly.
    """
    text = " ".join(str(prompt or "").split())
    low = text.casefold()
    names = []
    for match in NAME_IN_TITLE.finditer(text):
        name = " ".join(match.group(1).split()).strip(" ,-|")
        # "engineering colleges" is a type, not the name of a college.
        if (8 <= len(name) <= 90 and not re.match(r"^(?:the\s+)?\w+\s+colleges?$", name, re.I)
                and name.casefold() not in {n.casefold() for n in names}):
            names.append(name)
    kind = next((k for k in TYPE_WORDS if re.search(rf"\b{k}\b", low)), "")
    if kind in ("science", "arts") and re.search(r"\bengineering\b", low):
        kind = "engineering"
    # "in Karnataka except Bengaluru": the excluded city must not become the place asked for.
    parts = EXCLUSION.split(text, maxsplit=1)
    kept, left_out_text = parts[0], (parts[1] if len(parts) > 1 else "")
    excluded_locations = []
    if left_out_text:
        for piece in re.split(r"\s*(?:,|/| and | or )\s*", left_out_text):
            place = city_from(piece) or city_from(piece.title()) or ""
            if place and place not in excluded_locations:
                excluded_locations.append(CITY_CANON.get(place, place))
    city = city_from(kept) or city_from(kept.title()) or ""   # people type "pune", not "Pune"
    if city and names and city.casefold() in " ".join(names).casefold():
        city = ""  # the city appeared only inside a college's own name
    state = "" if city else state_from(kept)
    unread_place = "" if (city or state or names) else _place_word(kept)
    count_match = re.search(r"\b(\d{1,3})\s+(?:\w+\s+){0,2}?(?:colleges?|universit|institutes?)", low)
    word_match = re.search(r"\b(" + "|".join(COUNT_WORDS) + r")\s+(?:\w+\s+){0,2}?(?:colleges?|universit|institutes?)", low)
    stated = True
    if count_match:
        count = int(count_match.group(1))
    elif word_match:
        count = COUNT_WORDS[word_match.group(1)]
    elif names:
        count = len(names)
    else:
        count, stated = 10, False   # the run limit on the form decides instead
    from .relevance import departments_in
    departments = departments_in(text) if re.search(r"\bdepartment|\bcse\b|\bece\b|\beee\b|\bit\b|"
                                                    r"computer science|mechanical|civil|electronic", low) else []
    wants_office = bool(OFFICE_WORDS.search(low))
    wants_faculty = bool(FACULTY_WORDS.search(low)) or not wants_office
    target = "both" if wants_office and wants_faculty else ("offices" if wants_office else "faculty")
    return Request(count=max(1, min(count, 100)), count_stated=stated,
                   kind=kind, location=city, state=state, target=target, names=names,
                   excluded_locations=excluded_locations,
                   departments=departments, unfiltered_conditions=bool(PERSON_CONDITIONS.search(text)),
                   unread_place=unread_place)


@dataclass
class College:
    """One verified college: its own name, its own faculty pages."""
    name: str
    host: str
    homepage: str
    city: str = ""
    faculty_urls: list[str] = field(default_factory=list)
    office_urls: list[str] = field(default_factory=list)
    staff_found: int = 0
    offices_found: int = 0
    pages_read: int = 0
    evidence: dict = field(default_factory=dict)

    def as_dict(self):
        return {"name": self.name, "host": self.host, "homepage": self.homepage, "city": self.city,
                "faculty_urls": self.faculty_urls, "office_urls": self.office_urls,
                "staff_found": self.staff_found, "offices_found": self.offices_found,
                "pages_read": self.pages_read, "evidence": self.evidence}


@dataclass
class Rejected:
    host: str
    reason: str
    name: str = ""
    city: str = ""
    pages_read: int = 0


def site_queries(kind="", location="", extra=()):
    """Plain searches a person would type, plus the academic-domain shortcut."""
    what = " ".join(w for w in [kind.strip(), "colleges"] if w).strip()
    where = location.strip()
    base = [f"{what} in {where}" if where else what,
            f"{what} {where} official website".strip(),
            f"{what} {where} faculty".strip(),
            f"{what} {where} site:ac.in".strip(),
            f"{what} {where} department faculty list".strip(),
            f"list of {what} in {where}" if where else f"list of {what}",
            # More ways a person looks for the same thing. One search engine page
            # rarely holds twenty official sites, so ask in several directions.
            f"top {what} in {where}" if where else f"top {what}",
            f"{what} {where} faculty directory".strip(),
            f"{what} {where} department of computer engineering faculty".strip(),
            f"{what} {where} autonomous affiliated".strip(),
            f"engineering colleges {where} NAAC accredited".strip() if where else "",
            f"{what} near {where}".strip() if where else ""]
    return list(dict.fromkeys([q for q in [*extra, *base] if q and len(q) < 200]))


def is_marketing(name):
    """True when the text reads like a search headline rather than a college name."""
    return is_marketing_name(name)


def names_from_results(results, limit=25):
    """College names suggested by result titles. A hint for the next search only.

    A name is never output from here: the official site must confirm it.
    """
    names = []
    for item in results:
        title = str(item.get("title") or "")
        for match in NAME_IN_TITLE.finditer(title):
            name = " ".join(match.group(1).split()).strip(" ,-|")
            if (8 <= len(name) <= 90 and not is_marketing(name)
                    and name.casefold() not in {n.casefold() for n in names}):
                names.append(name)
    return names[:limit]


def candidate_hosts(results, allow=lambda url: True):
    """Likely official sites, best first, one entry per host."""
    scored, seen = [], {}
    for item in results:
        url = str(item.get("url") or "")
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        host = (parts.hostname or "").lower().removeprefix("www.")
        if not host or parts.scheme not in ("http", "https") or exclusion_reason(url) or not allow(url):
            continue
        text = " ".join(str(item.get(k) or "") for k in ("title", "content"))
        score = (4 * bool(ACADEMIC_HOST.search(host))
                 + 3 * bool(re.search(r"college|university|institute|vidyapeeth", text, re.I))
                 + 2 * bool(re.search(r"college|univ|institute|iit|nit|vjti|tech", host, re.I))
                 + bool(re.search(r"facult|staff|people|department", url + " " + text, re.I)))
        best = seen.get(host)
        if best is None or score > best[0]:
            seen[host] = (score, f"{parts.scheme}://{parts.netloc}/")
    for host, (score, homepage) in seen.items():
        scored.append((score, host, homepage))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [(host, homepage) for _, host, homepage in scored]


def _asked_for(request, host):
    """True when the prompt named this institution, so a foreign site was wanted.

    Matches the site's own label against the name: 'imperial.ac.uk' for
    'Imperial College London', 'mit.edu' for 'MIT'.
    """
    if request is None or not request.names:
        return False
    label = re.sub(r"[^a-z0-9]", "", host.casefold().removeprefix("www.").split(".")[0])
    if len(label) < 3:
        return False
    for name in request.names:
        letters = re.sub(r"[^a-z0-9]", "", name.casefold())
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", name))
        if label in letters or label == initials.casefold():
            return True
    return False


def _requested_city(location):
    city = city_from(location) or location.strip()
    return CITY_CANON.get(city, city)


def read_identity(page):
    """The site's own name and city, from its home page: title, banner, address."""
    from .dom import parse
    from .structured import meta as page_meta
    soup = parse(page.html)
    info = page_meta(soup)
    heading = soup.find("h1")
    candidates = [info.get("og:site_name") or "", heading.get_text(" ", strip=True) if heading else "",
                  *re.split(r"\s*[|\u2013\u2014]\s*|\s+[-]\s+", info.get("title") or "")]
    name = ""
    for option in candidates:
        option = " ".join(str(option).split()).strip(" ,-|")
        if 5 <= len(option) <= 90 and INSTITUTION_WORDS.search(option) and not is_marketing(option):
            name = option
            break
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    text = " ".join(soup.stripped_strings)[:20000]
    return name, (city_from(text) or ""), text


def survey_site(homepage, make_crawler, kind="", location="", target="faculty", budget=45, request=None):
    """Crawl a little of one site and let the extractor answer who is there.

    budget is the wall clock, in seconds, this site is allowed. A site that
    answers slowly is dropped with that stated, so one bad host cannot hold up
    the run; whatever was read before the budget ran out still counts.
    """
    started = time.monotonic()
    crawler = make_crawler()
    host = (urlsplit(homepage).hostname or "").removeprefix("www.")
    if budget:
        crawler.deadline = time.time() + budget
    home = crawler.fetcher.get(homepage)  # identity first, on the same paced fetcher
    if home.error or home.status != 200:
        return None, Rejected(host, f"Home page could not be read: {home.error or home.status}"[:200])
    name, home_city, home_text = read_identity(home)
    result = crawler.run([homepage])
    spent = time.monotonic() - started
    if budget and spent >= budget and len(result.pages) < 3:
        return None, Rejected(host, f"The site answered too slowly: {len(result.pages)} page(s) "
                                    f"in {budget} seconds", name, home_city, len(result.pages))
    people = result.faculty or result.doctors
    names = Counter(d.get("institution") for d in people if d.get("institution"))
    names.update(o.institution for o in result.offices if o.institution)
    if not name:  # the site's own pages name it, as long as they name a college
        name = next((n for n, _ in names.most_common()
                     if INSTITUTION_WORDS.search(n) and not is_marketing(n)), "")
    if not name:  # the web address is at least true; a search headline is not a name
        name = host
    cities = Counter(d.get("city") for d in people if d.get("city"))
    city = home_city or next(iter(cities), "") or city_from(name) or ""
    pages_read = len(result.pages)

    by_page = Counter()
    for page in result.pages:
        if page.doctors:
            by_page[page.url] += page.doctors
    faculty_urls = [url for url, _ in by_page.most_common(4)]
    office_urls = [o.page_url for o in result.offices][:4]

    college = College(name=name.strip(), host=host, homepage=homepage, city=city,
                      faculty_urls=faculty_urls, office_urls=office_urls,
                      staff_found=len(result.faculty), offices_found=len(result.offices),
                      pages_read=len(result.pages))

    # Only the site's own words decide. Text seen while crawling, nothing else.
    blob = " | ".join(filter(None, [name, home_text, *(d.get("department") or "" for d in people),
                                    *(d.get("designation") or "" for d in people),
                                    *(o.office_name or "" for o in result.offices)]))
    if not name or (name != host and not INSTITUTION_WORDS.search(name)):
        return None, Rejected(host, "No college or university name found on the site", name, city, pages_read)
    if name == host:
        college.evidence["name"] = "The site never states a plain college name; its web address is used instead"
    wanted_pages = faculty_urls if target != "offices" else office_urls
    if not wanted_pages:
        kinds = {"offices": "student office", "both": "faculty or student office"}.get(target, "faculty")
        return None, Rejected(host, f"No page on this site listed {kinds} contacts", name, city, pages_read)
    # An Indian-college run never returns a foreign university, whatever a search engine offers.
    if FOREIGN_HOST.search(host) and not _asked_for(request, host):
        return None, Rejected(host, "Not an Indian institution (foreign academic domain)", name, city, pages_read)
    places = list(request.places) if request is not None else ([location] if location else [])
    excluded = [c.casefold() for c in (request.excluded_locations if request is not None else [])]
    found = CITY_CANON.get(city, city)
    if excluded and found and found.casefold() in excluded:
        return None, Rejected(host, f"Site is in {found}, which you asked to leave out", name, city, pages_read)
    if places:
        wanted = [_requested_city(p) for p in places]
        where = request.state if (request is not None and request.state) else " / ".join(wanted)
        if not found:
            return None, Rejected(host, f"Site never states {where}", name, city, pages_read)
        hit = next((w for w in wanted if w.casefold() == found.casefold()), "")
        if not hit:
            return None, Rejected(host, f"Site says {found}, not {where}", name, city, pages_read)
        college.evidence["location"] = f"{hit} read from this site"
    elif location:
        want = _requested_city(location)
        if want and found and want.casefold() != found.casefold():
            return None, Rejected(host, f"Site says {found}, not {want}", name, city, pages_read)
        if want and not found:
            return None, Rejected(host, f"Site never states {want}", name, city, pages_read)
        college.evidence["location"] = f"{found} read from this site"
    if kind:
        pattern = TYPE_WORDS.get(kind.strip().casefold().split()[0], re.escape(kind.strip()))
        if not re.search(pattern, blob, re.I):
            return None, Rejected(host, f"Nothing on this site shows it is a {kind} college", name, city, pages_read)
        college.evidence["institution_type"] = f"{kind} confirmed in the site's own pages"
    college.evidence["staff"] = f"{college.staff_found} staff read from {len(faculty_urls)} page(s)"
    return college, None


def discover_colleges(search, make_crawler, want=10, kind="", location="", target="faculty",
                      queries=(), names=(), max_searches=12, max_sites=90, workers=6,
                      site_seconds=45, control=lambda: None, progress=lambda event: None,
                      request=None):
    """Search, then prove each site by crawling it. Returns (colleges, rejected, notes)."""
    notes, results, searched = [], [], []
    if names:  # named colleges: look them up, do not browse a city list
        planned = [q for n in names for q in (f"{n} official website", f"{n} faculty")]
    else:
            # A state is searched city by city so every part of it is reached.
            places = (request.places if request is not None else []) or [location]
            per_place = [site_queries(kind, p, queries) for p in places[:6]]
            planned = list(dict.fromkeys(q for group in zip(*per_place) for q in group)) if per_place else []
            planned += [q for group in per_place for q in group if q not in planned]

    def run_search(query):
        control()
        progress({"phase": "searching", "current": query})
        try:
            found = search.search(query)
        except Exception as exc:  # one bad query must not end discovery
            notes.append(f"Search failed for '{query}': {exc}"[:300])
            return
        searched.append(query)
        details = getattr(search, "search_details", {}).get(query, {})
        items = details.get("results") or [{"url": u} for u in found]
        results.extend(items)
        progress({"phase": "searching", "current": query, "searches": len(searched)})

    for query in planned[:max_searches]:
        run_search(query)
    # A name seen in any title is only a lead; the official site still has to prove it.
    for name in names_from_results(results)[: max(0, max_searches - len(searched))]:
        run_search(f"{name} official website")

    hosts = candidate_hosts(results)[:max_sites]
    progress({"phase": "checking_sites", "current": f"{len(hosts)} candidate sites"})
    colleges, rejected = [], []
    lock = threading.Lock()
    pending = list(hosts)

    fatal = []
    counts = {"checked": 0, "pages": 0}

    def report_counts(current):
        """The UI must show movement while sites are being read, not after."""
        progress({"phase": "checking_sites", "current": current, "found": len(colleges),
                  "checked": counts["checked"], "sites": len(hosts), "searches": len(searched),
                  "pages": counts["pages"]})

    def worker():
        while True:
            try:
                control()  # Pause and Stop must leave the threads, not be swallowed
                with lock:
                    if not pending or len(colleges) >= want or fatal:
                        return
                    host, homepage = pending.pop(0)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
                with lock:
                    fatal.append(exc)
                return
            report_counts(f"Reading {host}")
            try:
                college, reject = survey_site(homepage, make_crawler, kind, location, target, request=request,
                                              budget=site_seconds)
            except Exception as exc:
                college, reject = None, Rejected(host, f"Site could not be read: {exc}"[:200])
            with lock:
                counts["checked"] += 1
                counts["pages"] += college.pages_read if college else (reject.pages_read if reject else 0)
                if college and len(colleges) < want:
                    colleges.append(college)
                elif reject:
                    rejected.append(reject)
            report_counts(f"Verified {college.name}" if college else
                          f"{host}: {reject.reason if reject else 'no result'}")

    threads = [threading.Thread(target=worker, daemon=True, name=f"site-{i}")
               for i in range(max(1, min(workers, len(pending) or 1)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        while thread.is_alive():
            thread.join(0.2)

    if fatal:
        raise fatal[0]
    colleges.sort(key=lambda c: (-c.staff_found, c.name))
    if len(colleges) < want:
        notes.append(f"Requested {want} colleges; {len(colleges)} had faculty pages this run could verify "
                     f"({len(hosts)} sites checked, {len(rejected)} did not qualify)")
    return colleges[:want], rejected, notes


def sources_from_colleges(colleges, target="faculty"):
    """The existing Excel/batch source rows, straight from verified pages."""
    from .sources import Source
    rows = []
    for index, college in enumerate(colleges, start=2):
        urls = college.faculty_urls if target != "offices" else college.office_urls
        if target == "both":
            urls = list(dict.fromkeys(college.faculty_urls + college.office_urls))
        rows.append(Source(index, college.name, list(urls), city=college.city))
    return rows
