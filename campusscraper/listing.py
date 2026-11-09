"""Many-person pages: faculty card grids, staff tables and link lists."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import Tag

from . import emails, phones
from .dom import text_lines
from .fetch import normalize_url
from .models import PersonRecord
from .profile import (
    academic_links, assign_emails, assign_phones, city_from, find_name_in_text, parse_experience, parse_qualifications,
    plain_person_name, qualification_line, split_designation, sub_specialties,
)
from .vocab import (
    ALUMNI_CARD, INSTITUTION_WORDS, NAME_RE, NOT_STAFF_SECTION, PROFILE_URL_HINTS, STAFF_SECTION, TEACHING_WORDS,
    label_field,
)

NAME_TAGS = ("a", "h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "span", "div", "td", "li", "figcaption")
LINK_TEXT_HINT = re.compile(r"view|profile|know more|read more|details|more info|meet|vidwan|irins", re.I)
BOOKING = re.compile(r"apply|admission|enquir|whatsapp|video|login|register|scholar\.google|orcid\.org|scopus|"
                     r"researchgate|linkedin|vidwan|irins|dblp", re.I)
HONORIFIC_START = re.compile(r"^\s*(?:Dr|Prof|Doctor|Professor|Mr|Mrs|Ms|Miss|Shri|Sri|Smt|Kum|Er)\b\.?", re.I)


def _name_from_parts(el: Tag) -> str | None:
    """Read the name from the element's text pieces in order, stopping once a full name is
    found, so a department in the next piece ("Dr. X" then "Computer Science") is not glued on."""
    parts = [p for p in el.get_text("\n", strip=True).split("\n") if p.strip()]
    best = None
    for k in range(1, len(parts) + 1):
        name = find_name_in_text(" ".join(parts[:k]))
        if name:
            best = name
            if len(name.split()) >= 3:  # honorific + at least two name words
                return name
    return best


def _name_nodes(main: Tag) -> list[tuple[Tag, str]]:
    """Smallest elements whose whole text is a doctor's name (optionally with trailing degree)."""
    out: list[tuple[Tag, str]] = []
    for el in main.find_all(NAME_TAGS):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text or len(text) > 90:
            continue
        name = _name_from_parts(el)
        if not name or not HONORIFIC_START.match(text):
            continue
        # Skip if a child already carries the same name (keep the smallest node).
        if any(isinstance(c, Tag) and find_name_in_text(c.get_text(" ", strip=True) or "") == name for c in el.find_all(NAME_TAGS)):
            continue
        out.append((el, name))
    return out


def _card_for(node: Tag, name: str, all_names: set[str]) -> Tag:
    card = node
    for _ in range(8):
        parent = card.parent
        if parent is None or parent.name in ("body", "html", "main", "[document]"):
            break
        text = parent.get_text(" ", strip=True)
        if len(text) > 1800:
            break
        others = {find_name_in_text(m.group(0)) for m in NAME_RE.finditer(text)} - {None, name}
        if others & all_names:
            break
        card = parent
    return card


def _profile_link(card: Tag, name: str, base_url: str) -> str | None:
    surname = name.split()[-1].lower()
    best = None
    for a in card.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "tel:", "mailto:", "javascript:", "whatsapp:")):
            continue
        try:
            absolute = normalize_url(urljoin(base_url, href))
        except ValueError:
            continue
        text = a.get_text(" ", strip=True)
        if BOOKING.search(href) or (BOOKING.search(text) and not LINK_TEXT_HINT.search(text)):
            continue
        score = 0
        if surname in absolute.lower() or surname in text.lower():
            score += 3
        if PROFILE_URL_HINTS.search(urlsplit(absolute).path):
            score += 2
        if LINK_TEXT_HINT.search(text):
            score += 1
        if best is None or score > best[0]:
            best = (score, absolute)
    if card.name == "a" and card.get("href"):
        try:
            return normalize_url(urljoin(base_url, card["href"]))
        except ValueError:
            pass
    parent_a = card.find_parent("a", href=True)
    if (best is None or best[0] == 0) and parent_a:
        try:
            return normalize_url(urljoin(base_url, parent_a["href"]))
        except ValueError:
            pass
    return best[1] if best and best[0] > 0 else None


def leaf_lines(card: Tag) -> list[str]:
    """Texts of innermost elements; splits inline <span>name</span><span>role</span> layouts."""
    out: list[str] = []
    for el in card.find_all(True):
        if el.find(True) is None:
            t = " ".join(el.get_text(" ", strip=True).split())
            if t and (not out or out[-1] != t):
                out.append(t)
    return out


def _attr_words(el: Tag) -> str:
    cls = el.get("class") or []
    return " ".join([*(cls if isinstance(cls, list) else [cls]), el.get("id") or "", el.get("aria-label") or ""])


def section_role(card: Tag) -> str:
    """'alumni' when the card sits in an alumni, testimonial, topper or recruiter block of the page.

    Looks at the card's own wrappers (class/id such as 'alumni-slider') first, then the nearest heading above it.
    """
    el = card
    for _ in range(10):
        if el is None or getattr(el, "name", None) in (None, "body", "html", "[document]"):
            break
        words = _attr_words(el)
        if words.strip():
            if NOT_STAFF_SECTION.search(words):
                return "alumni"
            if STAFF_SECTION.search(words):
                return ""
        el = el.parent
    # The section heading above the card; card titles that are just a person's name are skipped.
    for heading in card.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"], limit=40):
        text = " ".join(heading.get_text(" ", strip=True).split())[:120]
        if not text or NAME_RE.search(text) or plain_person_name(text):
            continue
        return "alumni" if NOT_STAFF_SECTION.search(text) and not STAFF_SECTION.search(text) else ""
    return ""


def card_record(card: Tag, name: str, base_url: str) -> PersonRecord:
    rec = PersonRecord(source_url=base_url, page_type="listing")
    rec.section_role = section_role(card)
    card_text = " ".join(card.get_text(" ", strip=True).split())[:600]
    if not rec.section_role and ALUMNI_CARD.search(card_text) and not TEACHING_WORDS.search(card_text):
        rec.section_role = "alumni"  # a graduate's card ('Alumnus', 'Batch 2012', 'Software Engineer at X')
    lines = text_lines(card)
    leaves = leaf_lines(card)
    if len(lines) <= 2 and len(leaves) > len(lines):
        lines = leaves
    snippet = " | ".join(lines)[:500]
    rec.set("name", name, "listing_card", snippet)
    link = _profile_link(card, name, base_url)
    if link:
        rec.profile_url = link
    surname = name.split()[-1].lower()
    for line in lines:
        low = line.lower()
        if surname in low and len(line) < 160 and find_name_in_text(line):
            # The name line may carry degrees or the title: "Dr. A B, Ph.D", "Dr. A B, Training and Placement Officer"
            if qualification_line(line):
                rec.set("qualifications", parse_qualifications(line), "listing_card", line)
            tail = line[low.rfind(surname) + len(surname):]
            role, rest = split_designation(tail) if tail.strip(" ,-–:") else (None, None)
            if role and "designation" not in rec.values:
                rec.set("designation", role, "listing_card", line)
                if rest:
                    rec.set("department", rest, "listing_card", line)
            continue
        if ":" in line[:40]:
            label, value = line.split(":", 1)
            f = label_field(label)
            if f and value.strip():
                _set_card_field(rec, f, value.strip(), line)
                continue
        if BOOKING.search(line) and len(line) < 40:
            continue
        if "designation" not in rec.values:
            role, rest = split_designation(line)
            if role and len(line) < 160:
                rec.set("designation", role, "listing_card", line)
                if rest:
                    rec.set("department", rest, "listing_card", line)
                continue
        if "qualifications" not in rec.values and qualification_line(line):
            rec.set("qualifications", parse_qualifications(line), "listing_card", line)
            continue
        if "experience_years" not in rec.values and re.search(r"year|yrs|exp", line, re.I):
            v = parse_experience(line)
            if v:
                rec.set("experience_years", v, "listing_card", line)
                continue
        if "department" not in rec.values and len(line) < 90 and (sub_specialties(line) or re.search(r"\bdepartment of\b|\bdept\.? of\b", line, re.I)):
            rec.set("department", re.sub(r"^(?:the\s+)?(?:department|dept\.?)\s+of\s+", "", line, flags=re.I), "listing_card", line)
            continue
        if "institution" not in rec.values and INSTITUTION_WORDS.search(line) and len(line) < 120:
            rec.set("institution", line, "listing_card", line)
            continue
        if re.search(r"research|interest", low) and len(line) < 300 and "research_areas" not in rec.values:
            val = line.split(":", 1)[1] if ":" in line else ""
            if val.strip():
                rec.set("research_areas", [v.strip() for v in re.split(r"[,;]", val) if v.strip()], "listing_card", line)
    card_emails = emails.from_links(card) + emails.find(" | ".join(lines))
    if card_emails:
        assign_emails(rec, list(dict.fromkeys(card_emails)), "listing_card", snippet, base_url)
    card_phones = phones.from_links(card) + phones.find(" | ".join(lines))
    if card_phones:
        seen_nums: set[str] = set()
        unique = [(n, c) for n, c in card_phones if not (n in seen_nums or seen_nums.add(n))]
        assign_phones(rec, unique, "listing_card", snippet, base_url)
    if "city" not in rec.values:
        c = city_from(" ".join(lines))
        if c:
            rec.set("city", c, "listing_card", snippet)
    academic_links(rec, card, base_url, "listing_card")
    img = card.find("img")
    if img and (img.get("src") or img.get("data-src")) and not str(img.get("src", "")).startswith("data:"):
        rec.set("photo_url", urljoin(base_url, img.get("data-src") or img.get("src")), "listing_card", str(img)[:200])
    return rec


def _set_card_field(rec: PersonRecord, f: str, value: str, line: str) -> None:
    if f == "qualifications":
        rec.set(f, parse_qualifications(value, labelled=True) or [value], "listing_card", line)
    elif f == "experience_years":
        v = parse_experience(value) or parse_experience(value + " years experience")
        if v:
            rec.set(f, v, "listing_card", line)
    elif f == "designation":
        role, rest = split_designation(value)
        rec.set(f, role or value, "listing_card", line)
        if rest:
            rec.set("department", rest, "listing_card", line)
    elif f in ("research_areas", "subjects", "memberships", "awards", "education"):
        rec.set(f, [v.strip() for v in re.split(r"[,;]", value) if v.strip()], "listing_card", line)
    elif f in ("name", "about"):
        return
    elif f in ("email", "contact_email"):
        assign_emails(rec, emails.find(value), "listing_card", line, rec.source_url)
    elif f in ("phone", "contact_phone"):
        assign_phones(rec, [(n, line) for n, _ in phones.find(value)], "listing_card", line, rec.source_url)
    else:
        rec.set(f, value[:200], "listing_card", line)


def cards(main: Tag, base_url: str, exclude: set[str] | None = None) -> list[PersonRecord]:
    nodes = _name_nodes(main)
    names = {n for _, n in nodes}
    exclude = {e.casefold() for e in (exclude or set())}
    out: list[PersonRecord] = []
    used_cards: list[Tag] = []
    by_name: dict[str, PersonRecord] = {}
    for node, name in nodes:
        if name.casefold() in exclude:
            continue
        card = _card_for(node, name, names)
        if any(card is c or card in c.descendants for c in used_cards):
            # Another name already owns this region (e.g. name repeated in alt text).
            continue
        used_cards.append(card)
        rec = card_record(card, name, base_url)
        if name.casefold() in by_name:
            by_name[name.casefold()].absorb(rec)
        else:
            by_name[name.casefold()] = rec
            out.append(rec)
    return out


ACADEMIC_HINT = re.compile(r"\b(?:professor|lecturer|faculty|hod|head|dean|principal|director|ph\.?\s?d|m\.?\s?tech|"
                           r"m\.?\s?e\b|m\.?\s?sc|mca|mba|department|dept|research|scientist|reader|"
                           r"assistant|associate)\b", re.I)


def repeated_cards(main: Tag, base_url: str) -> list[PersonRecord]:
    """Card grids whose names carry no 'Dr.' prefix: find repeated sibling blocks."""
    groups: dict[tuple, list[Tag]] = {}
    for el in main.find_all(["div", "li", "article", "section", "a"]):
        cls = tuple(sorted(c for c in (el.get("class") or []) if not re.search(r"\d|active|odd|even|first|last", c)))
        if not cls or el.parent is None:
            continue
        groups.setdefault((id(el.parent), el.name, cls), []).append(el)
    out: list[PersonRecord] = []
    for members in groups.values():
        if len(members) < 3:
            continue
        good = []
        for m in members:
            text = m.get_text(" ", strip=True)
            if not 15 <= len(text) <= 1500 or not ACADEMIC_HINT.search(text):
                continue
            name = None
            for el in m.find_all(["a", "h2", "h3", "h4", "h5", "h6", "strong", "b", "span", "p", "div", "figcaption"]):
                t = " ".join(el.get_text(" ", strip=True).split())
                if t and len(t) <= 50:
                    name = find_name_in_text(t) or plain_person_name(t)
                    if name:
                        break
            if name:
                good.append((m, name))
        if len(good) >= 3 and len(good) >= len(members) * 0.6:
            for m, name in good:
                out.append(card_record(m, name, base_url))
    seen, unique = set(), []
    for r in out:
        k = r.get("name").casefold()
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique


# --------------------------------------------------------------------------
HEADER_FIELDS = {
    "name": "name", "faculty": "name", "faculty name": "name", "name of faculty": "name", "name of the faculty": "name",
    "name of the faculty member": "name", "staff name": "name", "name of the staff": "name", "name of staff": "name",
    "name of the teacher": "name", "teacher": "name", "faculty member": "name", "member": "name", "members": "name",
    "name designation": "name", "name and designation": "name", "faculty name designation": "name",
    "name of the faculty designation": "name", "name of the faculty and designation": "name",
    "college name": "institution", "name of college": "institution", "college": "institution",
    "subject": "subjects", "subject name": "subjects",
}


def _table_grid(table):
    """Expand HTML spans before mapping headers to data columns."""
    grid = []
    pending = {}
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = {}
        for col, (cell, remaining) in list(pending.items()):
            cells[col] = cell
            if remaining <= 1:
                del pending[col]
            else:
                pending[col] = (cell, remaining - 1)
        col = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while col in cells:
                col += 1
            try:
                width = max(1, min(100, int(cell.get("colspan", 1))))
                height = max(1, min(100, int(cell.get("rowspan", 1))))
            except (ValueError, TypeError):
                width = height = 1
            for offset in range(width):
                cells[col + offset] = cell
                if height > 1:
                    pending[col + offset] = (cell, height - 1)
            col += width
        if cells:
            grid.append((row, [cells.get(i) for i in range(max(cells) + 1)]))
    return grid


def tables(main: Tag, base_url: str) -> list[PersonRecord]:
    """Faculty tables: header row with Name + Designation/Department/Qualification."""
    out: list[PersonRecord] = []
    for table in main.find_all("table"):
        heading = table.find_previous(["h1", "h2", "h3", "h4", "caption"])
        if heading and re.search(r"\bstudents?\b|research scholars|office[- ]bearers", heading.get_text(" ", strip=True), re.I) and not re.search(r"faculty|staff", heading.get_text(" ", strip=True), re.I):
            continue
        rows = _table_grid(table)
        if len(rows) < 2:
            continue
        header_cells = rows[0][1]
        headers = [" ".join(c.get_text(" ", strip=True).lower().split()) if c else "" for c in header_cells]
        mapping: dict[int, str] = {}
        college_name_columns: set[int] = set()
        subject_columns: set[int] = set()
        for idx, h in enumerate(headers):
            h2 = " ".join(re.sub(r"[^a-z ]", " ", h).split())
            f = HEADER_FIELDS.get(h2) or label_field(h2)
            if f:
                mapping[idx] = f
                if h2 in {"college name", "name of college", "college"}:
                    college_name_columns.add(idx)
                if h2 in {"subject", "subject name"}:
                    subject_columns.add(idx)
        affiliation_table = bool(college_name_columns and subject_columns)
        if "name" not in mapping.values() or len(mapping) < 2:
            continue
        for row, cells in rows[1:]:
            if len(cells) < len(mapping):
                continue
            rec = PersonRecord(source_url=base_url, page_type="listing")
            rec.section_role = section_role(table)
            snippet = " | ".join(c.get_text(" ", strip=True) if c else "" for c in cells)[:500]
            for idx, f in mapping.items():
                if idx >= len(cells) or cells[idx] is None:
                    continue
                value = " ".join(cells[idx].get_text(" ", strip=True).split())
                if not value:
                    continue
                if f == "experience_years" and sum(v == f for v in mapping.values()) > 1:
                    # A grouped header (industry + academic) is not a scalar total.
                    # Keep no guessed total when the source does not label one.
                    continue
                if f == "name":
                    parts = [t for t in cells[idx].get_text("\n", strip=True).split("\n") if t.strip()]
                    name = find_name_in_text(value) or plain_person_name(value) or (
                        (find_name_in_text(parts[0]) or plain_person_name(parts[0])) if parts else None)
                    if name and "designation" not in mapping.values():
                        rest = value[value.find(name.split()[-1]) + len(name.split()[-1]):] if name.split()[-1] in value else ""
                        role, dept = split_designation(rest) if rest.strip(" ,-–:") else (None, None)
                        if role:
                            rec.set("designation", role, "table_row", snippet)
                            if dept:
                                rec.set("department", dept, "table_row", snippet)
                    if name:
                        rec.set("name", name, "table_row", snippet)
                        a = cells[idx].find("a", href=True)
                        if a:
                            try:
                                rec.profile_url = normalize_url(urljoin(base_url, a["href"]))
                            except ValueError:
                                pass
                elif f == "qualifications":
                    rec.set(f, parse_qualifications(value, labelled=True) or [value], "table_row", snippet)
                elif f == "designation":
                    role, rest = split_designation(value)
                    rec.set(f, role or value, "table_row", snippet)
                    if rest:
                        rec.set("department", rest, "table_row", snippet)
                elif f in ("email", "contact_email"):
                    addrs = emails.find(value) + emails.from_links(cells[idx])
                    assign_emails(rec, list(dict.fromkeys(addrs)), "table_row", snippet, base_url)
                elif f in ("phone", "contact_phone"):
                    nums = [(n, headers[idx] + " " + c) for n, c in phones.find(value) + phones.from_links(cells[idx])]
                    assign_phones(rec, nums, "table_row", snippet, base_url)
                elif f == "institution" and idx in college_name_columns:
                    rec.set(f, value, "affiliation_table", snippet)
                elif f == "subjects":
                    rec.set(f, [v.strip() for v in re.split(r"[,;]", value) if v.strip()], "table_row", snippet)
                    if affiliation_table and idx in subject_columns:
                        rec.set("department", value, "table_row", snippet)
                elif f in ("research_areas", "education", "awards", "memberships"):
                    rec.set(f, [v.strip() for v in re.split(r"[,;]", value) if v.strip()], "table_row", snippet)
                else:
                    rec.set(f, value, "table_row", snippet)
            if rec.get("name"):
                row_addrs = emails.find(row.get_text(" | ", strip=True)) + emails.from_links(row)
                assign_emails(rec, list(dict.fromkeys(row_addrs)), "table_row", snippet, base_url)
                if not rec.profile_url:
                    rec.profile_url = _profile_link(row, rec.get("name"), base_url)
                academic_links(rec, row, base_url, "table_row")
                # A table caption/heading often names the department.
                heading = table.find_previous(["h1", "h2", "h3", "h4", "caption"])
                if heading and "department" not in rec.values:
                    htext = heading.get_text(" ", strip=True)
                    if sub_specialties(htext) or re.search(r"department|dept", htext, re.I):
                        rec.set("department", re.sub(r"^.*?(?:department|dept\.?)\s+of\s+", "", htext, flags=re.I), "table_row", htext)
                out.append(rec)
    return out


def profile_links(all_links: list[tuple[str, str]]) -> list[str]:
    """Links that look like individual doctor profiles."""
    out = []
    for url, text in all_links:
        path = urlsplit(url).path
        if BOOKING.search(path):
            continue
        if NAME_RE.match(text.strip()) or PROFILE_URL_HINTS.search(path):
            out.append(url)
    return out


def pagination_links(main_soup: Tag, base_url: str) -> list[str]:
    out: list[str] = []
    base_path = urlsplit(base_url).path.rstrip("/")
    for a in main_soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        rel = " ".join(a.get("rel") or [])
        try:
            href = normalize_url(urljoin(base_url, a["href"]))
        except ValueError:
            continue
        p = urlsplit(href)
        is_pager = (
            "next" in rel
            or re.fullmatch(r"\d{1,3}|next|next ?›|›|»|>|next page|load more|more", text, re.I) is not None
            or re.search(r"[?&](?:page|p|pg|paged)=\d+|/page/\d+", href, re.I) is not None
        )
        if is_pager and (p.path.rstrip("/").startswith(base_path) or base_path.startswith(p.path.rstrip("/").rsplit("/page", 1)[0])):
            if href != normalize_url(base_url) and href not in out:
                out.append(href)
    for link in main_soup.find_all("link", rel=lambda r: r and "next" in r):
        if link.get("href"):
            try:
                out.append(normalize_url(urljoin(base_url, link["href"])))
            except ValueError:
                continue
    return out
