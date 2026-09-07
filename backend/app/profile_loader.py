"""Loads the candidate profile + methodology text that gets passed to Claude
as context for fit evaluation and document drafting.

Two sources, in priority order:
1. A mounted checkout of the ai-job-search repo (REPO_PATH env var) - reads
   CLAUDE.md and the job-application-assistant skill files directly, exactly
   like Claude Code would when running /apply.
2. The `profile` row in the Settings table, edited from the web UI.

If neither is populated, callers get an empty string and the UI should nudge
the user to fill in their profile first.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_PATH = Path(os.environ.get("REPO_PATH", "/repo"))

METHODOLOGY_FILES = [
    "CLAUDE.md",
    ".claude/skills/job-application-assistant/01-candidate-profile.md",
    ".claude/skills/job-application-assistant/02-behavioral-profile.md",
    ".claude/skills/job-application-assistant/03-writing-style.md",
    ".claude/skills/job-application-assistant/04-job-evaluation.md",
]


def load_repo_profile() -> str:
    """Concatenate the profile + methodology files from a mounted repo checkout.

    Returns an empty string if the repo isn't mounted or hasn't been through
    /setup yet (files still contain [PLACEHOLDER] tokens are still returned -
    it's up to the caller/UI to warn about that).
    """
    chunks: list[str] = []
    for rel in METHODOLOGY_FILES:
        f = REPO_PATH / rel
        if f.exists():
            chunks.append(f"### {rel}\n\n{f.read_text(errors='ignore')}")
    return "\n\n---\n\n".join(chunks)


def repo_mounted() -> bool:
    return REPO_PATH.exists() and (REPO_PATH / "CLAUDE.md").exists()
