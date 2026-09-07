---
name: naukri-search
description: Search jobs on Naukri.com (India's largest job portal). Unlike this repo's other portal skills, this one requires a real headless browser (Playwright) because Naukri's public search API is bot-protected.
enabled: true
---

# naukri-search

Searches job postings on [Naukri.com](https://www.naukri.com), India's
largest job portal.

## Why this CLI is different from its siblings

Every other portal CLI in `.agents/skills/` is a zero-dependency script:
plain `fetch()` against a public JSON API or HTML page, parsed with regex.
Naukri used to work the same way, but its `/jobapi/v3/search` endpoint now
requires a signed `nkparam` header generated inside Naukri's obfuscated
frontend JS bundle, and is additionally reCAPTCHA-gated. A static header or a
guessed token gets `403`/`406`.

Rather than reverse-engineer Naukri's token signing (which breaks the moment
they rotate it again — this has already happened once), this CLI drives a
real headless Chromium via [Playwright](https://playwright.dev): it loads
Naukri's own search-results page, lets Naukri's own JS make its own
correctly-signed API call, and captures that network response. Job detail
pages are read the same way — via the rendered page, preferring embedded
schema.org `JobPosting` structured data when present, falling back to a
largest-text-block heuristic otherwise.

**Practical implications:**
- First run downloads a Chromium build (~150MB) via `bun install`'s
  `postinstall` hook. Set `PLAYWRIGHT_BROWSERS_PATH` to a persistent
  location (see the dashboard's k8s manifests) so this doesn't re-download
  on every restart.
- Searches are noticeably slower than the other portals (real browser
  navigation vs. a bare HTTP request) — budget more time per call.
- More fragile than a documented API: if Naukri changes their frontend
  markup or search-page URL structure, this needs an update. The
  JSON-LD-first detail extraction is intentionally the most stable part,
  since that's a stable, purpose-built format many job boards keep for
  Google Jobs indexing rather than an internal implementation detail.

## Usage

```bash
bun run src/cli.ts search --query "data engineer" --location "Bengaluru" --limit 20
bun run src/cli.ts search --query "site reliability engineer" --location "Pune" --experience 3 --jobage 7
bun run src/cli.ts detail https://www.naukri.com/job-listings-example-123456
```

Run `bun run src/cli.ts search --help` for the full flag list.

`--experience` (minimum years) and `--jobage` (posted within N days) are
applied **client-side** after fetching each page of results, not passed as
Naukri query parameters — Naukri's own URL-based filters are undocumented
and have proven unreliable in current third-party scrapers, whereas the
raw per-job experience/posted-date fields in the API response are stable
enough to filter on directly.

## Personal use only

Automated access to Naukri.com is against their Terms of Service. Keep
request volume low, don't use this commercially, and don't use it for bulk
data collection.
