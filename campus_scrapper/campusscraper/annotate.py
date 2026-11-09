"""Turn the main content into annotated lines: headings, list items and paragraphs.

Section-based extraction works on these lines instead of raw HTML, so the same
code handles <h3>Qualification</h3><p>..</p>, <dt>/<dd>, <strong>Label</strong>
blocks and <div class="label">..</div><div class="value">..</div> layouts.
"""
from __future__ import annotations

from dataclasses import dataclass

from bs4 import Comment, NavigableString, Tag

from .dom import BLOCK_TAGS, DROP_TAGS
from .vocab import LABELS, label_key

LABEL_ROOT_TAGS = {"strong", "b", "dt", "th", "label", "span", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "em", "small", "td", "li"}


@dataclass
class Line:
    kind: str           # h (heading), label (short label element), li, p
    text: str
    level: int = 9      # 1-6 for <hN>, 7 for label-like elements
    tag: str = ""
    href: str | None = None


def _own_text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def _is_label_element(tag: Tag) -> bool:
    if tag.name not in LABEL_ROOT_TAGS:
        return False
    text = _own_text(tag)
    if not text or len(text) > 45:
        return False
    key = label_key(text)
    if key not in LABELS:
        return False
    # Must not contain block children holding more content.
    return not any(isinstance(c, Tag) and c.name in BLOCK_TAGS and _own_text(c) != text for c in tag.children)


def annotate(node: Tag) -> list[Line]:
    lines: list[Line] = []
    buf: list[str] = []
    in_li = [0]

    def flush():
        text = " ".join("".join(buf).replace("\xa0", " ").split())
        if text:
            lines.append(Line("li" if in_li[0] else "p", text))
        buf.clear()

    def walk(el: Tag):
        for child in el.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buf.append(str(child))
                continue
            if not isinstance(child, Tag) or child.name in DROP_TAGS:
                continue
            name = child.name
            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                flush()
                text = _own_text(child)
                if text:
                    lines.append(Line("h", text, int(name[1]), name))
                continue
            if _is_label_element(child):
                flush()
                lines.append(Line("label", _own_text(child), 7, name))
                continue
            if name == "li":
                flush()
                in_li[0] += 1
                walk(child)
                flush()
                in_li[0] -= 1
                continue
            if name in BLOCK_TAGS:
                flush()
                walk(child)
                flush()
                continue
            walk(child)

    walk(node)
    flush()
    return lines
