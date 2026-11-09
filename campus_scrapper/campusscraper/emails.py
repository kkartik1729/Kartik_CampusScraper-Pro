"""Find emails that a page publishes, including hidden or obfuscated forms.

Only addresses written on the page are returned. Addresses are never guessed
or built from name patterns.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}(?![\w-])")
# name [at] domain [dot] com  /  name(at)domain(dot)co(dot)in  /  name AT domain DOT com
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]{2,64})\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+|\s*@\s*)\s*"
    r"([A-Za-z0-9\-]{2,63}(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|\.)\s*[A-Za-z0-9\-]{2,63}){1,4})",
    re.I,
)
DOT_RE = re.compile(r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|\.)\s*", re.I)
GENERIC_LOCAL = re.compile(
    r"^(?:info|contact|contactus|enquir(?:y|ies)|inquir(?:y|ies)|appointments?|appt|bookings?|helpdesk|help|support|"
    r"care|customercare|patientcare|feedback|admin|office|hr|careers?|jobs|marketing|media|pr|webmaster|noreply|"
    r"no-reply|reception|frontdesk|opd|emergency|international|ipd|billing|accounts|query|queries|mail|email|hello|"
    r"principal|vice\.?principal|registrar|dean\w*|director\w*|dept\w*|department\w*|hod\w*|tpo\w*|tnp\w*|t\.?p\.?o|"
    r"placements?\w*|training\w*|career\w*|cdc|crc|alumni\w*|iro|internationa\w*|admissions?\w*|exam\w*|coe|"
    r"students?\w*|studentaffairs|dsw|library|librarian|iqac|naac|nba|research|rnd|r&d|"
    r"(?:cse|ece|eee|it|ise|mech|me|civil|ce|che|chem|mca|mba|aids|aiml|csm|csd|bt|eie|ae|maths|physics|chemistry|"
    r"english|hss|sh|bsh)(?:[._-]?(?:office|dept|hod|head|desk))?|office\w*|ms|msoffice)$",
    re.I,
)
IGNORE_DOMAINS = re.compile(r"(?:example\.(?:com|org)|sentry\.io|wixpress\.com|domain\.com|email\.com|yourdomain)$", re.I)
ASSET_SUFFIX = re.compile(r"\.(?:png|jpe?g|gif|webp|svg|css|js)$", re.I)


def _cf_decode(hexstr: str) -> str | None:
    try:
        data = bytes.fromhex(hexstr)
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    try:
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except UnicodeDecodeError:
        return None


def decode_protected(soup: BeautifulSoup) -> int:
    """Replace Cloudflare 'email protection' placeholders with the real address. Returns count."""
    count = 0
    for el in soup.select("[data-cfemail]"):
        addr = _cf_decode(el.get("data-cfemail", ""))
        if addr and EMAIL_RE.fullmatch(addr):
            el.replace_with(NavigableString(addr))
            count += 1
    for a in soup.select('a[href*="/cdn-cgi/l/email-protection#"]'):
        addr = _cf_decode(a["href"].split("#", 1)[1])
        if addr and EMAIL_RE.fullmatch(addr):
            a["href"] = "mailto:" + addr
            if not EMAIL_RE.search(a.get_text()):
                a.append(NavigableString(" " + addr))
            count += 1
    return count


# Endings an address can really have. Prose such as "served@ Dr. Ram" or "observer@St. Jude"
# looks like an email to a regex; its ending (.ram, .jude) is not a real domain ending.
REAL_TLDS = set("""
com org net edu gov mil int info biz co io ai me us uk in ae sa qa om kw bh sg my lk np bd pk au nz ca de fr it es nl
be ch at se no dk fi ie pt pl cz ru ua jp cn hk tw kr th ph id vn za ng ke eg br mx ar cl health care clinic hospital
doctor doctors med medical email online today live academy institute center centre foundation global world tech
example test
""".split())


def _valid(addr: str) -> bool:
    addr = addr.strip(".").lower()
    if not EMAIL_RE.fullmatch(addr) or ASSET_SUFFIX.search(addr):
        return False
    local, domain = addr.split("@", 1)
    labels = domain.split(".")
    if labels[-1] not in REAL_TLDS or any(not lab for lab in labels):
        return False
    if len(labels) == 2 and len(labels[0]) < 2:
        return False
    return not IGNORE_DOMAINS.search(domain)


def find(text: str) -> list[str]:
    """All emails in a block of text, plain or obfuscated, in order, de-duplicated."""
    out: list[str] = []
    for m in EMAIL_RE.finditer(text):
        out.append(m.group(0).strip("."))
    for m in OBFUSCATED_RE.finditer(text):
        if "@" in m.group(0) and EMAIL_RE.search(m.group(0)):
            continue  # already a plain address
        raw = m.group(0).lower()
        if not re.search(r"\bat\b|\[at\]|\(at\)|\{at\}", raw):
            continue
        addr = f"{m.group(1)}@{DOT_RE.sub('.', m.group(2))}".replace(" ", "")
        out.append(addr)
    seen: list[str] = []
    for a in out:
        a = a.lower()
        if _valid(a) and a not in seen:
            seen.append(a)
    return seen


def from_links(node) -> list[str]:
    out = []
    for a in node.find_all("a", href=re.compile(r"^\s*mailto:", re.I)):
        for part in a["href"].split(":", 1)[1].split("?")[0].split(","):
            part = part.strip().lower()
            if _valid(part) and part not in out:
                out.append(part)
    return out


def is_personal(addr: str, name: str | None) -> bool:
    """True if the address looks like the doctor's own, judged from the address itself.

    A generic desk address (info@, appointments@, oncology@ ...) is never personal.
    An address whose local part contains the doctor's first name or surname is personal.
    Otherwise it is unknown and treated as not personal.
    """
    local = addr.split("@", 1)[0].lower()
    if GENERIC_LOCAL.match(local):
        return False
    if not name:
        return False
    parts = [p.strip(".").lower() for p in re.sub(r"^(?:dr|prof)\.?\s+", "", name, flags=re.I).split()]
    parts = [p for p in parts if len(p) >= 3]
    compact = re.sub(r"[^a-z]", "", local)
    return any(p in compact for p in parts)


def classify(addrs: list[str], name: str | None, single_doctor: bool = True) -> tuple[list[str], list[str]]:
    """Split into (doctor's own emails, other contact emails shown on the page).

    On a page or card about one doctor, a single non-desk address that does not
    contain the name (e.g. 'drsks@...') is still taken as the doctor's.
    """
    own = [a for a in addrs if is_personal(a, name)]
    if not own and single_doctor:
        candidates = [a for a in addrs if not GENERIC_LOCAL.match(a.split("@", 1)[0])]
        if len(candidates) == 1:
            own = candidates
    other = [a for a in addrs if a not in own]
    return own, other
