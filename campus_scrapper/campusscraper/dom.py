"""HTML helpers: parsing, boilerplate removal, visible text, links."""
from __future__ import annotations

import copy
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .fetch import normalize_url

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "br", "label", "option",
}
DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas", "select", "button", "input", "textarea", "link", "meta")
BOILERPLATE_RE = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|menu|mega-?menu|footer|breadcrumb-?nav|cookie|consent|gdpr|header-top|top-?bar|"
    r"site-header|site-footer|sidebar-menu|offcanvas|modal|popup|newsletter|social|share|skip-link|banner-ad)(?:$|[\s_-])",
    re.I,
)


def parse(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _is_hidden(tag: Tag) -> bool:
    style = (tag.get("style") or "").replace(" ", "").lower()
    return (
        tag.has_attr("hidden")
        or tag.get("aria-hidden") == "true"
        or "display:none" in style
        or "visibility:hidden" in style
    )


def _attr_text(tag: Tag) -> str:
    classes = tag.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    return " ".join([*classes, tag.get("id") or "", tag.get("role") or ""])


def main_content(soup: BeautifulSoup) -> Tag:
    """Return a copy of the page with nav/header/footer/menus/hidden nodes removed."""
    body = copy.copy(soup.body or soup)
    for c in body.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in body.find_all(DROP_TAGS):
        t.decompose()
    for t in body.find_all(["nav", "footer"]):
        t.decompose()
    for t in body.find_all(True):
        if getattr(t, "decomposed", False) or t.attrs is None:
            continue
        if t.name == "header" and not t.find(["h1"]):
            t.decompose()
            continue
        if t.attrs is None:
            continue
        academic_panel = bool(re.search(r"\b(?:professor|lecturer|designation)\b", t.get_text(" ", strip=True)[:1500], re.I)
                              and t.find(["h2", "h3", "h4", "h5", "table"]))
        if (_is_hidden(t) and not academic_panel) or (t.get("role") in ("navigation", "contentinfo", "banner") and not t.find("h1")):
            t.decompose()
            continue
        attrs = _attr_text(t)
        if attrs and BOILERPLATE_RE.search(attrs) and not academic_panel and not t.find("h1") and len(t.get_text(" ", strip=True)) < 4000:
            # Keep big containers that merely carry a theme class; drop menus/footers.
            if t.name in ("div", "section", "ul", "aside", "header", "span") :
                t.decompose()
    main = body.find("main") or body.find(attrs={"role": "main"})
    if main and len(main.get_text(" ", strip=True)) > 200:
        return main
    return body


def text_lines(node: Tag) -> list[str]:
    """Visible text split into lines at block boundaries."""
    parts: list[str] = []

    def walk(el):
        for child in el.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                if child.name in DROP_TAGS:
                    continue
                block = child.name in BLOCK_TAGS
                if block:
                    parts.append("\n")
                walk(child)
                if block:
                    parts.append("\n")

    walk(node)
    text = "".join(parts).replace("\xa0", " ")
    lines = []
    for line in text.split("\n"):
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return lines


def node_text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(text_lines(node))


def visible_text(node: Tag) -> str:
    return "\n".join(text_lines(node))


def links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
    """All (absolute_url, anchor_text) pairs on the page, de-duplicated, fragments removed."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    base = soup.find("base", href=True)
    base_url = urljoin(base_url, base["href"]) if base else base_url
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "whatsapp:")):
            continue
        try:
            absolute = normalize_url(urljoin(base_url, href))
        except ValueError:
            continue  # One malformed link must not discard the faculty on the whole page.
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        label = " ".join(a.get_text(" ", strip=True).split()) or a.get("title") or a.get("aria-label") or ""
        out.append((absolute, label))
    return out


def same_site(url: str, start: str, include_subdomains: bool) -> bool:
    a = (urlsplit(url).hostname or "").lower()
    b = (urlsplit(start).hostname or "").lower()
    if a == b:
        return True
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    if strip(a) == strip(b):
        return True
    if include_subdomains:
        root = ".".join(strip(b).split(".")[-2:]) if not strip(b).endswith((".co.in", ".org.in", ".ac.in", ".gov.in", ".net.in")) else ".".join(strip(b).split(".")[-3:])
        return a == root or a.endswith("." + root)
    return False


def heading_level(tag: Tag) -> int | None:
    if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return int(tag.name[1])
    return None
