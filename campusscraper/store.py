"""Local database of runs, people and student offices (SQLite, one file, no server).

Every crawl and batch is a run. People and offices found are saved as they come
in, so the history, the faculty directory, the student-office directory and the
dashboard survive a restart of the app.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .crawl import name_key
from .models import OfficeRecord, PersonRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, state TEXT, started REAL, finished REAL,
    settings TEXT, stats TEXT, xlsx TEXT, upload_name TEXT, upload BLOB, out_path TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS run_sites (run_id TEXT, row INTEGER, data TEXT, PRIMARY KEY (run_id, row));
CREATE INDEX IF NOT EXISTS run_sites_run ON run_sites (run_id);
CREATE TABLE IF NOT EXISTS people (
    run_id TEXT, key TEXT, college TEXT, name TEXT, institution TEXT, city TEXT, department TEXT, designation TEXT,
    is_fac INTEGER, has_email INTEGER, has_phone INTEGER, has_contact INTEGER, has_profile INTEGER, updated REAL,
    data TEXT, PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS people_key ON people (key, updated);
CREATE INDEX IF NOT EXISTS people_run ON people (run_id, is_fac);
CREATE INDEX IF NOT EXISTS people_meta ON people (
    key, updated DESC, run_id, college, name, institution, city, department, designation,
    is_fac, has_email, has_phone, has_contact, has_profile
);
CREATE INDEX IF NOT EXISTS people_fac_college ON people (is_fac, college, department, city);
CREATE TABLE IF NOT EXISTS offices (
    run_id TEXT, key TEXT, college TEXT, office_type TEXT, office_name TEXT, city TEXT,
    has_email INTEGER, has_phone INTEGER, has_figures INTEGER, updated REAL, data TEXT,
    PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS offices_key ON offices (key, updated);
CREATE INDEX IF NOT EXISTS offices_run ON offices (run_id);
CREATE INDEX IF NOT EXISTS offices_meta ON offices (
    key, updated DESC, run_id, college, office_type, office_name, city, has_email, has_phone, has_figures
);
CREATE TABLE IF NOT EXISTS url_cache (
    url TEXT PRIMARY KEY,
    final_url TEXT,
    status INTEGER,
    content_type TEXT,
    html TEXT,
    rendered INTEGER DEFAULT 0,
    fetched_at REAL
);
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    ts REAL,
    level TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS job_logs_run ON job_logs (run_id, id DESC);
"""
CONTACT = ("phone", "email", "contact_phone", "contact_email")
PROFILE_LINKS = ("google_scholar", "orcid", "scopus_id", "researchgate", "vidwan", "linkedin")
OFFICE_TYPES = ("placement", "higher_studies", "student_chapter", "alumni", "international", "student_affairs")

PEOPLE_META_COLS = (
    "d.run_id, d.key, d.college, d.name, d.institution, d.city, d.department, d.designation, "
    "d.is_fac, d.has_email, d.has_phone, d.has_contact, d.has_profile, d.updated"
)
OFFICE_META_COLS = (
    "d.run_id, d.key, d.college, d.office_type, d.office_name, d.city, "
    "d.has_email, d.has_phone, d.has_figures, d.updated"
)


def _site(url: str) -> str:
    return (urlsplit(url or "").hostname or "").removeprefix("www.")


def person_key(d: PersonRecord) -> str:
    """Same person across runs: name + college list name (or website)."""
    where = d.source_institution or _site(d.profile_url or d.source_url)
    return f"{name_key(d.get('name') or '')}|{where.lower()}"


def office_key(o: OfficeRecord) -> str:
    where = o.source_institution or _site(o.page_url)
    return f"{o.office_type}|{(o.office_name or '').strip().lower()}|{where.lower()}"


def _college(rec_college: str, rec_inst: str) -> str:
    return rec_college or rec_inst or ""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=60.0)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.execute("PRAGMA busy_timeout=60000")
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA temp_store=MEMORY")
            self._db.execute("PRAGMA cache_size=-64000")
        except sqlite3.OperationalError:
            pass
        with self._lock:
            self._db.executescript(SCHEMA)
            # Runs that were going when the app closed cannot still be going.
            self._db.execute("UPDATE runs SET state='interrupted' WHERE state IN ('running','paused')")
            self._db.commit()

    # -- runs ------------------------------------------------------------------
    def create_run(self, run_id: str, kind: str, title: str, settings: dict, upload_name: str | None = None,
                   upload: bytes | None = None, out_path: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO runs (id, kind, title, state, started, settings, stats, upload_name, upload, out_path)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, kind, title, "running", time.time(), json.dumps(settings), "{}", upload_name, upload, out_path))
            self._db.commit()

    def update_run(self, run_id: str, **fields) -> None:
        if not fields:
            return
        if "stats" in fields and not isinstance(fields["stats"], str):
            fields["stats"] = json.dumps(fields["stats"])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
            self._db.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_dict(r) if r else None

    def list_runs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
            fac = dict(self._db.execute("SELECT run_id, SUM(is_fac) FROM people GROUP BY run_id").fetchall())
            offs = dict(self._db.execute("SELECT run_id, COUNT(*) FROM offices GROUP BY run_id").fetchall())
        out = []
        for r in rows:
            d = self._run_dict(r)
            d["faculty_saved"] = int(fac.get(r["id"]) or 0)
            d["offices_saved"] = int(offs.get(r["id"]) or 0)
            out.append(d)
        return out

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            for table, col in (
                ("people", "run_id"),
                ("offices", "run_id"),
                ("run_sites", "run_id"),
                ("job_logs", "run_id"),
                ("runs", "id"),
            ):
                self._db.execute(f"DELETE FROM {table} WHERE {col}=?", (run_id,))
            self._db.commit()

    @staticmethod
    def _run_dict(r: sqlite3.Row) -> dict:
        d = {k: r[k] for k in r.keys() if k != "upload"}
        d["settings"] = json.loads(d["settings"] or "{}")
        d["stats"] = json.loads(d["stats"] or "{}")
        d["has_upload"] = r["upload"] is not None
        return d

    def upload_of(self, run_id: str) -> tuple[str | None, bytes | None]:
        with self._lock:
            r = self._db.execute("SELECT upload_name, upload FROM runs WHERE id=?", (run_id,)).fetchone()
        return (r["upload_name"], r["upload"]) if r else (None, None)

    # -- job logs & url cache --------------------------------------------------
    def append_job_log(self, run_id: str, message: str, level: str = "info") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO job_logs (run_id, ts, level, message) VALUES (?,?,?,?)",
                (run_id, time.time(), level, message),
            )
            self._db.commit()

    def get_job_logs(self, run_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, level, message FROM job_logs WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def cache_url(self, url: str, final_url: str, status: int, content_type: str, html: str, rendered: bool = False) -> None:
        if status != 200 or not html or len(html) > 1_500_000:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO url_cache (url, final_url, status, content_type, html, rendered, fetched_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (url, final_url, status, content_type, html, int(rendered), time.time()),
            )
            self._db.commit()

    def get_cached_url(self, url: str, max_age_seconds: float = 86400 * 7) -> dict | None:
        with self._lock:
            r = self._db.execute(
                "SELECT url, final_url, status, content_type, html, rendered, fetched_at FROM url_cache WHERE url=?",
                (url,),
            ).fetchone()
        if not r:
            return None
        if (time.time() - (r["fetched_at"] or 0)) > max_age_seconds:
            return None
        return dict(r)

    # -- per-college summary rows of a batch --------------------------------------
    def save_site(self, run_id: str, site: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO run_sites VALUES (?,?,?)",
                             (run_id, int(site.get("row") or 0), json.dumps(site)))
            self._db.commit()

    def sites(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM run_sites WHERE run_id=? ORDER BY row", (run_id,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def all_sites(self, limit: int = 2000) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT rs.run_id, rs.row, rs.data, r.title, r.started FROM run_sites rs "
                "LEFT JOIN runs r ON rs.run_id = r.id ORDER BY r.started DESC, rs.row ASC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d["run_id"] = r["run_id"]
            d["run_title"] = r["title"]
            d["run_started"] = r["started"]
            out.append(d)
        return out

    # -- people ------------------------------------------------------------------
    def save_people(self, run_id: str, records: list[PersonRecord], default_college: str = "") -> int:
        """default_college names the site for a single-website crawl, so all its people group together."""
        now = time.time()
        rows = []
        for d in records:
            if not d.get("name"):
                continue
            if not d.source_institution and default_college:
                d = PersonRecord.from_full_dict(d.to_full_dict())
                d.source_institution = default_college
            rows.append((run_id, person_key(d), _college(d.source_institution, d.get("institution") or ""),
                         d.get("name"), d.get("institution") or d.source_institution, d.get("city"),
                         d.get("department"), d.get("designation"), int(d.is_relevant),
                         int(bool(d.get("email"))), int(bool(d.get("phone"))),
                         int(any(d.get(f) for f in CONTACT)), int(any(d.get(f) for f in PROFILE_LINKS)), now,
                         json.dumps(d.to_full_dict(), ensure_ascii=False)))
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO people VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
        return len(rows)

    def replace_run_people(self, run_id: str, records: list[PersonRecord]) -> int:
        """Atomically replace one run's rows after a verified remap/dedup pass."""
        now = time.time()
        rows = []
        for d in records:
            if not d.get("name"):
                continue
            rows.append((run_id, person_key(d), _college(d.source_institution, d.get("institution") or ""),
                         d.get("name"), d.get("institution") or d.source_institution, d.get("city"),
                         d.get("department"), d.get("designation"), int(d.is_relevant),
                         int(bool(d.get("email"))), int(bool(d.get("phone"))),
                         int(any(d.get(f) for f in CONTACT)), int(any(d.get(f) for f in PROFILE_LINKS)), now,
                         json.dumps(d.to_full_dict(), ensure_ascii=False)))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("DELETE FROM people WHERE run_id=?", (run_id,))
                self._db.executemany("INSERT INTO people VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        return len(rows)

    def save_offices(self, run_id: str, records: list[OfficeRecord], default_college: str = "") -> int:
        now = time.time()
        rows = []
        for o in records:
            if not o.source_institution and default_college:
                o = OfficeRecord.from_full_dict(o.to_full_dict())
                o.source_institution = default_college
            rows.append((run_id, office_key(o), _college(o.source_institution, o.institution), o.office_type,
                         o.office_name, o.city, int(bool(o.emails)), int(bool(o.phones)),
                         int(bool(o.higher_studies_lines)), now, json.dumps(o.to_full_dict(), ensure_ascii=False)))
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO offices VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
        return len(rows)

    def run_people(self, run_id: str) -> list[PersonRecord]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM people WHERE run_id=?", (run_id,)).fetchall()
        return [PersonRecord.from_full_dict(json.loads(r["data"])) for r in rows]

    def run_offices(self, run_id: str) -> list[OfficeRecord]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM offices WHERE run_id=?", (run_id,)).fetchall()
        return [OfficeRecord.from_full_dict(json.loads(r["data"])) for r in rows]

    @staticmethod
    def _latest(table: str, where: str) -> str:
        # The newest copy of each person or office across all runs (includes `data` column).
        return (f"SELECT d.* FROM {table} d JOIN (SELECT key, MAX(updated) AS u FROM {table} GROUP BY key) m "
                f"ON d.key = m.key AND d.updated = m.u WHERE {where}")

    @staticmethod
    def _latest_meta(table: str, where: str) -> str:
        # Covering-index metadata query that avoids reading the multi-hundred-MB `data` TEXT column!
        cols = PEOPLE_META_COLS if table == "people" else OFFICE_META_COLS
        return (f"SELECT {cols} FROM {table} d JOIN (SELECT key, MAX(updated) AS u FROM {table} GROUP BY key) m "
                f"ON d.key = m.key AND d.updated = m.u WHERE {where}")

    def directory(self, q: str = "", city: str = "", college: str = "", department: str = "",
                  has_email: bool = False, has_phone: bool = False, has_contact: bool = False,
                  has_profile: bool = False, faculty_only: bool = True, limit: int = 100,
                  offset: int = 0) -> tuple[int, list[dict]]:
        where, args = ["1=1"], []
        if faculty_only:
            where.append("d.is_fac=1")
        for col, val in (("d.city", city), ("d.college", college), ("d.department", department)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if q:
            where.append("LOWER(d.data) LIKE ?")
            args.append(f"%{q.lower()}%")
        for flag, col in ((has_email, "has_email"), (has_phone, "has_phone"), (has_contact, "has_contact"),
                          (has_profile, "has_profile")):
            if flag:
                where.append(f"d.{col}=1")
        where_clause = " AND ".join(where)
        with self._lock:
            if not q:
                # Fast path: use metadata covering index for COUNT and key selection, then fetch `data` only for the page!
                meta_sql = self._latest_meta("people", where_clause)
                total = self._db.execute(f"SELECT COUNT(*) FROM ({meta_sql})", args).fetchone()[0]
                if limit <= 5000:
                    page_keys = self._db.execute(
                        meta_sql + " ORDER BY d.college, d.department, d.name LIMIT ? OFFSET ?",
                        (*args, limit, offset),
                    ).fetchall()
                    if not page_keys:
                        return total, []
                    out = []
                    for pk in page_keys:
                        r = self._db.execute(
                            "SELECT data FROM people WHERE run_id=? AND key=?",
                            (pk["run_id"], pk["key"]),
                        ).fetchone()
                        if r:
                            out.append(json.loads(r["data"]) | {"run_id": pk["run_id"]})
                    return total, out
            sql = self._latest("people", where_clause)
            total = self._db.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
            rows = self._db.execute(sql + " ORDER BY d.college, d.department, d.name LIMIT ? OFFSET ?",
                                    (*args, limit, offset)).fetchall()
        return total, [json.loads(r["data"]) | {"run_id": r["run_id"]} for r in rows]

    def directory_records(self, **filters) -> list[PersonRecord]:
        _, rows = self.directory(**filters, limit=1_000_000, offset=0)
        return [PersonRecord.from_full_dict(r) for r in rows]

    def office_directory(self, q: str = "", college: str = "", office_type: str = "", has_email: bool = False,
                         has_phone: bool = False, has_figures: bool = False) -> list[dict]:
        where, args = ["1=1"], []
        for col, val in (("d.college", college), ("d.office_type", office_type)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        if q:
            where.append("LOWER(d.data) LIKE ?")
            args.append(f"%{q.lower()}%")
        for flag, col in ((has_email, "has_email"), (has_phone, "has_phone"), (has_figures, "has_figures")):
            if flag:
                where.append(f"d.{col}=1")
        sql = self._latest("offices", " AND ".join(where))
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY d.college, d.office_type, d.office_name", args).fetchall()
        return [json.loads(r["data"]) | {"run_id": r["run_id"], "college": r["college"]} for r in rows]

    def office_records(self, **filters) -> list[OfficeRecord]:
        out = []
        for r in self.office_directory(**filters):
            o = OfficeRecord.from_full_dict(r)
            o.source_institution = o.source_institution or r.get("college") or ""
            out.append(o)
        return out

    def facets(self) -> dict:
        people = self._latest_meta("people", "d.is_fac=1")
        offs = self._latest_meta("offices", "1=1")
        with self._lock:
            def distinct(sql, col):
                return [r[0] for r in self._db.execute(
                    f"SELECT DISTINCT {col} FROM ({sql}) WHERE {col} IS NOT NULL AND {col}<>'' ORDER BY {col}")]
            cities = distinct(people, "city")
            depts = distinct(people, "department")
            colleges = sorted(set(distinct(people, "college")) | set(distinct(offs, "college")))
        return {"cities": cities, "colleges": colleges, "departments": depts, "office_types": list(OFFICE_TYPES)}

    def dashboard(self) -> dict:
        people = self._latest_meta("people", "1=1")
        offs = self._latest_meta("offices", "1=1")
        with self._lock:
            t = self._db.execute(
                f"SELECT COUNT(*) everyone, SUM(is_fac) fac, SUM(is_fac*has_email) email, SUM(is_fac*has_phone) phone,"
                f" SUM(is_fac*has_contact) contact, SUM(is_fac*has_profile) profile,"
                f" COUNT(DISTINCT CASE WHEN is_fac=1 THEN college END) colleges,"
                f" COUNT(DISTINCT CASE WHEN is_fac=1 THEN department END) depts FROM ({people})").fetchone()
            o = self._db.execute(
                f"SELECT COUNT(*) n, SUM(has_email) email, SUM(has_figures) figures,"
                f" SUM(office_type='placement') placement, SUM(office_type='higher_studies') hs FROM ({offs})").fetchone()
            per = self._db.execute(
                f"SELECT COALESCE(NULLIF(college,''), '(unknown)') c, SUM(is_fac) fac, SUM(is_fac*has_email) email,"
                f" SUM(is_fac*has_phone) phone, SUM(is_fac*has_contact) contact, SUM(is_fac*has_profile) profile,"
                f" MAX(updated) last FROM ({people}) GROUP BY c HAVING fac > 0").fetchall()
            per_off = dict(self._db.execute(
                f"SELECT COALESCE(NULLIF(college,''), '(unknown)') c, COUNT(*) FROM ({offs}) GROUP BY c").fetchall())
            per_types = {}
            for r in self._db.execute(f"SELECT COALESCE(NULLIF(college,''), '(unknown)') c, office_type FROM ({offs})"):
                per_types.setdefault(r[0], set()).add(r[1])
            runs = self._db.execute("SELECT COUNT(*), SUM(state IN ('running','paused')) FROM runs").fetchone()
        colleges = {r["c"]: dict(r) | {"offices": per_off.get(r["c"], 0)} for r in per}
        for c, n in per_off.items():  # a college with offices but no faculty found still shows
            colleges.setdefault(c, {"c": c, "fac": 0, "email": 0, "phone": 0, "contact": 0, "profile": 0,
                                    "last": None, "offices": n})
        for c, row in colleges.items():
            row["office_types"] = sorted(per_types.get(c, set()))
        return {
            "totals": {"runs": runs[0] or 0, "active_runs": runs[1] or 0, "colleges": len(colleges),
                       "departments": t["depts"] or 0, "people": t["everyone"] or 0, "faculty": t["fac"] or 0,
                       "with_email": t["email"] or 0, "with_phone": t["phone"] or 0,
                       "with_contact": t["contact"] or 0, "with_profile": t["profile"] or 0,
                       "offices": o["n"] or 0, "offices_with_email": o["email"] or 0,
                       "placement_cells": o["placement"] or 0, "higher_studies_cells": o["hs"] or 0,
                       "offices_with_figures": o["figures"] or 0},
            "colleges": sorted(colleges.values(), key=lambda r: (-(r["fac"] or 0), r["c"])),
        }


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "run"
