# Changelog — CampusScraper Enterprise Optimization & Upgrade

All notable architectural, performance, extraction, monitoring, UI/UX, database, and export improvements made to CampusScraper are documented in this file.

---

## [2.0.0] — Complete Platform Optimization & Enterprise Upgrade

### 1. Maximum Scraping Performance (`campusscraper/crawl.py`, `campusscraper/fetch.py`, `campusscraper/config.py`)
- **Expanded Concurrent Worker Pools**: Increased configurable worker pool range up to `32` concurrent workers per site and `32` concurrent colleges per batch (`colleges_at_once`).
- **Optimized HTTP Connection Pooling**: Configured `httpx.Client` with explicit connection pooling (`max_connections=100`, `max_keepalive_connections=32`, `keepalive_expiry=30.0s`) for high-throughput session reuse across worker threads.
- **Duplicate Page Content Hash Deduplication**: Added MD5 signature hashing of visible page body text (`_content_hashes`) during crawling to detect and skip identical pages served under multiple URLs or query/path aliases, preventing redundant extraction and link expansion.
- **Enhanced Internal-Link Priority Scoring**: Upgraded URL frontier scoring (`_score`) to prioritize `/people`, `/staff`, `/management`, `/academic-staff`, `/leadership`, `/administration`, and `/faculty-directory` links alongside faculty and department directories.
- **Adaptive Concurrency Throttling**: Integrated real-time CPU and RAM telemetry (`MONITOR.snapshot()`) into worker loops so secondary workers automatically pace themselves when system CPU (>90%) or RAM (>88%) becomes constrained.

### 2. Intelligent Faculty Data Extraction & Quality Control (`campusscraper/quality.py`, `campusscraper/crawl.py`)
- **Confidence Scoring (`0–100`)**: Added `compute_confidence(rec)` evaluating name completeness, academic designation, normalized department, verified personal email/phone, academic profile identifiers (Google Scholar, ORCID, Scopus, Vidwan), qualifications, and multi-page evidence corroboration.
- **Comprehensive Data-Quality Validation (`audit_records`)**: Added pre-export quality auditing that checks and counts:
  - Valid vs. invalid email addresses
  - Valid vs. invalid phone numbers
  - Empty or suspicious/non-person faculty names
  - Duplicate faculty records across normalized `(institution, name)`, email, and profile URL keys
  - Missing designations and departments
  - Incorrect/missing college associations
  - Broken source URLs
- **Cross-Page Duplicate Tracking**: Enhanced `Merger` to track `duplicates_merged` and surface exact duplicate-removal counts in live monitoring and crawl statistics.

### 3. Real-Time Background Process Monitoring (`campusscraper/web.py`, `campusscraper/static/portal.js`)
- **Live Monitoring Endpoint (`GET /api/monitor/live`)**: Delivers real-time telemetry across single-site crawls, college-list batches, and prompt runs:
  - Total, completed, running, pending, and failed colleges
  - Total URLs discovered and processed
  - Total faculty extracted, valid email count, valid phone count, and duplicate records removed
  - Current scraping speed (`pages/min`), average time per college (`s`), overall progress percentage (`%`), and estimated remaining time (`ETA`)
- **Per-Running-College Live Telemetry**: Tracks college name, active website URL, current scraping stage (`initializing`, `fetching`, `extracting`, `completed`, `failed`), pages discovered, pages processed, faculty extracted, status, and live duration.
- **Timestamped Live Activity Console**: Streams timestamped activity events (`[HH:MM:SS] ...`) automatically to the UI without manual refresh.

### 4. Complete Frontend / UI Redesign (`campusscraper/static/index.html`, `campusscraper/static/style.css`, `campusscraper/static/portal.js`, `campusscraper/static/app.js`)
- **Modern Dark Glassmorphism Interface**: Redesigned the desktop-first web application with a deep slate/navy dark theme (`#070d19` / `#0c1526`), frosted glass cards (`backdrop-filter: blur(16px)`), blue and cyan accents (`#0ea5e9`, `#06b6d4`), clean typography, progress bars, and responsive grids.
- **11 Dedicated Navigation Sections (+ Student Offices)**:
  1. **Dashboard** (`#dashboard`)
  2. **Start New Scraping** (`#new`)
  3. **College Management** (`#colleges`)
  4. **Live Scraping Monitor** (`#monitor`)
  5. **Faculty Records** (`#directory`)
  6. **URL Discovery** (`#urls`)
  7. **Failed Websites** (`#failed`)
  8. **Excel Export Center** (`#exports`)
  9. **Scraping History** (`#runs`)
  10. **Settings** (`#settings`)
  11. **System Performance** (`#performance`)
  12. **Student Offices** (`#offices`)
- **Real Backend Controls**: Wired Start, Pause, Resume, Stop, Retry Failed Colleges, Export Excel (Async & Direct), Quality Audit, and Clear Filters buttons to live backend endpoints while maintaining 100% backward compatibility with all existing DOM IDs and automated UI tests.

### 5. Major Excel Generation Speed Improvement (`campusscraper/export.py`, `campusscraper/store.py`)
- **Eliminated Per-Cell `WriteOnlyCell` Bottleneck**: Optimized `_stream_sheet` in `campusscraper/export.py` so data rows without formula-injection prefixes are appended directly as native tuples to `openpyxl`'s write-only XML stream instead of instantiating `WriteOnlyCell` Python objects for every cell (`~6.3x` faster sheet streaming).
- **Exact Template & Column Preservation**: Preserved all 7 sheets (`Summary`, `Colleges`, `Faculty`, `All people`, `Student offices`, `Evidence`, `Pages`), exact column names, exact column order, header styling, freeze panes, and autofilters.
- **Asynchronous Background Export Manager (`ExportManager`)**: Added non-blocking background Excel generation (`POST /api/exports/start`, `GET /api/exports`, `GET /api/exports/{id}/download.xlsx`) with live progress percentage, state/city/college/department filtering, and duplicate job prevention.
- **Automatic Post-Export Validation (`validate_xlsx`)**: Automatically verifies ZIP/XML integrity, required worksheet presence, and exact `Faculty` header columns after every background export.

### 6. Smart Caching & Database Optimization (`campusscraper/store.py`)
- **SQLite WAL & Memory Tuning**: Enabled `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA temp_store=MEMORY`, and a 64 MB page cache (`PRAGMA cache_size=-64000`).
- **Covering Indexes & Metadata-Only Deduplication (`_latest_meta`)**:
  - Added composite covering indexes `people_meta`, `people_run`, `people_fac_college`, `offices_meta`, `offices_run`, and `run_sites_run`.
  - Refactored `dashboard()`, `facets()`, and paginated `directory()` queries to scan lightweight indexed columns instead of reading 479 MB of `data` JSON blobs on every call:
    - `Store.dashboard()`: **44.43s → 1.91s (23.3x speedup)**
    - `Store.directory(limit=100)`: **8.53s → 1.55s (5.5x speedup)**
    - `Store.facets()`: **13.01s → 2.55s (5.1x speedup)**
- **URL Cache & Job Logs Tables**: Added `url_cache` and `job_logs` tables to SQLite for persistent URL caching and structured job event logging.

### 7. Pause, Resume, and Failed Website Recovery (`campusscraper/web.py`, `campusscraper/batch.py`)
- **Independent Failed Website & Batch Retry**: Added `GET /api/failed-websites`, `POST /api/failed-websites/retry`, and `POST /api/batch/{job_id}/retry-failed` to retry failed colleges or individual failed URLs without re-scraping already completed colleges.
- **Checkpoint Recovery**: Preserved per-college JSON checkpoints (`site_XXXX.json`) and SQLite `run_sites` records so interrupted runs resume seamlessly.

### 8. System Resource Optimization (`campusscraper/system_monitor.py`)
- **Zero-Dependency Native Windows Telemetry**: Implemented `SystemMonitor` using Win32 `ctypes` (`GetSystemTimes`, `GlobalMemoryStatusEx`, `GetProcessMemoryInfo`) with cross-platform fallback.
- **Live Resource & Network Tracking**: Tracks CPU utilization %, system RAM used/available MB, process RSS MB, active workers, active colleges, browser instances, and network request rate (`req/min` and MB downloaded).
