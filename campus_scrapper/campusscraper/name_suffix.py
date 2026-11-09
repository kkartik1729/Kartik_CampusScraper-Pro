"""Crawl-level clean-up of names.

Some sites glue an affiliation acronym after every person's name
("Dr. Jyoti Anand IOSPL", "Prof. Ravi Kumar IOSPL"). One such token is impossible
to tell from an all-caps surname; the same token trailing several different
names on one site is an acronym, not a name. Those tokens are removed from all
of the names they trail, the same way a phone shared by several persons is
moved to the desk columns.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .vocab import HONORIFIC, QUALIFICATIONS

SHARED_THRESHOLD = 2  # trails this many different names -> acronym, not a surname
_TOKEN = re.compile(r"^[A-Z]{3,8}$")
_ROMAN = re.compile(r"^[IVX]+$")
_HONORIFIC_RE = re.compile(rf"^{HONORIFIC}\.?\s+", re.I)
_QUALS_UPPER = {q.upper().replace(".", "") for q in QUALIFICATIONS}


def _split(name: str) -> tuple[str, list[str]]:
    m = _HONORIFIC_RE.match(name or "")
    prefix = name[: m.end()] if m else ""
    return prefix, (name[m.end():] if m else name or "").split()


def trailing_acronym(name: str) -> str | None:
    """The glued all-caps token at the end of ``name``, if the rest still reads as a name."""
    _, toks = _split(name)
    if len(toks) < 3:
        return None  # 'Jyoti ANAND' keeps its surname
    tok = toks[-1].strip(".,")
    if not _TOKEN.match(tok) or _ROMAN.match(tok) or tok in _QUALS_UPPER:
        return None
    if all(t.isupper() for t in toks[:-1] if len(t.strip(".")) > 1):
        return None  # whole name in caps: nothing to tell the acronym apart
    return tok


def strip_shared_name_suffix(records) -> dict[str, int]:
    """Remove acronyms that trail SHARED_THRESHOLD+ different names. Returns {acronym: names changed}."""
    by_token: dict[str, list] = defaultdict(list)
    words_elsewhere: dict[str, set[str]] = defaultdict(set)
    for r in records:
        name = r.get("name") or ""
        tok = trailing_acronym(name)
        if tok:
            by_token[tok].append(r)
        _, toks = _split(name)
        for t in toks:
            words_elsewhere[t.strip(".,").upper()].add(name)
    changed: dict[str, int] = {}
    for tok, recs in by_token.items():
        bases = {" ".join(_split(r.get("name"))[1][:-1]).lower() for r in recs}
        if len(bases) < SHARED_THRESHOLD:
            continue
        # 'Dr. Ravi REDDY' and 'Dr. Suma REDDY' next to 'Dr. Anil Reddy': a surname, not an acronym.
        if len(words_elsewhere.get(tok, set())) > len(recs):
            continue
        for r in recs:
            prefix, toks = _split(r.get("name"))
            r.values["name"] = (prefix + " ".join(toks[:-1])).strip()
        changed[tok] = len(recs)
    return changed
