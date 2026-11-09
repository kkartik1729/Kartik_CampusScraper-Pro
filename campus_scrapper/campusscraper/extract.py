"""Page-level orchestrator: classify a page and run every extraction layer on it."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from . import emails, listing, offices, relevance, structured, templates
from .annotate import annotate
from .dom import links, main_content, parse
from .fetch import normalize_url
from .models import LIST_FIELDS, PersonRecord, Page, PageResult
from .profile import (
    _assign, find_name_in_text, finalize, labels_and_sections, parse_qualifications, patterns,
    plain_person_name, primary_name, qualification_line, site_institution,
)
from .vocab import ACADEMIC_LINKS, DESIGNATION_RE, NOT_STAFF_SECTION, ORCID_ID_RE, PROFILE_URL_HINTS, STAFF_SECTION


def _canon_name(raw: str) -> str | None:
    return find_name_in_text(raw) or plain_person_name(raw)


def not_staff_page(url: str, h1: str, breadcrumbs: list[str], meta: dict) -> bool:
    """A page about alumni, testimonials, toppers, recruiters or events lists no staff."""
    # Placement outcomes are not staff directories, even beneath /faculty/.
    if re.search(r"special[-_/ ]placement|placement[-_/ ](?:history|records?|highlights?|stories)|student[-_/ ]chapter[-_/ ]activities", urlsplit(url).path, re.I):
        return True
    path = urlsplit(url).path.replace("-", " ").replace("_", " ")
    for text in (path, h1, " ".join(breadcrumbs[-2:]), meta.get("og:title") or ""):
        if text and NOT_STAFF_SECTION.search(text) and not STAFF_SECTION.search(text):
            return True
    return False


def is_student_record(rec: PersonRecord) -> bool:
    """A person whose own card or title marks them as a student. Such records are dropped."""
    if rec.section_role == "student" or re.search(r"\bteaching assistant\b|\bresearch scholar\b", str(rec.get("designation") or ""), re.I):
        return True  # Teaching assistants may be enrolled students; do not retain ambiguous cases.
    if rec.get("designation") and not offices.is_student_line(str(rec.get("designation"))):
        return False
    texts = [str(rec.get("designation") or "")] + [e.snippet for e in rec.evidence if e.field in ("name", "designation")][:3]
    return any(offices.is_student_line(t) for t in texts if t)


def _surname(name: str) -> str:
    return name.split()[-1].strip(".").lower()


def apply_candidate(rec: PersonRecord, cand: structured.Candidate, url: str) -> None:
    for f, v in cand.values.items():
        if f == "name":
            continue
        if f == "_sameas":
            for link in (v if isinstance(v, list) else [v]):
                for lf, rx in ACADEMIC_LINKS.items():
                    if isinstance(link, str) and rx.search(link):
                        m = ORCID_ID_RE.search(link) if lf == "orcid" else None
                        rec.set(lf, m.group(0) if m else link, cand.layer, cand.snippet, url)
            continue
        if f == "photo_url":
            val = v[0] if isinstance(v, list) else v
            if isinstance(val, str) and not val.startswith("data:"):
                rec.set(f, urljoin(url, val), cand.layer, cand.snippet, url)
            continue
        if f in LIST_FIELDS:
            items = v if isinstance(v, list) else [v]
            if f == "qualifications":
                parsed: list[str] = []
                for it in items:
                    parsed.extend(parse_qualifications(it) or [it])
                rec.set(f, parsed, cand.layer, cand.snippet, url)
            elif f in ("research_areas", "subjects", "memberships"):
                rec.set(f, [x.strip() for it in items for x in re.split(r"[,;]", it) if x.strip()], cand.layer, cand.snippet, url)
            else:
                rec.set(f, items, cand.layer, cand.snippet, url)
            continue
        val = ", ".join(v) if isinstance(v, list) else v
        if f in ("about",):
            rec.set(f, val[:1500], cand.layer, cand.snippet, url)
        else:
            _assign(rec, f, val, cand.layer, cand.snippet, url)
    if cand.url:
        rec.profile_url = rec.profile_url or normalize_url(urljoin(url, cand.url))


def candidate_record(cand: structured.Candidate, url: str) -> PersonRecord | None:
    name = _canon_name(cand.name or "")
    if not name:
        return None
    rec = PersonRecord(source_url=url, page_type="listing")
    rec.set("name", name, cand.layer, cand.snippet, url)
    apply_candidate(rec, cand, url)
    finalize(rec, [], rec.profile_url or url)
    return rec


def _page_contacts(soup) -> list[str]:
    """Every phone and email the whole page shows (header, footer and contact lists included)."""
    from . import phones
    body = soup.body or soup
    text = " | ".join(body.get_text(" | ", strip=True).split(" | "))
    found = [n for n, _ in phones.find(text)] + [n for n, _ in phones.from_links(body)]
    found += emails.find(text) + emails.from_links(body)
    for node in soup.find_all("script", type="application/ld+json"):  # structured data too
        raw = node.string or ""
        found += [n for n, _ in phones.find(raw.replace('"', " | "))] + emails.find(raw)
    return list(dict.fromkeys(found))


def extract_page(page: Page) -> PageResult:
    url = page.final_url
    if page.error or not page.html:
        return PageResult(url, "error", signals={"error": page.error})
    soup = parse(page.html)
    emails.decode_protected(soup)
    meta = structured.meta(soup)
    ld_docs, ld_page = structured.jsonld(soup)
    md_docs = structured.microdata(soup)
    emb_docs = structured.embedded_json(soup)
    breadcrumbs = structured.html_breadcrumbs(soup) or ld_page.get("breadcrumbs", [])
    all_links = links(soup, url)
    from .discovery import directory_links, directory_next
    all_links.extend((u, "Faculty directory") for u in directory_links(soup, url))
    main = main_content(soup)
    lines = annotate(main)
    page_contacts = _page_contacts(soup)

    struct_docs = ld_docs + md_docs
    h1 = soup.find("h1")
    h1_text = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""
    h1_name = find_name_in_text(h1_text) if h1_text and len(h1_text) < 120 else None

    # ---- decide whether this is one doctor's profile -------------------
    single_struct = None
    if len(struct_docs) == 1:
        single_struct = struct_docs[0]
    elif h1_name:
        sur = _surname(h1_name)
        single_struct = next((c for c in struct_docs + emb_docs if c.name and sur in c.name.lower()), None)
    elif len(emb_docs) == 1 and not struct_docs:
        single_struct = emb_docs[0]

    struct_names = [n for n in [_canon_name(single_struct.name)] if n] if single_struct and single_struct.name else []
    name, name_ev, name_idx, name_src = primary_name(lines, soup, meta, url, struct_names)
    if single_struct is None and name:
        sur = _surname(name)
        first = name.split()[1].lower() if len(name.split()) > 2 else ""
        matches = [c for c in struct_docs + emb_docs if c.name and sur in c.name.lower() and first in c.name.lower()]
        if matches:
            single_struct = max(matches, key=lambda c: len(c.values))

    tpl_rec = PersonRecord(source_url=url, profile_url=url)
    tpl_hit = templates.apply_profile(soup, url, tpl_rec)
    tpl_cards = templates.apply_listing(soup, url)

    exclude = {name} if name and (h1_name or single_struct is not None or (tpl_hit and tpl_rec.get("name"))) else set()
    card_recs = tpl_cards if tpl_cards else listing.cards(main, url, exclude)
    if not card_recs and not tpl_cards:
        card_recs = listing.repeated_cards(main, url)
    table_recs = listing.tables(main, url)
    multi = [c for c in (struct_docs + emb_docs) if c is not single_struct]

    head_text = " ".join(l.text for l in lines[name_idx: name_idx + 15])
    strong = bool(tpl_hit and tpl_rec.get("name")) or bool(h1_name) or single_struct is not None
    weak = bool(
        name and name_src in ("meta", "h1plain", "url", "heading")
        and (PROFILE_URL_HINTS.search(urlsplit(url).path) or qualification_line(head_text) or DESIGNATION_RE.search(head_text))
        and len(card_recs) <= 1 and len(table_recs) < 2
    )
    profile_evidence = strong or weak
    if not strong and table_recs:
        profile_evidence = False  # Even a one-row directory can link to a separate CV.
    # A page listing three or more other doctors with links is a listing, even if one name sits in the h1
    # (AOI '/our-doctors/clinical-faculty' put the first doctor's name in a heading).
    linked_cards = {normalize_url(urljoin(url, c.profile_url)) for c in card_recs if c.profile_url} - {normalize_url(url)}
    if profile_evidence and name and len(linked_cards) >= 3 and not tpl_cards:
        # Only when the heading doctor is themselves one of the linked cards; a real profile with a
        # 'related doctors' strip keeps its own doctor.
        all_cards = listing.cards(main, url, set())
        in_cards = [c for c in all_cards if (c.get("name") or "").casefold() == name.casefold() and c.profile_url
                    and normalize_url(urljoin(url, c.profile_url)) != normalize_url(url)]
        if in_cards:
            profile_evidence = False
            card_recs = all_cards
    signals = {"h1": h1_text[:120], "name": name, "name_source": name_src, "name_evidence": name_ev[:200], "cards": len(card_recs),
               "table_rows": len(table_recs), "jsonld_doctors": len(ld_docs), "microdata_doctors": len(md_docs),
               "embedded_doctors": len(emb_docs), "template": bool(tpl_hit or tpl_cards)}

    result = PageResult(url, "other", links=[u for u, _ in all_links], link_texts=dict(all_links), signals=signals)
    result.profile_links = listing.profile_links(all_links)
    result.pagination_links = listing.pagination_links(soup, url)
    next_directory = directory_next(url, page.html)
    if next_directory:
        result.pagination_links.append(next_directory)
    page_context = " ".join([h1_text, " ".join(breadcrumbs)])
    first_heading = next((l.text for l in lines if l.kind == "h" and len(l.text) < 120), "")
    page_depts = (relevance.departments_in(h1_text) or relevance.departments_in(" ".join(breadcrumbs[-2:]))
                  or relevance.departments_in(first_heading) or relevance.departments_in(meta.get("title") or ""))

    if profile_evidence and name:
        rec = tpl_rec if tpl_hit else PersonRecord(source_url=url, profile_url=url)
        rec.page_type = "profile"
        rec.profile_url = normalize_url(meta.get("canonical") and urljoin(url, meta["canonical"]) or url)
        rec.set("name", tpl_rec.get("name") and _canon_name(tpl_rec.get("name")) or name,
                "template" if tpl_hit and tpl_rec.get("name") else ("h1" if h1_name else single_struct.layer if single_struct and struct_names else "patterns"),
                name_ev, url)
        if single_struct:
            apply_candidate(rec, single_struct, url)
            rec.profile_url = normalize_url(urljoin(url, meta.get("canonical") or url))
        if ld_page.get("institution"):
            rec.set("institution", ld_page["institution"], "jsonld", "JSON-LD organisation", url)
        if ld_page.get("city"):
            rec.set("city", ld_page["city"], "jsonld", "JSON-LD address", url)
        labels_and_sections(rec, lines, url)
        patterns(rec, lines, name_idx, main, url, meta, breadcrumbs)
        finalize(rec, breadcrumbs, url)
        relevance.score(rec, " ".join(breadcrumbs))
        result.page_type = "profile"
        result.doctors.append(rec)

    listing_recs: list[PersonRecord] = table_recs + list(card_recs)
    for cand in multi:
        r = candidate_record(cand, url)
        if r:
            listing_recs.append(r)
    seen = {d.get("name", "").casefold() for d in result.doctors}
    for r in listing_recs:
        key = (r.get("name") or "").casefold()
        if not key or key in seen:
            continue
        if result.page_type == "profile" and (not r.profile_url or normalize_url(urljoin(url, r.profile_url)) == normalize_url(url)):
            # On one doctor's page, other 'Dr. X' mentions without their own profile link are text
            # (awards, 'Dr. Krishna has a special interest...', video titles), not more doctors.
            continue
        seen.add(key)
        if not r.get("department") and page_depts and len(page_depts) == 1:
            # A department's faculty page names the department once, in its heading or breadcrumbs.
            r.set("department", page_depts[0], "page", page_context[:200], url)
        if r.layers.get("name") != "template":
            finalize(r, [], r.profile_url or url)
        relevance.score(r, page_context)
        result.doctors.append(r)
    # Alumni, toppers, testimonials, recruiters and guests are not the college's people: they are left out.
    if not_staff_page(url, h1_text, breadcrumbs, meta):
        result.doctors = []
        result.signals["not_staff_page"] = True
    # Keep what was dropped, and why: a page whose only people are students or
    # alumni is evidence about the page, not just noise to discard.
    result.signals["dropped_students"] = sum(1 for d in result.doctors if is_student_record(d))
    result.signals["dropped_alumni"] = sum(1 for d in result.doctors if d.section_role == "alumni")
    result.doctors = [d for d in result.doctors if not is_student_record(d) and d.section_role != "alumni"]
    # Where these people WORK is stated by the site they are published on. A
    # profile page also names the universities they studied at; those are not
    # their employer, so the site's own name outranks anything in the body.
    whose_site = site_institution(soup, meta)
    if whose_site:
        for d in result.doctors:
            d.set("institution", whose_site, "site", "the site's own name", url)
        result.signals["site_institution"] = whose_site
    office = offices.extract_office(soup, main, url, meta, breadcrumbs)
    if office:
        if not office.institution and ld_page.get("institution"):
            office.institution = ld_page["institution"]
        if whose_site:
            office.institution = whose_site
        result.offices.append(office)
        result.signals["office"] = office.office_type
    result.signals["page_contacts"] = page_contacts
    if result.page_type != "profile" and len(result.doctors) >= 1:
        result.page_type = "listing"
    # Profile links found in cards come first so the crawler follows them.
    card_links = [d.profile_url for d in result.doctors if d.page_type == "listing" and d.profile_url]
    result.profile_links = list(dict.fromkeys(card_links + result.profile_links))
    return result
