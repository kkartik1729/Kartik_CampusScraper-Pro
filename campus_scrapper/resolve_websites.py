"""Website resolver: fills the registry's blank website column.

The registry gives a statutory name but no URL. This finds the institution's
own site by searching for the name, then scoring candidate domains. Scoring is
the whole point: a search for an Indian college returns aggregator pages long
before it returns the college, so a resolver that takes the first result is
worse than no resolver.

A candidate is accepted only when the domain itself carries enough of the
institution's name. Nothing is written on a weak match; the row stays blank and
is reported, because a wrong website silently poisons every later crawl.

The search function is injected, so the same scoring serves ddgs, SearXNG or
anything else, and so it can be tested without a network.

CLI:
  python resolve_websites.py --db results/registry.db --district Pune --limit 25
  python resolve_websites.py --db results/registry.db --district Pune --dry-run
  python resolve_websites.py --self-test
"""

from __future__ import annotations

import argparse
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import registry_aicte as registry

# The blocklist, trusted suffixes, stopwords and scoring weights now live in
# seedsources/rules.json so they can be corrected without a code change. The
# fallbacks below are only what runs when that file is missing.
try:
    from seedsources import config as _config
except ImportError:  # running standalone, outside the package
    _config = None

_FALLBACK = {
    "blocked_domains": [
        "collegedunia.com", "shiksha.com", "careers360.com", "getmyuni.com",
        "collegedekho.com", "justdial.com", "facebook.com", "linkedin.com",
        "wikipedia.org", "practo.com", "scribd.com", "indeed.com",
    ],
    "trusted_suffixes": [".ac.in", ".edu.in", ".edu", ".org.in"],
    "stopwords": [
        "the", "of", "and", "for", "society", "trust", "education", "foundation",
        "institute", "college", "school", "university", "research", "technology",
        "engineering", "management", "science", "arts", "commerce", "hospital",
        "medical", "law", "legal", "associates", "partners",
    ],
}


def rules() -> dict:
    return _config.load() if _config else _FALLBACK


def weight(name: str, default: float) -> float:
    return _config.weight(name, default) if _config else default


def blocked_domains() -> set:
    return set(rules().get("blocked_domains") or _FALLBACK["blocked_domains"])


def trusted_suffixes() -> tuple:
    return tuple(rules().get("trusted_suffixes") or _FALLBACK["trusted_suffixes"])


def stopwords() -> set:
    return set(rules().get("stopwords") or _FALLBACK["stopwords"])


def service_subdomains() -> set:
    """Subdomains that serve a function rather than represent the institution."""
    return set(rules().get("service_subdomains") or [])


_WORD = re.compile(r"[a-z0-9]+")


def tokens(name: str) -> list[str]:
    """Distinctive words of an institution name, longest first."""
    words = [w for w in _WORD.findall(registry.name_key(name)) if len(w) > 2]
    keep = [w for w in words if w not in stopwords()]
    return sorted(set(keep or words), key=len, reverse=True)


def registrable(host: str) -> str:
    """Strip www and any leading subdomain noise for blocklist checks."""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def is_blocked(host: str) -> bool:
    host = registrable(host)
    return any(host == bad or host.endswith("." + bad) for bad in blocked_domains())


def initials(name: str) -> str:
    """AISSMS from All India Shri Shivaji Memorial Society, for acronym domains."""
    words = [w for w in _WORD.findall(registry.name_key(name)) if len(w) > 2]
    return "".join(w[0] for w in words)[:10]


@dataclass
class Candidate:
    url: str
    host: str
    score: float
    reasons: list[str]


def score_candidate(url: str, name: str) -> Candidate | None:
    """Score one search result against an institution name.

    Returns None for anything disqualified outright.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = parsed.netloc.split(":")[0].lower()
    if not host or parsed.scheme not in {"http", "https"}:
        return None
    if is_blocked(host):
        return None

    bare = registrable(host)
    # Match against the whole host, not just its first label: a college often
    # lives on a subdomain of its trust's domain (garwarecollege.mespune.in,
    # soe.adypu.edu.in), and reading only the first label loses that.
    labels = [lab for lab in bare.split(".") if lab not in {"ac", "edu", "org", "in", "com", "net"}]
    domain_text = re.sub(r"[^a-z0-9]", "", "".join(labels))
    score, reasons = 0.0, []

    name_tokens = tokens(name)
    matched = [t for t in name_tokens if t in domain_text]
    if matched:
        score += min(len(matched), int(weight("name_word_cap", 3))) * weight("name_word_in_domain", 1.2)
        reasons.append(f"name words in domain: {','.join(matched[:3])}")

    # A domain often opens with the name's first words run together
    # (alameencoe.com for Al-Ameen ... College of Engineering). Short leading
    # words like "al" are dropped by the token filter, so compare the squashed
    # name directly and reward a long shared opening.
    squashed = re.sub(r"[^a-z0-9]", "", registry.name_key(name))
    shared = 0
    for a, b in zip(squashed, domain_text):
        if a != b:
            break
        shared += 1
    if shared >= int(weight("shared_opening_min_chars", 6)):
        score += weight("shared_opening", 1.0)
        reasons.append(f"shared opening: {squashed[:shared]}")

    # An acronym domain is common and legitimate: aissms.org, vit.edu.
    acronym = initials(name)
    for size in range(len(acronym), 2, -1):
        prefix = acronym[:size]
        if prefix and prefix == domain_text[: len(prefix)]:
            # Longer acronyms are far stronger evidence: three letters could be
            # coincidence, seven letters in order could not.
            score += min(weight("acronym_per_letter", 0.25) * size, weight("acronym_cap", 2.0))
            reasons.append(f"acronym match: {prefix}")
            break

    if bare.endswith(trusted_suffixes()):
        score += weight("trusted_suffix", 1.0)
        reasons.append("academic domain")
    elif bare.endswith(".in"):
        # An Indian domain is weak evidence on its own, but it is evidence.
        score += weight("indian_domain", 0.4)
        reasons.append("indian domain")

    # An admissions, alumni or webmail subdomain belongs to the institution
    # but is not its site: crawling applynow.example.edu finds a form, not a
    # faculty directory. The parent's name still matches, so without this the
    # service host can outscore the real one.
    first_label = bare.split(".")[0]
    if len(bare.split(".")) > 2 and first_label in service_subdomains():
        score += weight("service_subdomain_penalty", -1.5)
        reasons.append(f"service subdomain: {first_label}")

    # A deep path is a page about the college, not the college's front door.
    depth = len([p for p in parsed.path.split("/") if p])
    if depth == 0:
        score += weight("homepage", 0.5)
        reasons.append("homepage")
    elif depth > 2:
        score += weight("deep_path_penalty", -0.5)
        reasons.append("deep path")

    return Candidate(url=f"{parsed.scheme}://{host}/", host=bare, score=score, reasons=reasons)


def pick(
    results: list[str], name: str, threshold: float | None = None
) -> tuple[Candidate | None, list[Candidate], str]:
    """Choose the best candidate, or nothing when none is convincing.

    Returns the pick, every scored candidate, and why the decision went that
    way — a refusal is only useful if it says what it could not settle.
    """
    scored = [c for c in (score_candidate(u, name) for u in results) if c]
    scored.sort(key=lambda c: c.score, reverse=True)
    if threshold is None:
        threshold = weight("threshold", 2.0)
    if not scored:
        return None, scored, "no usable results"
    if scored[0].score < threshold:
        return None, scored, f"below threshold ({scored[0].score:.2f} < {threshold})"
    # A near-tie between two different domains means we cannot tell them apart.
    # Common for a trust that runs several colleges on sibling domains.
    if len(scored) > 1 and scored[1].host != scored[0].host and scored[0].score - scored[1].score < weight("ambiguity_gap", 0.6):
        return None, scored, f"too close to call: {scored[1].host} at {scored[1].score:.2f}"
    return scored[0], scored, "accepted"


def ddgs_search(query: str, max_results: int = 8, attempts: int = 3) -> list[str]:
    """Default searcher. Needs `pip install ddgs`.

    DuckDuckGo throttles a steady stream of queries, and a throttled call raises
    rather than returning nothing. Back off and retry, because the alternative
    is a batch where a third of the rows fail for a reason that had nothing to
    do with the institution.
    """
    from ddgs import DDGS

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with DDGS() as client:
                return [r["href"] for r in client.text(query, max_results=max_results) if r.get("href")]
        except Exception as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(5 * (attempt + 1))
    raise last_error if last_error else RuntimeError("search failed")


def resolve(
    conn,
    district: str | None = None,
    discipline: str | None = None,
    limit: int | None = None,
    searcher=ddgs_search,
    delay: float = 2.0,
    dry_run: bool = False,
    threshold: float = 2.0,
    use_model: bool = False,
    progress: bool = True,
) -> dict:
    """Resolve websites for registry rows that have none."""
    rows = registry.query(
        conn, district=district, discipline=discipline,
        missing_website=True, limit=limit,
    )
    report = {"attempted": 0, "resolved": 0, "unresolved": [], "accepted": [],
              "review": [], "shared": []}

    # A trust that runs several colleges outranks each of them in search, so
    # sibling institutions resolve to the same parent domain. Accepting both
    # would crawl one site twice and attribute the same staff to two colleges,
    # which is worse than leaving a row blank. Websites already recorded count
    # as claimed, so a second run cannot undo the first run's decisions.
    claimed: dict[str, str] = {
        row["website"]: row["name"]
        for row in conn.execute(
            "SELECT name, website FROM institutions WHERE website IS NOT NULL AND website != ''"
        )
    }

    total = len(rows)
    started = time.time()

    for position, row in enumerate(rows, 1):
        report["attempted"] += 1
        if progress:
            done = position - 1
            rate = (time.time() - started) / done if done else 0
            left = f", ~{int(rate * (total - done) / 60)} min left" if done >= 3 else ""
            print(f"  [{position}/{total}{left}] {row['name'][:48]}", flush=True)
        district_hint = (row.get("district") or "").title()
        query = f"{row['name']} {district_hint} official website"
        try:
            results = searcher(query)
        except Exception as error:  # a dead search must not kill the batch
            report["unresolved"].append((row["name"], f"search failed: {type(error).__name__}"))
            time.sleep(delay)
            continue

        best, scored, why = pick(results, row["name"], threshold)

        # Where the scorer refused, the question left is which of two plausible
        # domains belongs to this institution, and that is judgement rather
        # than arithmetic. A local model may answer it, choosing from the list
        # the scorer already shortlisted; it can never supply a URL of its own
        # and its "cannot tell" is respected.
        #
        # The model breaks ties; it does not rescue weak candidates. Only
        # candidates that already clear the threshold on their own evidence are
        # offered, and a pick is accepted only if it still clears it. Asked to
        # choose among domains that score near zero, a model will choose one
        # anyway, which is how an unrelated site becomes a confident answer.
        model_note = ""
        if not best and use_model:
            import tiebreak

            plausible = [c for c in scored if c.score >= threshold]
            if len(plausible) > 1:
                chosen, verdict = tiebreak.break_tie(
                    row["name"], plausible, (row.get("district") or "").title()
                )
                if chosen and chosen.score >= threshold:
                    best = chosen
                    model_note = " (model chose between close candidates)"
                elif verdict.error:
                    model_note = f" (model unavailable: {verdict.error})"

        owner = claimed.get(best.url) if best else None
        if owner and owner != row["name"]:
            report["shared"].append((row["name"], best.url, owner, row["aicte_id"]))
            best = None
            why = f"shared domain, already claimed by {owner[:45]}"

        if best:
            report["resolved"] += 1
            report["accepted"].append(
                (row["name"], best.url, round(best.score, 2),
                 "; ".join(best.reasons) + model_note)
            )
            claimed[best.url] = row["name"]
            if progress:
                print(f"        -> {best.url}", flush=True)
            if not dry_run:
                registry.set_website(
                    conn, row["aicte_id"], best.url,
                    "ddgs+model" if model_note.startswith(" (model chose") else "ddgs",
                )
        elif scored and scored[0].score >= threshold - 0.9:
            why = why + model_note
            # Close enough to be worth a human glance rather than a silent drop.
            report["review"].append(
                (row["aicte_id"], row["name"], scored[0].url, round(scored[0].score, 2), why)
            )
        else:
            report["unresolved"].append((row["name"], why))
        time.sleep(delay)

    return report


def self_test() -> None:
    """Scoring checks with canned results. No network."""
    cases = [
        (
            "COEP TECHNOLOGICAL UNIVERSITY",
            ["https://collegedunia.com/college/coep", "https://www.coep.org.in/",
             "https://en.wikipedia.org/wiki/COEP"],
            "coep.org.in",
        ),
        (
            "ALL INDIA SHRI SHIVAJI MEMORIAL SOCIETY'S COLLEGE OF ENGINEERING",
            ["https://www.shiksha.com/college/aissms", "https://aissmscoe.com/"],
            "aissmscoe.com",
        ),
        (
            "ANANTRAO PAWAR COLLEGE OF ENGINEERING & RESEARCH",
            ["https://www.careers360.com/colleges/anantrao-pawar",
             "https://www.abmspcoerpune.org/"],
            None,  # nothing convincing: domain does not carry the name
        ),
        (
            "D.Y.PATIL COLLEGE OF ENGINEERING",
            ["https://collegedunia.com/x", "https://justdial.com/y"],
            None,  # every result blocked
        ),
        (
            # Subdomain of a trust's domain: reading only the first label missed it.
            "ABASAHEB GARWARE COLLEGE",
            ["https://www.shiksha.com/garware", "https://garwarecollege.mespune.in/"],
            "garwarecollege.mespune.in",
        ),
        (
            "AL-AMEEN EDUCATIONAL & MEDICAL FOUNDATION'S COLLEGE OF ENGINEERING",
            ["https://collegedunia.com/alameen", "https://alameencoe.com/"],
            "alameencoe.com",
        ),
        (
            # An admissions subdomain carries the parent's name and so used to
            # outscore the institution's own site.
            "BHARATI VIDYAPEETH'S JAWAHARLAL NEHRU INSTITUTE OF TECHNOLOGY",
            ["https://applynow.bharatividyapeeth.edu/", "https://www.bharatividyapeeth.edu/"],
            "bharatividyapeeth.edu",
        ),
    ]
    failures = 0
    for name, results, expected in cases:
        best, _, _why = pick(results, name)
        got = best.host if best else None
        status = "ok " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"  {status} {name[:45]:45}  expected={expected}  got={got}")

    # Blocklist must survive subdomains.
    assert is_blocked("www.collegedunia.com")
    assert is_blocked("m.facebook.com")
    assert not is_blocked("coep.org.in")
    print(f"\n{len(cases) - failures}/{len(cases)} scoring cases passed, blocklist checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve institution websites")
    parser.add_argument("--db", type=Path, default=registry.DB_PATH)
    parser.add_argument("--district")
    parser.add_argument("--discipline", choices=sorted(registry.DISCIPLINE_WORDS))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--threshold", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true", help="score but write nothing")
    parser.add_argument("--quiet", action="store_true", help="no per-row progress")
    parser.add_argument("--use-model", action="store_true",
                        help="let the local Ollama model break ties the scorer refused")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--accept", nargs=2, metavar=("AICTE_ID", "URL"),
                        help="record a website you checked by hand")
    parser.add_argument("--review-csv", type=Path,
                        help="write the held-back rows to a CSV for bulk review")
    parser.add_argument("--apply-csv", type=Path,
                        help="read a reviewed CSV back and record the rows marked yes")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    if args.use_model:
        import tiebreak

        reachable, note = tiebreak.available()
        print(f"model: {note}")
        if not reachable:
            print("  continuing without it; every refusal stands as it is\n")

    conn = registry.connect(args.db)

    if args.apply_csv:
        import csv as csv_module

        applied = skipped_rows = 0
        with args.apply_csv.open(encoding="utf-8-sig", newline="") as handle:
            for record in csv_module.DictReader(handle):
                keep = str(record.get("accept", "")).strip().lower()
                url = str(record.get("url", "")).strip()
                aicte_id = str(record.get("aicte_id", "")).strip()
                if keep in {"y", "yes", "1", "true"} and url and aicte_id:
                    registry.set_website(conn, aicte_id, url, "reviewed")
                    applied += 1
                else:
                    skipped_rows += 1
        print(f"recorded {applied} websites, left {skipped_rows} rows alone")
        return

    if args.accept:
        aicte_id, url = args.accept
        registry.set_website(conn, aicte_id, url, "manual")
        row = conn.execute("SELECT name, website FROM institutions WHERE aicte_id=?", (aicte_id,)).fetchone()
        print(f"set {row['name']} -> {row['website']}" if row else f"no institution with id {aicte_id}")
        return
    report = resolve(
        conn, district=args.district, discipline=args.discipline,
        limit=args.limit, delay=args.delay, dry_run=args.dry_run,
        threshold=args.threshold, use_model=args.use_model,
        progress=not args.quiet,
    )

    print()
    print(f"attempted {report['attempted']}, resolved {report['resolved']}"
          f"{' (dry run, nothing written)' if args.dry_run else ''}\n")
    for name, url, score, why in report["accepted"]:
        print(f"  OK   {name[:42]:42}  {url:38}  {score}  {why}")
    if report["review"]:
        print("\n  Held back — check these by eye, then accept the good ones:")
        for aicte_id, name, url, score, why in report["review"]:
            print(f"  ?    {name[:40]:40}  {url:40}  {score:<5}  {why}")
            print(f"         {aicte_id}")
        print("\n  To accept one:")
        print(f"    python tools\\{Path(__file__).name} --db {args.db} --accept <aicte-id> <url>")
    if report.get("shared"):
        print("\n  Shared domain, left blank so one site is not crawled twice:")
        for name, url, owner, _rid in report["shared"]:
            print(f"  =    {name[:40]:40}  {url:38}  held by {owner[:30]}")
    if args.review_csv and (report["review"] or report.get("shared")):
        import csv as csv_module

        args.review_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.review_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv_module.writer(handle)
            writer.writerow(["accept", "name", "url", "score", "reason", "aicte_id"])
            for aicte_id, name, url, score, why in report["review"]:
                writer.writerow(["", name, url, score, why, aicte_id])
            for name, url, owner, rid in report.get("shared", []):
                writer.writerow(["", name, url, "", f"shared with {owner}", rid])
        print(f"\n{len(report['review']) + len(report.get('shared', []))} rows for review "
              f"written to {args.review_csv}")
        print("  Put y in the accept column for the rows you want, then:")
        print(f"    python resolve_websites.py --db {args.db} --apply-csv {args.review_csv}")

    if report["unresolved"]:
        print()
        for name, why in report["unresolved"]:
            print(f"  --   {name[:42]:42}  {why}")


if __name__ == "__main__":
    main()
