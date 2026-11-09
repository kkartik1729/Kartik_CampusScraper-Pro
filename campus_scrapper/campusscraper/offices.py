"""Student-facing offices: placement cell, higher-studies cell, student chapters, alumni and international offices.

These pages are how a college reaches its students. The extractor records the
office, its role contacts (tpo@..., the office landline), its faculty or staff
coordinators, and any published counts of students going for higher studies.

Lines about individual students (student coordinators, club office-bearers,
roll numbers, 'III Year B.Tech') are left out together with their phones and
emails. Only the count of such lines is kept, so the reviewer can see that a
page had student contacts that were not taken.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from . import emails, phones
from .annotate import Line, annotate
from .models import Evidence, OfficeRecord
from .profile import city_from, find_name_in_text, split_designation
from .vocab import HIGHER_STUDIES_LINE, INSTITUTION_WORDS, OFFICE_TYPES, STAFF_IN_LINE, STUDENT_MARKERS

OFFICE_LABELS = {
    "placement": "Training & Placement", "higher_studies": "Higher Studies / Study Abroad",
    "student_chapter": "Student Chapter / Club", "alumni": "Alumni Office", "international": "International Office",
    "student_affairs": "Student Affairs / Welfare",
}


def office_type(url: str, title: str, h1: str, breadcrumbs: list[str]) -> str | None:
    """Classify from the page's own identity (path, h1, title, breadcrumbs), never from body text."""
    path = urlsplit(url).path.replace("/", " ")
    for text in (h1, path, " ".join(breadcrumbs[-2:]), title):
        if not text:
            continue
        for kind, rx in OFFICE_TYPES:
            if rx.search(text):
                return kind
    return None


SHOWCASE = re.compile(r"notable|prestigious|distinguished|eminent|achievers?|hall of fame|success|stars|"
                      r"testimonial|speak|stories|wall of", re.I)
OFFICE_ROLE = re.compile(r"officer|coordinator|co-ordinator|in-?charge|counsell?or|advisor|adviser|convener|convenor|"
                         r"\bdean\b|\bdirector\b|\bhead\b|\bchair(?:man|person)? of the (?:cell|committee)|tpo\b|"
                         r"mentor|nodal", re.I)


def _values(text: str) -> set[str]:
    return {n for n, _ in phones.find(text)} | set(emails.find(text))


ROLE_RE = re.compile(
    r"(?:[A-Z][A-Za-z&.]*\s+){0,4}(?i:officer|coordinator|co-ordinator|in-?charge|counsell?or|advisor|adviser|"
    r"convener|convenor|dean|director|head)\b(?:\s*\([^)]{2,30}\))?")


def is_student_line(text: str) -> bool:
    return bool(STUDENT_MARKERS.search(text)) and not STAFF_IN_LINE.search(text)


def _student_blocks(main: Tag) -> list[str]:
    """Texts of table rows, list items and cards that describe a student. Their contacts are skipped."""
    out: list[str] = []
    for el in main.find_all(["tr", "li", "div", "article", "p"]):
        if el.find(["tr", "li", "div", "article"]) is not None and el.name not in ("tr", "li"):
            continue  # innermost blocks only (plus rows and list items)
        text = " ".join(el.get_text(" ", strip=True).split())
        if 0 < len(text) <= 400 and is_student_line(text):
            out.append(text)
    # A table whose header says it lists students: every row is a student row.
    for table in main.find_all("table"):
        head = " ".join(c.get_text(" ", strip=True) for c in (table.find("tr") or table).find_all(["th", "td"])[:8])
        caption = table.find_previous(["h2", "h3", "h4", "caption", "strong"])
        ctext = (caption.get_text(" ", strip=True) if caption else "") + " " + head
        if re.search(r"\bstudent|roll\s*no|year\s*/\s*sem|\bbranch\b.*\byear\b|office[- ]bearers|core team", ctext, re.I) \
                and not re.search(r"faculty|staff", ctext, re.I):
            for tr in table.find_all("tr")[1:]:
                t = " ".join(tr.get_text(" ", strip=True).split())
                if t:
                    out.append(t)
    return list(dict.fromkeys(out))


def student_blocks(main: Tag) -> list[str]:
    """Texts on the page that describe individual students (public wrapper)."""
    return _student_blocks(main)


def extract_office(soup: BeautifulSoup, main: Tag, url: str, meta: dict, breadcrumbs: list[str]) -> OfficeRecord | None:
    h1_el = soup.find("h1")
    h1 = " ".join(h1_el.get_text(" ", strip=True).split()) if h1_el else ""
    title = meta.get("og:title") or meta.get("title") or ""
    kind = office_type(url, title, h1, breadcrumbs)
    if not kind:
        return None
    if kind == "alumni" and SHOWCASE.search(" ".join([h1, title, urlsplit(url).path.replace("-", " ")])):
        return None  # 'Notable Alumni', 'Prestigious Alumni': a showcase of graduates, not an office
    rec = OfficeRecord(page_url=url, office_type=kind, office_name=(h1 or title or OFFICE_LABELS[kind])[:150])
    site = meta.get("og:site_name") or ""
    inst = next((b for b in breadcrumbs if INSTITUTION_WORDS.search(b)), "") or (site if INSTITUTION_WORDS.search(site) else site)
    rec.institution = inst[:200]
    rec.city = city_from(inst) or ""

    student_texts = _student_blocks(main)
    student_values: set[str] = set()
    for t in student_texts:
        student_values.update(_values(t))
    # Each cell or piece of a student row on its own too: 'IV Year +91 90000 33333' reads as a year, not a phone.
    for el in main.find_all(["tr", "li", "div", "p"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if text in student_texts:
            for piece in el.get_text("\n", strip=True).split("\n"):
                student_values.update(_values(piece))
            student_values.update(n for n, _ in phones.from_links(el))
            student_values.update(emails.from_links(el))
    rec.skipped_student_lines = len(student_texts)

    lines: list[Line] = annotate(main)
    for i, line in enumerate(lines):
        text = line.text
        if is_student_line(text):
            continue
        # Coordinators: a staff name with an office role on the same or the next line.
        name = find_name_in_text(text) if len(text) < 200 else None
        if name:
            role, _ = split_designation(text)
            if not role and i + 1 < len(lines) and not is_student_line(lines[i + 1].text):
                role, _ = split_designation(lines[i + 1].text)
                if not role and re.search(r"officer|coordinator|co-ordinator|in-?charge|counsell?or|advisor|adviser|"
                                          r"head|dean|director|convener|convenor", lines[i + 1].text, re.I):
                    role = lines[i + 1].text[:80]
            if not role:
                after = text[text.find(name.split()[-1]) + len(name.split()[-1]):] if name.split()[-1] in text else text
                before = text[: max(0, text.find(name.split()[0]))]
                m = ROLE_RE.search(after) or ROLE_RE.search(before)
                role = m.group(0).strip(" ,:-") if m else None
            near = text + " " + (lines[i + 1].text if i + 1 < len(lines) else "")
            if not OFFICE_ROLE.search(near + " " + (role or "")):
                continue  # a name without an office role ('Dr. X, Professor, University of Y') is not a coordinator
            entry = f"{name}, {role}" if role else name
            if entry not in rec.coordinators and not any(c.startswith(name + ",") for c in rec.coordinators):
                rec.coordinators.append(entry)
                rec.evidence.append(Evidence("coordinators", entry, "office", text[:300], url))
        if HIGHER_STUDIES_LINE.search(text) and re.search(r"\d", text) and len(text) <= 300 \
                and re.search(r"stud|admit|offer|placed|selected|universit", text, re.I):
            if text not in rec.higher_studies_lines:
                rec.higher_studies_lines.append(text)
                rec.evidence.append(Evidence("higher_studies_lines", text, "office", text, url))

    full = "\n".join(l.text for l in lines if not is_student_line(l.text))
    found_emails = list(dict.fromkeys(emails.from_links(main) + emails.find(full)))
    for e in found_emails:
        if e not in student_values and e not in rec.emails:
            rec.emails.append(e)
            rec.evidence.append(Evidence("emails", e, "office", e, url))
    found_phones = phones.from_links(main) + phones.find(full)
    for n, ctx in found_phones:
        if n in student_values or n in rec.phones:
            continue
        # An unlabelled mobile on a club page is most likely a student's: keep only labelled or landline numbers.
        if kind == "student_chapter" and phones.is_mobile(n) and not re.search(
                r"faculty|professor|prof\.|dr\.|office|coordinator|counsell?or|advisor|officer", ctx, re.I):
            rec.skipped_student_lines += 1
            continue
        rec.phones.append(n)
        rec.evidence.append(Evidence("phones", n, "office", ctx[-120:], url))
    if not (rec.emails or rec.phones or rec.coordinators or rec.higher_studies_lines):
        return None if not h1 else rec
    return rec
