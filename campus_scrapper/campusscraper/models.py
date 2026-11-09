"""Data shapes shared by the fetcher, extractor, crawler and exporters.

A PersonRecord is one faculty or staff member. An OfficeRecord is one
student-facing office or body of an institution (placement cell, higher
studies cell, student chapter, alumni office): the channel for reaching students.
"""
from __future__ import annotations

import html

from dataclasses import dataclass, field
from typing import Any

# Fields every doctor record can carry. Order is the export column order.
SCALAR_FIELDS = (
    "name", "designation", "department", "specialty", "institution", "city",
    "experience_years", "phone", "contact_phone", "email", "contact_email",
    "google_scholar", "orcid", "scopus_id", "researchgate", "linkedin", "vidwan", "homepage",
    "photo_url", "about",
)
LIST_FIELDS = (
    "qualifications", "research_areas", "subjects", "education", "awards", "memberships",
)
ALL_FIELDS = SCALAR_FIELDS + LIST_FIELDS

# Higher number wins when two layers disagree on a scalar field.
LAYER_PRIORITY = {
    "template": 100,
    "h1": 95,
    "site": 92,      # the college whose website this is: the employer, not an alma mater
    "jsonld": 90,
    "microdata": 85,
    "embedded_json": 80,
    "labels": 70,
    "sections": 65,
    "listing_card": 55,
    "table_row": 55,
    "affiliation_table": 60,
    "patterns": 50,
    "page": 40,
    "meta": 30,
    "url": 10,
}


@dataclass
class Evidence:
    field: str
    value: str
    layer: str
    snippet: str
    source_url: str


@dataclass
class PersonRecord:
    source_url: str
    profile_url: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    layers: dict[str, str] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    relevance_score: float = 0.0
    relevance_terms: list[str] = field(default_factory=list)
    found_on: list[str] = field(default_factory=list)
    page_type: str = "profile"
    source_institution: str = ""   # set in batch mode: which college list row produced this record
    section_role: str = ""         # extraction only: 'alumni' when the card sits in an alumni / testimonial section

    # -- field helpers -------------------------------------------------
    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def set(self, name: str, value: Any, layer: str, snippet: str = "", source_url: str | None = None) -> bool:
        """Set a field if empty or if ``layer`` outranks the current one.

        List fields are unioned instead of replaced. Returns True if the
        record changed.
        """
        if value is None:
            return False
        if isinstance(value, str):
            if not name.endswith("_url"):  # URLs keep '&region=' etc. exactly
                value = html.unescape(html.unescape(value))  # '&amp;#038;' -> '&'
            value = " ".join(value.split()).strip(" ,;:|-")
            if not value:
                return False
        src = source_url or self.source_url
        if name in LIST_FIELDS:
            items = value if isinstance(value, (list, tuple)) else [value]
            items = [html.unescape(i) if isinstance(i, str) else i for i in items]
            current = list(self.values.get(name, []))
            seen = {i.casefold() for i in current}
            changed = False
            for item in items:
                item = " ".join(str(item).split()).strip(" ,;:|-•·")
                if name == "qualifications" and item:
                    base = item.split()[0].casefold()
                    bases = {c.split()[0].casefold(): idx for idx, c in enumerate(current)}
                    if base in bases:
                        idx = bases[base]
                        # Keep the more detailed form: 'MD (Medicine)' beats 'MD'.
                        if len(item) > len(current[idx]) and " " not in current[idx]:
                            current[idx] = item
                            changed = True
                        continue
                if item and item.casefold() not in seen and len(item) <= 300:
                    current.append(item)
                    seen.add(item.casefold())
                    changed = True
                    self.evidence.append(Evidence(name, item, layer, (snippet or item)[:500], src))
            if changed:
                self.values[name] = current
                self.layers.setdefault(name, layer)
            return changed
        old_layer = self.layers.get(name)
        if old_layer and LAYER_PRIORITY.get(old_layer, 0) >= LAYER_PRIORITY.get(layer, 0):
            return False
        self.values[name] = value
        self.layers[name] = layer
        self.evidence.append(Evidence(name, str(value), layer, (snippet or str(value))[:500], src))
        return True

    def absorb(self, other: "PersonRecord", fill_only: bool = False) -> None:
        """Merge another record of the same doctor into this one.

        ``fill_only`` keeps every scalar this record already has (used when a
        listing card is merged into a full profile page).
        """
        for name in ALL_FIELDS:
            if name not in other.values:
                continue
            if fill_only and name in SCALAR_FIELDS and self.values.get(name):
                continue
            layer = other.layers.get(name, "patterns")
            snippet = next((e.snippet for e in other.evidence if e.field == name), "")
            self.set(name, other.values[name], layer, snippet, other.source_url)
        if not self.profile_url and other.profile_url:
            self.profile_url = other.profile_url
        self.relevance_score = max(self.relevance_score, other.relevance_score)
        for term in other.relevance_terms:
            if term not in self.relevance_terms:
                self.relevance_terms.append(term)
        for url in [other.source_url, *other.found_on]:
            if url and url not in self.found_on and url != self.source_url:
                self.found_on.append(url)
        if other.page_type == "profile" and self.page_type != "profile":
            self.page_type = "profile"
            self.source_url = other.source_url

    @property
    def is_relevant(self) -> bool:
        return self.relevance_score >= 1.0

    def filled_fields(self) -> list[str]:
        return [f for f in ALL_FIELDS if self.values.get(f)]

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.values.get("name")}
        for f in ALL_FIELDS:
            if f != "name":
                out[f] = self.values.get(f)
        out.update({
            "profile_url": self.profile_url,
            "source_url": self.source_url,
            "found_on": self.found_on,
            "is_relevant": self.is_relevant,
            "relevance_score": round(self.relevance_score, 2),
            "relevance_terms": self.relevance_terms,
            "page_type": self.page_type,
            "source_institution": self.source_institution,
            "field_sources": dict(self.layers),
        })
        return out

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        d["values"] = dict(self.values)
        d["evidence"] = [e.__dict__ for e in self.evidence]
        return d

    @classmethod
    def from_full_dict(cls, d: dict[str, Any]) -> "PersonRecord":
        rec = cls(source_url=d.get("source_url") or "", profile_url=d.get("profile_url"))
        rec.values = dict(d.get("values") or {})
        rec.layers = dict(d.get("field_sources") or {})
        rec.evidence = [Evidence(**e) for e in d.get("evidence") or []]
        rec.relevance_score = float(d.get("relevance_score") or 0)
        rec.relevance_terms = list(d.get("relevance_terms") or [])
        rec.found_on = list(d.get("found_on") or [])
        rec.page_type = d.get("page_type") or "profile"
        rec.source_institution = d.get("source_institution") or ""
        return rec


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    content_type: str = "text/html"
    rendered: bool = False
    error: str | None = None


@dataclass
class PageResult:
    url: str
    page_type: str  # profile | listing | other | error
    doctors: list[PersonRecord] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    link_texts: dict[str, str] = field(default_factory=dict)
    profile_links: list[str] = field(default_factory=list)
    pagination_links: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    offices: list["OfficeRecord"] = field(default_factory=list)


OFFICE_FIELDS = ("office_type", "office_name", "institution", "city", "emails", "phones", "coordinators",
                 "higher_studies_lines", "page_url")


@dataclass
class OfficeRecord:
    """One student-facing office or body: who runs it and how to reach it.

    Contacts are the office's own (role addresses, office landlines) and its
    faculty or staff coordinators. Lines naming individual students are left
    out, with their phones and emails.
    """
    page_url: str
    office_type: str                      # placement | higher_studies | student_chapter | alumni | international | student_affairs
    office_name: str = ""
    institution: str = ""
    city: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    coordinators: list[str] = field(default_factory=list)   # 'Dr. A B, Training & Placement Officer'
    higher_studies_lines: list[str] = field(default_factory=list)  # published counts of students going abroad
    evidence: list[Evidence] = field(default_factory=list)
    source_institution: str = ""
    skipped_student_lines: int = 0

    def key(self) -> tuple[str, str]:
        return (self.office_type, (self.office_name or self.page_url).casefold())

    def absorb(self, other: "OfficeRecord") -> None:
        for name in ("emails", "phones", "coordinators", "higher_studies_lines"):
            cur = getattr(self, name)
            for v in getattr(other, name):
                if v not in cur:
                    cur.append(v)
        self.evidence.extend(other.evidence)
        self.skipped_student_lines += other.skipped_student_lines
        for name in ("institution", "city"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, getattr(other, name))

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in OFFICE_FIELDS} | {
            "source_institution": self.source_institution, "skipped_student_lines": self.skipped_student_lines}

    def to_full_dict(self) -> dict[str, Any]:
        return self.to_dict() | {"evidence": [e.__dict__ for e in self.evidence]}

    @classmethod
    def from_full_dict(cls, d: dict[str, Any]) -> "OfficeRecord":
        rec = cls(page_url=d.get("page_url") or "", office_type=d.get("office_type") or "")
        for f in OFFICE_FIELDS:
            if f in d and f not in ("page_url", "office_type"):
                setattr(rec, f, d[f] if not isinstance(d[f], list) else list(d[f]))
        rec.evidence = [Evidence(**e) for e in d.get("evidence") or []]
        rec.source_institution = d.get("source_institution") or ""
        rec.skipped_student_lines = int(d.get("skipped_student_lines") or 0)
        return rec
