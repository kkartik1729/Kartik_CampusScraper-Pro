"""Real local model evaluation on labeled fixture pages, NOT a live-web coverage test.

Run after ollama pull qwen3:0.6b. Exits nonzero on any incorrect acceptance,
missed faculty page, incorrect requested count/location, or invalid model response.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campusscraper.discovery_services import DiscoveryDB, ModelRouter
from campusscraper.models import Page
from campusscraper.prompt_discovery import Limits
from campusscraper.verified_discovery import discover_verified

CASES = [
    ('valid_faculty', 'Mumbai', '<h2>Faculty</h2><p>Dr. Anitha Rao, Professor, Computer Engineering</p>', True),
    ('wrong_city', 'Pune', '<h2>Faculty</h2><p>Dr. Anitha Rao, Professor, Computer Engineering</p>', False),
    ('homepage_only', 'Mumbai', '<p>Admissions open. Engineering courses and student clubs.</p>', False),
    ('student_directory', 'Mumbai', '<h2>Students</h2><p>Anitha Rao, second-year engineering student.</p>', False),
]
PROMPT = 'Find 1 engineering college in Mumbai and return its official faculty page URL.'
URL = 'https://example-college.edu/faculty'

class Search:
    def search(self, query):
        return [URL]

class Fetch:
    cfg = SimpleNamespace(allow_private_hosts=False)
    def __init__(self, city, content):
        self.html = f'<h1>Example Engineering College</h1><p>Official website. Address: {city}, Maharashtra, India.</p>{content}'
    def get(self, url):
        return Page(url, url, 200, self.html)


def main():
    os.environ['MODEL_PROVIDERS'] = 'ollama'
    results = []
    with tempfile.TemporaryDirectory() as temp:
        db = DiscoveryDB(Path(temp) / 'evaluation.db')
        try:
            for name, city, html, expected in CASES:
                started = time.monotonic()
                router = ModelRouter(db, name)
                row = {'case': name, 'expected_acceptance': expected}
                checkpoints = []
                try:
                    report = discover_verified(PROMPT, router, Search(), Fetch(city, html), limits=Limits(rounds=1),
                        progress=lambda event: checkpoints.append(event))
                    actual = bool(report.candidates)
                    plan = report.plan
                    location_preserved = any('mumbai' in c['prompt_quote'].lower() and c['applies_to']=='institution' for c in plan.get('criteria', []))
                    assessed = any(a['url'] == URL and not a.get('validation_error') for a in report.attempts)
                    passed = (assessed and actual == expected
                              and plan.get('requested_institutions') == 1 and location_preserved
                              and plan.get('requested_people') is None
                              and plan.get('requested_people_per_institution') is None)
                    row.update(passed=passed, accepted=actual, report=report.__dict__)
                except Exception as exc:
                    row.update(passed=False, error=str(exc))
                finally:
                    row['model_diagnostics'] = router.diagnostics
                    row['last_phase'] = checkpoints[-1].get('phase') if checkpoints else 'not_started'
                    if checkpoints and 'report' not in row:
                        row['partial_report'] = checkpoints[-1].get('report', {})
                    router.close()
                row['seconds'] = round(time.monotonic()-started, 2)
                results.append(row)
                if row.get("error"):
                    print("  " + row["error"])
                print(f"{name}: {'PASS' if row['passed'] else 'FAIL'} ({row['seconds']}s)")
        finally:
            db.db.close()
    output = Path('outputs/ollama-evaluation.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'evaluation':'Real model on synthetic labeled pages; not live web coverage', 'cases':results}, indent=2),encoding='utf-8')
    print(f'Report: {output}. Passing does not establish live search coverage.')
    return 0 if all(r['passed'] for r in results) else 1

if __name__ == '__main__':
    raise SystemExit(main())
