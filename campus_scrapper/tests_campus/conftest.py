import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests_campus"))

import make_college_site as make_site  # noqa: E402

SITE = ROOT / "tests_campus" / "site"


class Handler(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        Handler.hits.append(self.path)
        parts = urlsplit(self.path)
        path = parts.path.strip("/") or "index"
        base = f"http://{self.headers['Host']}"
        if path in ("robots.txt", "sitemap.xml") or path.endswith(".json"):
            file = SITE / path
            ctype = {"txt": "text/plain", "xml": "application/xml", "json": "application/json"}[path.rsplit(".", 1)[1]]
        else:
            name = path + (f"__{parts.query}" if parts.query else "") + ".html"
            file = SITE / name
            ctype = "text/html; charset=utf-8"
        if not file.is_file():
            self.send_response(404)
            self.end_headers()
            return
        body = file.read_text(encoding="utf-8").replace("{BASE}", base).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def site_url():
    make_site.build()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def hits():
    Handler.hits.clear()
    return Handler.hits


class WafHandler(Handler):
    """Imitates a hospital firewall: 403 for every non-browser client, normal site for browsers."""

    def do_GET(self):  # noqa: N802
        ua = self.headers.get("User-Agent", "")
        if "CampusScraper" in ua or "python" in ua.lower():
            self.send_response(403)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Access Denied</body></html>")
            return
        super().do_GET()


@pytest.fixture(scope="session")
def waf_url():
    make_site.build()
    server = ThreadingHTTPServer(("127.0.0.1", 0), WafHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def _ignore_developer_dotenv(monkeypatch):
    """A developer's .env must never decide what the tests do.

    settings() reloads .env on every call, so a key a test deletes comes back
    from the file and the run reaches the network with a real key. Tests set
    every variable they need through monkeypatch instead.
    """
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)


@pytest.fixture(autouse=True)
def _history_db(tmp_path, monkeypatch):
    """Each test writes run history to its own temporary database."""
    monkeypatch.setenv("CAMPUSSCRAPER_DB", str(tmp_path / "history.db"))
