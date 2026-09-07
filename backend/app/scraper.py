"""Runs the existing portal-search CLIs (jobindex-search, jobnet-search, etc.)
that ship under .agents/skills/<portal>/cli in the ai-job-search repo, and
normalizes their JSON output into a portal-agnostic shape.
These CLIs need network access to the job portals themselves and `bun`
installed in the container - see the backend Dockerfile.
"""
from __future__ import annotations

import json
import subprocess

from app.profile_loader import REPO_PATH

SUPPORTED_PORTALS = [
    "jobindex-search",
    "jobnet-search",
    "jobbank-search",
    "jobdanmark-search",
    "linkedin-search",
    "freehire-search",
    "naukri-search",
]

# Each portal CLI uses a different flag name for the free-text query - they
# enforce unknown flags strictly (exit 1 + JSON error), so a mismatched name
# here fails the whole search rather than silently degrading. Verified
# against each CLI's own options schema.
QUERY_FLAG = {
    "jobindex-search": "--query",
    "jobnet-search": "--search-string",
    "jobbank-search": "--key",
    "jobdanmark-search": "--text",
    "linkedin-search": "--query",
    "freehire-search": "--query",
    "naukri-search": "--query",
}

# Location filter flag per portal, where supported. jobbank-search's
# --location takes a region *code* (amt), not a free-text place name -
# passing a city name there will likely just match nothing rather than error.
LOCATION_FLAG = {
    "jobnet-search": "--region",
    "jobbank-search": "--location",
    "jobdanmark-search": "--municipality",
    "linkedin-search": "--location",
    "freehire-search": "--city",
    "naukri-search": "--location",
    # jobindex-search: no location filter in this CLI - omitted on purpose
}

# linkedin-search's CLI hard-requires --location and exits with NO_LOCATION
# if it's missing - enforce that here with a clear message instead of
# letting it fail as an opaque 502.
LOCATION_REQUIRED = {"linkedin-search"}

# Only these portals' CLIs actually expose a --jobage (days) flag.
JOBAGE_SUPPORTED = {"jobindex-search", "linkedin-search", "freehire-search", "naukri-search"}

# `search` only ever returns list-view summaries - full descriptions require
# a separate `detail <arg>` call. Each portal's detail command wants a
# different kind of arg: some accept either a bare id/slug or a full URL,
# others require the bare id/slug specifically (passing a URL fails).
DETAIL_ARG_SOURCE = {
    "jobindex-search": "url",          # accepts id or URL - URL is unambiguous
    "jobnet-search": "external_id",    # numeric job ad id only
    "jobbank-search": "external_id",   # bare id only
    "jobdanmark-search": "external_id",  # slug only
    "linkedin-search": "url",          # accepts id or URL
    "freehire-search": "url",          # accepts slug or URL
    "naukri-search": "url",            # accepts id or URL
}

# naukri-search drives a real headless Chromium (Playwright) instead of a
# bare fetch() like every other portal here, since Naukri's public search
# API is bot-protected (see .agents/skills/naukri-search/SKILL.md) - browser
# launch + page navigation is meaningfully slower, so it gets a longer
# subprocess timeout than the 60s/30s used everywhere else.
SEARCH_TIMEOUT_OVERRIDE = {"naukri-search": 90}
DETAIL_TIMEOUT_OVERRIDE = {"naukri-search": 45}


class ScrapeError(RuntimeError):
    pass


def _cli_path(portal: str) -> str:
    path = REPO_PATH / ".agents" / "skills" / portal / "cli" / "src" / "cli.ts"
    if not path.exists():
        raise ScrapeError(
            f"CLI for portal '{portal}' not found at {path}. "
            "Make sure the repo checkout is mounted at REPO_PATH and `bun install` "
            "has been run for this portal's cli/ directory."
        )
    return str(path)


def search(
    portal: str,
    query: str,
    limit: int = 20,
    job_age_days: int | None = None,
    location: str | None = None,
) -> tuple[list[dict], list[str]]:
    """Returns (results, warnings). Warnings note filters silently skipped
    because the target portal's CLI doesn't support them (rather than
    failing the whole search over a cosmetic mismatch)."""
    if portal not in SUPPORTED_PORTALS:
        raise ScrapeError(f"Unknown portal '{portal}'. Supported: {', '.join(SUPPORTED_PORTALS)}")

    if portal in LOCATION_REQUIRED and not location:
        raise ScrapeError(
            f"'{portal}' requires a location (e.g. \"Berlin, Germany\", "
            f"\"Bengaluru, Karnataka, India\", or \"Remote\") - this portal's "
            f"CLI rejects the request without one."
        )

    cli = _cli_path(portal)
    warnings: list[str] = []

    cmd = ["bun", "run", cli, "search", QUERY_FLAG[portal], query, "--format", "json", "--limit", str(limit)]

    if job_age_days:
        if portal in JOBAGE_SUPPORTED:
            cmd += ["--jobage", str(job_age_days)]
        else:
            warnings.append(f"'{portal}' doesn't support a posted-within-days filter - ignored.")

    if location:
        loc_flag = LOCATION_FLAG.get(portal)
        if loc_flag:
            cmd += [loc_flag, location]
        else:
            warnings.append(f"'{portal}' doesn't support a location filter - ignored.")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
            timeout=SEARCH_TIMEOUT_OVERRIDE.get(portal, 60), cwd=str(REPO_PATH),
        )
    except subprocess.TimeoutExpired as e:
        raise ScrapeError(f"'{portal}' search timed out after {SEARCH_TIMEOUT_OVERRIDE.get(portal, 60)}s") from e
    if result.returncode != 0:
        raise ScrapeError(f"'{portal}' search failed: {result.stderr.strip()[:500]}")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ScrapeError(f"'{portal}' returned non-JSON output: {result.stdout[:300]}") from e
    items = raw if isinstance(raw, list) else raw.get("results") or raw.get("jobs") or []
    return [_normalize(portal, item) for item in items], warnings


def _normalize(portal: str, item: dict) -> dict:
    """Different portal CLIs use slightly different field names; coalesce to
    a common shape the rest of the app expects."""
    return {
        "portal": portal,
        "title": item.get("title") or item.get("job_title") or item.get("position") or "Untitled",
        "company": item.get("company") or item.get("employer") or item.get("company_name") or "Unknown",
        "url": item.get("url") or item.get("link") or item.get("apply_url"),
        "location": item.get("location") or item.get("area") or item.get("city"),
        "description": item.get("description") or item.get("summary") or item.get("snippet") or "",
        "external_id": str(item.get("id") or item.get("job_id") or "") or None,
    }


def fetch_description(portal: str, external_id: str | None, url: str | None) -> str:
    """Calls the portal's `detail` CLI command to get the full posting body -
    `search` only ever returns a list-view summary, never the full text."""
    if portal not in SUPPORTED_PORTALS:
        raise ScrapeError(f"Unknown portal '{portal}'. Supported: {', '.join(SUPPORTED_PORTALS)}")

    arg_source = DETAIL_ARG_SOURCE.get(portal, "url")
    arg = external_id if arg_source == "external_id" else (url or external_id)
    if not arg:
        raise ScrapeError(
            f"Can't fetch detail for this job - no {'ID' if arg_source == 'external_id' else 'URL'} "
            f"was captured when it was scraped."
        )

    cli = _cli_path(portal)
    cmd = ["bun", "run", cli, "detail", arg, "--format", "json"]
    timeout = DETAIL_TIMEOUT_OVERRIDE.get(portal, 30)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout, cwd=str(REPO_PATH))
    except subprocess.TimeoutExpired as e:
        raise ScrapeError(f"'{portal}' detail fetch timed out after {timeout}s") from e
    if result.returncode != 0:
        raise ScrapeError(f"'{portal}' detail fetch failed: {result.stderr.strip()[:500]}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ScrapeError(f"'{portal}' detail returned non-JSON output: {result.stdout[:300]}") from e

    # jobnet's detail JSON has no top-level "description" - the full text is
    # in "body" as raw HTML. Every other portal already exposes plain text
    # under "description".
    description = data.get("description")
    if not description and data.get("body"):
        description = _strip_html(data["body"])
    return description or ""


def _strip_html(html: str) -> str:
    import html as html_module
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = html_module.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()
