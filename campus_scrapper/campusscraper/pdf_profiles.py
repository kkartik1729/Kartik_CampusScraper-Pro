"""Read public faculty CVs only when a named faculty row links to the PDF."""
from __future__ import annotations

import re

from . import emails, relevance
from .models import Page, PageResult, PersonRecord
from .profile import assign_emails, assign_phones, clean_fields
from . import phones


def _name_confirmed(tokens: list[str], text: str) -> bool:
    identity = "\n".join(line for line in text.splitlines()[:40] if "@" not in line and "http" not in line.lower())
    pattern = r"(?<![a-z])" + r"[\W_]*".join(re.escape(t) for t in tokens) + r"(?![a-z])"
    if len(tokens) >= 2 and re.search(pattern, identity, re.I):
        return True
    # A CV's explicitly labelled name may reorder surname/first name, abbreviate
    # middle names, or omit a middle name. Require both full first and last names
    # and reject any conflicting extra name, rather than matching a surname alone.
    if len(tokens) < 2 or min(len(tokens[0]), len(tokens[-1])) < 3:
        return False
    for line in text.splitlines()[:40]:
        match = re.match(r"^\s*name(?: of (?:the )?(?:faculty|teacher))?\s*[:\-]?\s+(.{3,100})$", line, re.I)
        if not match:
            continue
        candidate = re.sub(r"\b(dr|prof|professor|mr|mrs|ms)\b", "", match[1].lower())
        words = re.findall(r"[a-z]+", candidate)
        if tokens[0] in words and tokens[-1] in words and all(
                w in tokens or (len(w) == 1 and any(t.startswith(w) for t in tokens)) for w in words):
            return True
    return False


def extract_faculty_pdf(page: Page, expected: PersonRecord | None) -> PageResult:
    result = PageResult(page.final_url, "other")
    if page.error:
        return PageResult(page.final_url, "error", signals={"error": page.error})
    if expected is None:
        return result
    # Only the introductory identity/contact block; publications may name other people.
    text = re.split(r"\b(?:publications|references|research papers|journal papers)\b", page.html[:6000], flags=re.I)[0]
    name = str(expected.get("name") or "")
    tokens = re.findall(r"[a-z]+", re.sub(r"\b(dr|prof|professor|mr|mrs|ms)\b", "", name.lower()))
    if not _name_confirmed(tokens, text):
        return result
    if not re.search(r"professor|lecturer|head of department", text, re.I):
        return result
    if re.search(r"\bteaching assistant\b|\bresearch scholar\b", str(expected.get("designation") or ""), re.I):
        return result
    rec = PersonRecord(source_url=page.final_url, profile_url=page.final_url, page_type="profile")
    rec.set("name", name, "pdf", "Faculty name confirmed in linked PDF", page.final_url)
    for field in ("designation", "department", "institution"):
        if expected.get(field):
            rec.set(field, expected.get(field), "listing", "Linked faculty row", expected.source_url)
    # Multiple addresses in a CV are not all the author's. Accept name-associated addresses,
    # or a single explicitly labelled non-desk email in the identity block.
    addrs = emails.find(text)
    own = [a for a in addrs if emails.is_personal(a, name)]
    if not own and len(addrs) == 1 and re.search(r"e[- ]?mail\s*[:\-]", text, re.I):
        own = addrs
    assign_emails(rec, own, "pdf", "Published in the linked faculty CV", page.final_url)
    for line in text.splitlines():
        if re.search(r"phone|mobile|contact\s*(?:no|number)|tel\b", line, re.I):
            assign_phones(rec, phones.find(line), "pdf", line[:200], page.final_url)
    clean_fields(rec)
    relevance.score(rec, "faculty profile")
    result.page_type = "profile"
    result.doctors = [rec]
    return result
