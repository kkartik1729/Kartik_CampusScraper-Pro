"""Write seeds into whichever upload template a portal already accepts.

The upload path is the part of each scraper with the most evidence behind it —
the onco one was audited column by column against seven real hospitals. The
discovery work is new. Rather than make the new path carry the crawl, this
writes discovery's output into the file the proven path already reads, so a
prompt produces a spreadsheet a person can inspect, correct and upload exactly
as they would one they built by hand.

    python seed_export.py "20 engineering colleges in Pune" \
        --registry results/registry.db --template campus --out pune.xlsx

Templates:
    onco       Hospital Name, Official Website, City, State, Enabled
    campus     Institution Name, Website, City, Enabled
    attorney   organization, url, city, state
    urls       one URL per line, for anything that takes a plain list
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seedsources import default_chain, parse  # noqa: E402

TEMPLATES = {
    "onco": ["Hospital Name", "Official Website", "City", "State", "Enabled", "Notes"],
    "campus": ["Institution Name", "Website", "City", "Enabled", "Notes"],
    "attorney": ["organization", "url", "city", "state"],
    "urls": ["url"],
}


def host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def rows_for(template: str, seeds: list) -> tuple[list[list], list[tuple[str, str]]]:
    """Build template rows. Returns the rows and the seeds left out, with why.

    One row per host. A seed sharing a host with an earlier one is left out
    rather than written, because every one of these portals crawls per site and
    a repeated host means the same people attributed to two organisations.
    """
    rows: list[list] = []
    skipped: list[tuple[str, str]] = []
    claimed: dict[str, str] = {}

    for seed in seeds:
        if not seed.crawlable:
            # The seed's note may carry something unrelated to the URL — the
            # registry writes a discipline grade there — so only a note that
            # actually explains the missing website is worth repeating.
            reason = seed.note if "url" in seed.note.lower() or "domain" in seed.note.lower() \
                else "no website found"
            skipped.append((seed.name, reason))
            continue
        host = host_of(seed.url)
        if host in claimed:
            skipped.append((seed.name, f"shares {host} with {claimed[host]}"))
            continue
        claimed[host] = seed.name

        name = seed.name[:200]
        city = seed.location or ""
        note = f"{seed.source}, confidence {seed.confidence}"
        if template == "onco":
            rows.append([name, seed.url, city, "", "yes", note])
        elif template == "campus":
            rows.append([name, seed.url, city, "yes", note])
        elif template == "attorney":
            rows.append([name, seed.url, city, ""])
        else:
            rows.append([seed.url])
    return rows, skipped


def write(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import Workbook
        from openpyxl.styles import Font

        book = Workbook()
        sheet = book.active
        sheet.title = "Sources"
        sheet.append(header)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append(row)
        for index, column in enumerate(header, start=1):
            widest = max([len(column)] + [len(str(r[index - 1])) for r in rows] or [0])
            sheet.column_dimensions[chr(64 + index)].width = min(widest + 3, 60)
        sheet.freeze_panes = "A2"
        book.save(path)
    else:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt to an upload-ready sheet")
    parser.add_argument("prompt")
    parser.add_argument("--template", choices=sorted(TEMPLATES), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--no-searxng", action="store_true")
    parser.add_argument("--no-resolve", action="store_true",
                        help="skip website resolution; writes only seeds that already have one")
    args = parser.parse_args()

    intent, unparsed = parse(args.prompt)
    print(f"Read as: {intent.describe()}  (target: {intent.target})")
    for item in unparsed:
        print(f"  could not read {item}")

    chain = default_chain(
        registry_db=args.registry,
        searxng=None if args.no_searxng else "http://127.0.0.1:8080/search",
        delay=args.delay,
    )
    if args.no_resolve:
        for source in chain.sources:
            if hasattr(source, "resolve"):
                source.resolve = lambda blanks, taken=None: None

    seeds, trail = chain.find(intent, args.limit or intent.count)

    print("\nSources tried:")
    for result in trail:
        print(f"  {result.source:12} "
              + (f"{len(result.seeds)} seeds" if result.ok else f"FAILED - {result.error}"))

    rows, skipped = rows_for(args.template, seeds)
    if not rows:
        print("\nNothing to write. No seed resolved to a website.")
        return 1

    write(args.out, TEMPLATES[args.template], rows)
    print(f"\n{len(rows)} rows written to {args.out}")
    print(f"Upload it in the {args.template} portal as you would any sheet.")

    if skipped:
        print(f"\n{len(skipped)} left out:")
        for name, reason in skipped[:20]:
            print(f"  -  {name[:46]:46} {reason[:60]}")
        print("\nCheck these by hand and add rows for any that matter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
