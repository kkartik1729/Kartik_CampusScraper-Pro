"""Scrape settings. Every limit has a safe default and can be changed from the CLI or UI."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 CampusScraper/1.0"
)


@dataclass
class ScrapeConfig:
    max_pages: int = 200            # total pages fetched per run
    workers: int = 3                # pages fetched at the same time; the gap between requests to one site stays delay_seconds
    patience: int = 80              # stop a site after this many pages in a row add nobody new (0 = never)
    max_depth: int = 4              # link hops from the start URL
    delay_seconds: float = 1.5      # minimum gap between requests to one host
    timeout_seconds: float = 25.0
    max_bytes: int = 8_000_000      # per response
    retries: int = 2                # extra attempts on 429/5xx/network errors
    respect_robots: bool = True
    include_subdomains: bool = False  # www.x.com -> doctors.x.com
    relevant_only: bool = True      # final output filter; everyone is still kept in the "All people" sheet
    departments: list[str] = field(default_factory=list)  # e.g. ["CSE", "IT"]; empty = all departments
    include_offices: bool = True    # also visit placement / higher-studies / student-chapter / alumni pages
    use_sitemaps: bool = True
    render: str = "auto"            # never | auto | always  (auto needs Playwright installed)
    show_browser: bool = False      # open a visible browser window (some firewalls block headless)
    user_agent: str = DEFAULT_USER_AGENT
    # Local/private addresses are refused unless explicitly allowed (tests, intranet sites).
    allow_private_hosts: bool = field(default_factory=lambda: os.getenv("CAMPUSSCRAPER_ALLOW_PRIVATE") == "1")
    strict_public_network: bool = False  # automatic prompt discovery checks DNS and every redirect
    save_html_dir: str | None = None   # write every fetched page here for debugging
    extra_profile_patterns: list[str] = field(default_factory=list)  # e.g. ["/consultant/"]
    adaptive_concurrency: bool = True  # automatically throttle workers when system CPU/RAM are constrained
    use_url_cache: bool = False        # opt-in response caching across runs for incremental scraping

    def validate(self) -> "ScrapeConfig":
        if self.render not in {"never", "auto", "always"}:
            raise ValueError("render must be never, auto or always")
        if not 1 <= self.max_pages <= 20000:
            raise ValueError("max_pages must be 1..20000")
        if not 0 <= self.max_depth <= 10:
            raise ValueError("max_depth must be 0..10")
        if not 1 <= self.workers <= 32:
            raise ValueError("workers must be 1..32")
        if self.patience < 0:
            raise ValueError("patience must be >= 0")
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be >= 0")
        return self
