# Second Pune batch audit — 24 October 2026

## Confirmed causes and changes

1. HTML `colspan`/`rowspan` were ignored. An experience header spanning two columns shifted every subsequent field. Emails were read from the research column and experience numbers became research interests. The parser now expands the table grid, extracts emails from each identified person's row even with unfamiliar contact headers, and leaves unlabelled grouped experience totals blank.
2. A one-person table could be misclassified as a free-text profile, discarding its structured fields. Recognized tables now retain their row mapping unless independent strong profile evidence exists.
3. `Asst. Prof.` titles were removed by title validation. Abbreviations now survive normalization, and a published `Prof.` honorific is teaching evidence. This restores faculty classification for contacts previously kept only in All people.
4. The sitewide-contact cleanup mistook named personal emails repeated across faculty directories for office addresses. Strong person-scoped email evidence now survives repetition. Generic role mailboxes and contacts assigned to distinct people remain subject to shared-contact demotion.
5. Special-placement, placement-history and student-chapter activity pages admitted student outcomes and visiting speakers. These known non-roster page types are now excluded from person extraction while the separate office extractor remains available. Low-value placement outcome and faculty-achievement links no longer consume the crawl queue. This is not a claim that every possible student-page layout has been eliminated.
6. MIT-WPU uses a public POST directory endpoint. An adapter discovers school IDs from the official selector, follows its paginated response and then visits faculty profiles. Requests retain robots checks, pacing, retries, response size and page budget limits. A mock transport integration test exercises directory → POST → profile → email.
7. Resume reused old results regardless of extractor changes, URLs or settings. Caches now require a matching extraction revision and source/configuration fingerprint. Partial exports say colleges processed/failed and Exported at, rather than implying successful completion; partial college rows are sorted by upload row.

## Controlled replay

Old code: commit 58d139a. New code: this change. Both processed the same **896 saved official HTML pages**, using identical name normalization and reference matching. The replay includes extraction, merger and contact cleanup. It does not simulate network availability, discovery, department filters or the user's 150-page budget. It is an extraction regression benchmark, not a fresh end-to-end ten-college crawl.

| College | Matching reference emails before | After |
|---|---:|---:|
| MIT-WPU | 23 | 23 |
| MIT ADT | 0 | 0 |
| SCOE | 51 | 158 |
| SKNCOE | 46 | 117 |
| D. Y. Patil | 2 | 2 |
| RSCOE | 1 | 6 |
| JSCOE | 142 | 142 |
| Zeal | 110 | 110 |
| **Total** | **375** | **558** |

This is **183 additional matching email contacts**, a 48.8% increase on identical inputs. A contact match requires the same college and normalized name, with an exact reference email in either personal or office-email fields. Case, punctuation and common honorifics are normalized; initials are not expanded. Ambiguous names, name variants and errors in the research reference remain possible. These counts measure reference coverage, not verified deliverability or overall precision.

AIT and Trinity have no usable official HTML contact snapshots in this replay. AIT's research reference was third-party, Trinity's was an older indexed page, and MIT ADT emails came mainly from a historical appointment PDF. Those are intentionally not imported as current confirmed contacts. The earlier uploaded partial export recovered 289 reference emails, but its inputs and budget differ, so do not describe 289 → 558 as a code-only improvement.

Run the replay locally with the research snapshots (not committed to the repository):

```bash
PYTHONPATH=. python -m tools.replay_faculty_snapshots /path/to/pune_new_contacts /tmp/pune-replay.json
```

The snapshot directory contains `final-contacts.json`, `0.json` through `9.json`, `extra-pages.json` and HTML named by college index plus the first 12 hex characters of the URL's SHA1. This reuses local snapshots and makes no network requests. Individual contact data and student records are not committed.

## Live checks and tests

- MIT-WPU engineering directory POST: HTTP 200; 100 faculty cards on the first paginated response. Contacts require following the profiles.
- SKNCOE official CMS computer faculty mirror: HTTP 200; 47 person records, 45 with personal emails.
- RSCOE Computer Applications contact page: HTTP 200; six person records, five personal email fields plus the HOD role mailbox.
- Unit/integration suite: 68 tests pass. Includes existing portal/batch/export tests, merged headers, single-person tables, exclusions, contact demotion, professor abbreviations, public directory pagination and stale resume invalidation.
- `git diff --check` passes. One pre-existing Starlette/httpx deprecation warning remains.

## Retest input and limits

`examples/pune-10-new-colleges.csv` uses the existing 16-column upload format and imports all ten rows without errors. It replaces SKNCOE's failing hostname with the observed official CMS mirror, seeds MIT-WPU's two engineering-school directory requests, and adds RSCOE's Computer Applications contacts. RSCOE's department filter is cleared so those explicitly requested department contacts are included. Budgets are 600 for MIT-WPU and 300 for the others.

Start a fresh run with this CSV after pulling. The same file is accepted by the portal or CLI. Network/TLS failures, unpublished contacts, historical-only records, inaccessible pages and page-budget exhaustion can still leave gaps. Certificate verification remains enabled. The code does not guess emails or claim that research-reference parity has been achieved. The original 695-record research file itself includes 116 records requiring rechecking and some Teaching Assistant roles excluded under the project's stricter no-students rule.
