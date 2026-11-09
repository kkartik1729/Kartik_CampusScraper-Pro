"""Prompt to Intent, by pattern rather than by model.

A local 0.6B model given this job pluralised quotes, invented counts and failed
the run before a single search. The common prompt shape is narrow enough that
rules read it correctly every time, and rules cannot hallucinate a number.

Anything the rules cannot read is reported as unparsed. A caller may then ask
the user, or hand the leftover to a model, but the model never gets to overrule
what the rules already settled.
"""

from __future__ import annotations

import re

from .base import Intent

# A vertical is recognised by the thing being looked for, not by the industry.
VERTICALS = {
    "colleges": r"\b(colleges?|universit(y|ies)|institutes?|campus(es)?|schools?)\b",
    "hospitals": r"\b(hospitals?|clinics?|medical cent(re|er)s?|nursing homes?)\b",
    "lawyers": r"\b(law firms?|lawyers?|attorneys?|advocates?|legal)\b",
    "companies": r"\b(companies|compan(y|ies)|firms?|employers?|startups?)\b",
}

# What the user wants out: the organisations themselves, or people inside them.
PEOPLE_WORDS = r"\b(facult(y|ies)|professors?|teachers?|staff|doctors?|oncologists?|" \
               r"surgeons?|physicians?|consultants?|attorneys?|lawyers?|partners?|" \
               r"associates?|recruiters?|contacts?|emails?|directory|directories)\b"

DISCIPLINES = {
    "engineering": r"\b(engineering|technical|technolog(y|ical)|polytechnic)\b",
    "management": r"\b(management|business|mba|pgdm|commerce)\b",
    "pharmacy": r"\b(pharmac(y|eutical))\b",
    "architecture": r"\b(architect(ure|ural)?)\b",
    "oncology": r"\b(oncolog(y|ist|ists)|cancer|tumou?r)\b",
    "cardiology": r"\b(cardiolog(y|ist|ists)|cardiac|heart)\b",
    "healthcare": r"\b(health ?care|health)\b",
}

# "in Pune", "from Mumbai", "at Bengaluru", "Pune ke", "Pune mein" — the last
# two because prompts arrive in Hinglish as often as English.
LOCATION = re.compile(
    r"\b(?:in|from|at|near|across|of)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})"
    r"|\b([A-Z][A-Za-z]+)\s+(?:ke|ki|ka|mein|me)\b"
)

COUNT = re.compile(r"\b(\d{1,4})\b")

WORD_NUMBERS = {
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "dozen": 12,
}

# Words that look like a place but are not one.
NOT_A_PLACE = {
    "find", "give", "get", "list", "show", "their", "official", "faculty",
    "the", "all", "every", "please", "top", "best", "india", "indian",
}


def _first_group(match: re.Match | None) -> str | None:
    if not match:
        return None
    for group in match.groups():
        if group:
            return group.strip()
    return None


def parse(prompt: str) -> tuple[Intent, list[str]]:
    """Read a prompt. Returns the intent and a list of what could not be read."""
    text = prompt.strip()
    unparsed: list[str] = []

    vertical = None
    for name, pattern in VERTICALS.items():
        if re.search(pattern, text, re.I):
            vertical = name
            break
    if not vertical:
        unparsed.append("vertical: no colleges/hospitals/lawyers/companies word found")
        vertical = "unknown"

    discipline = None
    for name, pattern in DISCIPLINES.items():
        if re.search(pattern, text, re.I):
            discipline = name
            break

    location = _first_group(LOCATION.search(text))
    if location and location.lower() in NOT_A_PLACE:
        location = None
    if not location:
        unparsed.append("location: no place name found")

    count = None
    number = COUNT.search(text)
    if number:
        count = int(number.group(1))
    else:
        for word, value in WORD_NUMBERS.items():
            if re.search(rf"\b{word}\b", text, re.I):
                count = value
                break

    target = "people" if re.search(PEOPLE_WORDS, text, re.I) else "organisation"

    return Intent(
        vertical=vertical,
        location=location,
        discipline=discipline,
        target=target,
        count=count,
        raw=text,
    ), unparsed
