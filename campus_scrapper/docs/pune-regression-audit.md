# Pune faculty regression audit — 24 October 2026

The changes address missed departments, discarded faculty CVs, incorrect identities, wasted requests, and misleading completion states. They were checked against the user's uploaded batch and the previously assembled web reference. **Coverage of this reference is not an accuracy score for every exported row or a complete census of these colleges.**

## Results against the original reference

Matching uses college plus a name normalised for titles, punctuation and case. Initials are not guessed. The same matching function is applied to both outputs.

| College | Reference entries | Original upload matched | After fixes and expanded directory seeds | Still unmatched by exact normalised name |
|---|---:|---:|---:|---:|
| COEP | 118 | 75 | 115 | 3 |
| PICT | 172 | 172 | 171 | 1 |
| VIT | 186 | 42 | 184 | 2 |
| Cummins | 119 | 116 | 118 | 1 |
| AISSMS | 130 | 20 | 130 | 0 |
| Modern | 200 | 138 | 197 | 3 |
| BVDU | 172 | 171 | 171 | 1 |
| PVG | 97 | 97 | 97 | 0 |
| PCCOE | 61 | 11 | 60 | 1 |
| Dhole | 54 | 12 | 54 | 0 |
| **Total** | **1309** | **854** | **1297** | **12** |

Raw reference matching improved from **65.2% to 99.1%**. The original 455 unmatched entries fell to 12. This combines code fixes **and better input URLs**; it is not a code-only or unseen-college benchmark.

## Why the remaining name differences are not twelve demonstrated missing people

Seven are name variants or previously truncated names: three at COEP, one each at PICT, Cummins, BVDU and PCCOE. Examples include a missing middle initial, `Unal` versus `Mrunal`, and the surname `More` previously being dropped. Same-college source pages and, where published, matching emails support these reconciliations.

Three Modern reference entries are shortened duplicates of fuller entries already present: Chandrakant, Vidula, and Yogeshrao Yashwantrao. Two VIT reference entries are institutions extracted from education/employment history, not faculty: “Dr. Babasaheb Ambedkar” and “Dr. J.J.MCOE”. Their source profiles are [12325](https://facultyprofile.vit.edu/profile/12325) and [10631](https://facultyprofile.vit.edu/profile/10631).

The reference therefore contains errors too. Do not describe every reference mismatch as a missing faculty member, or treat the reference as a complete faculty register.

## Contact checks

These counts require an exact normalised name match first. They remain conservative because the seven reviewed name variants above are not silently added to the matching algorithm.

| Check | Original upload | Live check snapshots | Reference entries with that contact |
|---|---:|---:|---:|
| At least one matching published email | 325 | 606 | 667 |
| At least one matching phone in own-phone or office-phone fields | 71 | 117 | 120 |

Contact extraction is still incomplete. Missing contact fields can reflect a page budget, unreadable PDFs, a name variant, or information that the current source does not publish. A phone kept as an office number must not be misreported as missing just because the personal-phone field is empty.

Two AISSMS snapshot email differences were legitimate personal addresses from faculty CVs versus institutional directory addresses. A separate live replay of both directory/PDF pairs verified that the final merge retains **both** published addresses. The conservative snapshot table above does not retroactively replace those two results with reference values.

The earlier wrong-person email assignment at PCCOE was checked again: Aparna Joshi now has the address published on her M.Tech faculty listing; a shared coordinator URL cannot merge her with its different current occupant.

## Causes fixed

- Individual profiles consumed the page budget before department directories. Directory links now receive priority and can be discovered from profile pages too.
- The upload omitted department URLs and restricted PCCOE to the Computer Engineering host. The example CSV includes known official department directories and enables PCCOE subdomains.
- Named faculty PDF links were discarded. Text-based, individually linked CVs now enrich the corresponding faculty record after identity checks; labelled name reordering/abbreviation is supported without surname-only matching. Generic/shared brochures are not treated as individual profiles.
- A no-new-names threshold stopped contact enrichment even when profiles were adding emails or phones. Newly populated fields now reset that threshold.
- Repeated starting URLs downloaded the same site's sitemap repeatedly. Each origin is processed once; slow-paced sites skip bulk sitemap prefetch.
- A malformed URL could discard an entire valid faculty page, including its pagination extraction. Bad links are isolated.
- A department head repeated in an introductory heading and a faculty card could disappear from the listing, and the title "Head of Department" could be shortened to "Head". Repeated cards now retain the identity/contact, and the full teaching title is preserved.
- Hidden faculty panels could be removed with navigation/modal boilerplate. Academic detail panels are preserved.
- Shared URLs, stale SEO titles, partial honorifics and truncated names could mix identities or create apparent omissions. Names and merge constraints are stricter, while confirmed contacts are preserved when records merge.
- University-wide links could mix other BVDU colleges into the Pune result. Multiple supplied pages under a specific campus folder now constrain discovery to that folder.
- DNS failures on a previously reachable host no longer permanently poison later page attempts. A run that reads no pages because of robots policy/unavailable robots is failed and not cached as a successful completed college.
- Research-topic lists and degree strings are rejected as departments. Teaching assistants/research scholars are conservatively excluded under the no-students rule.

## Verification and limits

**55 automated tests passed**, covering existing crawl/export/portal behaviour plus identity separation, malformed links, name variants, PDF identity/contact handling, student exclusion, directory priority, duplicate sitemaps, enrichment progress, campus scope, DNS recovery, robots failures and request pacing. Compilation and whitespace checks passed. The suite emits one dependency deprecation warning from the FastAPI/Starlette test client.

For a same-page replay, the old extractor threw a malformed-port error on [COEP's profile](https://www.coeptech.ac.in/faculty/dr-sawant-suraj-tanaji/); the fixed extractor recovered its faculty record. The PCCOE coordinator replay recovered the visible occupant instead of relying on a stale title. Real AISSMS directory/CV pairs separately verified contact preservation.

Live checks were collected during the fixes, with affected colleges rerun and targeted regressions replayed. Nine colleges used up to 150 pages, three workers per site, a 1.5-second configured gap, no network retries and HTTP-only rendering. Five independent colleges could run concurrently. There was no Chromium/browser validation. Dhole Patil was checked directly on its seven faculty directories: **7 pages, 0 errors, 441.5 seconds**. A focused follow-up on the mechanical faculty page verified recovery of the repeated department-head record and was merged into the checked faculty snapshot. Its robots policy asks for 60 seconds between requests, which is respected. The sample upload gives it eight starting pages including its office page; deeper enrichment needs a larger budget and more time.

These are iterative live regression checks, not a controlled timing comparison with the old workbook. The earlier 14-hour elapsed value may include pauses or sleep. Do not claim a measured speed-up ratio. The broader output contains additional people beyond the reference; every extra row has not been manually audited. Scanned PDFs, inaccessible pages, and unpublished contacts remain limitations.

## Recheck locally

Install updated requirements, restart the server, then upload `examples/pune-10-colleges.csv` as a **new batch**. Resume reuses cached completed colleges and does not retest them.

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m campusscraper serve --port 8001
```

To run the automated suite:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q tests_campus
```
