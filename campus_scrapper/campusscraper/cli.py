"""Command line: python -m campusscraper <command> ..."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ScrapeConfig
from .crawl import Crawler
from .export import write
from .extract import extract_page
from .fetch import Fetcher
from .models import Page


def _config(a) -> ScrapeConfig:
    return ScrapeConfig(
        max_pages=a.max_pages, max_depth=a.depth, delay_seconds=a.delay, render=a.render,
        relevant_only=not a.all_people, use_sitemaps=not a.no_sitemap, include_subdomains=a.subdomains,
        departments=[d for d in (a.departments or "").split(",") if d.strip()], include_offices=not a.no_offices,
        workers=a.workers, patience=a.patience,
        save_html_dir=a.save_html, extra_profile_patterns=a.pattern or [],
        **({"user_agent": a.user_agent} if a.user_agent else {}),
        **({"allow_private_hosts": True} if a.allow_local else {}),
        show_browser=a.show_browser,
    ).validate()


def _print_progress(e: dict) -> None:
    if e["event"] == "page":
        mark = {"profile": "P", "listing": "L", "other": ".", "error": "x"}.get(e["type"], "?")
        extra = f"  ({e['error']})" if e.get("error") else (f"  +{e['doctors_on_page']}" if e["doctors_on_page"] else "")
        print(f"[{e['fetched']:>4}] {mark} {e['url'][:110]}{extra}   | people {e['doctors_total']}, faculty {e['faculty']},"
              f" offices {e.get('offices', 0)}", flush=True)
    elif e["event"] == "sitemap":
        print(f"sitemap: {e['urls']} URLs listed", flush=True)


def _doc_lines(pr) -> None:
    # page_contacts lists every number on the page (students' too); it is used internally, never shown.
    signals = {k: v for k, v in pr.signals.items() if k != "page_contacts"}
    print(f"\nPage type: {pr.page_type}   signals: {json.dumps(signals)}")
    for d in pr.doctors:
        data = {k: v for k, v in d.to_dict().items() if v not in (None, [], {}, "")}
        print(json.dumps(data, ensure_ascii=False, indent=2))
    for o in pr.offices:
        print("Student office:", json.dumps({k: v for k, v in o.to_dict().items() if v not in (None, [], "", 0)},
                                            ensure_ascii=False, indent=2))
    if pr.profile_links:
        print(f"\nProfile links ({len(pr.profile_links)}):", *pr.profile_links[:25], sep="\n  ")
    if pr.pagination_links:
        print("Next pages:", *pr.pagination_links, sep="\n  ")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="campusscraper", description="Find faculty and student offices on college websites (no API key needed).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--max-pages", type=int, default=200, help="page budget per run (default 200)")
        sp.add_argument("--depth", type=int, default=4, help="max link hops from the start URL (default 4)")
        sp.add_argument("--delay", type=float, default=1.5, help="seconds between requests to one site (default 1.5)")
        sp.add_argument("--render", choices=["never", "auto", "always"], default="auto",
                        help="use a headless browser for JavaScript pages (needs Playwright; default auto)")
        sp.add_argument("--departments", help="keep only these departments in the Faculty sheet, e.g. --departments \"CSE,IT,ECE\"")
        sp.add_argument("--all-people", action="store_true", help="CSV/JSON output: everyone found, not only the Faculty sheet")
        sp.add_argument("--no-offices", action="store_true", help="skip placement / higher-studies / student-chapter pages")
        sp.add_argument("--workers", type=int, default=3,
                        help="pages fetched at the same time per site (1-8, default 3); the gap between requests stays --delay")
        sp.add_argument("--patience", type=int, default=80,
                        help="stop a site after this many pages in a row add nobody new (default 80, 0 = never)")
        sp.add_argument("--no-sitemap", action="store_true", help="do not read sitemap.xml")
        sp.add_argument("--subdomains", action="store_true", help="also crawl other subdomains of the same site")
        sp.add_argument("--save-html", metavar="DIR", help="save every fetched page to DIR (for debugging)")
        sp.add_argument("--pattern", action="append", help="extra profile URL fragment, e.g. --pattern /faculty-profile/")
        sp.add_argument("--user-agent", help="override the User-Agent header")
        sp.add_argument("--show-browser", action="store_true", help="open a visible browser window (if a site blocks headless)")
        sp.add_argument("--allow-local", action="store_true", help="allow localhost/intranet URLs (demo site, internal sites)")

    s = sub.add_parser("scrape", help="crawl a college site (or start from any page) and export faculty and offices")
    s.add_argument("urls", nargs="+", help="start URL(s): home page, faculty page, department page or one profile")
    s.add_argument("--out", default="faculty.xlsx", help="output file: .xlsx, .csv or .json (default faculty.xlsx)")
    common(s)

    b = sub.add_parser("batch", help="crawl every college in a list and combine the results")
    b.add_argument("file", help="college list in the standard template (.xlsx or .csv)")
    b.add_argument("--out", default="india_colleges.xlsx", help="combined Excel file (default india_colleges.xlsx)")
    b.add_argument("--pages-per-site", type=int, default=150, help="page budget for each college (default 150)")
    b.add_argument("--only", help="only colleges whose name contains this text, e.g. --only Vasavi")
    b.add_argument("--no-resume", action="store_true", help="re-crawl colleges that already finished")
    b.add_argument("--colleges-at-once", type=int, default=3,
                   help="colleges crawled at the same time (default 3); rows on the same website never run together")
    common(b)

    t = sub.add_parser("template", help="write the blank college-list Excel template")
    t.add_argument("--out", default="college_list_template.xlsx")

    pg = sub.add_parser("page", help="fetch ONE page and print what was extracted (no crawling)")
    pg.add_argument("url")
    common(pg)

    f = sub.add_parser("file", help="extract from a saved .html file (offline)")
    f.add_argument("path")
    f.add_argument("--url", default="https://example.invalid/page", help="the page's original URL (helps relative links)")

    sv = sub.add_parser("serve", help="start the local web UI")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1")

    pd = sub.add_parser("discover", help="find official college sources from a natural-language prompt and scrape them")
    pd.add_argument("prompt", nargs="?", help="describe the faculty or campus offices you need")
    pd.add_argument("--resume", metavar="RUN_ID", help="resume saved prompt discovery/extraction")
    pd.add_argument("--sources-only", action="store_true")
    pd.add_argument("--max-colleges", type=int, default=10)
    pd.add_argument("--discovery-pages", type=int, default=60)
    pd.add_argument("--searches", type=int, default=12)
    pd.add_argument("--pages-per-college", type=int, default=200)
    pd.add_argument("--render", choices=["never", "auto", "always"], default="auto")

    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles choke on ₹, – and similar
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if a.cmd == "discover":
        import os
        from .discovery_services import ConfigurationError, DiscoveryDB
        from .prompt_jobs import PromptIn, PromptManager, export_sources
        from .store import Store
        folder = Path.cwd() / "results"
        history = Store(os.getenv("CAMPUSSCRAPER_DB") or folder / "campusscraper.db")
        db = DiscoveryDB(history.path.with_name("prompt-discovery.db"))
        manager = PromptManager(db, history, folder)
        try:
            if a.resume:
                job = db.get(a.resume)
                if not job:
                    print("Saved prompt run not found")
                    return 2
                if job["state"] in {"done", "partial", "sources_ready", "clarify", "unsupported", "no_candidates"}:
                    print("This run has ended. Start a new prompt to change its scope or limits.")
                    return 2
            else:
                if not a.prompt:
                    print("Provide a prompt or --resume RUN_ID")
                    return 2
                job = manager.create(PromptIn(prompt=a.prompt, auto_scrape=not a.sources_only,
                    max_colleges=a.max_colleges, discovery_pages=a.discovery_pages, searches=a.searches,
                    pages_per_college=a.pages_per_college, render=a.render), start=False)
            print("Saved run:", job["id"], flush=True)
            manager.execute(job["id"])
            job = db.get(job["id"])
            print("State:", job["state"])
            if job.get("error"):
                print(job["error"])
            for issue in job.get("report", {}).get("issues", []):
                print("Note:", issue)
            for question in job.get("report", {}).get("plan", {}).get("questions", []):
                print("Clarify:", question)
            if job.get("xlsx"):
                print("Contacts:", Path(job["xlsx"]).resolve())
            if job.get("report", {}).get("candidates"):
                print("Sources:", export_sources(job, folder / f"prompt-{job['id']}-sources.xlsx").resolve())
            if job["state"] == "waiting":
                print(f"Resume after the service/quota recovers: python -m campusscraper discover --resume {job['id']}")
                print("The web portal retries saved waiting jobs automatically while its server is running.")
                return 75
            return 0 if job["state"] in {"done", "partial", "sources_ready"} else 2
        except (ConfigurationError, ValueError) as exc:
            print(str(exc))
            return 2
        except KeyboardInterrupt:
            manager.stop.set()
            print("Interrupted. Resume the saved run using its ID.")
            return 130
        finally:
            manager.close()

    if a.cmd == "scrape":
        cfg = _config(a)
        print(f"Crawling {', '.join(a.urls)}  (max {cfg.max_pages} pages, {cfg.delay_seconds}s delay, render={cfg.render})")
        crawler = Crawler(cfg, _print_progress)
        result = crawler.run(a.urls)  # Ctrl+C stops early and keeps what was found
        if result.stats.get("stopped_early"):
            print("\nStopped early; saving what was found so far.")
        path = write(result, a.out, cfg.relevant_only)
        st = result.stats
        print(f"\nDone in {st['seconds']}s: {st['pages_fetched']} pages, {st['people_found']} people, "
              f"{st['faculty']} in the Faculty sheet ({st['profiles_fetched']} with full profile pages), "
              f"{st['offices']} student offices.")
        print(f"Saved: {path.resolve()}")
        return 0
    if a.cmd == "template":
        from .sources import write_template
        print(f"Template saved: {write_template(a.out).resolve()}")
        return 0
    if a.cmd == "batch":
        from .batch import run_batch
        from .sources import read_sources
        try:
            sources, problems = read_sources(a.file)
        except (ValueError, OSError) as exc:  # wrong columns, wrong file type, file not found
            print(f"Cannot read {a.file}: {exc}")
            return 2
        if a.only:
            sources = [s for s in sources if a.only.lower() in s.institution.lower()]
        for p_ in problems:
            print(f"  row {p_.row} {p_.institution}: {p_.problem}")
        for s_ in sources:
            for w in s_.warnings:
                print(f"  row {s_.row} {s_.institution}: {w}")
        if not sources:
            print("No usable colleges in", a.file)
            return 1
        print(f"{len(sources)} colleges, up to {a.pages_per_site} pages each. Finished sites are saved and skipped on re-run.")

        def show(e):
            if e["event"] == "site_start":
                print(f"\n=== [{e['index']}/{e['total']}] {e['institution']}  {' '.join(e['urls'])}", flush=True)
            elif e["event"] == "site_done":
                print(f"--- {e['institution']}: {e['status']}, {e['pages']} pages, {e['people']} people, "
                      f"{e['faculty']} faculty, {e['offices']} offices ({e['with_phone']} with phone, {e['with_email']} with email)  "
                      f"{e['note']}", flush=True)
            elif e["event"] in ("page", "sitemap"):
                _print_progress(e)
        path = run_batch(sources, a.out, _config(a), a.pages_per_site, resume=not a.no_resume, progress=show,
                         parallel_sites=max(1, min(a.colleges_at_once, 8)))
        print(f"\nSaved combined file: {path.resolve()}")
        return 0
    if a.cmd == "page":
        cfg = _config(a)
        fetcher = Fetcher(cfg)
        try:
            page = fetcher.get(a.url)
        finally:
            fetcher.close()
        print(f"HTTP {page.status}  final URL: {page.final_url}  rendered: {page.rendered}  {page.error or ''}")
        for origin, st in fetcher.robots_status.items():
            print(f"robots.txt at {origin}: HTTP {st}")
        if fetcher.browser_hosts:
            print(f"Fetched with a real browser (site blocks plain HTTP): {', '.join(sorted(fetcher.browser_hosts))}")
        _doc_lines(extract_page(page))
        return 0 if not page.error else 1
    if a.cmd == "file":
        html = Path(a.path).read_text(encoding="utf-8", errors="replace")
        url = a.url
        if html.startswith("<!-- source: "):
            url = html.split("-->", 1)[0][13:].strip()
        _doc_lines(extract_page(Page(url, url, 200, html)))
        return 0
    if a.cmd == "serve":
        import uvicorn
        print(f"Open http://{a.host}:{a.port}/ in your browser. Press Ctrl+C to stop.")
        uvicorn.run("campusscraper.web:app", host=a.host, port=a.port, log_level="warning")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
