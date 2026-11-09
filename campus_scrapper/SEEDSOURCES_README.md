# seedsources — shared URL discovery for all three scrapers

One prompt in, crawlable seeds out. Campus, onco and attorney keep their own
fetcher, extractor, journal and export; only "which URLs" moves here.

## Install

Copy into each repo's root:

    seedsources/        the package
    seed_cli.py         command line, for testing without the portal
    registry_aicte.py   needed by the registry adapter (tools/ is fine too)
    resolve_websites.py needed by the search adapter

## Check it

    python seed_cli.py --self-test

Nine checks, no network, no database.

## Use it from a portal

    from seedsources import seeds_for_prompt

    seeds, trail = seeds_for_prompt(
        prompt,
        registry_db="results/registry.db",   # colleges only, for now
        upload=uploaded_file_or_None,
        limit=20,
    )

    for seed in seeds:
        if seed.crawlable:
            crawler.crawl(seed.url)          # your existing crawler
        else:
            report_unresolved(seed.name, seed.note)

`trail` lists what every source did, failures included, so a thin result can be
explained rather than guessed at.

## Order and why

    upload      a person chose these rows, so nothing outranks them
    registry    statutory list, complete and offline; colleges today
    search      ddgs with scored acceptance; the only route for lawyers and
                companies, and the resolver that fills registry blanks
    searxng     last, because its degraded state is silent; the adapter now
                refuses a result set that came from a single engine

The first source with results wins. A second pass hands every seed that still
has no URL to the remaining sources. A seed that cannot be resolved stays as a
named row with a reason, never as a guessed URL.

## Per vertical

    colleges    registry (AICTE, 13,085 rows) -> search fills websites
    hospitals   search; add a NABH registry the same way as AICTE
    lawyers     search, or upload firm directory seeds
    companies   search; job-board feeds would plug in as another adapter

## Adding a source

Subclass SeedSource, implement supports() and find(), add it to default_chain.
Add resolve(blanks) if it can fill in a missing URL. Nothing else changes.
