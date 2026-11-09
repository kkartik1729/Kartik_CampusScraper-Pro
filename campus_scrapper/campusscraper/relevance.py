"""Who goes in the main Faculty sheet: teaching faculty, optionally only in chosen departments.

relevance_score >= 1 means 'teaching faculty in a wanted department'. Non-teaching
staff (lab assistants, clerks, librarians) score 0 and stay in the 'All people'
sheet only.
"""
from __future__ import annotations

import re
import threading
from urllib.parse import urlsplit

from .models import PersonRecord
from .vocab import DEPARTMENTS, NON_TEACHING, TEACHING_WORDS

# Set by each crawler thread from ScrapeConfig.departments. Empty = every department counts.
# Per thread: colleges crawled at the same time can have different department filters.
_LOCAL = threading.local()


class _Targets:
    def __iter__(self):
        return iter(getattr(_LOCAL, "targets", []))

    def __bool__(self):
        return bool(getattr(_LOCAL, "targets", []))


_TARGETS = _Targets()


def set_targets(departments: list[str] | None) -> None:
    """Departments to keep in this thread, as typed by the user ('CSE', 'computer science, IT')."""
    out = []
    for d in departments or []:
        for part in re.split(r"[,;/|]", d):
            part = part.strip().lower()
            if part:
                out.append(part)
    _LOCAL.targets = out


def targets() -> list[str]:
    return list(_TARGETS)


def departments_in(text: str) -> list[str]:
    """Canonical department names mentioned in the text."""
    low = (text or "").lower()
    found = []
    for canon, phrases in DEPARTMENTS.items():
        if any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low) for p in phrases):
            found.append(canon)
    return found


def _target_hit(text: str) -> str | None:
    """The wanted department this text points to, by typed words or by canonical department."""
    if not _TARGETS:
        return "any"
    low = (text or "").lower()
    canon = {c.lower() for c in departments_in(low)}
    for t in _TARGETS:
        if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
            return t
        wanted = {c.lower() for c in departments_in(t)}
        if wanted & canon:
            return t
    return None


def veto(rec: PersonRecord) -> bool:
    """Non-teaching staff are never in the Faculty sheet, whatever the page says."""
    title = " | ".join(str(rec.get(f) or "") for f in ("designation",))
    if title and NON_TEACHING.search(title) and not re.search(r"professor|lecturer|faculty", title, re.I):
        rec.relevance_score = 0.0
        rec.relevance_terms = ["non-teaching staff: " + title[:80]]
        return True
    return False


def score(rec: PersonRecord, page_context: str = "") -> None:
    title = " | ".join(str(rec.get(f) or "") for f in ("designation", "specialty"))
    terms: list[str] = []
    total = 0.0
    if TEACHING_WORDS.search(title) or re.match(r"^Prof\.?\s", str(rec.get("name") or ""), re.I):
        total += 1.0
        terms.append("teaching title")
    elif rec.get("qualifications") and any(q.split()[0] in ("PhD", "MTech", "ME", "MPhil", "MSc", "MCA", "MBA")
                                           for q in rec.get("qualifications")):
        total += 0.6  # a postgraduate degree but no title: likely faculty, needs one more sign
        terms.append("postgraduate degree")
    path = " ".join(urlsplit(u).path for u in [rec.profile_url or "", rec.source_url] if u)
    if re.search(r"faculty|teaching|professor", path + " " + page_context, re.I):
        total += 0.5
        terms.append("faculty page")
    # Department: the person's own department, then the page they were listed on.
    dept_text = " | ".join(str(rec.get(f) or "") for f in ("department", "specialty", "designation"))
    hit = _target_hit(dept_text)
    if hit is None and not rec.get("department"):
        hit = _target_hit(page_context + " " + path.replace("-", " ").replace("_", " "))
        if hit and hit != "any":
            terms.append("listed on department page")
    if _TARGETS:
        if hit is None:
            total = min(total, 0.5)  # teaching, but not in a wanted department
            terms.append("other department")
        else:
            terms.append("department: " + hit)
    if veto(rec):
        return
    rec.relevance_score = max(rec.relevance_score, total)
    for t in terms:
        if t not in rec.relevance_terms:
            rec.relevance_terms.append(t)


def rescore(rec: PersonRecord) -> None:
    """Final check after merging: the person's own department decides when they have one."""
    if veto(rec):
        return
    if _TARGETS and rec.get("department"):
        hit = _target_hit(" | ".join(str(rec.get(f) or "") for f in ("department", "specialty")))
        if hit is None and "listed on department page" not in rec.relevance_terms:
            rec.relevance_score = min(rec.relevance_score, 0.5)
            if "other department" not in rec.relevance_terms:
                rec.relevance_terms.append("other department")
