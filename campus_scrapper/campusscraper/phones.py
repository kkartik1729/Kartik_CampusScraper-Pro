"""Indian phone numbers published on a page: find, validate, normalise and classify.

Only numbers written on the page (text, tel: links, WhatsApp links, structured
data) are returned. Numbers are never generated.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

# A loose candidate: optional country/trunk prefix, then 8-13 digits in groups.
CANDIDATE_RE = re.compile(
    r"(?<![\w/.-])(?:\+\s?91|0091|91(?=[\s.-]?[6-9]\d{4}))?[\s.-]?\(?0?\d{2,5}\)?(?:[\s.-]?\d{2,5}){1,4}(?![\w/-])"
)
TOLL_FREE_RE = re.compile(r"(?<!\d)(1800|1860)[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
DESK_WORDS = re.compile(
    r"appointment|book|helpline|help ?line|call us|call centre|call center|toll ?free|emergency|ambulance|"
    r"reception|enquir|inquir|board ?line|switchboard|hospital|front ?desk|24 ?x ?7|24/7|customer|patient care|"
    r"international|opd|billing|ipd|helpdesk|desk|landline|office|corporate|registration desk|whatsapp us|sms|"
    r"principal|registrar|admission|placement|tpo|t ?& ?p|department|dept|ext\b|extn|intercom|campus|college|university",
    re.I,
)
PERSONAL_WORDS = re.compile(
    r"\b(?:mobile|mob|cell|personal|direct|cabin|chamber|private)\b",
    re.I,
)
NOT_PHONE_CONTEXT = re.compile(
    r"\b(?:reg(?:istration)?\.?\s*(?:no|number)?|mci|nmc|dmc|kmc|tnmc|pin\s*code|pincode|fax|gst|cin|pan|"
    r"license|licence|ref|id|year|since|batch|isbn|doi|pmid)\b[\s.:#-]*$",
    re.I,
)


def _national(digits: str) -> str | None:
    """Return the 10-digit national number, or None if not a valid Indian number."""
    if digits.startswith("0091"):
        digits = digits[4:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10 or digits[0] == "0":
        return None
    if len(set(digits)) <= 2:  # 9999999999, 1212121212 and similar placeholders
        return None
    if _is_sequence(digits) or digits.startswith("10"):  # page numbers '10 11 12 13 14'; no STD code starts 10
        return None
    return digits


def _is_sequence(digits: str) -> bool:
    """'1011121314', '1516171819', '0123456789': counting runs, not phone numbers."""
    for size in (1, 2):
        chunks = [int(digits[i:i + size]) for i in range(0, len(digits) - len(digits) % size, size)]
        steps = {b - a for a, b in zip(chunks, chunks[1:])}
        if len(chunks) >= 4 and len(steps) == 1 and steps <= {1, -1}:
            return True
    return False


# Two-digit STD codes that start like a mobile number (Bengaluru, Ahmedabad).
STD_6TO8 = ("80", "79")


def normalise(raw: str) -> str | None:
    """'011-4055 4055' -> '+91 11 4055 4055'; '98110 12345' -> '+91 98110 12345'; toll-free as 1800-xxx-xxxx
    (toll-free numbers are recognised so they can be left out; see find())."""
    m = TOLL_FREE_RE.search(raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    digits = re.sub(r"\D", "", raw)
    nat = _national(digits)
    if not nat:
        return None
    # An STD code written separately ('(080) ...', '011-...', '+91 (80) ...', '+91 22 6630 1000')
    # marks a landline even when the number starts with 6-9 (Bengaluru 80, Chennai... ).
    std_m = (re.match(r"^\s*(?:\+\s?91|0091)?[\s.-]*\(\s*0?(\d{2,4})\s*\)", raw)
             or re.match(r"^\s*0(\d{2,4})[\s.-]+\d", raw)
             or re.match(r"^\s*(?:\+\s?91|0091)[\s.-]*0?(\d{2,4})[\s.-]+\d{3,4}[\s.-]?\d{3,4}\s*$", raw))
    if nat[0] in "6789":
        # 9 never starts an STD code. 6/7/8 only when the STD code is written separately AND the
        # local part looks like a landline (starts 2-6); otherwise it is a mobile, whatever the spacing.
        std = std_m.group(1) if std_m and nat.startswith(std_m.group(1)) else ""
        local = nat[len(std):]
        landline = (nat[0] != "9" and std and local[:1] in "23456"
                    and (len(std) >= 3 or std in STD_6TO8))
        if not landline:
            return f"+91 {nat[:5]} {nat[5:]}"
    std = std_m.group(1) if std_m and nat.startswith(std_m.group(1)) else nat[:2]
    rest = nat[len(std):]
    return f"+91 {std} {rest[:-4]} {rest[-4:]}".replace("  ", " ")


def is_mobile(number: str) -> bool:
    """Mobiles are normalised as '+91 XXXXX XXXXX' (two 5-digit groups)."""
    return re.fullmatch(r"\+91 [6-9]\d{4} \d{5}", number) is not None


def is_toll_free(number: str) -> bool:
    return number.startswith(("1800", "1860"))


def find(text: str) -> list[tuple[str, str]]:
    """All phone numbers in text as (normalised number, 70 characters of text before it)."""
    hits: list[tuple[int, int, str]] = []
    for m in TOLL_FREE_RE.finditer(text):
        hits.append((m.start(), m.end(), m.group(0)))
    for m in CANDIDATE_RE.finditer(text):
        if any(s <= m.start() < e for s, e, _ in hits):
            continue
        hits.append((m.start(), m.end(), m.group(0)))
    hits.sort()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    prev_end = 0
    for start, end, raw in hits:
        lead = len(raw) - len(raw.lstrip(" .-"))
        start += lead
        raw = raw.strip(" .-")
        digits = re.sub(r"\D", "", raw)
        if not 10 <= len(digits) <= 13 and not TOLL_FREE_RE.fullmatch(raw):
            continue
        # Context = text since the previous number or the last separator, so labels don't bleed.
        before = text[max(prev_end, start - 70):start]
        before = re.split(r"[|\n•;]|,\s|\s{2,}", before)[-1]
        prev_end = end
        if NOT_PHONE_CONTEXT.search(before):
            continue
        # Part of a longer run of numbers (page numbers, tables of figures): not a phone.
        if re.match(r"\s{1,2}\d{1,2}(?!\d)", text[end:end + 5]) or re.search(r"(?<!\d)\d{1,2}\s{1,2}$", text[max(0, start - 5):start]):
            continue
        num = normalise(raw)
        if num and not is_toll_free(num) and num not in seen:
            seen.add(num)
            out.append((num, before))
    return out


def from_links(node) -> list[tuple[str, str]]:
    """tel: and WhatsApp links as (number, link text + nearby text)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in node.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        raw = None
        if low.startswith("tel:"):
            raw = unquote(href[4:])
        elif "wa.me/" in low:
            raw = urlsplit(href).path.rsplit("/", 1)[-1]
        elif "whatsapp.com/send" in low or low.startswith("whatsapp:"):
            raw = (parse_qs(urlsplit(href).query).get("phone") or [""])[0]
        if not raw:
            continue
        num = normalise(raw)
        if num and not is_toll_free(num) and num not in seen:
            seen.add(num)
            context = " ".join(a.get_text(" ", strip=True).split())
            parent = " ".join(a.parent.get_text(" ", strip=True).split()) if a.parent else ""
            # Use the surrounding element only when it is a small label block, not a whole section.
            near = parent if len(parent) <= 120 else context
            label = "whatsapp " if ("wa.me" in low or "whatsapp" in low) else ""
            out.append((num, f"{label}{near}"))
    return out


def classify(found: list[tuple[str, str]], single_doctor: bool = True) -> tuple[list[str], list[str]]:
    """Split into (doctor's own numbers, hospital/desk numbers).

    Desk words near the number (appointment, helpline, toll free, emergency ...)
    or a toll-free number -> desk. Personal words (mobile, direct, clinic) ->
    doctor. Unlabelled numbers on a single doctor's page: a mobile number is
    taken as the doctor's, a landline as the hospital's. Numbers repeated
    across many doctors are moved to desk later, at crawl level.
    """
    own: list[str] = []
    desk: list[str] = []
    for num, context in found:
        ctx = context[-70:]
        if is_toll_free(num) or (DESK_WORDS.search(ctx) and not PERSONAL_WORDS.search(ctx[-25:])):
            desk.append(num)
        elif PERSONAL_WORDS.search(ctx):
            own.append(num)
        elif single_doctor and is_mobile(num):
            own.append(num)
        else:
            desk.append(num)
    return list(dict.fromkeys(own)), list(dict.fromkeys(d for d in desk if d not in own))
