"""Excel / CSV / JSON output and background export job manager."""
from __future__ import annotations

import csv
import io
import json
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .crawl import CrawlResult
from .models import ALL_FIELDS, LIST_FIELDS, OfficeRecord, PersonRecord

COLUMNS = ["name", "designation", "department", "specialty", "institution", "city",
           "email", "phone", "contact_email", "contact_phone",
           "qualifications", "experience_years", "research_areas", "subjects",
           "google_scholar", "orcid", "scopus_id", "researchgate", "linkedin", "vidwan", "homepage",
           "education", "awards", "memberships", "about", "photo_url"]
HEADERS = {"phone": "Faculty Phone", "email": "Faculty Email", "contact_phone": "Department / Office Phone",
           "contact_email": "Department / Office Email", "specialty": "Specialization", "orcid": "ORCID",
           "scopus_id": "Scopus ID", "researchgate": "ResearchGate", "linkedin": "LinkedIn", "vidwan": "Vidwan / IRINS",
           "google_scholar": "Google Scholar", "homepage": "Homepage", "research_areas": "Research Areas"}
assert set(COLUMNS) == set(ALL_FIELDS)
META_COLUMNS = ["source_institution", "profile_url", "source_url", "found_on", "is_relevant", "relevance_score",
                "relevance_terms", "page_type", "fields_filled"]
META_HEADERS = {"source_institution": "Source College", "is_relevant": "In Faculty Sheet", "relevance_score": "Score",
                "relevance_terms": "Why"}
OFFICE_COLUMNS = ["office_type", "office_name", "institution", "city", "emails", "phones", "coordinators",
                  "higher_studies_lines", "page_url", "source_institution", "skipped_student_lines"]
OFFICE_HEADERS = {"office_type": "Office Type", "office_name": "Office / Body", "emails": "Office Emails",
                  "phones": "Office Phones", "coordinators": "Faculty / Staff Coordinators",
                  "higher_studies_lines": "Published Higher-Studies Figures", "page_url": "Page URL",
                  "source_institution": "Source College", "skipped_student_lines": "Student Lines Left Out"}

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="164E63")
_HEADER_ALIGN = Alignment(vertical="center")


def office_row(o) -> list:
    d = o.to_dict() if hasattr(o, "to_dict") else o
    return [_cell(d.get(c)) if c != "skipped_student_lines" else d.get(c, 0) for c in OFFICE_COLUMNS]


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value)
    return str(value)[:32000]


def _csv_safe(value):
    value = str(value)
    if value[:1] in ("=", "+", "-", "@"):
        value = "'" + value  # never let page text become a spreadsheet formula in CSV
    return value


def row(rec: PersonRecord | dict) -> list:
    if isinstance(rec, PersonRecord):
        d = rec.values
        filled = sum(1 for f in ALL_FIELDS if d.get(f) not in (None, [], ""))
        return [_cell(d.get(c)) for c in COLUMNS] + [
            _cell(rec.source_institution),
            _cell(rec.profile_url),
            _cell(rec.source_url),
            _cell(rec.found_on),
            "YES" if rec.is_relevant else "no",
            round(rec.relevance_score, 2),
            _cell(rec.relevance_terms),
            rec.page_type,
            filled,
        ]
    vals = rec.get("values") or rec
    filled = sum(1 for f in ALL_FIELDS if vals.get(f, rec.get(f)) not in (None, [], ""))
    is_rel = bool(rec.get("is_relevant", True))
    return [_cell(vals.get(c, rec.get(c))) for c in COLUMNS] + [
        _cell(rec.get("source_institution")),
        _cell(rec.get("profile_url")),
        _cell(rec.get("source_url")),
        _cell(rec.get("found_on")),
        "YES" if is_rel else "no",
        round(float(rec.get("relevance_score") or 0.0), 2),
        _cell(rec.get("relevance_terms")),
        rec.get("page_type") or "listing",
        filled,
    ]


def _sheet(wb, title, headers, rows, widths=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for r in rows:
        ws.append(r)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # page text stays text, never a formula; "+91 ..." shows as is
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(ws.columns):
        letter = col[0].column_letter
        width = (widths or {}).get(headers[i]) or min(60, max(10, max(len(str(c.value or "")) for c in col[:200]) + 2))
        ws.column_dimensions[letter].width = width
    return ws


def _stream_sheet(wb, title, headers, rows, widths=None, on_row: Callable[[int], None] | None = None):
    """Write one formatted sheet at a time using fast row streaming without per-cell WriteOnlyCell allocation."""
    ws = wb.create_sheet(title)
    ws.freeze_panes = "B2"
    iterator = iter(rows)
    sample = []
    for _ in range(199):  # match the legacy width calculation (header plus first 199 rows)
        try:
            sample.append(next(iterator))
        except StopIteration:
            break
    header_cells = []
    for value in headers:
        cell = WriteOnlyCell(ws, value=value)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN
        header_cells.append(cell)
    ws.append(header_cells)
    count = 1

    def append_row(values):
        nonlocal count
        # Fast path: only construct WriteOnlyCell if any string in the row starts with '='
        has_formula = False
        for v in values:
            if isinstance(v, str) and v.startswith("="):
                has_formula = True
                break
        if has_formula:
            cells = []
            for value in values:
                if isinstance(value, str) and value.startswith("="):
                    cell = WriteOnlyCell(ws, value=value)
                    cell.data_type = "s"
                    cells.append(cell)
                else:
                    cells.append(value)
            ws.append(cells)
        else:
            ws.append(values)
        count += 1
        if on_row and (count % 500 == 0):
            on_row(count)

    for values in sample:
        append_row(values)
    for values in iterator:
        append_row(values)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{count}"
    for i, header in enumerate(headers):
        letter = get_column_letter(i + 1)
        width = (widths or {}).get(header)
        if width is None:
            observed = [header] + [str(values[i] or "") for values in sample if i < len(values)]
            width = min(60, max(10, max(map(len, observed)) + 2))
        ws.column_dimensions[letter].width = width
    return ws


def to_xlsx(
    result: CrawlResult,
    path: str | Path,
    sites: list[dict] | None = None,
    progress_cb: Callable[[str, int], None] | None = None,
) -> Path:
    """Stream a complete workbook so large batches are exported rapidly without cell-memory blowups."""
    path = Path(path)
    wb = Workbook(write_only=True)
    headers = [HEADERS.get(c) or META_HEADERS.get(c) or c.replace("_", " ").title() for c in COLUMNS + META_COLUMNS]
    widths = {"About": 60, "Research Areas": 45, "Profile Url": 45, "Source Url": 45, "Found On": 40}
    onco = sorted(result.faculty, key=lambda d: (d.get("institution") or "", d.get("department") or "", d.get("name") or ""))
    all_people = sorted(result.doctors, key=lambda d: d.get("name") or "")
    offices = sorted(getattr(result, "offices", []) or [], key=lambda o: (o.source_institution or o.institution, o.office_type))

    if progress_cb:
        progress_cb("Summary", 5)
    summary = [["Start URL(s)", ", ".join(result.start_urls)], ["Exported at", datetime.now().strftime("%Y-%m-%d %H:%M")]]
    summary += [[k.replace("_", " "), json.dumps(v) if isinstance(v, dict) else v] for k, v in result.stats.items()]
    fill = {f: sum(1 for d in onco if d.get(f)) for f in COLUMNS}
    summary.append(["", ""])
    summary.append(["Field coverage (faculty sheet)", f"{len(onco)} records"])
    summary += [[f, f"{n} ({(100 * n // len(onco)) if onco else 0}%)"] for f, n in fill.items()]
    _stream_sheet(wb, "Summary", ["Item", "Value"], summary, {"Item": 32, "Value": 80})

    if sites is not None:
        if progress_cb:
            progress_cb("Colleges", 15)
        keys = ["row", "institution", "status", "pages", "people", "faculty", "offices", "with_phone", "with_email",
                "with_any_contact", "start_urls", "note"]
        _stream_sheet(wb, "Colleges", [k.replace("_", " ").title() for k in keys],
                      ([_cell(s.get(k)) for k in keys] for s in sites), {"Start Urls": 70, "Note": 50})
    if progress_cb:
        progress_cb("Faculty", 30)
    _stream_sheet(wb, "Faculty", headers, (row(d) for d in onco), widths)
    if progress_cb:
        progress_cb("All people", 55)
    _stream_sheet(wb, "All people", headers, (row(d) for d in all_people), widths)
    if progress_cb:
        progress_cb("Student offices", 75)
    _stream_sheet(wb, "Student offices", [OFFICE_HEADERS.get(c) or c.replace("_", " ").title() for c in OFFICE_COLUMNS],
                  (office_row(o) for o in offices),
                  {"Office / Body": 36, "Faculty / Staff Coordinators": 50, "Published Higher-Studies Figures": 60, "Page URL": 50})

    def evidence_rows():
        for d in result.doctors:
            grouped: dict[str, dict] = {}
            for e in d.evidence:
                g = grouped.setdefault(e.field, {"values": [], "layers": [], "snippet": e.snippet, "urls": []})
                for bucket, item in (("values", _cell(e.value)), ("layers", e.layer), ("urls", e.source_url)):
                    if item and item not in g[bucket]:
                        g[bucket].append(item)
            for field_name, g in grouped.items():
                yield [d.get("name"), field_name, "; ".join(map(str, g["values"])), ", ".join(g["layers"]),
                       _cell(g["snippet"]), " ".join(g["urls"])]
        for o in offices:
            for e in o.evidence:
                yield [o.office_name, e.field, _cell(e.value), e.layer, _cell(e.snippet), e.source_url]

    if progress_cb:
        progress_cb("Evidence", 85)
    _stream_sheet(wb, "Evidence", ["Person / Office", "Field", "Value", "Found by", "Page text", "Page URL"],
                  evidence_rows(), {"Page text": 70, "Value": 40, "Page URL": 45})
    if progress_cb:
        progress_cb("Pages", 95)
    _stream_sheet(wb, "Pages", ["URL", "HTTP status", "Page type", "People on page", "Depth", "Note"],
                  ([p.url, p.status, p.page_type, p.doctors, p.depth, p.note] for p in result.pages), {"URL": 70})
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    if progress_cb:
        progress_cb("Done", 100)
    return path


def validate_xlsx(path: str | Path) -> dict:
    """Verify exported Excel file integrity, sheet names, and column headers."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return {"valid": False, "ok": False, "error": "File does not exist or is empty"}
    if not zipfile.is_zipfile(p):
        return {"valid": False, "ok": False, "error": "Not a valid ZIP/XLSX archive"}
    try:
        wb = load_workbook(p, read_only=True)
        sheets = list(wb.sheetnames)
        required = {"Summary", "Faculty", "All people", "Student offices", "Evidence", "Pages"}
        missing = required - set(sheets)
        expected_headers = [HEADERS.get(c) or META_HEADERS.get(c) or c.replace("_", " ").title() for c in COLUMNS + META_COLUMNS]
        fac_ws = wb["Faculty"]
        rows_iter = fac_ws.iter_rows(values_only=True)
        first_row = list(next(rows_iter, ()))
        fac_count = sum(1 for _ in rows_iter)
        wb.close()
        if missing:
            return {"valid": False, "ok": False, "error": f"Missing sheets: {sorted(missing)}", "sheets": sheets}
        if first_row != expected_headers:
            return {"valid": False, "ok": False, "error": "Faculty header mismatch", "sheets": sheets}
        return {
            "valid": True,
            "ok": True,
            "faculty_rows": fac_count,
            "size_bytes": p.stat().st_size,
            "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
            "sheets": sheets,
            "columns": len(expected_headers),
        }
    except Exception as exc:
        return {"valid": False, "ok": False, "error": str(exc)}


def to_csv(doctors: list[PersonRecord]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS + META_COLUMNS)
    for d in doctors:
        w.writerow([_csv_safe(v) for v in row(d)])
    return buf.getvalue()


def to_json(result: CrawlResult, relevant_only: bool = True) -> str:
    docs = result.faculty if relevant_only else result.doctors
    return json.dumps({"start_urls": result.start_urls, "stats": result.stats,
                       "people": [d.to_dict() for d in docs],
                       "offices": [o.to_dict() for o in getattr(result, "offices", [])]}, ensure_ascii=False, indent=2)


def write(result: CrawlResult, out: str, relevant_only: bool = True) -> Path:
    p = Path(out)
    suffix = p.suffix.lower()
    if suffix == ".xlsx":
        return to_xlsx(result, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        p.write_text(to_csv(result.faculty if relevant_only else result.doctors), encoding="utf-8-sig")
    elif suffix == ".json":
        p.write_text(to_json(result, relevant_only), encoding="utf-8")
    else:
        raise ValueError("output must end in .xlsx, .csv or .json")
    return p


class ExportManager:
    """Manages asynchronous Excel generation jobs with deduplication, progress tracking, and validation."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self._active_signatures: dict[str, str] = {}

    def start_export(
        self,
        signature: str,
        title: str,
        loader: Callable[[], tuple[list[PersonRecord], list[OfficeRecord], list[dict] | None]],
        filename_prefix: str = "campus-export",
    ) -> dict:
        with self._lock:
            existing_id = self._active_signatures.get(signature)
            if existing_id and existing_id in self.jobs:
                job = self.jobs[existing_id]
                if job["state"] in ("queued", "running"):
                    return job
            job_id = uuid.uuid4().hex[:10]
            job = {
                "id": job_id,
                "signature": signature,
                "title": title,
                "state": "running",
                "stage": "Loading records from database",
                "progress": 2,
                "started": time.time(),
                "finished": None,
                "elapsed_ms": 0,
                "records_count": 0,
                "faculty_count": 0,
                "offices_count": 0,
                "path": None,
                "filename": None,
                "validation": None,
                "error": None,
            }
            self.jobs[job_id] = job
            self._active_signatures[signature] = job_id

        def _worker():
            t0 = time.perf_counter()
            try:
                docs, offices, sites = loader()
                res = CrawlResult(["campusscraper://export"], docs, [], offices=offices)
                fac = res.faculty
                res.stats = {
                    "people_found": len(docs),
                    "faculty": len(fac),
                    "student_offices": len(offices),
                    "faculty_with_own_email": sum(1 for d in fac if d.get("email")),
                    "faculty_with_own_phone": sum(1 for d in fac if d.get("phone")),
                }
                job["records_count"] = len(docs)
                job["faculty_count"] = len(fac)
                job["offices_count"] = len(offices)
                self.out_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{filename_prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{job_id[:6]}.xlsx"
                out_path = self.out_dir / fname

                def _on_prog(stage_name: str, pct: int):
                    job["stage"] = f"Writing {stage_name} sheet"
                    job["progress"] = pct
                    job["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)

                to_xlsx(res, out_path, sites=sites, progress_cb=_on_prog)
                job["stage"] = "Validating workbook structure"
                val = validate_xlsx(out_path)
                job["validation"] = val
                job["path"] = str(out_path)
                job["filename"] = fname
                job["progress"] = 100
                job["state"] = "done" if val.get("valid") else "error"
                if not val.get("valid"):
                    job["error"] = val.get("error")
                job["stage"] = "Completed"
            except Exception as exc:
                job["state"] = "error"
                job["error"] = f"{type(exc).__name__}: {exc}"
                job["stage"] = "Failed"
            finally:
                job["finished"] = time.time()
                job["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                with self._lock:
                    if self._active_signatures.get(signature) == job_id:
                        self._active_signatures.pop(signature, None)

        threading.Thread(target=_worker, daemon=True, name=f"export-{job_id}").start()
        return job

    def list_jobs(self) -> list[dict]:
        with self._lock:
            items = list(self.jobs.values())
        return sorted(items, key=lambda j: -(j.get("started") or 0))[:50]

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            return self.jobs.get(job_id)


__all__ = ["to_xlsx", "to_csv", "to_json", "write", "validate_xlsx", "ExportManager", "COLUMNS", "LIST_FIELDS"]
