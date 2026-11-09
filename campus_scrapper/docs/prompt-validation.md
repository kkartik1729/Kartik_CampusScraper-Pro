# Prompt discovery validation

## 2026-10-26 — discovery relevance and resume correction

The next supplied evidence report preserved sensible Pune/engineering queries,
but showed zero pages and a `planning` report status after the screenshot had
already shown five pages. Provider usage had increased to nine requests. Code
inspection confirmed that discovery rebuilt its queue/report on every retry,
while failed fetches were not cached. The screenshot also showed unrelated
dictionary pages being fetched. The old export lacked returned search metadata,
so it cannot establish which upstream engine produced those results or why.

The fix saves the discovery queue, pending page/chunk, context and report together
in SQLite, and commits each completed step before honoring a pause. Search metadata
and skipped-source reasons are exported. Known dictionary hosts are excluded before
fetch, result ranking uses query relevance without a college/city/TLD allowlist,
and queries can relax exact-phrase/TLD syntax without weakening final eligibility.
Permanent fetch failures are reused; transient errors have bounded cache backoff.

- **190 Python tests passed**, including a new-process SQLite reopen during a
  discovery quota wait that then completes actual local HTTP crawling, person
  filtering and Excel export. No completed search, page fetch or planning call
  repeats in that test; page counts never reset.
- Other interruption tests cover multi-chunk pages, completed blocked pages,
  pending searches, refinement, incomplete engines, and pause immediately after
  a successful operation. A controlled SERP using the exact three supplied Pune
  queries skips dictionary URLs and returns two observed campus candidates with
  both institution-type and location evidence.
- **8 DOM tests passed**; compilation and Git whitespace checks passed. The one
  Starlette test-client deprecation warning remains. External proxy variables
  were unset for the local HTTP tests, as in the earlier test run.

No live provider credentials are available here. The controlled SERP/campus
fixtures establish workflow behavior, not real-college recall, inference quality,
or a measured speed/token improvement. Existing quota budgets are unchanged.
Update and restart the Windows portal, then start a new run with the same prompt;
the new report will expose returned sources and engine availability if needed.

## 2026-10-26 — planning failure correction

The reported run stopped before search when a model-supplied `prompt_quote` did
not occur literally in the prompt. Its original evidence export omitted the
rejected plan and error, so the exact offending quotation cannot be recovered
from that report. Replaying the old code with the user's Pune prompt and a
case-only quotation change reproduced the same error with zero searches.

The correction restores original quote spans for case/whitespace differences,
allows one bounded model repair for other mismatches, and retains the independent
semantic audit and all search/page/person evidence checks. Planner instructions
separate counts/output instructions and built-in policy from user eligibility
criteria. Rejected proposals and grounding errors are now saved, and evidence
exports include the actual job state and error. The UI no longer says it is
interpreting a failed or waiting request.

- **173 Python tests passed**, including repaired prompt → real local HTTP crawl
  → person filtering → Excel; a quota interruption during repair resumes using
  the saved initial response. Invalid plans never search or enable final export.
- **8 DOM integration tests passed**. A Chromium visual check with controlled API
  responses also verified the failed-run message, enabled new-run control,
  disabled final export and absence of JavaScript errors.
- Regression cases cover both reported prompts, Hinglish negation, whitespace
  and case, paraphrased/combined/invented quotations, semantic-audit rejection,
  and genuinely missing context. Compilation, JS syntax and whitespace checks
  passed. Local HTTP tests ran with this environment's external proxy variables
  unset; the one Starlette test-client deprecation warning remains.

No live Groq/Gemini credentials are available here. These controlled tests do not
establish live-college accuracy or recall. Restart the updated portal, hard-refresh
the page and rerun the Pune prompt on the configured Windows installation.

## 2026-10-25 — original integration

### Automated results

- **156 Python tests passed**, including the existing campus crawler and discovery
  prototype regression tests. One dependency deprecation warning was reported
  by Starlette's HTTP test client; there were no test failures.
- **6 browser-free DOM tests passed** with Node.js and jsdom 26.1.0, executing the
  portal scripts with controlled API responses.
- Python compilation, JavaScript syntax checks and Git whitespace checks passed.
- The SearXNG Compose file was parsed, and setup generation/idempotence were tested.

Commands:

```text
python -m pytest tests_campus -q
node --test tests_campus/prompt_ui.test.cjs
python -m compileall -q campusscraper tools
node --check campusscraper/static/prompt.js
node --check campusscraper/static/portal.js
git diff --check
```

The DOM checks require the optional jsdom development dependency; see
[setup instructions](prompt-setup.md).

### What the integration tests establish

The integration suite starts local HTTP services for a fictional college and a
SearXNG-compatible search fixture. It uses the actual HTTP fetcher, campus crawler,
SQLite journals/history and Excel exporter. Model responses and quota failures
are scripted HTTP fixtures, so the suite checks application behavior rather than
measuring live model quality.

| Check | Observed result |
|---|---|
| Unfiltered prompt versus manual crawl of the same fixture | Identical faculty names, emails and phone numbers, and identical office contacts; at least eight faculty records checked. |
| Research-specific prompt | Matching faculty exported; unrelated faculty and students excluded. |
| Quota interruption during record filtering | Job enters a saved waiting state; resume reuses completed discovery and extraction. |
| Provider failure | Configured fallback receives the same schema; unavailable providers cause a saved wait or an explicit setup error. |
| Empty search with some failed engines | Other queries can still supply candidates; usable results are retained. |
| Process restart and user controls | Interrupted work is resumable; paused jobs stay paused. |
| Prompt fidelity | Controlled English/Hinglish, exclusions, individual criteria, counts and clarification cases preserve their intended constraints. |
| Evidence checks | Invented quotation text, missing decisions and invalid record/link IDs are rejected. |
| Portal/export boundary | Final download stays unavailable until filtering finishes; working unfiltered exports are not served by the prompt API. |
| Resource destinations | Private DNS destinations and redirects are rejected in automatic discovery. |
| UI behavior | Quota waits, partial coverage, clarification, empty input, output escaping and final-download gating pass DOM tests. |

### What had not been validated at original integration

No live Groq or Gemini key, running SearXNG service, or Docker executable was
available in the implementation environment. Real-provider inference, current
search-engine availability, Docker startup, ten-college recall, elapsed time and
peak memory on the user's Windows machine remain unmeasured.

A visual browser check was attempted, but the available browser could not reach
the local server. Installing a local Chromium binary also failed in this
environment. DOM tests do not substitute for a visual/end-to-end browser check.

Consequently, these results do **not** establish that automatic discovery matches
or exceeds manual ChatGPT/Claude research on real institutions. They establish a
tested workflow with durable recovery, evidence checks and explicit coverage
limits. Literal quotations constrain invented evidence but do not prove that a
model correctly understood a page or identified its official owner.

## Live acceptance on a configured machine

1. Start SearXNG and run `python tools/check_prompt_setup.py --live-model`.
2. Open the portal and run a small prompt for two known colleges. Check source
   ownership, constraints, progress states, links and workbook contents.
3. Run the same ten-college benchmark through ordinary Excel upload and prompt
   discovery. Compare institutions, faculty identities, correct email/phone
   attribution, omissions and student exclusion against official pages.
4. Try a research/department filter, an exclusion, a named-college request, an
   ambiguous prompt and Hinglish wording. Check both missing and extra records.
5. Pause/resume and restart an active run. Check the saved state and repeated
   request counts. Test fallback with provider credentials controlled by the
   operator; do not deliberately exhaust a shared provider quota.
6. Record wall-clock time, peak application/browser/container memory and provider
   token counts before increasing crawl budgets or accepting the feature for
   routine use.

The original stable branch `before-prompting` remains available at
`34e3bb43973160e04a489012394090bd6a5bfcf7`. This change belongs only to the
`campus_scrapper` repository; SWARM remains separate.


## Search response quality repair (2026-10-26)

Run 073d72c45b correctly interpreted the Mumbai request, but every returned
result came from Bing while Google, DuckDuckGo and Brave were unavailable.
Replay of its five recorded query responses rejects all 47 unrelated result
entries before crawling. No live model call is needed for this triage.

Search now screens metadata for minimal query overlap, requests strict safe
search, and tries one alternative-engine request if every result is unrelated.
Failure to recover is an explicit search configuration/availability failure,
not an empty success or an indefinite model-quota wait. Evidence retains rejected
results and recovery diagnostics. This is a heuristic, not a guarantee of
location, official ownership, faculty coverage or semantic correctness; those
still require page verification. Sparse metadata and short queries remain eligible.

Live end-to-end validation remains blocked here: no configured Groq/Gemini key
and no reachable SearXNG instance. The recorded-response replay and mocked
recovery tests do not establish that the user's unavailable engines are restored.


## Query-local recovery and navigation audit (2026-10-26)

The follow-up screenshot exposed a regression in the quality gate: one unusable
query terminated the entire job. SearchQualityError now denotes a query-local
failure. Discovery records it, continues the other planned queries and adds
bounded relaxed/institution-based queries within the existing search budget.
Real configuration errors (e.g. JSON access refused) still stop with evidence.
An all-unrelated run produces no candidates, rather than inventing URLs or
repeatedly charging the model for bad pages.

A second regression test covers 30 About links before a faculty link: staff
navigation now survives the 20-link model budget. URL safety checks remain active.

Validation: 199 Python tests and 8 DOM tests passed. Controlled recovery tests
cover five distinct college pages, exhausted bad searches, configuration errors,
and navigation crowding. Live fetches of SPIT, TSEC and VJTI were attempted but
DNS resolution failed in this execution environment. No live Groq/SearXNG
end-to-end success or production accuracy is claimed.


## Engine isolation, model repair and results UI (2026-10-26)

Run 5a92c45a06 exposed category/engine union behavior in SearXNG: an explicit
alternative engine selection combined with categories=general re-added Bing.
Confirmed against upstream searx/webadapter.py parse_generic. Recovery now omits
categories and rejects Bing-only provenance. Generated settings can be upgraded
to include Startpage, Yahoo and Qwant, preserving the secret and an ignored backup.

Completed but invalid structured model responses get one bounded repair attempt.
The counter survives quota waits/restarts, raw failed responses are not stored,
and local schema length/list limits are stated in model instructions. Optional
refinement failure retains verified source results rather than failing the whole
job. Required evidence and semantic checks remain enforced.

UI now states failure/no-output explicitly, distinguishes evidence logs from
result files, exposes source-only/contact mode, displays requested-count
shortfalls, and hides extraction stages for URL-only runs. Headless Chromium
verified ready and failed states with controlled API fixtures and no JS errors.

Validation includes the full Python suite, DOM tests, controlled actual crawler
and Excel integration, engine-isolation/provenance regressions, malformed-model
repair and resume bounds, and settings migration/secret preservation. These are
not live-provider accuracy measurements. This environment has no configured
Groq/Gemini credentials or reachable SearXNG, and direct college-domain DNS probes
failed. Full live completion remains unverified; do not label this release a
proven production accuracy result or an exhaustive security audit.


## Discovery latency and literal evidence repair (2026-10-26)

Run bae36f7d39 had 24 provider requests / 67,433 tokens for 11 fetched pages and
no accepted sources; the screenshots later showed 25 requests / 70,956 tokens
and roughly 20 minutes. Search was now finding faculty URLs. The next bottleneck
was repeated 4,000-character fragment assessment and lost institutional evidence.

Changes: one bounded 6,000-character literal evidence packet per page; navigation
removed after link extraction; college logo alt text retained; focus excerpts
include institution criteria, faculty and address text. Up to one observed
same-origin contact/about/home link supplies a 2,200-character identity excerpt
without a second model assessment. Redirects to other hosts cannot supply that
identity. Identity fetches count against the discovery page budget. Known
aggregators such as Scribd and FacultyPlus are excluded before fetching.

Short literal headings (Faculty) and locations (Mumbai) are no longer rejected
solely by an eight-character minimum. Quote normalization only restores case and
whitespace from the actual source; paraphrases remain invalid. Invalid page
proposals now retain validation diagnostics. The normal page-decision output
reservation is 1,200 tokens, with the existing 2,000-token repair allowance.
Provider rate limits are unchanged. Rejected pages with no requested follow-up
links are not assessed again in refinement.

214 Python tests passed, including long-page single-assessment, bounded evidence,
identity/link/redirect safety, literal quoting, quota resume and actual local
crawl-to-Excel integration. This proves bounded work in controlled fixtures, not
a production completion time or live-college precision. Live Groq/SearXNG testing
remains unavailable in this workspace. Start a new run after updating; discovery
checkpoint revision changed to avoid reusing old multi-fragment state.


## Ollama-first integration (2026-10-26)

Default provider is local Ollama with qwen3:0.6b. Cloud fallbacks require explicit
MODEL_PROVIDERS configuration. Old Groq cooldowns and token budgets cannot block
local inference. Request schema, completed-response checks, bounded repair, literal
evidence checks, and model/endpoint cache separation are covered by regressions.
Observed same-origin faculty/department navigation no longer depends entirely on
model-selected links. Invalid page responses leave pages unverified and preserve
other results. Plan validation still fails closed.

Validation: 226 Python tests passed initially; adding Ollama-to-crawler-to-Excel
integration produced 41 passing focused workflow/provider tests (one additional
test). Ten UI DOM tests passed. Model HTTP responses in automated tests are mocked;
the workflow test uses the real fixture HTTP crawler and workbook export.

Real qwen3:0.6b evaluation was attempted with tools/evaluate_ollama.py and blocked:
no local Ollama server/executable is installed here, and direct access to
ollama.com fails DNS (proxied download also timed out). Therefore there are NO
measured real-model accuracy or live 15/20-college coverage results for this
release. The four-case evaluator is included for running against the installed
local model; its output is not a live-web benchmark. SearXNG remains necessary
for discovery; this change does not claim to fix unavailable upstream engines.


## Uploaded local evaluation failure: structured response diagnostics

The user's four-case report failed every case with the same generic model error.
It did not record schema name, field validation failures, completion reason or
partial discovery state. It therefore cannot establish whether page eligibility
was ever assessed, or that the 0.6B model is semantically incapable.

The local request now states its JSON schema explicitly in the instruction as well
as the constrained-output format, explains null optional counts, and passes
sanitized field/type validation feedback to the existing one repair attempt.
Errors distinguish truncated output, empty output, malformed JSON, and typed field
failures. Diagnostic records omit raw response values and arbitrary extra keys.
The evaluator includes model stage diagnostics and its last discovery checkpoint.
The model-cache revision changed so failed validation markers from the old request
are not reused. Existing evidence gates and the two-attempt bound remain intact.

55 focused provider tests passed. This is a diagnostic/repair improvement, not a
verified fix for the uploaded live failure; a rerun on the user's local Ollama is
still required to determine the exact cause and whether repair succeeds.


## Concrete local-model planning failure (uploaded 2026-10-26)

The revised report showed zero searches and zero checked pages for all cases.
Qwen3:0.6b pluralized the singular prompt quote, classified Mumbai as a page
condition, and invented both a total and per-college people count of one. The
repair repeated those mistakes. This is a planning failure, not a search failure.

Added an exact full-request grammar for digit count + supported institution type
+ city + faculty URL output. Unmatched or complex prompts retain the existing
model planner/reviewer. This avoids model quote/count generation for the common
request; it does not loosen page evidence acceptance. Discovery checkpoint
revision is bumped to avoid resuming older plans. The evaluator now also requires
a completed, valid page assessment for negative cases; a failed model response
can no longer count as a correct rejection.

Regression coverage includes the exact uploaded prompt, arbitrary cities and
counts, exclusions/person filters falling through, and rejection of invented
Mumbai evidence on a Pune page. Tests use controlled model page responses; actual
0.6B page-classification quality still requires the local evaluator.


## Student directory false acceptance in local 0.6B evaluation

The latest uploaded report fragment begins during the wrong-city case; it does
not contain the valid-faculty result. It confirms successful bounded planning and
one Decision call per shown case. Wrong-city invented Mumbai evidence and empty
homepage staff evidence were rejected by existing gates. The student case was
incorrectly accepted: the model cited “Students Anitha Rao, second-year engineering
student.” as staff evidence. Literal quote matching alone did not prevent this.

Added a conservative veto for faculty/staff requests when the selected quote
explicitly describes students/alumni/applicants without a staff-role signal. It
preserves student-facing professor/coordinator/staff evidence and leaves office
targets unchanged. This is a narrow rejection guard, not a general proof of
semantic correctness. The exact false acceptance is covered by regression tests.
Discovery revision increased so older accepted work checkpoints are not reused.
Evaluation reports no longer duplicate a complete report under partial_report.

The observed qwen3:0.6b responses are not reliable enough for unattended source
acceptance. A larger local model such as qwen3:4b can be evaluated with the same
checks, but its accuracy and performance have not been measured here. Do not
weaken tests or call gate-blocked model errors correct model classifications.

Validation for this guard: full suite 257 passed, one existing portal profile-count
test failed; that test plus all 25 simple-plan/guard cases passed on targeted rerun
(26 passed). The intermittent profile-count failure is not established as fixed.

## Population corroboration instead of trusting the model (26 October 2026)

The student-only quote veto closed one observed hole but still let a model's
`accepted=true` decide the population. Two cases proved that was not enough: an
alumni page whose quote names a professor, and a student directory whose quote
is `Anitha Rao CS21B041 CSE II Year` — no population word at all, so nothing for
a text guard to catch.

Every fetched page is now read by the same extractor that builds the final
directory, and that reading can veto the model:

- the page's own identity marks it alumni/testimonial/topper/news, or
- it yields no staff at all while yielding 3+ student entries, or
- it yields no staff at all while yielding 2+ alumni entries.

A thin or unusually built staff page yields nothing either way and stays
inconclusive: it is still judged on evidence, never rejected for being hard to
parse. Office targets are unaffected, so a placement cell listing student
coordinators beside its officer is still accepted. The number of staff actually
read from each accepted page is recorded on the attempt (`extracted_staff`) and
shown beside the URL in the portal, so a verified source that yielded zero
people is visible rather than implied.

This is corroboration by an independent deterministic component, not proof of
model correctness. A page can still be wrong in ways neither layer detects —
wrong campus of a multi-campus trust, a branch address in a footer, an outdated
page. Live evaluation of a larger local model is still outstanding.

Validation: full suite **267 passed** (258 before, 9 new population cases), DOM
suite **10 passed**. The previously intermittent portal profile-count test passed
8/8 targeted reruns and 2 full-suite runs here; the budget-limited crawl it
depends on produced identical results in 24 runs at both 1 and 3 workers, so the
cause reported earlier was not reproduced on this machine and is not established
as fixed. Ollama could not be installed in this environment (ollama.com
unreachable, GitHub releases 403), so no live local-model inference was run.
