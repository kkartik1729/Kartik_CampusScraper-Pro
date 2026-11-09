# Prompt discovery: setup and operation

This feature belongs to `OneSmarterInc/campus_scrapper`. SWARM is a separate repository.
The stable pre-feature checkpoint is branch `before-prompting`, commit
`34e3bb43973160e04a489012394090bd6a5bfcf7`.

## Ollama-first setup (Windows)

Install Ollama from https://ollama.com/download/windows, start it, then:

```powershell
ollama pull qwen3:0.6b
```

Add or update these entries in your existing `.env` (preserve other settings):

```dotenv
MODEL_PROVIDERS=ollama
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:0.6b
```

Restart the portal after changing configuration. Stop the old waiting run and
start a new one so that old discovery checkpoints are not reused.

```powershell
.\.venv\Scripts\python.exe tools\check_prompt_setup.py --live-model
.\.venv\Scripts\python.exe tools\evaluate_ollama.py
.\.venv\Scripts\python.exe -m campusscraper serve --port 8001
```

The first command checks search connectivity and structured model output. The
second uses the real installed model against four labeled synthetic pages:
correct faculty, wrong city, homepage-only, and students. It checks count and
location preservation too. Its JSON report is `outputs/ollama-evaluation.json`.
Neither command proves live website coverage. A 0.6B model is a small model in
parameters, not a token allowance; it may fail semantic reasoning. Do not bypass
evidence checks to make it pass. A larger locally installed model can be selected
with OLLAMA_MODEL after the same evaluation.

For college-name/faculty-URL Excel output, uncheck **Also extract faculty contacts**.
Download the source workbook and upload it into the existing Excel crawler.
Alternatively keep the checkbox checked to extract published contacts in the same
run. URLs are discovered from search and observed links, never invented by a model.
Increase Maximum colleges above 10 when requesting 15 or 20 colleges.

Cloud fallback is optional and explicit:

```dotenv
MODEL_PROVIDERS=ollama,groq
GROQ_API_KEY=your-key
```

For cloud-only use, select `MODEL_PROVIDERS=groq` (or `groq,gemini`). Existing
cloud usage limits remain intact; local Ollama requests do not use these budgets.
Ollama must run on the server machine; if the portal runs in Docker, loopback
refers to the container, so configure the appropriate reachable host address.

SearXNG still supplies web search. Ollama does not replace it. The crawler now
follows observed same-origin faculty/department links even when a small model
fails to suggest them. A malformed page assessment leaves that page unverified
and retains other results instead of terminating the entire discovery run.

## What happens after you enter a prompt

1. The selected model (Ollama by default) interprets the original wording and checks that the plan preserves its requirements and exclusions.
2. Your SearXNG service searches for real pages. The model chooses only URLs returned by search or links actually found on retrieved pages.
3. Official faculty/office pages need supporting page quotations. Institution evidence can come from multiple pages on the same origin, with a check that they describe the same campus.
4. The existing CampusScraper crawler extracts published contacts. It keeps the existing student exclusion rules and never creates email addresses from name patterns.
5. Research, department, role and other person/page conditions are checked on the extracted records. Only matching records reach the final Excel and the portal directory.

Simple location-based requests do not need a model call for every faculty member.
When individual filtering is needed, small groups of records share one request.
The model does not generate the exported names, phone numbers or email addresses.
Ambiguous requests receive a clarification field. Requests for individual students
or other industries are unsupported. Staff-run placement and higher-studies offices are supported.

## Windows PowerShell setup

Run these commands from your existing campus scraper folder. Keep the application
on localhost; it is a local tool, not an authenticated public web service.

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

For optional cloud inference, explicitly select `MODEL_PROVIDERS=groq,gemini`.
Put your Groq key after `GROQ_API_KEY=`. Optionally put a Gemini free-project key
after `GEMINI_API_KEY=`. Leave the Gemini value blank if you prefer waiting for
Groq capacity. Never send keys in chat or commit `.env`; it is ignored by Git.
Restart the server after changing environment settings.

Use accounts/projects that remain on the providers' free tiers. This program
does not upgrade accounts or use paid search tools. **It cannot detect your billing
tier or prevent provider charges if you supply a key for a paid account.** Its
local request/token budgets control usage; they are not a billing guarantee.

### Start SearXNG

You need a working Docker installation (Linux containers). Docker Engine on Linux
or WSL can host it; an existing SearXNG service also works. The portal itself runs
in your Python environment, outside Docker. SearXNG itself does not require a GPU or a language model.

```powershell
.\.venv\Scripts\python.exe tools\setup_search.py
docker compose -f compose.search.yml up -d
.\.venv\Scripts\python.exe tools\check_prompt_setup.py --live-model
```

The setup script generates a random search-service secret and preserves existing
settings on subsequent runs. The container listens on `127.0.0.1:8080`. JSON search
is enabled. Google, DuckDuckGo, Brave, Startpage, Yahoo and Qwant engines are configured; availability
depends on their current behavior. A blocked engine does not discard usable
results from the others. No results with engine failures causes a saved wait.

`--live-model` makes one small structured-output request to each configured
provider. Without that flag, the checker tests search and provider access only.
The checker never prints keys or provider response bodies. It does not prove
real-world faculty coverage or perfect prompt interpretation.

If search fails:

```powershell
docker compose -f compose.search.yml logs --tail 80
```

An existing search service can be selected through `SEARXNG_URL` in `.env`.
Use the service root, not its `/search` endpoint. It must allow `format=json`.
The included container uses the official image's `latest` tag. For a managed
installation, pin `SEARXNG_IMAGE` to the digest you have validated; test upgrades
before changing that digest.

### Open the portal

```powershell
.\.venv\Scripts\python.exe -m campusscraper serve --port 8001
```

Open <http://127.0.0.1:8001/#new>. Use **Find colleges and contacts from a prompt**.
For example: `Find 10 engineering colleges in Mumbai and their faculty contacts.
Exclude Navi Mumbai.` The default maximum is 10 colleges; increase it if your
prompt explicitly asks for more. Existing Excel uploads and one-site crawls remain available.

The UI separates interpretation, source discovery, extraction, record matching,
and file writing. A college crawl ending does not mark the overall run finished.
Coverage notes explain shortages, blocked pages and limits. No invented percentage
or completion time is shown while the total work is unknown.

## Quotas, pause and recovery

- Ollama is the default and requires no cloud key. MODEL_PROVIDERS controls the explicit order. Cloud providers are never used merely because a key exists.
- The same schema and evidence checks apply to all providers. A fallback does
  not guarantee identical semantic judgment; live acceptance tests are still needed.
- Successful searches, retrieved pages, model decisions and extracted data are
  saved in SQLite. Completed colleges also keep the crawler's existing resume caches.
- For temporary cloud quota/service failures, the portal saves the job and retries
  at the displayed time. Missing Ollama models or unreachable local servers stop
  with setup instructions; fix the server and retry the run. It does not switch
  to a paid service, rotate API keys, or export unfiltered contacts.
- Pause/Stop finish the current network operation. A paused/interrupted college
  may be crawled again on resume; completed colleges are reused. Discovery/model
  steps already saved are replayed without new requests.
- After a server restart, interrupted work requires **Resume saved run**. Jobs
  already waiting for quota can retry automatically. Use **Runs → Open** to view
  an older prompt job. Start a new job to change its prompt or budgets.
- Local budgets are conservative rolling windows shared by runs in this
  installation. Other apps using the same provider account can still exhaust the
  account's quota. Failed requests retain their token reservation when no usage
  figure is returned. The UI's usage counters are not an invoice.

Run one portal process per results directory. Do not run the CLI against the
same results database while the portal is running. This lightweight local
architecture uses one discovery worker and up to two college crawler workers;
it does not require Redis, Celery, a GPU or a separate database server.

Stop the app before backing up the **whole `results` folder**, including
`campusscraper.db`, `prompt-discovery.db`, any SQLite WAL/SHM files and per-college
caches. It contains prompts, retrieved evidence and working extraction data.
Deleting a prompt run from Runs removes its discovery journal and job files.

## Downloads and filtering

The final contacts workbook uses the existing Faculty, All people, Student offices,
Evidence, Pages, Colleges and Summary sheets. Both people sheets contain only
records that passed the prompt filters. Shared office contacts remain distinct
from personal contacts. Unknown contacts stay empty.

**Download source URLs** uses the standard college-upload columns. When a prompt
requires person/page filtering, these rows are marked `Enabled=no` with an
explanation: uploading them as an ordinary batch would otherwise lose the filters.
Use the prompt run's contacts workbook for the filtered output.

The evidence JSON records the run state/error, proposed plans and quote-validation
errors, validated interpretation, page decisions, supporting quotes, record
decisions and provider usage. Literal evidence checks prevent invented
quotes, but do not prove an LLM's semantic judgment or official-site ownership.
For explicit people counts, matching records are selected in college/name order;
they are not ranked by quality. Per-college counts are treated separately from
total people counts, and shortages are reported.

If planning cannot validate the model's quotations, case/whitespace differences
are mapped back to exact text from your prompt. Other mismatches receive one
automatic repair request before the normal semantic audit. The repair uses the
same quota limits and saved journal as other calls; a quota wait is resumable.
Unsupported or still-invalid plans never start searching. For a planning failure,
download the evidence report to inspect the proposed criteria and failure details.
After updating an older installation that reported "requirements were not
grounded", restart the portal, hard-refresh the browser, and try the prompt again.

Discovery now checkpoints its search queue, current page/chunk, related-page
context and report counters. A quota wait or process restart continues that
checkpoint instead of rebuilding discovery from zero. Terminal page failures
(such as 403/challenge pages and 404s) are also cached for the run; transient
fetch failures have a five-minute cache backoff. Cancelled fetches are not cached.
The evidence report includes each query's returned URLs, titles/snippets and
unavailable engine names, plus sources skipped before fetching. Reference and
dictionary sites are skipped; relevant campus pages are ranked first and hosts
are interleaved. These are selection hints, never substitutes for source evidence.
When exact-phrase/TLD searches fail to find enough sources, another discovery
round can relax search syntax while retaining all original eligibility criteria.

After installing the discovery checkpoint fix, start a **new run** for the same
prompt to refresh older search results and use the updated planner. Existing
saved results remain intact. Per-minute quota waiting still applies: a connected
Groq account and SearXNG service do not guarantee unlimited capacity or coverage.

## Command line alternative

```powershell
.\.venv\Scripts\python.exe -m campusscraper discover "Find 10 engineering colleges in Mumbai and faculty contacts"
.\.venv\Scripts\python.exe -m campusscraper discover "Faculty pages for COEP and PICT" --sources-only
.\.venv\Scripts\python.exe -m campusscraper discover --resume YOUR_RUN_ID
```

The CLI exits with code 75 when it must wait for a service/quota. Resume using the
printed ID, or open the portal for automatic quota retries. Other incomplete or
clarification outcomes return code 2. Finished/partial exports return code 0.

## Validation and practical limits

See [the implementation validation record](prompt-validation.md) for the exact
checks run and the live checks that still require a configured installation.

Run the Python tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests_campus -q
```

The automated suite includes provider contracts, fallback and quota interruptions,
process-restart checkpoints, exact quotation checks, plan auditing, per-person
filters, private DNS/redirect rejection, the standard template adapter and the
full search → HTTP crawl → filtered Excel/history flow using a fictional college.
Semantic-service answers in these integration tests are controlled fixtures;
these are not claims that a live model achieves a measured recall score.

Optional browser-free DOM checks require Node.js and `jsdom@26.1.0` in your
development environment; they are not production dependencies:

```powershell
npm install --prefix "$env:TEMP\campus-ui-tests" jsdom@26.1.0 --no-save --ignore-scripts
$env:NODE_PATH = "$env:TEMP\campus-ui-tests\node_modules"
node --test tests_campus/prompt_ui.test.cjs
```

Before accepting the live feature, run the setup checker and compare real prompt
results with the manual-upload baseline for the same colleges. Check missing
institutions, matching faculty, email/phone attribution, exclusions, student
exclusion, quota recovery, elapsed time and peak memory. Include English, Hinglish,
named colleges, unspecified locations, research topics and ambiguous requests.
The deploy-time live checks require your configured keys and SearXNG service.

Search coverage, robots policies, inaccessible websites, outdated pages, scanned
PDFs and unpublished contacts still limit recall. Discovery evaluates bounded
text windows and navigation links; extraction reads the full fetched pages within
the existing crawl budget. Any model reasoning can be wrong. Finishing a run does
not prove it found every person. There is no unlimited free cloud quota promise.

Automatic discovery additionally checks public DNS destinations and HTTP redirect
targets. Browser subrequests also reject non-public destinations. This does not
turn the local unauthenticated portal into a hardened multi-tenant service; keep
it bound to localhost and leave `CAMPUSSCRAPER_ALLOW_PRIVATE` unset outside tests.

Primary implementation references:

- <https://console.groq.com/docs/structured-outputs>
- <https://console.groq.com/docs/rate-limits>
- <https://ai.google.dev/api/generate-content>
- <https://ai.google.dev/gemini-api/docs/pricing>
- <https://docs.searxng.org/dev/search_api.html>
- <https://docs.searxng.org/admin/settings/settings.html>

## Return to the pre-prompt version

Stop the server, back up `results`, then inspect the checkpoint on a new branch:

```powershell
git fetch origin
git switch -c inspect-before-prompting origin/before-prompting
```

This preserves later commits. Do not use a hard reset to inspect the checkpoint.


If search produces unrelated results, update the search container and run the
quality probe before starting a new job:

```powershell
docker compose -f compose.search.yml pull
docker compose -f compose.search.yml up -d
.\.venv\Scripts\python.exe tools\check_prompt_setup.py --query "Mumbai engineering college faculty directory"
```

The probe prints candidate URLs and reports failed recovery explicitly. Candidate
URLs are not yet verified faculty directories. If alternative engines remain
unavailable, inspect SearXNG container logs; changing the language model key will
not repair the search service. Start a new run after updating the portal so old
queued search results are not reused.


### Upgrade search recovery (October 26)

Older generated settings only kept four engines. Upgrade those settings to add
Startpage, Yahoo and Qwant, remove Bing from the default set, and recreate the
container so it loads the new config. The upgrade preserves the secret and saves
a git-ignored backup. Custom layouts are left untouched with an explanation.

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe tools\setup_search.py --upgrade
docker compose -f compose.search.yml up -d --force-recreate
.\.venv\Scripts\python.exe -m campusscraper serve --port 8001
```

Hard-refresh the portal and start a new run. The visible contact-extraction
checkbox controls whether discovery stops with downloadable source URLs or
continues through scraping, record filtering and contact Excel export. Source
count shortfalls are displayed explicitly. An evidence report is a diagnostic
log, never a substitute for a source or contacts results file.

Alternative-engine requests omit `categories`: SearXNG adds category engines to
explicit engine selections, which previously reintroduced Bing. Recovery also
checks returned engine provenance. Engine error reasons are saved in evidence.
External engine availability still depends on the local installation and network;
this migration cannot guarantee that an upstream provider will accept requests.


### Simple faculty URL requests

Requests of the form `Find 20 engineering colleges in Mumbai and return their
 official faculty directory URLs.` now use a complete, bounded grammar for count,
college type and location. Quotes come directly from the prompt; people counts
remain unset. No model planning or model plan-review calls are needed for this
form. Every candidate still needs the existing page/evidence checks. Other
wording, exclusions, email conditions, and person filters use the semantic planner;
this is not a general natural-language parser. Keep the UI maximum colleges high
enough for the requested count.

## How a prompt becomes college names and faculty URLs

The default engine (**Crawl each site**) uses no AI model at all.

1. **Read the request.** Count, college type, city, target and any named colleges
   are read from your words with plain rules — the city list and department names
   already used by the extractor. `Find 20 engineering colleges in Mumbai and
   return their official faculty directory URLs` becomes
   `20 · engineering · Mumbai · faculty pages`. Nothing is invented: a city the
   reader cannot find stays empty and widens the search instead of narrowing it
   wrongly.
2. **Search for candidate sites.** SearXNG runs the plain searches a person would
   type, plus `site:ac.in`. Listing sites (Collegedunia, Shiksha, Careers360,
   FacultyPlus, Scribd) are never used as a source; at most a college *name* seen
   in a title becomes another search, which must then be proved on the official site.
3. **Prove each site by crawling it.** Each candidate site is crawled briefly
   (14 pages by default) with the same extractor that builds your contact sheet.
   That answers the only questions that matter, from the site's own pages:
   its name, its city, whether it is the kind of college you asked for, and
   **which page the extractor reads faculty from**. That page is the faculty URL.
4. **Return names and URLs.** A college appears because staff were read from one of
   its pages, and the count is shown beside each URL. Sites that fail say why:
   `Site says Pune, not Mumbai`, `No page on this site listed faculty contacts`.
5. **Hand over to the existing crawler.** The verified URLs become ordinary source
   rows — download them as the familiar Excel template, or let the run continue
   straight into contact extraction.

Named colleges work the same way: `I want the faculty URLs from Sardar Patel
Institute of Technology and K. J. Somaiya College of Engineering` searches for
those two sites and proves each one.

If your prompt has conditions about individual people (research area, must have
an email, years of experience), this engine says so in the coverage notes and
does not filter on them. Switch **How colleges are verified** to **AI judges each
page** for those, which needs a working Ollama or cloud model.
