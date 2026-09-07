"""Database models for the job search dashboard.

These mirror the concepts in the original ai-job-search CLI workflow
(job posting -> fit evaluation -> drafted documents -> tracked outcome)
but store everything in a database instead of markdown files, so a
web UI can drive the same workflow.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlmodel import Field, SQLModel


class JobStatus(str, enum.Enum):
    scraped = "scraped"          # found, not yet evaluated
    evaluated = "evaluated"      # fit score computed
    drafted = "drafted"          # CV + cover letter generated
    applied = "applied"          # sent to employer
    interview = "interview"      # got a response / interview scheduled
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


class DocType(str, enum.Enum):
    cv = "cv"
    cover_letter = "cover_letter"


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    company: str
    url: str | None = None
    location: str | None = None
    portal: str | None = None
    external_id: str | None = None        # portal's own id/slug - needed to fetch full detail
    description: str = ""
    status: JobStatus = Field(default=JobStatus.scraped)

    fit_score: int | None = None          # 0-100
    fit_reasoning: str | None = None
    fit_flags: str | None = None          # JSON-encoded list of dealbreaker flags

    notes: str | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    applied_at: datetime | None = None


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    doc_type: DocType
    content_markdown: str = ""
    reviewer_notes: str | None = None      # critique from the reviewer pass
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Settings(SQLModel, table=True):
    """Single-row key/value settings table (profile text, model choice, etc.)."""
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str = ""
