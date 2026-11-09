"""Serve the fictional test college at http://127.0.0.1:8766 so you can try the scraper offline.

    python tests_campus/demo_site.py
    python -m campusscraper scrape http://127.0.0.1:8766/ --allow-local --delay 0
"""
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import conftest  # noqa: E402

conftest.make_site.build()
print("Demo college site running at http://127.0.0.1:8766/  (Ctrl+C to stop)")
ThreadingHTTPServer(("127.0.0.1", 8766), conftest.Handler).serve_forever()
