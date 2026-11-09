"""Editable rules, loaded from rules.json.

The blocklist, the stopwords and the scoring weights are judgement calls, not
logic. Keeping them in code meant every adjustment was a code change and a
re-test; keeping them in a file means the person who spots a bad domain can fix
it without touching Python.

A missing or malformed file falls back to the built-in defaults rather than
stopping a run, and says so, because a run that works with yesterday's rules
beats a run that does not start.
"""

from __future__ import annotations

import json
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")

DEFAULTS = {
    "blocked_domains": [],
    "trusted_suffixes": [".ac.in", ".edu.in", ".edu", ".org.in"],
    "scoring": {
        "threshold": 2.0,
        "name_word_in_domain": 1.2,
        "name_word_cap": 3,
        "acronym_per_letter": 0.25,
        "acronym_cap": 2.0,
        "shared_opening_min_chars": 6,
        "shared_opening": 1.0,
        "trusted_suffix": 1.0,
        "indian_domain": 0.4,
        "homepage": 0.5,
        "deep_path_penalty": -0.5,
        "ambiguity_gap": 0.6,
    },
    "stopwords": [],
}

_cache: dict | None = None
_warned = False


def load(path: Path | None = None, force: bool = False) -> dict:
    """Read the rules. Cached, since every candidate scored calls this."""
    global _cache, _warned
    if _cache is not None and not force and path is None:
        return _cache

    target = path or RULES_PATH
    rules = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        for key, value in loaded.items():
            if key.startswith("_"):
                continue
            if isinstance(value, dict) and isinstance(rules.get(key), dict):
                rules[key].update(value)
            else:
                rules[key] = value
    except FileNotFoundError:
        if not _warned:
            print(f"[seedsources] no rules file at {target}, using built-in defaults")
            _warned = True
    except (json.JSONDecodeError, OSError) as error:
        if not _warned:
            print(f"[seedsources] rules file unreadable ({error}); using built-in defaults")
            _warned = True

    if path is None:
        _cache = rules
    return rules


def weight(name: str, default: float = 0.0) -> float:
    return float(load()["scoring"].get(name, default))
