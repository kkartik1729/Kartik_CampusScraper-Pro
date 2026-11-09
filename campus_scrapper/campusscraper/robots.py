"""Small RFC 9309 robots.txt parser (wildcards, $ anchors, longest match, crawl-delay).

The stdlib ``urllib.robotparser`` ignores ``*`` and ``$`` inside paths, which
real hospital robots files use (for example ``Disallow: *?speciality=*``).
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit


def _norm(path: str) -> str:
    path = quote(path, safe="/%*?$:@!&'()+,;=-._~")

    def fix(m: re.Match) -> str:
        ch = chr(int(m.group(0)[1:], 16))
        return ch if ch.isascii() and (ch.isalnum() or ch in "-._~") else m.group(0).upper()

    return re.sub(r"%[0-9a-fA-F]{2}", fix, path)


def _match(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    parts = pattern.split("*")
    if len(parts) == 1:
        return path == pattern if anchored else path.startswith(pattern)
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    for seg in parts[1:-1]:
        found = path.find(seg, pos)
        if found < 0:
            return False
        pos = found + len(seg)
    last = parts[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= pos
    return path.find(last, pos) >= 0


class RobotsPolicy:
    def __init__(self, content: str, user_agent: str):
        groups: list[tuple[list[str], list[tuple[bool, str]], list[float]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delays: list[float] = []
        in_rules = False
        self.sitemaps: list[str] = []
        for raw in content.splitlines()[:20000]:
            line = raw.split("#", 1)[0].strip().lstrip("﻿")
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    groups.append((agents, rules, delays))
                    agents, rules, delays, in_rules = [], [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:
                    rules.append((key == "allow", _norm(value)))
            elif key == "crawl-delay":
                in_rules = True
                try:
                    delays.append(max(0.0, float(value)))
                except ValueError:
                    pass
            elif key == "sitemap" and value:
                self.sitemaps.append(value)
        if agents:
            groups.append((agents, rules, delays))
        token = user_agent.lower()
        best, chosen = -1, []
        for g_agents, g_rules, g_delays in groups:
            score = max((0 if a == "*" else len(a) for a in g_agents
                         if a == "*" or (a and a in token)), default=-1)
            if score > best:
                best, chosen = score, [(g_rules, g_delays)]
            elif score == best and score >= 0:
                chosen.append((g_rules, g_delays))
        self.rules = [r for rs, _ in chosen for r in rs]
        self.crawl_delay = max((d for _, ds in chosen for d in ds), default=0.0)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = _norm(parts.path or "/")
        if parts.query:
            path += "?" + _norm(parts.query)
        best: tuple[int, bool] | None = None
        for allow, rule in self.rules:
            if _match(rule, path):
                length = len(rule.replace("*", ""))
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]


class AllowAll(RobotsPolicy):
    def __init__(self):  # noqa: D401 - trivial
        self.rules, self.crawl_delay, self.sitemaps = [], 0.0, []

    def allowed(self, url: str) -> bool:
        return True
