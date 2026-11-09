"""Single-person (faculty) profile extraction from labels, sections and text patterns."""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from . import emails, phones
from .annotate import Line
from .models import LIST_FIELDS, PersonRecord, Evidence
from .relevance import departments_in
from .vocab import (
    ACADEMIC_LINKS, CITY_CANON, CITY_RE, DESIGNATION_RE, EXPERIENCE_RE, INSTITUTION_WORDS, NAME_RE, NAME_STOP,
    ORCID_ID_RE, QUAL_RE, QUAL_STRONG, WEAK_QUALS, clean_name, honorific_of, is_marketing_name, label_field,
    normalize_quals,
)

SPLIT_RE = re.compile(r"\s*(?:[,;|•·]|\s/\s|\n)\s*")


# --------------------------------------------------------------------------
# value parsers
def parse_experience(text: str) -> str | None:
    m = EXPERIENCE_RE.search(text)
    if m:
        years = next(g for g in m.groups() if g)
        if 0 < int(years) < 60:
            return years
    m = re.fullmatch(r"\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)?\.?\s*", text, re.I)
    if m and 0 < int(m.group(1)) < 60:
        return m.group(1)
    return None


def parse_qualifications(text: str, labelled: bool = False) -> list[str]:
    """Return qualification phrases like 'PhD (Computer Science)' or 'MTech (CSE), IIT Madras' in page order."""
    text = normalize_quals(text)
    out: list[str] = []
    for m in QUAL_RE.finditer(text):
        token = {"Ph.D": "PhD"}.get(m.group(1), m.group(1))
        tail = text[m.end():m.end() + 90]
        detail = re.match(r"\s*(\([^)]{2,70}\)|(?:in|-|–)\s+[A-Z][A-Za-z &]{2,50}(?=,|;|\(|$| from| at))", tail)
        out.append(token + (" " + detail.group(1).strip() if detail else ""))
    strong = labelled or any(q.split()[0] in QUAL_STRONG for q in out)
    if not strong:
        out = [q for q in out if q.split()[0] not in WEAK_QUALS]
    seen: list[str] = []
    for q in out:
        if q not in seen:
            seen.append(q)
    return seen


def qualification_line(text: str) -> bool:
    quals = parse_qualifications(text)
    return len(quals) >= 2 or any(q.split()[0] in QUAL_STRONG for q in quals)


def sub_specialties(text: str) -> list[str]:
    """Departments named in the text (kept under this name for code shared with the hospital scraper)."""
    return departments_in(text)


def split_designation(text: str) -> tuple[str | None, str | None]:
    """'Associate Professor, Department of CSE' -> ('Associate Professor', 'CSE')."""
    text = re.sub(r"\bAsst\.?\s*Prof\.?", "Assistant Professor", text, flags=re.I)
    text = re.sub(r"\bAssoc\.?\s*Prof\.?", "Associate Professor", text, flags=re.I)
    m = DESIGNATION_RE.search(text)
    if not m:
        return None, None
    full = m.group(0).strip(" -–,:")
    if re.fullmatch(r"Head of (?:the )?Department", full, re.I):
        return full, None
    hod = re.match(r"^(Head of (?:the )?Department)\s*(?:of\s+|[-–,:]\s*|\(\s*)(.+?)\)?$", full, re.I)
    if hod:
        return "Head of Department", re.sub(r"^(?:the\s+)?(?:department|dept\.?)\s+of\s+", "", hod.group(2), flags=re.I) or None
    full = re.split(r"\s+at\s+|\s*,\s*(?=[^,]*\b(?:College|University|Institute|Vidyapeeth|IIT|NIT|IIIT)\b)", full,
                    maxsplit=1)[0].strip(" -–,:")
    q = QUAL_RE.search(normalize_quals(full))
    if q and q.start() > 3:
        full = full[: q.start()].strip(" -–,:")
    keep_grade = re.match(r"^(.*?\(\s*(?:(?:Sr\.?|Senior|Selection|Sel\.?)\s*(?:Grade|Scale)?|Grade[- ]?(?:I{1,3}|[1-3])|SG|SS|Contract|Contractual|Adhoc|Ad-hoc)\s*\))", full, re.I)
    head = keep_grade.group(1) if keep_grade else None
    rest_text = full[len(head):] if head else full
    if head:
        parts = [head, rest_text.strip(" -–,:&")] if rest_text.strip(" -–,:&") else [head]
    else:
        parts = re.split(r"\s*(?:[-–:,|]|\bin\b|\bof\b(?! Practice)(?=\s+(?:the\s+)?(?:dep|dept|school|computer|"
                         r"electr|mech|civil|chem|info|math|phys|manag|human|bio|appl)))\s*", full, maxsplit=1, flags=re.I)
    role = parts[0].strip()
    rest = parts[1].strip(" -–,") if len(parts) > 1 else None
    if rest:
        rest = re.sub(r"^(?:the\s+)?(?:department|dept\.?|division|school|centre|center)\s+(?:of\s+)?", "", rest,
                      flags=re.I).strip() or None
    if rest and len(rest) < 2:
        rest = None
    return (full if len(full) <= 120 and not rest else role), rest


def city_from(text: str) -> str | None:
    m = CITY_RE.search(text or "")
    if not m:
        return None
    return CITY_CANON.get(m.group(1), m.group(1))


def split_list(text: str) -> list[str]:
    parts = [p.strip(" .-–") for p in SPLIT_RE.split(text) if p.strip(" .-–")]
    return [p for p in parts if 1 < len(p) <= 200]


def name_from_url(url: str) -> str | None:
    seg = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    seg = re.sub(r"\.(?:html?|php|aspx?|jsp)$", "", seg)
    seg = re.sub(r"[-_]\d+$", "", seg)
    m = re.match(r"^(dr|prof|mr|ms|mrs)[-_.]+([a-z][a-z\-_.]+)$", seg, re.I)
    if not m:
        return None
    words = [w for w in re.split(r"[-_.]+", m.group(2)) if w]
    if not 1 <= len(words) <= 5 or any(w.lower() in {"faculty", "department", "staff", "profile"} for w in words):
        return None
    hon = {"dr": "Dr.", "prof": "Prof.", "mr": "Mr.", "ms": "Ms.", "mrs": "Mrs."}[m.group(1).lower()]
    return hon + " " + " ".join(w.capitalize() for w in words)


# --------------------------------------------------------------------------
def add_contacts(rec: PersonRecord, field: str, items: list[str], layer: str, snippet: str, url: str) -> None:
    """Union contact values ('a; b') instead of replacing them, keeping first-seen order."""
    items = [i for i in items if i]
    if not items:
        return
    existing = [x for x in (rec.get(field) or "").split("; ") if x]
    merged = list(dict.fromkeys(existing + items))
    if merged == existing:
        return
    rec.values[field] = "; ".join(merged)
    rec.layers.setdefault(field, layer)
    for item in items:
        if item not in existing:
            rec.evidence.append(Evidence(field, item, layer, (snippet or item)[:500], url))


def assign_emails(rec: PersonRecord, addrs: list[str], layer: str, snippet: str, url: str,
                  single_doctor: bool = True) -> None:
    """Person's own address -> email; department/office addresses -> contact_email."""
    if not addrs:
        return
    own, other = emails.classify(addrs, rec.get("name"), single_doctor)
    add_contacts(rec, "email", own, layer, snippet, url)
    add_contacts(rec, "contact_email", [o for o in other if o not in (rec.get("email") or "")], layer, snippet, url)


def assign_phones(rec: PersonRecord, found: list[tuple[str, str]], layer: str, snippet: str, url: str,
                  single_doctor: bool = True) -> None:
    """Person's own number -> phone; office/department numbers -> contact_phone."""
    if not found:
        return
    own, desk = phones.classify(found, single_doctor)
    add_contacts(rec, "phone", own, layer, snippet, url)
    add_contacts(rec, "contact_phone", [d for d in desk if d not in (rec.get("phone") or "")], layer, snippet, url)


def academic_links(rec: PersonRecord, scope: Tag, base_url: str, layer: str = "patterns") -> None:
    """Google Scholar, ORCID, Scopus, ResearchGate, LinkedIn and Vidwan links inside the person's own block."""
    for a in scope.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        for f, rx in ACADEMIC_LINKS.items():
            if rx.search(href) and not rec.get(f):
                value = href
                if f == "orcid":
                    m = ORCID_ID_RE.search(href)
                    value = m.group(0) if m else href
                rec.set(f, value, layer, a.get_text(" ", strip=True)[:80] or href, base_url)


def find_name_in_text(text: str) -> str | None:
    m = NAME_RE.search(text)
    if not m:
        return None
    name = clean_name(m.group(1))
    if not name:
        return None
    hon = honorific_of(m.group(0))
    return f"{hon} {name}".strip()


def plain_person_name(text: str) -> str | None:
    """A heading like 'Meenu Walia' with no honorific."""
    text = re.sub(r"\s*[|–-].*$", "", text).strip()
    words = text.split()
    if not 2 <= len(words) <= 5 or len(text) > 50:
        return None
    for w in words:
        bare = w.strip(".,")
        if bare in NAME_STOP or (bare.endswith("'s") and bare[:-2] in NAME_STOP):
            return None  # "About Us", "Principal's Message": a page heading, not a person
    if not all(re.match(r"^[A-Z][a-zA-Z'.\-]*$", w) for w in words):
        return None
    if all(w.isupper() and len(w) > 1 for w in words):
        text = " ".join(w.capitalize() for w in words)
    return text


def primary_name(lines: list[Line], soup: BeautifulSoup, meta: dict, url: str, structured_names: list[str]) -> tuple[str | None, str, int, str]:
    """Pick the profile's person name. Returns (name, evidence, name line index, source)."""
    for i, line in enumerate(lines):
        if line.kind == "h" and line.level == 1:
            n = find_name_in_text(line.text)
            if n and len(line.text) < 120:
                return n, line.text, i, "h1"
    h1 = soup.find("h1")
    if structured_names:
        return structured_names[0], "structured data", _index_of(lines, structured_names[0]), "struct"
    if h1 is None:
        for i, line in enumerate(lines[:40]):
            if line.kind == "h" and line.level <= 3:
                n = find_name_in_text(line.text)
                if n and len(line.text) < 90:
                    return n, line.text, i, "heading"
    # Coordinator pages often retain an old person's name in their SEO title.
    # Prefer the single person explicitly named in the visible introductory text.
    visible = [(i, l, find_name_in_text(l.text)) for i, l in enumerate(lines[:40])
               if len(l.text) < 100 and not re.search(r"@|https?://", l.text)]
    visible = [(i, l, n) for i, l, n in visible if n]
    if len({n.casefold() for _, _, n in visible}) == 1:
        i, line, n = visible[0]
        return n, line.text, i, "heading"
    for key in ("og:title", "twitter:title", "title"):
        if meta.get(key):
            n = find_name_in_text(meta[key])
            if n and any(_surname(n) in l.text.lower() for l in lines[:40]):
                return n, meta[key], _index_of(lines, n), "meta"
    if h1:
        n = plain_person_name(h1.get_text(" ", strip=True))
        if n:
            idx = next((i for i, l in enumerate(lines) if l.kind == "h" and l.level == 1), 0)
            context = " ".join(l.text for l in lines[idx: idx + 8])
            if qualification_line(context) or DESIGNATION_RE.search(context):
                return n, h1.get_text(" ", strip=True), idx, "h1plain"
    n = name_from_url(url)
    if n:
        return n, url, _index_of(lines, n), "url"
    return None, "", 0, ""


def _surname(name: str) -> str:
    return name.split()[-1].strip(".").lower()


def _index_of(lines: list[Line], name: str) -> int:
    sur = _surname(name)
    for i, l in enumerate(lines):
        if sur in l.text.lower() and len(l.text) < 120:
            return i
    return 0


# --------------------------------------------------------------------------
def labels_and_sections(rec: PersonRecord, lines: list[Line], url: str) -> None:
    """Inline 'Label: value' lines and heading/label blocks followed by content."""
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if line.kind in ("p", "li", "label") and ":" in line.text[:45]:
            label, value = line.text.split(":", 1)
            f = label_field(label)
            if f and value.strip():
                _assign(rec, f, value.strip(), "labels", line.text, url)
                i += 1
                continue
        if line.kind in ("h", "label") and len(line.text) <= 60:
            f = label_field(line.text)
            if f == "name":
                f = None
            if f:
                block: list[Line] = []
                j = i + 1
                if line.kind == "label" and j < n and lines[j].kind != "li":
                    if lines[j].kind not in ("h", "label"):
                        _assign_block(rec, f, [lines[j]], line, url)
                        i = j + 1
                    else:
                        i = j
                    continue
                while j < n:
                    nxt = lines[j]
                    if nxt.kind == "h" and (nxt.level <= line.level or label_field(nxt.text)):
                        break
                    if nxt.kind == "label" and label_field(nxt.text):
                        break
                    block.append(nxt)
                    j += 1
                    if len(block) >= 40:
                        break
                if block:
                    _assign_block(rec, f, block, line, url)
                i = j
                continue
        i += 1


def _assign_block(rec: PersonRecord, f: str, block: list[Line], head: Line, url: str) -> None:
    layer = "sections" if head.kind == "h" else "labels"
    snippet = head.text + ": " + " | ".join(b.text for b in block[:6])
    if f in LIST_FIELDS:
        items: list[str] = []
        li = [b.text for b in block if b.kind == "li"]
        source = li or [b.text for b in block]
        for t in source:
            if f == "qualifications":
                items.extend(parse_qualifications(t, labelled=True) or ([t] if len(t) < 160 else []))
            elif li:
                items.append(t)
            else:
                items.extend(split_list(t) if len(t) < 300 else [t[:300]])
        rec.set(f, items, layer, snippet, url)
    elif f == "about":
        text = " ".join(b.text for b in block if b.kind in ("p", "li"))[:1500]
        rec.set("about", text, layer, snippet, url)
    else:
        _assign(rec, f, block[0].text, layer, snippet, url)


def _assign(rec: PersonRecord, f: str, value: str, layer: str, snippet: str, url: str) -> None:
    value = value.strip()
    if f == "name":
        n = find_name_in_text(value)
        if not n:
            bare = re.sub(r"^\s*(?:dr|prof|mr|mrs|ms)\.?\s+", "", value, flags=re.I)
            n = plain_person_name(bare) if clean_name(bare) == bare.strip(" .") else None
        if n and "name" not in rec.values:
            rec.set("name", n, layer, snippet, url)
        return
    if f == "experience_years":
        v = parse_experience(value) or parse_experience(value + " years experience")
        if v:
            rec.set(f, v, layer, snippet, url)
    elif f == "qualifications":
        rec.set(f, parse_qualifications(value, labelled=True) or [value[:150]], layer, snippet, url)
    elif f in ("email", "contact_email"):
        assign_emails(rec, emails.find(value), layer, snippet, url)
    elif f in ("phone", "contact_phone"):
        found = [(n, (snippet or "") + " " + ctx) for n, ctx in phones.find(value)]
        if f == "contact_phone":
            add_contacts(rec, "contact_phone", [n for n, _ in found], layer, snippet, url)
        else:
            assign_phones(rec, found, layer, snippet, url)
    elif f == "designation":
        role, rest = split_designation(value)
        rec.set(f, role or value[:120], layer, snippet, url)
        if rest:
            rec.set("department", rest, layer, snippet, url)
    elif f == "department":
        rec.set(f, re.sub(r"^(?:the\s+)?(?:department|dept\.?)\s+of\s+", "", value, flags=re.I)[:150], layer, snippet, url)
    elif f == "institution":
        rec.set(f, value[:200], layer, snippet, url)
        c = city_from(value)
        if c:
            rec.set("city", c, layer, snippet, url)
    elif f == "orcid":
        m = ORCID_ID_RE.search(value)
        if m:
            rec.set(f, m.group(0), layer, snippet, url)
    elif f in ("google_scholar", "scopus_id", "researchgate", "linkedin", "vidwan", "homepage"):
        m = re.search(r"https?://\S+", value)
        if m:
            rec.set(f, m.group(0).rstrip(").,;"), layer, snippet, url)
        elif f == "scopus_id" and re.fullmatch(r"\d{8,12}", value.strip()):
            rec.set(f, value.strip(), layer, snippet, url)
    elif f in LIST_FIELDS:
        rec.set(f, split_list(value), layer, snippet, url)
    else:
        rec.set(f, value[:300], layer, snippet, url)


# --------------------------------------------------------------------------
def patterns(rec: PersonRecord, lines: list[Line], name_idx: int, main: Tag, url: str, meta: dict, breadcrumbs: list[str]) -> None:
    """Fallback text patterns, focused on the header block just under the name."""
    head = lines[name_idx + 1: name_idx + 14]
    head_text = "\n".join(l.text for l in head)
    full_text = "\n".join(l.text for l in lines)

    for l in head:
        if len(l.text) > 160:
            continue
        if "designation" not in rec.values:
            role, rest = split_designation(l.text)
            if role:
                rec.set("designation", role, "patterns", l.text, url)
                if rest:
                    rec.set("department", rest, "patterns", l.text, url)
                continue
        if "department" not in rec.values:
            if (departments_in(l.text) or re.search(r"\bdepartment of [a-z ]+", l.text, re.I)) and len(l.text) < 90 \
                    and not qualification_line(l.text):
                role, rest = split_designation(l.text)
                value = rest if role else l.text
                if value:
                    rec.set("department", re.sub(r"^(?:the\s+)?(?:department|dept\.?)\s+of\s+", "", value, flags=re.I),
                            "patterns", l.text, url)
                continue
        if "qualifications" not in rec.values and qualification_line(l.text):
            rec.set("qualifications", parse_qualifications(l.text), "patterns", l.text, url)
            continue
        # The person's own college comes from labels, structured data, breadcrumbs or the site name below.
        # A line under the name that mentions a university is usually where they studied.

    if "qualifications" not in rec.values:
        for l in lines[: name_idx + 40]:
            if qualification_line(l.text) and len(l.text) < 250:
                rec.set("qualifications", parse_qualifications(l.text), "patterns", l.text, url)
                break

    if "experience_years" not in rec.values:
        for l in lines:
            v = parse_experience(l.text) if re.search(r"experience|exp\b|years", l.text, re.I) else None
            if v:
                rec.set("experience_years", v, "patterns", l.text, url)
                break

    academic_links(rec, main, url)
    if "orcid" not in rec.values:
        m = ORCID_ID_RE.search(full_text)
        if m and re.search(r"orcid", full_text[max(0, m.start() - 40): m.start()], re.I):
            rec.set("orcid", m.group(0), "patterns", full_text[max(0, m.start() - 40): m.end()], url)

    found = emails.from_links(main) + emails.find(full_text)
    if found:
        assign_emails(rec, list(dict.fromkeys(found)), "patterns", "; ".join(found)[:300], url)
    found_phones = phones.from_links(main) + phones.find(full_text)
    if found_phones:
        seen_nums: set[str] = set()
        unique = [(n, c) for n, c in found_phones if not (n in seen_nums or seen_nums.add(n))]
        assign_phones(rec, unique, "patterns", "; ".join(n for n, _ in unique)[:300], url)

    if "department" not in rec.values:
        for crumb in reversed(breadcrumbs):
            if departments_in(crumb) or re.search(r"department|dept", crumb, re.I):
                rec.set("department", crumb, "patterns", " > ".join(breadcrumbs), url)
                break
    if "institution" not in rec.values:
        for crumb in breadcrumbs:
            if INSTITUTION_WORDS.search(crumb):
                rec.set("institution", crumb, "patterns", " > ".join(breadcrumbs), url)
                break
    if "institution" not in rec.values and meta.get("og:site_name"):
        rec.set("institution", meta["og:site_name"], "meta", "og:site_name", url)

    if "city" not in rec.values:
        for source in [rec.get("institution") or "", head_text, " ".join(breadcrumbs)]:
            c = city_from(source)
            if c:
                rec.set("city", c, "patterns", source[:200], url)
                break

    if "photo_url" not in rec.values:
        name = rec.get("name") or ""
        sur = _surname(name) if name else ""
        img = main.find("img", alt=re.compile(re.escape(sur), re.I)) if sur else None
        if img and (img.get("src") or img.get("data-src")):
            rec.set("photo_url", urljoin(url, img.get("src") or img.get("data-src")), "patterns", str(img)[:200], url)
        elif meta.get("og:image"):
            rec.set("photo_url", urljoin(url, meta["og:image"]), "meta", "og:image", url)

    if "about" not in rec.values:
        sur = _surname(rec.get("name") or "x y")
        for l in lines[name_idx:]:
            if l.kind == "p" and len(l.text) > 120 and (sur in l.text.lower() or re.search(r"\b(?:he|she|dr\.?|prof\.?)\b", l.text, re.I)):
                rec.set("about", l.text[:1500], "patterns", l.text[:300], url)
                break
    desc = meta.get("og:description") or meta.get("description") or ""
    if "about" not in rec.values and len(desc) > 60:
        rec.set("about", desc, "meta", "meta description", url)
    if desc:
        if "experience_years" not in rec.values:
            v = parse_experience(desc)
            if v:
                rec.set("experience_years", v, "meta", desc, url)
        if "qualifications" not in rec.values and qualification_line(desc):
            rec.set("qualifications", parse_qualifications(desc), "meta", desc, url)


# --------------------------------------------------------------------------
# Value checks. Each field must look like what it is; anything else is dropped.
TITLE_WORD = re.compile(
    r"\b(?:professor|prof|lecturer|reader|dean|director|principal|head|hod|chair(?:man|person)?|registrar|chancellor|"
    r"faculty|scientist|fellow|officer|coordinator|in-?charge|incharge|assistant|associate|adjunct|visiting|emeritus|"
    r"instructor|technician|librarian|tutor|researcher|vice|senior|guest|honorary)\b", re.I)
DEPT_BAD = re.compile(r"^(?:home|faculty|people|staff|our team|team|about|about us|department|departments|academics)$|"
                      r"\b(?:mobile|phone|e-?mail|ext\b|extn|room|cabin|year|years|mail|contact|https?)\b|\d{3,}", re.I)
INSTITUTION_BAD = re.compile(r"https?://|www\.|\b\d+\+?\s*(?:years?|yrs?)\b|^\s*\d|^(?:explore|discover|welcome|best|top|"
                             r"apply|call|click|find|search|view|know|admissions?)\b|"
                             # a degree line names where someone STUDIED, not where they work
                             r"\b(?:ph\.?\s?d|m\.?\s?tech|b\.?\s?tech|m\.?\s?sc|b\.?\s?sc|m\.?\s?e|b\.?\s?e|"
                             r"m\.?\s?phil|mba|pursuing|graduated|conference|symposium|workshop|formerly)\b|"
                             r"\b(?:19|20)\d\d\b", re.I)
HOSPITAL_BAD = INSTITUTION_BAD


def site_institution(soup, meta_info=None) -> str:
    """The college a page belongs to, as the page itself states it.

    Read from the site banner, not from the body: a profile page mentions the
    universities a person studied at, and those are not their employer.
    """
    from .structured import meta as page_meta
    info = meta_info if meta_info is not None else page_meta(soup)
    heading = soup.find("h1")
    options = [info.get("og:site_name") or "", heading.get_text(" ", strip=True) if heading else "",
               *re.split(r"\s*[|\u2013\u2014]\s*|\s+[-]\s+", info.get("title") or "")]
    for option in options:
        option = " ".join(str(option).split()).strip(" ,-|")
        if (5 <= len(option) <= 90 and INSTITUTION_WORDS.search(option)
                and not is_marketing_name(option) and not INSTITUTION_BAD.search(option)):
            return option
    return ""


def clean_designation(value) -> str | None:
    v = str(value or "").strip(" .-–:,")
    v = re.split(r"\s+at\s+|\s*,\s*(?=[^,]*\b(?:College|University|Institute|IIT|NIT)\b)", v, maxsplit=1)[0].strip(" .-–:,&")
    if not v or "|" in v or re.search(r"https?://|@", v) or len(v) > 100 or not TITLE_WORD.search(v):
        return None
    return v


def _drop(rec: PersonRecord, f: str) -> None:
    rec.values.pop(f, None)
    rec.layers.pop(f, None)


def clean_fields(rec: PersonRecord) -> None:
    inst = rec.get("institution")
    if inst and (INSTITUTION_BAD.search(str(inst)) or len(str(inst).split()) > 14):
        _drop(rec, "institution")
    quals = rec.get("qualifications")
    if quals:
        keep = [q for q in (quals if isinstance(quals, list) else [quals])
                if QUAL_RE.match(normalize_quals(str(q)).strip()) or not TITLE_WORD.search(str(q))]
        if keep:
            rec.values["qualifications"] = keep
        else:
            _drop(rec, "qualifications")
    if rec.get("designation"):
        des = clean_designation(rec.get("designation"))
        if des:
            rec.values["designation"] = des
        else:
            _drop(rec, "designation")
    dept = rec.get("department")
    if dept:
        d = re.sub(r"^(?:the\s+)?(?:department|dept\.?)\s+of\s+", "", str(dept), flags=re.I).strip(" .,-–:")
        if not d or DEPT_BAD.search(d) or len(d) > 120 or "@" in d or qualification_line(d) or re.match(r"(?:B|M)\s*\.?\s*(?:E|Tech)\s*\.?\s*\(", d, re.I) or re.search(r"research|interests?|expertise|speciali[sz]ation|,", d, re.I):
            _drop(rec, "department")
        else:
            canon = departments_in(d)
            # 'CSE', 'Dept. of IT', 'Mech' -> one canonical name, so a department groups the same across colleges.
            rec.values["department"] = canon[0] if len(canon) == 1 and len(d) <= 14 else d
    spec = rec.get("specialty")
    if spec and (len(str(spec)) > 200 or "@" in str(spec)):
        _drop(rec, "specialty")


def finalize(rec: PersonRecord, breadcrumbs: list[str], url: str) -> None:
    """Fill the department from the page path when the page says nothing else, and clean up noise."""
    clean_fields(rec)
    if not rec.get("department"):
        texts = " | ".join(str(rec.get(f) or "") for f in ("designation", "specialty"))
        found = departments_in(texts)
        if found:
            rec.set("department", found[0], "patterns", texts, url)
    inst = rec.get("institution")
    if isinstance(inst, list):
        rec.values["institution"] = "; ".join(inst)
    spec = rec.get("specialty") or ""
    if spec and qualification_line(str(spec)):
        rec.set("qualifications", parse_qualifications(str(spec)), "patterns", str(spec), url)
        _drop(rec, "specialty")
