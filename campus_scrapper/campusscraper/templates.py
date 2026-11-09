"""Optional per-site CSS selector templates for exact extraction on known college sites.

A template is a JSON file in ``templates/`` (next to the package or in the
current folder) named after the host, e.g. ``www.example-college.ac.in.json``:

{
  "host": "www.example-college.ac.in",
  "profile": {
    "name": "h1.faculty-name",
    "designation": ".faculty-designation",
    "department": ".faculty-dept",
    "qualifications": ".faculty-qualification",
    "experience_years": ".faculty-exp",
    "research_areas": ".research li"
  },
  "listing": {
    "card": ".faculty-card",
    "name": ".faculty-card__name",
    "link": "a.view-profile",
    "designation": ".faculty-card__designation",
    "department": ".faculty-card__dept"
  }
}

Selectors ending in list fields (research_areas, qualifications, subjects ...) take
every match; scalar fields take the first match.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .fetch import normalize_url
from .models import LIST_FIELDS, PersonRecord

log = logging.getLogger(__name__)
_DIRS = [Path(__file__).parent / "templates", Path.cwd() / "templates"]
_cache: dict[str, dict | None] = {}


def load(host: str) -> dict | None:
    host = host.lower()
    if host in _cache:
        return _cache[host]
    tpl = None
    for d in _DIRS:
        for candidate in (d / f"{host}.json", d / f"{host.removeprefix('www.')}.json"):
            if candidate.is_file():
                try:
                    tpl = json.loads(candidate.read_text(encoding="utf-8"))
                except ValueError as exc:
                    log.warning("bad template %s: %s", candidate, exc)
                break
        if tpl:
            break
    _cache[host] = tpl
    return tpl


def _values(root, selector: str, many: bool) -> list[str]:
    nodes = root.select(selector) if many else [root.select_one(selector)]
    out = []
    for n in nodes:
        if n is None:
            continue
        if n.name == "img":
            out.append(n.get("src") or n.get("data-src") or "")
        elif n.name == "meta":
            out.append(n.get("content", ""))
        else:
            out.append(" ".join(n.get_text(" ", strip=True).split()))
    return [v for v in out if v]


def apply_profile(soup: BeautifulSoup, url: str, rec: PersonRecord) -> bool:
    tpl = load(urlsplit(url).hostname or "")
    if not tpl or "profile" not in tpl:
        return False
    hit = False
    for field, selector in tpl["profile"].items():
        try:
            vals = _values(soup, selector, field in LIST_FIELDS)
        except Exception as exc:  # bad selector
            log.warning("template selector %r failed: %s", selector, exc)
            continue
        if not vals:
            continue
        hit = True
        if field == "photo_url":
            rec.set(field, urljoin(url, vals[0]), "template", selector)
        else:
            rec.set(field, vals if field in LIST_FIELDS else vals[0], "template", f"{selector} -> {vals[0][:200]}")
    return hit


def apply_listing(soup: BeautifulSoup, url: str) -> list[PersonRecord] | None:
    tpl = load(urlsplit(url).hostname or "")
    if not tpl or "listing" not in tpl or "card" not in tpl["listing"]:
        return None
    spec = tpl["listing"]
    out = []
    for card in soup.select(spec["card"]):
        rec = PersonRecord(source_url=url, page_type="listing")
        for field, selector in spec.items():
            if field in ("card", "link"):
                continue
            vals = _values(card, selector, field in LIST_FIELDS)
            if vals:
                rec.set(field, vals if field in LIST_FIELDS else vals[0], "template", selector)
        if spec.get("link"):
            a = card.select_one(spec["link"])
            if a and a.get("href"):
                rec.profile_url = normalize_url(urljoin(url, a["href"]))
        if rec.get("name"):
            out.append(rec)
    return out
