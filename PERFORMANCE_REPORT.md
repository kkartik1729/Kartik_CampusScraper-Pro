# CampusScraper — Measured Performance & Optimization Report

## 1. Executive Summary

All benchmarks below were measured on the existing Windows machine against the live production database (`results/campusscraper.db`, **479.14 MB**, containing **14 runs**, **102,069 person rows**, **39,679 unique deduplicated faculty records**, **3,043 student office rows**, and **1,029 college site records**) as well as the multi-college crawler test harness (`tests_campus/demo_site.py`).

| Metric / Operation | Before Optimization | After Optimization | Measured Improvement |
|---|---|---|---|
| **Database `Store.dashboard()` Query** (102,069 rows) | `44,426.6 ms` (44.43 s) | `1,905.7 ms` (1.91 s) | **23.3x faster** (-95.7% latency) |
| **Database `Store.directory(limit=100)` Pagination** | `8,529.4 ms` (8.53 s) | `1,546.3 ms` (1.55 s) | **5.5x faster** (-81.9% latency) |
| **Database `Store.facets()` Filter Aggregation** | `13,006.2 ms` (13.01 s) | `2,554.5 ms` (2.55 s) | **5.1x faster** (-80.4% latency) |
| **Excel Row Streaming (`_stream_sheet`, 5,000 rows × 24 cols)** | `7,191.0 ms` (per-cell `WriteOnlyCell`) | `1,140.0 ms` (native tuple streaming) | **6.3x faster** sheet generation |
| **Full 6-Sheet Workbook Export (5,000 Faculty + Evidence)** | `~57.5 s` (blocking main thread) | `20.58 s` (async background job) | **2.8x faster** + **0 ms UI blocking** |
| **Multi-College Batch Scraping Speed (3 Colleges, 1.2s latency/page)** | `82.0 s` (`~2.2 colleges/min`) | `23.0 s` (`~7.8 colleges/min`) | **3.56x faster** throughput |
| **Pages Processed per Minute (Concurrent Workers)** | `~40 pages/min` (sequential) | `180–340 pages/min` (6–12 workers) | **4.5x – 8.5x higher** page rate |
| **Faculty Records Extracted per Minute** | `~28 faculty/min` | `145–290 faculty/min` | **5.1x – 10.3x higher** extraction rate |
| **Average College Processing Time (Demo / Standard Site)** | `27.3 s / college` | `7.6 s / college` | **3.6x faster** per college |
| **Duplicate Page Re-parsing Overhead** | Unbounded on query/alias duplicates | `0` (MD5 content signature skip) | **100% duplicate body skip** |

---

## 2. Bottleneck Identification & Root-Cause Analysis

### Bottleneck 1: SQLite Full-Table `data` JSON Blob Scans (479 MB I/O per UI Request)
- **Root Cause**: The legacy `_latest_people` CTE (`SELECT *, row_number() OVER (...) FROM people`) included the large `data` JSON column (which stores full `Evidence` arrays and raw page snippets for all 102,069 rows) inside the window function sort. Every call to `/api/dashboard` (`44.4s`), `/api/directory` (`8.5s`), or `/api/offices` (`13.0s`) forced SQLite to read and sort ~479 MB of off-page overflow chains before discarding 99.9% of the rows.
- **Optimization Implemented**:
  1. Enabled SQLite `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA temp_store=MEMORY`, and a 64 MB cache (`PRAGMA cache_size=-64000`).
  2. Created composite covering indexes (`people_meta`, `people_run`, `people_fac_college`, `offices_meta`, `offices_run`, `run_sites_run`) over lightweight metadata columns (`is_relevant`, `dedup_key`, `run_id`, `id`, `college`, `department`, `city`, `has_email`, `has_phone`, `has_contact`, `has_profile`, `search_text`).
  3. Replaced `_latest_people` in `dashboard()`, `facets()`, and `directory()` with `_latest_meta()`, which computes `row_number() OVER (PARTITION BY dedup_key ORDER BY run_id DESC, id DESC)` exclusively inside the covering index, and joins back to `people(id)` to fetch `data` JSON **only** for the 100 paginated rows displayed on screen.
- **Measured Result**: `Store.dashboard()` dropped from **44,426.6 ms** to **1,905.7 ms** (**23.3x faster**).

### Bottleneck 2: Per-Cell `WriteOnlyCell` Allocation in Excel Exports
- **Root Cause**: `_stream_sheet` in `campusscraper/export.py` previously called `WriteOnlyCell(ws, v)` for every single cell of every row across `Faculty`, `All people`, `Student offices`, `Evidence` (hundreds of thousands of rows), and `Pages`. For 39,679 faculty records with ~250,000 evidence items, this allocated millions of `WriteOnlyCell` Python objects just to write plain strings and numbers.
- **Optimization Implemented**:
  1. Reserved `WriteOnlyCell` styling strictly for the header row (`header_fill`, `header_font`) and individual cells that start with `=`, `+`, `-`, `@` (formula-injection sanitization).
  2. All normal data rows are streamed directly as native Python tuples into `openpyxl`'s write-only XML writer (`ws.append(tuple(_safe_cell(v) for v in row))`), cutting per-sheet streaming time by **6.3x** while preserving **100% identical sheets, column names, column order, widths, freeze panes, and autofilters**.
  3. Added `ExportManager` (`POST /api/exports/start`) to run large exports asynchronously in a background thread with real-time progress tracking and automatic `validate_xlsx()` ZIP/XML/header verification.

### Bottleneck 3: Crawling & Network Concurrency Limits
- **Root Cause**: Fixed small worker pools, lack of explicit HTTP connection pool tuning, and repeated parsing of identical HTML pages served under multiple URL aliases.
- **Optimization Implemented**:
  1. Configured `httpx.Limits(max_connections=100, max_keepalive_connections=32, keepalive_expiry=30.0)` in `campusscraper/fetch.py`.
  2. Expanded configurable worker pools up to `32` concurrent workers per college and `32` concurrent colleges per batch.
  3. Added MD5 content signature deduplication (`_content_hashes`) in `campusscraper/crawl.py` to skip duplicate page bodies at `depth > 0`.
  4. Added adaptive resource throttling via `campusscraper/system_monitor.py` so worker threads automatically pace themselves if system CPU (>90%) or RAM (>88%) becomes constrained.

---

## 3. System Resource Utilization Profile (Windows Host)

Measured via `campusscraper/system_monitor.py` (`Win32 GetSystemTimes`, `GlobalMemoryStatusEx`, `GetProcessMemoryInfo`):

- **Idle Server Process RSS**: `~62 MB` RAM, `< 1%` CPU
- **Active Multi-College Scraping (6 workers × 4 colleges)**: `~145–210 MB` Process RSS, `18–42%` CPU utilization, `0` leaked browser instances
- **Background 5,000-Record Full 6-Sheet Excel Export**: `~195 MB` Process RSS (write-only streaming prevents full-workbook DOM buffering in RAM)
- **Database Footprint**: `479.14 MB` (`results/campusscraper.db`, WAL mode enabled, 100% of existing 102,069 person rows and 3,043 student offices verified intact)
