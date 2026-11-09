"""Read a request written in ordinary words, and keep only what it really says.

The plain reader in :mod:`site_discovery` matches patterns. It is literal and
never wrong about what it found, but it misses the ways people actually write:
"we need profs from the IT dept of a few Pune colleges, with their emails".

A model reads those well, so it is asked to fill the gaps. Everything it
returns is then checked against the request's own words: a city must be a city
this software knows and must appear in the request, a college name must appear
in the request, a number must appear in the request. Anything that fails the
check is dropped and the plain reader's answer stands. So the model can widen
understanding; it cannot invent a place, a college or a count.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from .profile import city_from
from .relevance import departments_in
from .site_discovery import COUNT_WORDS, TYPE_WORDS, read_request

INSTRUCTION = (
    "Read one request for college or university contact pages and report only what it asks for.\n"
    "Report a field only when the request itself says it; leave it empty otherwise.\n"
    "Never add a city, a college or a number that the request does not contain.\n"
    "city: the city or town the colleges must be in, exactly as the request spells it.\n"
    "college_type: engineering, medical, dental, pharmacy, management, law, arts, science or "
    "architecture, when the request asks for one kind.\n"
    "count: how many colleges are wanted, when the request says a number.\n"
    "target: 'faculty' for teaching staff, 'offices' for placement, higher-studies, alumni or "
    "student-chapter offices, 'both' when the request asks for both.\n"
    "named_colleges: colleges the request names one by one.\n"
    "departments: departments or branches the request limits the contacts to."
)


class RequestRead(BaseModel):
    """What a model reports back. Every field is checked before it is used."""

    city: str = Field(default="", max_length=80)
    college_type: str = Field(default="", max_length=40)
    count: int | None = Field(default=None, ge=1, le=100)
    target: str = Field(default="", max_length=20)
    named_colleges: list[str] = Field(default_factory=list, max_length=25)
    departments: list[str] = Field(default_factory=list, max_length=25)


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", text.casefold())


def _in_prompt(value, prompt_words):
    value = _words(str(value)).strip()
    return bool(value) and f" {value} " in f" {prompt_words} "


def check(answer: RequestRead, prompt: str, base):
    """Keep the parts of a model's reading that the request itself supports."""
    prompt_words = _words(prompt)
    kept = {}

    city = city_from(answer.city) or city_from(answer.city.title()) or ""
    if city and (_in_prompt(answer.city, prompt_words) or _in_prompt(city, prompt_words)):
        kept["location"] = city

    kind = (answer.college_type or "").strip().casefold().split()[0] if answer.college_type.strip() else ""
    # The request's own wording must show the kind: "B.Tech colleges" is evidence
    # of an engineering college even though the word "engineering" is absent.
    if kind in TYPE_WORDS and re.search(TYPE_WORDS[kind], prompt, re.I):
        kept["kind"] = kind

    if answer.count is not None:
        said = str(answer.count) in re.findall(r"\d+", prompt) or any(
            w for w, n in COUNT_WORDS.items() if n == answer.count and _in_prompt(w, prompt_words))
        if said:
            kept["count"] = answer.count

    if answer.target in {"faculty", "offices", "both"}:
        kept["target"] = answer.target

    names = [" ".join(str(n).split()) for n in answer.named_colleges]
    names = [n for n in names if 8 <= len(n) <= 90 and _in_prompt(n, prompt_words)]
    if names:
        kept["names"] = names

    # Departments are checked against the request, not against the model's wording.
    allowed = set(departments_in(prompt))
    departments = [d for name in answer.departments for d in departments_in(str(name)) if d in allowed]
    departments = list(dict.fromkeys(departments))
    if departments:
        kept["departments"] = departments
    return kept


def read_request_naturally(prompt: str, ask=None):
    """The plain reading of a request, widened by a model where it was silent.

    ``ask`` is a function taking (schema, instruction, payload) - the model
    router's ``ask``. Without it, or when the model fails, the plain reading is
    returned unchanged, so a request is never left unread.
    """
    base = read_request(prompt)
    if ask is None:
        return base, []
    try:
        answer = ask(RequestRead, INSTRUCTION, {"request": prompt})
    except Exception as exc:  # the plain reading already works; a model is a bonus
        return base, [f"The request was read without a model ({type(exc).__name__})"]

    kept = check(answer, prompt, base)
    notes = []
    for field, value in kept.items():
        current = getattr(base, field)
        empty = not current or (field == "count" and not base.count_stated)
        if empty and value:
            setattr(base, field, value)
            if field in ("location", "kind", "count"):
                notes.append(f"Read from your words: {field.replace('kind', 'college type')} = {value}")
            if field == "count":
                base.count_stated = True
            if field == "location":
                base.unread_place = ""
    return base, notes
