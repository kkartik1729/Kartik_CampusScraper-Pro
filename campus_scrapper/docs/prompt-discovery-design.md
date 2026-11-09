# Flexible prompts to campus contacts: practical design and prototype results

> Historical prototype record. The integrated Groq + SearXNG + Gemini workflow
> now lives in `prompt_jobs.py`, `verified_discovery.py` and `discovery_services.py`.
> Use [prompt-setup.md](prompt-setup.md) for current setup, behavior and limits.
> The local Ollama probe below remains an isolated experimental tool.

Date: 25 October 2026. Repository: campus_scrapper only. SWARM is separate.

## Decision

Use a local language model for interpreting arbitrary wording and resolving
ambiguous evidence, a self-hosted search service for discovering pages, and the
existing Python crawler for fetching and extracting contacts. No paid API key is
required. Internet, electricity, hardware and maintenance remain real costs.

Do not use a city/count regex parser as the primary interpreter. Do not require a
fixed directory of colleges before a request can work. A directory is a cache,
not the boundary of discovery. Do not use model memory as a source of URLs or
contacts. Do not let the model execute Python, shell commands or unrestricted
browser actions.

Arbitrary wording is supported by the architecture; perfect understanding of
every possible request is not a defensible guarantee. Missing context must lead
to a targeted clarification. A campus extractor cannot silently become a lawyer
or hospital extractor: record the intent, explain the unsupported sector, and
eventually route to a separately supported SWARM adapter without mixing repos.

## What exists now

An isolated experimental module, `campusscraper/prompt_discovery.py`, and CLI probe,
`tools/prompt_discovery_probe.py`. The portal is unchanged. No production prompt
button or automatic live contact export has been enabled.

Implemented in the prototype:

- Ollama HTTP adapter with JSON-schema output and strict Pydantic validation.
- SearXNG JSON search adapter, including explicit engine outage detection.
- Generic requirements with prompt quotes, not fixed city/profession fields.
- Separate institution, page and person requirements.
- Clarification and unsupported-scope outcomes before network discovery.
- Bounded query refinement and navigation through actual observed link IDs.
- URL normalization and deduplication, page failure reporting, cancellation
  between operations, explicit missing-count and exhausted-budget reporting.
- Literal page-evidence checks for every criterion and staff/site identity.
- Candidate-source conversion to the existing `Source` representation.
- Refusal to convert person-filtered results into whole-college source batches.
- Real crawler and Excel integration tested against our local college fixture.

Candidates are explicitly model-assessed, not proven correct. An exact quotation
can be real but irrelevant to a requirement. A valid schema cannot establish that
the model preserved every condition or correctly understood an exclusion.

## End-to-end production flow

1. **Interpret the prompt.** Keep original wording. Extract the objective,
   inclusion/exclusion requirements, desired output fields, optional count and
   count unit. Do not infer a college count from a faculty count. Resolve relative
   geography or ambiguous references using explicit conversation context; ask
   when missing. Show a short interpretation in the UI while work proceeds.
2. **Create complementary searches.** Search for institutions, faculty directories,
   profiles, topic/research pages and explicit user-supplied institutions. Search
   snippets propose candidates; they are not proof of official identity or contact
   ownership. A named person need not require an institution-first search.
3. **Build an evidence packet per institution/campus.** Combine official homepage,
   address/about page, department page and directory/profile evidence. Prefer
   independent official directory references when the identity is uncertain.
   Preserve source URL, retrieval time, quote and criterion it supports. Keep
   different campuses distinct; matching a name alone is insufficient.
4. **Discover relevant faculty sources.** Follow observed site links and sitemaps,
   run targeted site searches and reuse tested directory adapters. Map department
   coverage when the request asks for whole-college faculty. Use HTTP first;
   browser rendering only when needed. Chunk long pages rather than silently
   omitting content. PDFs follow the existing linked-faculty safeguards.
5. **Refine within limits.** Search again for uncovered departments or constraints.
   Never remove a user's exclusion just to fill a requested count. When a budget
   is exhausted, retain progress and expose the uncovered requirements. A budget
   limit is not proof of completion and should be extensible without restarting.
6. **Run the existing scraper.** Use source records directly; no intermediate
   Excel upload. Reuse extraction, deduplication, evidence and no-student rules.
7. **Apply person-level conditions after extraction.** A research-interest match
   requires evidence attached to that faculty member. Do not export everyone at a
   robotics department as a robotics researcher. Separate confirmed matches from
   unknowns, and never count unknowns as fulfilling the request. Keep generic
   office numbers separate from personally attributed contacts.
8. **Export with coverage information.** Include why each result matches, the
   supporting URL and outstanding gaps. A valid faculty page does not mean every
   faculty contact was found, and a published email is not proof of deliverability.

## Example requests and required behavior

| Prompt | Expected behavior |
|---|---|
| Find engineering faculty pages in Mumbai | Institution discovery, no invented requested count |
| Mumbai colleges, but leave Navi Mumbai out | Preserve the exclusion and check campus addresses |
| Robotics pe research karne wale professors ki profiles chahiye | Topic-based profile discovery; filter individual people |
| COEP and PICT placement office contacts | Named institutions, office contacts, not student records |
| Marine biology research groups anywhere | No city required; search subject and staff pages |
| Find ten professors | Person count, not ten colleges; clarify scope if necessary |
| Faculty near me | Ask for location unless explicitly supplied in context |
| The same colleges as before | Use saved conversation/run context or ask which colleges |
| Best colleges with good international exposure | Ask what ranking/evidence defines these subjective terms |
| Find students graduating next year | Explain the no-students boundary |
| Faculty and students who won robotics prizes | Do not silently process only half of the request |
| Lawyers handling medical negligence | Explain unsupported campus extraction scope |

## Free services and deployment

**Initial model candidate:** `qwen3:4b-instruct` through Ollama, subject to live
accuracy evaluation. Its listed model download is about 2.5 GB, not its total RAM
requirement. Use a fixed model version/digest for reproducible comparisons. A
larger model is an optional fallback on capable hardware; do not silently download
or load it. The initial candidate is not an accuracy claim or a universal winner.

**Search:** a privately operated SearXNG instance with JSON format enabled. It
queries upstream engines and may encounter throttling, CAPTCHA or blocked engines.
Do not rely on someone else's public instance as an unlimited backend. Search
failure must remain visible. Never circumvent an access restriction.

**Small deployment:** run the portal and Python worker on the user's machine. Run
Ollama and SearXNG on that machine if resources permit. For several ordinary office
machines, host one copy of these services on an existing suitable office computer
and let the portals call it over a controlled LAN. This keeps clients lightweight;
the shared host still needs enough resources and becomes a shared dependency.

**Concurrency:** start with one model inference at a time, a small bounded HTTP
pool and one browser process. Queue additional jobs. Keep models and browser
contexts out of per-page worker startup. Measure RAM and wall-clock time on the
actual target machines before promising performance. No Kubernetes, vector database
or additional distributed task system is needed for the first deployment.

**Caching and restart:** use SQLite for job stages, evidence metadata and bounded
cache entries, with content snapshots on disk. Key semantic decisions by original
prompt/plan, model version, extraction version and source content hash. Apply TTLs
and recheck stale sources. Do not cache blocked/failed fetches as successful empty
results. Persist cancellation and restart state. The prototype has only per-run
URL deduplication; persistent discovery resume/cache are not implemented yet.

**UI:** add a prompt mode beside Excel upload. Show interpreting, searching,
checking sources, extracting and exporting separately, with actual counts. Use
an indeterminate indicator while total work is unknown. Show approximate time only
after sufficient observations. Final states distinguish completed work, partial
coverage, cancellation, missing clarification and service failure. No compulsory
review queue; display evidence and ask only questions necessary to resolve intent.

## Prototype limits that must be resolved before release

- No actual local model or SearXNG service was available in this environment.
  Model interpretation quality, search recall, RAM and latency are unmeasured.
- Evidence currently comes from one page at a time. It cannot combine a campus
  address on one page with staff membership on another. This is conservative but
  will miss valid sources; implement institution evidence packets before release.
- Prototype page input is capped at 14,000 text characters and 100 links. It reports
  the cap but does not yet chunk/merge the remainder. This is not full coverage.
- Person-specific final filtering, persistent restart/cache, production portal
  progress and conversation-context resolution are not implemented.
- The probe uses plain HTTP only; JavaScript, PDF and sitemap coverage must be
  validated when connected to production discovery. The existing scraper retains
  its own capabilities; they have not all been wired into this probe.
- The URL guard rejects unsafe schemes, credentials and literal local addresses.
  It inherits the crawler's lack of DNS-resolution and redirect-hop address
  enforcement. Harden the fetch boundary before exposing arbitrary discovery as
  a network-accessible service. Tests explicitly allow localhost fixtures only.
- Prompt injection instructions are excluded from the permitted model tools, but
  semantic misclassification is still possible. Test adversarial pages using the
  real model; literal quote validation is not an injection-proof guarantee.

## Tests actually run

The complete regression run passed **101 tests**: 68 existing tests and the first
33 prompt discovery tests. After adding explicit evidence-cap reporting, the
focused suite passed **34 tests**, bringing the tested total to **102**.
One existing FastAPI/Starlette test-client deprecation warning
was reported. New tests cover:

- arbitrary prompt text carried intact to the model interface;
- missing context and unsupported student/mixed/non-campus scope;
- malformed model output, ungrounded criteria and invented evidence/links;
- literal private URLs, invalid schemes and credential-bearing URLs;
- normalized duplicate URLs and following an observed faculty link;
- missing model, failed search engines, throttling and robots-denied pages;
- cancellation, bounded search refinement, count shortfalls and budgets;
- refusing whole-college exports for person-level criteria;
- Ollama/SearXNG HTTP adapter contracts using mocked transports;
- discovery -> local HTTP site -> real crawler -> batch -> Excel, recovering
  `anitha.rao@sunrise-it.ac.in` and excluding the fixture student.

The model responses and search results in these tests are scripted. They prove
contract handling and integration behavior, not language understanding or web
accuracy. An actual CLI run with the missing local model returned
`planning_failed`, wrote a diagnostic report and produced no guessed URLs.

## Live evaluation and release gates

Use the three existing Pune college batches as source baselines, plus unseen
institutions in at least two other locations. Test at least 50 varied prompts,
including Hinglish, negation, missing fields, research interests, unsupported
sectors and adversarial text. Keep a held-out set; do not tune and report on only
the same examples. Save date-stamped source snapshots to separate website changes
from code changes.

Evaluate interpretation and discovery separately from extraction:

| Measure | Proposed release condition |
|---|---|
| Explicit inclusions/exclusions preserved | No silent losses in the reviewed critical test set |
| Wrong institution/campus, student inclusion | Zero observed in the release benchmark; not a universal guarantee |
| Faculty source and department coverage | At least manual baseline on equivalent scope and snapshots |
| Correctly attributed emails and phones | At least baseline, with no increase in wrong-person assignments |
| Person/topic matches | Every exported match has person-specific supporting evidence |
| Clarifications/unsupported requests | Correct action, no speculative search or silent scope changes |
| Partial failures and budget limits | Visible and resumable; never labelled fully complete |
| Runtime and peak memory | Measured on the intended hardware, reported alongside accuracy |

Count correct contacts against an independently reviewed source set, not raw email
totals. Run automatic and manual-source crawls with identical page budgets and
settings. Report per-college failures, not only an aggregate that hides them.

## Running the experiment after local services are configured

No installation is performed automatically. Ollama must have the chosen model
installed, and SearXNG must be running with `search.formats: [html, json]`.

From the repository root in PowerShell:

```powershell
ollama pull qwen3:4b-instruct
.\.venv\Scripts\python.exe tools\prompt_discovery_probe.py "Robotics pe research karne wale professors ki official profiles chahiye" --model qwen3:4b-instruct --search-url http://127.0.0.1:8080 --out discovery-report.json
.\.venv\Scripts\python.exe -m pytest tests_campus -q
```

The probe reports candidate URLs, reasoning, evidence, counters and gaps. It does
not start a production batch. The next implementation milestone is cross-page
evidence plus person-level filtering, followed by real-model/live-search evaluation
and then the portal workflow. Existing Excel-based scraping remains available.

## Technical sources checked

- Ollama structured output schema: https://docs.ollama.com/capabilities/structured-outputs
- Ollama local API: https://docs.ollama.com/api/introduction
- Model tags and download sizes: https://ollama.com/library/qwen3/tags
- SearXNG JSON API and format configuration: https://docs.searxng.org/dev/search_api.html
- SearXNG upstream blocking explanation: https://docs.searxng.org/admin/searx.limiter
