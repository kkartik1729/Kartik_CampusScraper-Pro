"""Data quality validation, confidence scoring, and pre-export quality auditing.

Validates extracted faculty records against strict accuracy criteria:
- Email syntax & domain plausibility
- Phone number validity & formatting
- Faculty name integrity (non-empty, non-placeholder, person-like)
- Designation & department presence
- College association consistency
- Source URL & profile URL validity
- Duplicate detection (normalized name, email, profile URL)
- Confidence scoring (0..100)
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from .crawl import name_key, url_key
from .emails import GENERIC_LOCAL, find as find_emails
from .models import PersonRecord
from .phones import normalise as normalise_phone
from .vocab import DESIGNATION_RE

_SUSPICIOUS_NAME_RE = re.compile(
    r"(?:^|\b)(?:admin|administrator|webmaster|admission|admissions|enquiry|inquiry|contact|"
    r"office|department|faculty|staff|click here|read more|view profile|know more|download|"
    r"syllabus|curriculum|timetable|notice|circular|placement|library|hostel|alumni|"
    r"laboratory|research center|unknown|test|sample|example|n/?a|nil|none|tbd|vacant)(?:\b|$)",
    re.I,
)
_EMAIL_SYNTAX_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,15}$")
_VALID_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.I)


def is_valid_email(email: str | None) -> bool:
    if not email:
        return False
    first = email.split(";")[0].strip()
    if not _EMAIL_SYNTAX_RE.match(first):
        return False
    return bool(find_emails(first))


def is_personal_email(email: str | None) -> bool:
    if not is_valid_email(email):
        return False
    first = email.split(";")[0].strip().lower()
    local = first.split("@", 1)[0]
    base_local = re.sub(r"\d+$", "", local)
    return not GENERIC_LOCAL.match(local) and not GENERIC_LOCAL.match(base_local)


def is_valid_phone(phone: str | None) -> bool:
    if not phone:
        return False
    first = phone.split(";")[0].strip()
    digits = re.sub(r"\D", "", first)
    if len(digits) < 7 or len(digits) > 15:
        return False
    return normalise_phone(first) is not None or len(digits) >= 10


def is_valid_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    if not _VALID_URL_RE.match(url.strip()):
        return False
    parts = urlsplit(url.strip())
    return bool(parts.scheme in ("http", "https") and parts.netloc and "." in parts.netloc)


def is_suspicious_name(name: str | None) -> tuple[bool, str]:
    if not name or not name.strip():
        return True, "empty_name"
    s = name.strip()
    if len(s) < 3:
        return True, "too_short"
    if len(s) > 85:
        return True, "too_long"
    if re.search(r"\d{2,}", s):
        return True, "contains_numbers"
    if "@" in s or "http://" in s.lower() or "https://" in s.lower():
        return True, "contains_url_or_email"
    if _SUSPICIOUS_NAME_RE.search(s):
        return True, "non_person_keyword"
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) < 3:
        return True, "insufficient_letters"
    return False, ""


def compute_confidence(rec: PersonRecord | dict) -> dict:
    """Compute a 0..100 confidence score and list of quality flags for a record."""
    if isinstance(rec, PersonRecord):
        get = rec.get
        source_url = rec.source_url
        profile_url = rec.profile_url
        page_type = rec.page_type
        institution = rec.source_institution or rec.get("institution")
        evidence_count = len(rec.evidence)
        fac = bool(rec.is_relevant)
    else:
        vals = rec.get("values") or rec
        get = lambda f, default=None: vals.get(f, rec.get(f, default))
        source_url = rec.get("source_url") or ""
        profile_url = rec.get("profile_url") or get("profile_url")
        page_type = rec.get("page_type") or ""
        institution = rec.get("source_institution") or get("institution") or ""
        evidence_count = len(rec.get("evidence") or [])
        fac = bool(rec.get("is_relevant", True))

    name = get("name")
    designation = get("designation")
    department = get("department")
    email = get("email")
    contact_email = get("contact_email")
    phone = get("phone")
    contact_phone = get("contact_phone")
    qualifications = get("qualifications") or get("education")

    score = 0
    issues: list[str] = []

    suspicious, reason = is_suspicious_name(name)
    if suspicious:
        issues.append(f"suspicious_name:{reason}")
    else:
        score += 25
        if re.match(r"^(?:dr|prof|professor|mr|mrs|ms|shri|smt|er)\.?\s", (name or "").strip(), re.I):
            score += 5

    if designation and len(designation.strip()) >= 2:
        score += 20
    else:
        issues.append("missing_designation")

    if department and len(department.strip()) >= 2:
        score += 12
    else:
        issues.append("missing_department")

    if institution and len(str(institution).strip()) >= 2:
        score += 8
    else:
        issues.append("missing_college")

    any_email = email or contact_email
    if email:
        if is_personal_email(email):
            score += 15
        elif is_valid_email(email):
            score += 10
        else:
            issues.append("invalid_email")
    elif contact_email:
        if is_valid_email(contact_email):
            score += 6
        else:
            issues.append("invalid_email")

    any_phone = phone or contact_phone
    if phone:
        if is_valid_phone(phone):
            score += 8
        else:
            issues.append("invalid_phone")
    elif contact_phone:
        if is_valid_phone(contact_phone):
            score += 4
        else:
            issues.append("invalid_phone")

    if profile_url:
        if is_valid_url(profile_url):
            score += 7
        else:
            issues.append("broken_profile_url")

    if source_url and is_valid_url(source_url):
        score += 5
    else:
        issues.append("broken_source_url")

    if qualifications:
        score += 3
    if page_type == "profile":
        score += 4
    if evidence_count >= 3:
        score += 3

    score = max(0, min(100, score))
    if suspicious:
        score = min(score, 25)

    grade = "high" if score >= 75 else ("medium" if score >= 50 else "low")
    return {
        "score": score,
        "grade": grade,
        "is_faculty": fac,
        "valid_email": is_valid_email(any_email) if any_email else False,
        "personal_email": is_personal_email(email) if email else False,
        "valid_phone": is_valid_phone(any_phone) if any_phone else False,
        "valid_source_url": is_valid_url(source_url),
        "issues": issues,
    }


def audit_records(records: list[PersonRecord | dict]) -> dict:
    """Run a comprehensive data-quality audit over a list of PersonRecord or dicts."""
    total = len(records)
    empty_names = 0
    suspicious_records = 0
    missing_designations = 0
    missing_departments = 0
    missing_colleges = 0
    valid_emails = 0
    invalid_emails = 0
    valid_phones = 0
    invalid_phones = 0
    broken_source_urls = 0
    with_profile_url = 0
    duplicate_records = 0

    high_conf = 0
    med_conf = 0
    low_conf = 0
    score_sum = 0

    seen_keys: set[tuple[str, str]] = set()
    seen_emails: set[str] = set()
    seen_profiles: set[str] = set()
    issue_counts: Counter = Counter()
    sample_issues: list[dict] = []

    for rec in records:
        if isinstance(rec, PersonRecord):
            name = rec.get("name") or ""
            college = (rec.source_institution or rec.get("institution") or "").strip().lower()
            email = rec.get("email") or rec.get("contact_email") or ""
            phone = rec.get("phone") or rec.get("contact_phone") or ""
            profile_url = rec.profile_url or ""
            source_url = rec.source_url or ""
        else:
            vals = rec.get("values") or rec
            name = vals.get("name") or rec.get("name") or ""
            college = (
                rec.get("source_institution")
                or vals.get("institution")
                or rec.get("institution")
                or rec.get("college")
                or ""
            ).strip().lower()
            email = vals.get("email") or vals.get("contact_email") or rec.get("email") or ""
            phone = vals.get("phone") or vals.get("contact_phone") or rec.get("phone") or ""
            profile_url = rec.get("profile_url") or vals.get("profile_url") or ""
            source_url = rec.get("source_url") or ""

        conf = compute_confidence(rec)
        score = conf["score"]
        score_sum += score
        if conf["grade"] == "high":
            high_conf += 1
        elif conf["grade"] == "medium":
            med_conf += 1
        else:
            low_conf += 1

        if not name.strip():
            empty_names += 1
        if any(i.startswith("suspicious_name:") for i in conf["issues"]):
            suspicious_records += 1
        if "missing_designation" in conf["issues"]:
            missing_designations += 1
        if "missing_department" in conf["issues"]:
            missing_departments += 1
        if "missing_college" in conf["issues"]:
            missing_colleges += 1

        if email:
            if conf["valid_email"]:
                valid_emails += 1
            else:
                invalid_emails += 1
        if phone:
            if conf["valid_phone"]:
                valid_phones += 1
            else:
                invalid_phones += 1

        if not conf["valid_source_url"]:
            broken_source_urls += 1
        if profile_url and is_valid_url(profile_url):
            with_profile_url += 1

        # Duplicate check across (college, normalized_name), personal email, or profile URL
        nk = name_key(name)
        uk = url_key(profile_url) if profile_url else None
        em_first = email.split(";")[0].strip().lower() if email and is_personal_email(email) else None

        is_dup = False
        if nk and (college, nk) in seen_keys:
            is_dup = True
        if uk and uk in seen_profiles:
            is_dup = True
        if em_first and em_first in seen_emails and nk and any(k[1] == nk for k in seen_keys):
            is_dup = True

        if is_dup:
            duplicate_records += 1
            issue_counts["duplicate_record"] += 1
        else:
            if nk:
                seen_keys.add((college, nk))
            if uk:
                seen_profiles.add(uk)
            if em_first:
                seen_emails.add(em_first)

        for iss in conf["issues"]:
            issue_counts[iss] += 1

        if (conf["grade"] == "low" or is_dup or "invalid_email" in conf["issues"] or "invalid_phone" in conf["issues"]) and len(sample_issues) < 25:
            sample_issues.append({
                "name": name or "(empty)",
                "college": college or "(unknown)",
                "score": score,
                "issues": conf["issues"] + (["duplicate_record"] if is_dup else []),
                "source_url": source_url,
            })

    avg_score = round(score_sum / total, 1) if total > 0 else 0.0
    quality_index = round(((total - suspicious_records - empty_names - broken_source_urls) / total) * 100.0, 1) if total > 0 else 100.0

    return {
        "total_records": total,
        "average_confidence": avg_score,
        "quality_index_pct": max(0.0, min(100.0, quality_index)),
        "quality_score": max(0.0, min(100.0, quality_index)),
        "high_confidence_records": high_conf,
        "valid_emails": valid_emails,
        "invalid_emails": invalid_emails,
        "valid_phones": valid_phones,
        "invalid_phones": invalid_phones,
        "empty_names": empty_names,
        "suspicious_records": suspicious_records,
        "duplicate_records": duplicate_records,
        "missing_designations": missing_designations,
        "missing_departments": missing_departments,
        "broken_source_urls": broken_source_urls,
        "confidence_breakdown": {
            "high": high_conf,
            "medium": med_conf,
            "low": low_conf,
        },
        "checks": {
            "valid_emails": valid_emails,
            "invalid_emails": invalid_emails,
            "valid_phones": valid_phones,
            "invalid_phones": invalid_phones,
            "empty_names": empty_names,
            "suspicious_records": suspicious_records,
            "duplicate_records": duplicate_records,
            "missing_designations": missing_designations,
            "missing_departments": missing_departments,
            "incorrect_college_associations": missing_colleges,
            "broken_source_urls": broken_source_urls,
            "with_profile_urls": with_profile_url,
        },
        "issue_counts": dict(issue_counts.most_common(15)),
        "sample_flagged": sample_issues,
        "flagged_samples": sample_issues,
    }
