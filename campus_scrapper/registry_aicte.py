"""AICTE registry seed source.

Loads the AICTE approved-institutions list into SQLite and answers prompt-style
queries against it, so a run never has to search the web for a college that is
already on a statutory list.

Two load paths share one table:
  load_file()  reads a master .xlsx/.csv snapshot (works offline)
  load_api()   pulls from the AICTE dashboard backend (fill in when reachable)

CLI:
  python registry_aicte.py load AICTE_Approved_Colleges_Master.xlsx
  python registry_aicte.py query --district Pune --limit 20
  python registry_aicte.py query --district Pune --type private --missing-website
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("results/registry.db")

FACULTY_API = "https://facilities.aicte-india.org/dashboard/pages/php/faculty.php"
COURSE_API = "https://facilities.aicte-india.org/dashboard/pages/php/approvedcourse.php"
CURRENT_YEAR = "2024-2025"

SCHEMA = """
CREATE TABLE IF NOT EXISTS institutions (
    aicte_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    name_key       TEXT NOT NULL,
    address        TEXT,
    district       TEXT,
    state          TEXT,
    inst_type      TEXT,
    women_only     INTEGER DEFAULT 0,
    minority       INTEGER DEFAULT 0,
    website        TEXT,
    website_source TEXT,
    source         TEXT NOT NULL,
    source_year    TEXT,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_district ON institutions(district);
CREATE INDEX IF NOT EXISTS idx_type ON institutions(inst_type);
CREATE INDEX IF NOT EXISTS idx_name_key ON institutions(name_key);

-- Rankings live in their own table because they come from a different body on
-- a different cycle, and because an institution can be ranked in several
-- categories at once. Joining on name_key is approximate; aicte_id is filled
-- in where a confident match exists.
CREATE TABLE IF NOT EXISTS rankings (
    source      TEXT NOT NULL,       -- nirf, qs, user
    category    TEXT NOT NULL,       -- engineering, management, overall
    year        TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL,
    city        TEXT,
    aicte_id    TEXT,
    PRIMARY KEY (source, category, year, rank)
);
CREATE INDEX IF NOT EXISTS idx_rank_key ON rankings(name_key);
CREATE INDEX IF NOT EXISTS idx_rank_aicte ON rankings(aicte_id);
"""

# Prompt words mapped to the values AICTE actually stores.
TYPE_ALIASES = {
    "private": ("Private-Self Financing", "State Private University", "Deemed to be University(Pvt)"),
    "government": ("Government", "Govt aided", "State Government University",
                   "Central University", "Deemed to be University(Govt)"),
    "aided": ("Govt aided",),
    "university": ("State Government University", "State Private University",
                   "Central University", "Deemed to be University(Pvt)",
                   "Deemed to be University(Govt)"),
    "deemed": ("Deemed to be University(Pvt)", "Deemed to be University(Govt)"),
}

# Discipline is not a column in the AICTE institutions list; the authoritative
# answer is that institution's approved courses. Until the course endpoint is
# reachable, the name is the only signal available, so matches are graded rather
# than silently dropped: a name carrying only discipline words is "clear", one
# carrying discipline and other-field words is "mixed", and a name that says
# nothing either way is "unknown" and is never thrown away without being counted.
DISCIPLINE_WORDS = {
    "engineering": r"\b(engineering|technolog(y|ical)|polytechnic|technical)\b",
    "management": r"\b(management|business|mba|pgdm|commerce)\b",
    "pharmacy": r"\b(pharmac(y|eutical))\b",
    "architecture": r"\b(architect(ure|ural)?|planning|design)\b",
    "hotel": r"\b(hotel|hospitality|catering|culinary)\b",
    "computer": r"\b(computer applicat\w*|information technolog\w*|mca|bca)\b",
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


def classify_discipline(name: str, wanted: str) -> str:
    """Grade a name against a discipline: clear, mixed, or unknown."""
    pattern = DISCIPLINE_WORDS.get(wanted.lower().strip())
    if not pattern:
        return "unknown"
    hit = re.search(pattern, name, re.I) is not None
    others = [
        key for key, expr in DISCIPLINE_WORDS.items()
        if key != wanted.lower().strip() and re.search(expr, name, re.I)
    ]
    if hit and not others:
        return "clear"
    if hit and others:
        return "mixed"
    if others:
        return "other"
    return "unknown"


def name_key(name: str) -> str:
    """Stable join key: lowercase, accent-free, punctuation-free, single-spaced."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = _PUNCT.sub(" ", text.lower())
    return _SPACE.sub(" ", text).strip()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def faculty_url(aicte_id: str, year: str = CURRENT_YEAR) -> str:
    """AICTE's own faculty endpoint for one institution."""
    quoted = urllib.parse.quote(str(aicte_id))
    return f"{FACULTY_API}?method=fetchdata&aicteid={quoted}&pid={quoted}&year={urllib.parse.quote(year)}"


def course_url(aicte_id: str, year: str = CURRENT_YEAR) -> str:
    quoted = urllib.parse.quote(str(aicte_id))
    return f"{COURSE_API}?method=fetchdata&aicteid={quoted}&course=1&year={urllib.parse.quote(year)}"


def _flag(value) -> int:
    return 1 if str(value).strip().upper() in {"Y", "YES", "1", "TRUE"} else 0


def load_file(path: Path, conn: sqlite3.Connection, source_year: str = CURRENT_YEAR) -> int:
    """Load a master snapshot. Existing rows are updated, websites are preserved."""
    import pandas as pd

    frame = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    frame.columns = [str(c).strip() for c in frame.columns]

    required = {"Aicte ID", "Name"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for record in frame.to_dict("records"):
        aicte_id = str(record["Aicte ID"]).strip()
        if not aicte_id or aicte_id.lower() == "nan":
            continue
        name = str(record["Name"]).strip()
        rows.append((
            aicte_id,
            name,
            name_key(name),
            str(record.get("Address", "") or "").strip(),
            str(record.get("District", "") or "").strip().upper(),
            str(record.get("State", "") or "").strip().upper() or None,
            str(record.get("Institution Type", "") or "").strip(),
            _flag(record.get("Women")),
            _flag(record.get("Minority")),
            f"file:{path.name}",
            source_year,
            now,
        ))

    conn.executemany(
        """
        INSERT INTO institutions
            (aicte_id, name, name_key, address, district, state, inst_type,
             women_only, minority, source, source_year, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(aicte_id) DO UPDATE SET
            name=excluded.name, name_key=excluded.name_key,
            address=excluded.address, district=excluded.district,
            state=COALESCE(excluded.state, institutions.state),
            inst_type=excluded.inst_type, women_only=excluded.women_only,
            minority=excluded.minority, source=excluded.source,
            source_year=excluded.source_year, updated_at=excluded.updated_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def load_rankings(path: Path, conn: sqlite3.Connection, source: str = "nirf",
                  category: str = "engineering", year: str = "2025") -> dict:
    """Load a ranking table and match it to institutions already held.

    The file needs a rank column and a name column; a city column improves
    matching. NIRF publishes this, and anything in the same shape works.

    Matching is by normalised name, then by a contained-name fallback, because
    a ranking body and a statutory register write the same institution
    differently. Every match is recorded with its aicte_id when one is found
    and left unmatched when one is not, rather than guessed at.
    """
    import pandas as pd

    frame = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    frame.columns = [str(c).strip().lower() for c in frame.columns]

    def column(*names: str) -> str | None:
        for candidate in names:
            if candidate in frame.columns:
                return candidate
        return None

    rank_col = column("rank", "nirf rank", "ranking", "position")
    name_col = column("name", "institute", "institution", "college", "institute name")
    city_col = column("city", "district", "location")
    if not (rank_col and name_col):
        raise ValueError("needs a rank column and a name column")

    known = {row["name_key"]: row["aicte_id"]
             for row in conn.execute("SELECT name_key, aicte_id FROM institutions")}

    rows, matched = [], 0
    for record in frame.to_dict("records"):
        name = str(record[name_col]).strip()
        if not name or name.lower() == "nan":
            continue
        key = name_key(name)
        aicte_id = known.get(key)
        if not aicte_id:
            # A ranking body may write "IIT Bombay" where the register writes
            # the full legal name. Accept a containment match only when exactly
            # one candidate contains the whole ranked name.
            hits = [kid for k, kid in known.items() if key and key in k]
            if len(hits) == 1:
                aicte_id = hits[0]
        if aicte_id:
            matched += 1
        rows.append((source, category, year, int(record[rank_col]), name, key,
                     str(record[city_col]).strip() if city_col else None, aicte_id))

    conn.executemany(
        """INSERT INTO rankings (source, category, year, rank, name, name_key, city, aicte_id)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(source, category, year, rank) DO UPDATE SET
             name=excluded.name, name_key=excluded.name_key,
             city=excluded.city, aicte_id=excluded.aicte_id""",
        rows,
    )
    conn.commit()
    return {"loaded": len(rows), "matched_to_registry": matched,
            "unmatched": len(rows) - matched}


def load_api(conn: sqlite3.Connection, state: str, year: str = CURRENT_YEAR) -> int:
    """Refresh from the live AICTE dashboard backend.

    The endpoint takes method=fetchdata plus year, state, program, level,
    institutiontype, Women, Minority and course, and answers with JSON. It
    requires browser headers (User-Agent, Referer, X-Requested-With). Wire this
    to the portal's existing fetcher so robots, pacing and the Chromium fallback
    all apply, then feed the parsed records through the same upsert as
    load_file. Left unimplemented until the host is reachable.
    """
    raise NotImplementedError("AICTE dashboard host was unreachable at build time")


def query(
    conn: sqlite3.Connection,
    district: str | None = None,
    inst_type: str | None = None,
    limit: int | None = None,
    missing_website: bool = False,
    discipline: str | None = None,
    include_uncertain: bool = True,
    order: str = "name",
) -> list[dict]:
    """Answer a prompt-style filter. Returns seed records for the crawler."""
    clauses, params = [], []
    if district:
        clauses.append("district LIKE ?")
        params.append(f"%{district.strip().upper()}%")
    if inst_type:
        values = TYPE_ALIASES.get(inst_type.lower().strip())
        if values:
            clauses.append(f"inst_type IN ({','.join('?' * len(values))})")
            params.extend(values)
        else:
            clauses.append("inst_type LIKE ?")
            params.append(f"%{inst_type}%")
    if missing_website:
        clauses.append("(website IS NULL OR website = '')")

    # Left join so an unranked institution is still returned, just later.
    sql = ("SELECT i.*, r.rank AS rank, r.source AS rank_source "
           "FROM institutions i "
           "LEFT JOIN rankings r ON r.aicte_id = i.aicte_id")
    if order == "rank":
        clauses = [c.replace("district", "i.district").replace("inst_type", "i.inst_type")
                    .replace("website", "i.website") for c in clauses]
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    if order == "rank":
        # NULLs last: ranked institutions first, then the rest alphabetically.
        sql += " ORDER BY CASE WHEN r.rank IS NULL THEN 1 ELSE 0 END, r.rank, i.name"
    elif order == "ranked-only":
        sql += (" AND r.rank IS NOT NULL" if clauses else " WHERE r.rank IS NOT NULL")
        sql += " ORDER BY r.rank"
    else:
        sql += " ORDER BY i.name"

    keep = {"clear", "mixed", "unknown"} if include_uncertain else {"clear"}
    out = []
    for row in conn.execute(sql, params):
        record = dict(row)
        if discipline:
            grade = classify_discipline(record["name"], discipline)
            if grade not in keep:
                continue
            record["discipline_match"] = grade
        record["faculty_api"] = faculty_url(record["aicte_id"], record["source_year"] or CURRENT_YEAR)
        record["course_api"] = course_url(record["aicte_id"], record["source_year"] or CURRENT_YEAR)
        out.append(record)
        if limit and len(out) >= limit:
            break
    return out


def set_website(conn: sqlite3.Connection, aicte_id: str, url: str, source: str) -> None:
    """Record a website found by another seed source. Provenance is kept."""
    conn.execute(
        "UPDATE institutions SET website=?, website_source=?, updated_at=? WHERE aicte_id=?",
        (url, source, datetime.now(timezone.utc).isoformat(timespec="seconds"), aicte_id),
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="AICTE registry seed source")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    loader = sub.add_parser("load", help="load a master snapshot")
    loader.add_argument("path", type=Path)
    loader.add_argument("--year", default=CURRENT_YEAR)

    finder = sub.add_parser("query", help="filter the registry")
    finder.add_argument("--district")
    finder.add_argument("--type", dest="inst_type")
    finder.add_argument("--limit", type=int)
    finder.add_argument("--missing-website", action="store_true")
    finder.add_argument("--discipline", choices=sorted(DISCIPLINE_WORDS))
    finder.add_argument("--clear-only", action="store_true",
                        help="keep only names that clearly say the discipline")
    finder.add_argument("--order", choices=["name", "rank", "ranked-only"], default="name",
                        help="rank puts ranked institutions first; ranked-only drops the rest")

    ranker = sub.add_parser("load-rankings", help="load a NIRF-style ranking file")
    ranker.add_argument("path", type=Path)
    ranker.add_argument("--source", default="nirf")
    ranker.add_argument("--category", default="engineering")
    ranker.add_argument("--year", default="2025")
    finder.add_argument("--csv", type=Path, help="write results to a CSV instead of printing")

    args = parser.parse_args()
    conn = connect(args.db)

    if args.command == "load":
        count = load_file(args.path, conn, args.year)
        total = conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
        print(f"loaded {count} rows, table now holds {total}")
        return

    if args.command == "load-rankings":
        summary = load_rankings(args.path, conn, args.source, args.category, args.year)
        print(f"loaded {summary['loaded']} ranked rows, "
              f"{summary['matched_to_registry']} matched to the registry, "
              f"{summary['unmatched']} unmatched")
        return

    results = query(
        conn, args.district, args.inst_type, args.limit, args.missing_website,
        discipline=args.discipline, include_uncertain=not args.clear_only,
        order=args.order,
    )
    if args.csv:
        import csv

        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()) if results else [])
            writer.writeheader()
            writer.writerows(results)
        print(f"{len(results)} rows written to {args.csv}")
        return

    print(f"{len(results)} institutions")
    if args.discipline:
        grades = {}
        for record in results:
            grades[record["discipline_match"]] = grades.get(record["discipline_match"], 0) + 1
        print("  match quality: " + ", ".join(f"{k} {v}" for k, v in sorted(grades.items())))
    for record in results[:25]:
        grade = f"  [{record['discipline_match']}]" if args.discipline else ""
        rank = f"  #{record['rank']}" if record.get("rank") else ""
        print(f"  {record['aicte_id']}  {record['name'][:52]}  {record['inst_type']}{rank}{grade}")


if __name__ == "__main__":
    main()
