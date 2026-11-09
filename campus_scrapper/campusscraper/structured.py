"""Machine-readable data on a page: JSON-LD, microdata, meta tags and embedded JS state.

Many college sites built with React/Next/Nuxt/Angular ship the faculty data as
JSON inside the HTML (``__NEXT_DATA__``, ``window.__INITIAL_STATE__`` and similar)
even when the visible HTML is an empty shell. Reading that JSON gives the full
record without a browser.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from bs4 import BeautifulSoup, Tag

from .vocab import NAME_RE

# Normalised JSON key -> field
KEY_MAP: dict[str, str] = {}
for _field, _keys in {
    "name": ["name", "fullname", "displayname", "facultyname", "staffname", "empname", "employeename", "membername",
             "personname", "teachername"],
    "specialty": ["specialization", "specialisation", "areaofspecialization", "areaofspecialisation", "specialty",
                  "speciality", "major"],
    "department": ["department", "departmentname", "dept", "deptname", "departments", "division", "school",
                   "schoolname", "branch", "branchname", "discipline"],
    "designation": ["designation", "position", "jobtitle", "role", "designationname", "post", "title"],
    "qualifications": ["qualification", "qualifications", "degree", "degrees", "educationqualification", "qualificationname",
                       "hascredential", "credential", "credentials", "highestqualification"],
    "experience_years": ["experience", "experienceyears", "yearsofexperience", "totalexperience", "exp", "experienceinyears",
                         "teachingexperience"],
    "institution": ["institution", "institute", "college", "collegename", "university", "universityname", "worksfor",
                    "affiliation", "organisation", "organization", "campus", "campusname"],
    "city": ["city", "cityname", "addresslocality"],
    "photo_url": ["image", "photo", "profileimage", "imageurl", "picture", "thumbnail", "profilepic", "profilephoto",
                  "facultyimage", "staffimage", "avatar", "img"],
    "about": ["about", "description", "bio", "biography", "overview", "summary", "profilesummary", "shortbio", "aboutme"],
    "research_areas": ["researchinterests", "researchinterest", "researchareas", "researcharea", "areasofinterest",
                       "areaofinterest", "interests", "expertise", "areasofexpertise", "knowsabout", "researchfocus"],
    "subjects": ["subjects", "subjectstaught", "courses", "coursestaught", "teaching", "teachinginterests"],
    "awards": ["awards", "award", "achievements", "honours", "honors"],
    "memberships": ["memberships", "membership", "memberof", "professionalmemberships", "professionalbodies"],
    "education": ["education", "alumniof", "educationdetails", "academicbackground"],
    "email": ["email", "emailid", "emailaddress", "facultyemail", "staffemail", "mail", "officialemail"],
    "phone": ["telephone", "phone", "mobile", "contactnumber", "phonenumber", "contact", "mobileno", "mobilenumber",
              "phoneno", "contactno"],
    "contact_phone": ["extension", "ext", "officephone", "intercom", "landline"],
    "google_scholar": ["googlescholar", "scholar", "googlescholarurl", "scholarlink"],
    "orcid": ["orcid", "orcidid"],
    "scopus_id": ["scopus", "scopusid", "scopusauthorid"],
    "researchgate": ["researchgate", "researchgateurl"],
    "linkedin": ["linkedin", "linkedinurl"],
    "vidwan": ["vidwan", "vidwanid", "vidwanurl", "irins"],
    "homepage": ["homepage", "website", "personalwebsite"],
    "_sameas": ["sameas"],
    "_url": ["url", "profileurl", "permalink", "link", "slug", "href", "detailurl", "seourl", "pageurl", "facultyurl"],
}.items():
    for _k in _keys:
        KEY_MAP[_k] = _field

DOCTOR_KEYS = {"specialty", "department", "designation", "qualifications", "experience_years", "institution",
               "research_areas", "email"}
SCHEMA_SPECIALTY: dict[str, str] = {}


def _nk(key: str) -> str:
    return re.sub(r"[^a-z]", "", key.lower())


def flatten(value: Any, depth: int = 0) -> Any:
    """Turn nested JSON into a string or list of strings."""
    if depth > 4 or value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        v = re.sub(r"<[^>]+>", " ", value)  # some CMSs store HTML fragments
        v = " ".join(v.split())
        if v.startswith(("https://schema.org/", "http://schema.org/")):
            tail = v.rsplit("/", 1)[-1]
            return SCHEMA_SPECIALTY.get(tail.lower(), tail)
        return v or None
    if isinstance(value, dict):
        for k in ("name", "title", "label", "value", "text", "displayName", "fullName", "url", "@id"):
            if k in value and isinstance(value[k], (str, int, float)):
                return flatten(value[k], depth + 1)
        return None
    if isinstance(value, list):
        out = [flatten(v, depth + 1) for v in value[:60]]
        out2: list[str] = []
        for item in out:
            if isinstance(item, list):
                out2.extend(i for i in item if i)
            elif item:
                out2.append(item)
        return out2 or None
    return None


@dataclass
class Candidate:
    """Doctor facts found in one structured object."""
    layer: str
    values: dict[str, Any] = field(default_factory=dict)
    url: str | None = None
    snippet: str = ""

    @property
    def name(self) -> str | None:
        v = self.values.get("name")
        return v if isinstance(v, str) else None


def _doctorish(obj: dict) -> tuple[int, dict[str, Any], str | None]:
    """Score how much a JSON object looks like a doctor and map its fields."""
    mapped: dict[str, Any] = {}
    url = None
    for k, v in obj.items():
        if not isinstance(k, str):
            continue
        f = KEY_MAP.get(_nk(k))
        if not f:
            continue
        val = flatten(v)
        if val is None:
            continue
        if f == "_url":
            if isinstance(val, str) and url is None:
                url = val
            continue
        if f == "_sameas":
            mapped[f] = val
            continue
        if f not in mapped:
            mapped[f] = val
    types = obj.get("@type")
    types = [types] if isinstance(types, str) else (types or [])
    types = {str(t).rsplit("/", 1)[-1].lower() for t in types if isinstance(t, str)}
    if types & {"collegeoruniversity", "educationalorganization", "organization", "place",
                "breadcrumblist", "website", "webpage", "listitem", "imageobject", "postaladdress"}:
        return 0, {}, None
    name = mapped.get("name")
    if not isinstance(name, str) or len(name) > 80 or len(name) < 4:
        return 0, {}, None
    score = sum(1 for k in DOCTOR_KEYS if k in mapped)
    if re.match(r"^\s*(?:dr|prof|doctor|mr|mrs|ms)\b", name, re.I):
        score += 2
    if types & {"person"}:
        score += 2
    return score, mapped, url


def walk_json(data: Any, limit: int = 300_000) -> Iterator[dict]:
    stack = [data]
    seen = 0
    while stack and seen < limit:
        item = stack.pop()
        seen += 1
        if isinstance(item, dict):
            yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


NOT_PERSON_TYPES = {
    "videoobject", "article", "newsarticle", "blogposting", "webpage", "webpageelement", "event", "review", "rating",
    "aggregaterating", "imageobject", "mediaobject", "faqpage", "question", "answer", "howto", "creativework",
    "socialmediaposting", "medicalwebpage", "collectionpage", "itemlist", "breadcrumblist", "organization",
    "collegeoruniversity", "educationalorganization", "localbusiness", "product", "offer", "service", "course",
}


def doctors_in_json(data: Any, layer: str, min_score: int = 3) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for obj in walk_json(data):
        t = obj.get("@type") if isinstance(obj, dict) else None
        t = {str(x).rsplit("/", 1)[-1].lower() for x in ([t] if isinstance(t, str) else (t or [])) if isinstance(x, str)}
        if t & NOT_PERSON_TYPES:
            continue  # a video, article or event that names a doctor is not the doctor
        score, mapped, url = _doctorish(obj)
        if score < min_score:
            continue
        key = mapped["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        snippet = json.dumps({k: v for k, v in obj.items() if isinstance(v, (str, int, float))}, ensure_ascii=False)[:500]
        out.append(Candidate(layer, mapped, url, snippet))
    return out


# --------------------------------------------------------------------------
def _loads_lenient(text: str) -> Any:
    text = text.strip().removeprefix("<!--").removesuffix("-->").strip()
    text = text.replace("//<![CDATA[", "").replace("//]]>", "")
    try:
        return json.loads(text)
    except ValueError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    fixed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", fixed)
    try:
        return json.loads(fixed)
    except ValueError:
        return scan_json_objects(fixed) or None


def scan_json_objects(text: str, max_objects: int = 2000) -> list[Any]:
    """Pull every decodable JSON object/array out of arbitrary script text."""
    decoder = json.JSONDecoder()
    out: list[Any] = []
    i = 0
    n = len(text)
    while i < n and len(out) < max_objects:
        j = text.find("{", i)
        if j < 0:
            break
        if j + 1 < n and text[j + 1] in '"\'\n\r\t ':
            try:
                obj, end = decoder.raw_decode(text, j)
                out.append(obj)
                i = end
                continue
            except ValueError:
                pass
        i = j + 1
    return out


def jsonld(soup: BeautifulSoup) -> tuple[list[Candidate], dict[str, Any]]:
    """Person records plus page-level info (institution, breadcrumbs, city)."""
    doctors: list[Candidate] = []
    page: dict[str, Any] = {"breadcrumbs": [], "institution": None, "city": None}
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        data = _loads_lenient(script.string or script.get_text() or "")
        if data is None:
            continue
        for obj in walk_json(data, 20000):
            types = obj.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            types = {str(t).rsplit("/", 1)[-1].lower() for t in types if isinstance(t, str)}
            if "breadcrumblist" in types:
                for el in obj.get("itemListElement") or []:
                    if isinstance(el, dict):
                        nm = el.get("name")
                        if nm is None and isinstance(el.get("item"), dict):
                            nm = el["item"].get("name")
                        nm = flatten(nm)
                        if isinstance(nm, str):
                            page["breadcrumbs"].append(nm)
            elif types & {"collegeoruniversity", "educationalorganization", "organization"}:
                nm = flatten(obj.get("name"))
                if isinstance(nm, str) and not page["institution"]:
                    page["institution"] = nm
                addr = obj.get("address")
                if isinstance(addr, dict) and not page["city"]:
                    page["city"] = flatten(addr.get("addressLocality"))
        for cand in doctors_in_json(data, "jsonld", min_score=2):
            doctors.append(cand)
    return doctors, page


def microdata(soup: BeautifulSoup) -> list[Candidate]:
    out = []
    for scope in soup.find_all(attrs={"itemscope": True, "itemtype": re.compile(r"Person", re.I)}):
        obj: dict[str, Any] = {"@type": "Person"}
        for prop in scope.find_all(attrs={"itemprop": True}):
            # Skip properties belonging to a nested scope (but take its name).
            parent_scope = prop.find_parent(attrs={"itemscope": True})
            key = prop["itemprop"].split()[0]
            if parent_scope is not scope:
                continue
            if prop.has_attr("itemscope"):
                inner = prop.find(attrs={"itemprop": "name"})
                value = inner.get_text(" ", strip=True) if inner else prop.get_text(" ", strip=True)
            elif prop.name == "meta":
                value = prop.get("content", "")
            elif prop.name in ("a", "link"):
                value = prop.get("href", "") if key in ("url", "sameAs") else prop.get_text(" ", strip=True)
            elif prop.name == "img":
                value = prop.get("src", "")
            else:
                value = prop.get("content") or prop.get_text(" ", strip=True)
            if not value:
                continue
            if key in obj and key != "@type":
                existing = obj[key]
                obj[key] = (existing if isinstance(existing, list) else [existing]) + [value]
            else:
                obj[key] = value
        score, mapped, url = _doctorish(obj)
        if score >= 2:
            out.append(Candidate("microdata", mapped, url, scope.get_text(" ", strip=True)[:500]))
    return out


EMBED_PATTERNS = re.compile(
    r"(?:window\.)?__(?:INITIAL_STATE|PRELOADED_STATE|APOLLO_STATE|NEXT_DATA|NUXT|DATA|STATE|APP_STATE|SERVER_DATA)__\s*=\s*",
)
DOCTOR_HINT = re.compile(r"qualification|speciali[sz]ation|designation|faculty|department|research", re.I)


def embedded_json(soup: BeautifulSoup) -> list[Candidate]:
    """Doctor objects inside script tags that are not JSON-LD."""
    results: list[Candidate] = []
    for script in soup.find_all("script"):
        stype = (script.get("type") or "").lower()
        if "ld+json" in stype:
            continue
        text = script.string or script.get_text() or ""
        if len(text) < 40 or len(text) > 6_000_000 or not DOCTOR_HINT.search(text):
            continue
        datas: list[Any] = []
        if script.get("id") == "__NEXT_DATA__" or stype in ("application/json", "text/json"):
            d = _loads_lenient(text)
            if d is not None:
                datas.append(d)
        else:
            # Next.js app router streams escaped JSON: self.__next_f.push([1,"..."])
            if "__next_f" in text:
                for m in re.finditer(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)', text):
                    try:
                        text_chunk = json.loads('"' + m.group(1) + '"')
                    except ValueError:
                        continue
                    datas.extend(scan_json_objects(text_chunk))
            else:
                m = EMBED_PATTERNS.search(text)
                chunk = text[m.end():] if m else text
                datas.extend(scan_json_objects(chunk))
        for d in datas:
            results.extend(doctors_in_json(d, "embedded_json"))
    # De-duplicate by name, keep the candidate with most fields.
    best: dict[str, Candidate] = {}
    for c in results:
        k = c.name.casefold() if c.name else ""
        if k and (k not in best or len(c.values) > len(best[k].values)):
            best[k] = c
    return list(best.values())


def meta(soup: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    title = soup.find("title")
    if title:
        out["title"] = " ".join(title.get_text().split())
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        content = (tag.get("content") or "").strip()
        if key and content and key in ("og:title", "og:description", "og:image", "og:site_name", "twitter:title",
                                       "description", "twitter:image", "og:url", "keywords"):
            out.setdefault(key, " ".join(content.split()))
    canon = soup.find("link", rel=lambda r: r and "canonical" in r)
    if canon and canon.get("href"):
        out["canonical"] = canon["href"]
    return out


def html_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    node = soup.find(attrs={"aria-label": re.compile("breadcrumb", re.I)}) or soup.find(
        class_=re.compile(r"breadcrumb", re.I))
    if not isinstance(node, Tag):
        return []
    items = [" ".join(li.get_text(" ", strip=True).split()) for li in node.find_all(["li", "a", "span"]) if li.get_text(strip=True)]
    seen: list[str] = []
    for it in items:
        if it not in seen and len(it) < 80 and it not in (">", "/", "»"):
            seen.append(it)
    return seen[:8]


def name_from_meta(m: dict[str, str]) -> tuple[str | None, str]:
    for key in ("og:title", "twitter:title", "title"):
        v = m.get(key)
        if v:
            match = NAME_RE.search(v)
            if match:
                return match.group(0), v
    return None, ""
