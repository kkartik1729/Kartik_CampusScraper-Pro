"""Crawl-level clean-up of contacts.

A phone number or email that appears on the pages of several different people
is the department office or college switchboard, not any one person's. Those
values are moved from the personal columns to the department/office columns.
"""
from __future__ import annotations

from collections import Counter

from .models import PersonRecord
from . import emails

SHARED_THRESHOLD = 2  # seen on this many different people -> department/office contact


def _split(value) -> list[str]:
    return [v for v in (value or "").split("; ") if v]


def _move(r: PersonRecord, own: str, desk: str, gone: list[str]) -> None:
    keep = [v for v in _split(r.get(own)) if v not in gone]
    if keep:
        r.values[own] = "; ".join(keep)
    else:
        r.values.pop(own, None)
        r.layers.pop(own, None)
    existing = _split(r.get(desk))
    r.values[desk] = "; ".join(dict.fromkeys(existing + gone))
    r.layers.setdefault(desk, "patterns")


def demote_sitewide(records: list[PersonRecord], value_pages: dict[str, set[str]], extra_pages: int = 2) -> int:
    """A doctor's number or email that the site also shows on pages that are not about that doctor
    (the header, footer, a branch contact list repeated on every page) belongs to the college."""
    moved = 0
    for r in records:
        mine = {u for u in [r.source_url, r.profile_url, *r.found_on] if u}
        for own, desk in (("phone", "contact_phone"), ("email", "contact_email")):
            gone = [v for v in _split(r.get(own))
                    if len(value_pages.get(v, set()) - mine) >= extra_pages
                    and not (own == "email" and emails.is_personal(v, r.get("name"))
                             and any(e.field == "email" and e.value == v
                                     and e.layer in {"table_row", "listing_card", "labels", "template", "jsonld", "microdata"}
                                     for e in r.evidence))]
            if gone:
                _move(r, own, desk, gone)
                moved += len(gone)
    return moved


def demote_shared(records: list[PersonRecord], threshold: int = SHARED_THRESHOLD) -> int:
    moved = 0
    for own, desk in (("phone", "contact_phone"), ("email", "contact_email")):
        counts = Counter(v for r in records for v in set(_split(r.get(own))))
        shared = {v for v, n in counts.items() if n >= threshold}
        if not shared:
            continue
        for r in records:
            values = _split(r.get(own))
            keep = [v for v in values if v not in shared]
            gone = [v for v in values if v in shared]
            if not gone:
                continue
            if keep:
                r.values[own] = "; ".join(keep)
            else:
                r.values.pop(own, None)
                r.layers.pop(own, None)
            existing = _split(r.get(desk))
            r.values[desk] = "; ".join(dict.fromkeys(existing + gone))
            r.layers.setdefault(desk, "patterns")
            moved += len(gone)
    return moved
