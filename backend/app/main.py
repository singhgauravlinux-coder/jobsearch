from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import backup as backup_mod
from app import gemini_client, scraper
from app.db import get_session, init_db
from app.models import DocType, Document, Job, JobStatus, Settings
from app.profile_loader import load_repo_profile, repo_mounted

app = FastAPI(title="AI Job Search Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------------------------------------------------------- settings --

def _get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.exec(select(Settings).where(Settings.key == key)).first()
    return row.value if row else default


def _set_setting(session: Session, key: str, value: str) -> None:
    row = session.exec(select(Settings).where(Settings.key == key)).first()
    if row:
        row.value = value
        session.add(row)
    else:
        session.add(Settings(key=key, value=value))
    session.commit()


def current_profile(session: Session) -> str:
    """Prefer a manually-edited profile in Settings; fall back to the
    mounted repo's CLAUDE.md + methodology files."""
    manual = _get_setting(session, "profile")
    if manual.strip():
        return manual
    return load_repo_profile()


@app.get("/api/settings")
def get_settings(session: Session = Depends(get_session)):
    return {
        "profile": _get_setting(session, "profile"),
        "repo_mounted": repo_mounted(),
        "repo_profile_preview": load_repo_profile()[:2000],
        "model": gemini_client.MODEL,
    }


class SettingsIn(BaseModel):
    profile: str


@app.put("/api/settings")
def put_settings(body: SettingsIn, session: Session = Depends(get_session)):
    _set_setting(session, "profile", body.profile)
    return {"ok": True}


# ------------------------------------------------------------------ scrape --

class ScrapeIn(BaseModel):
    portal: str
    query: str
    limit: int = 20
    job_age_days: Optional[int] = None
    location: Optional[str] = None


@app.get("/api/portals")
def list_portals():
    return {"portals": scraper.SUPPORTED_PORTALS}


@app.post("/api/scrape")
def do_scrape(body: ScrapeIn, session: Session = Depends(get_session)):
    try:
        results, warnings = scraper.search(
            body.portal, body.query, body.limit, body.job_age_days, body.location
        )
    except scraper.ScrapeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    created, skipped = 0, 0
    for item in results:
        existing = None
        if item["url"]:
            existing = session.exec(select(Job).where(Job.url == item["url"])).first()
        if existing:
            skipped += 1
            continue
        job = Job(
            title=item["title"],
            company=item["company"],
            url=item["url"],
            location=item["location"],
            portal=item["portal"],
            external_id=item["external_id"],
            description=item["description"],
            status=JobStatus.scraped,
        )
        session.add(job)
        created += 1
    session.commit()
    return {"created": created, "skipped_duplicates": skipped, "found": len(results), "warnings": warnings}


# -------------------------------------------------------------------- jobs --

@app.get("/api/jobs")
def list_jobs(status: Optional[str] = None, search: Optional[str] = None, session: Session = Depends(get_session)):
    q = select(Job)
    if status:
        q = q.where(Job.status == status)
    jobs = session.exec(q.order_by(Job.created_at.desc())).all()
    if search:
        s = search.lower()
        jobs = [j for j in jobs if s in j.title.lower() or s in j.company.lower()]
    return jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    docs = session.exec(select(Document).where(Document.job_id == job_id).order_by(Document.created_at.desc())).all()
    return {"job": job, "documents": docs}


@app.post("/api/jobs/{job_id}/fetch-description")
def fetch_description(job_id: int, session: Session = Depends(get_session)):
    """Scraped search results are list-view summaries only - this calls the
    portal's `detail` CLI to pull the full posting body."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.portal:
        raise HTTPException(400, "This job wasn't scraped from a portal, so there's no detail CLI to call.")
    try:
        description = scraper.fetch_description(job.portal, job.external_id, job.url)
    except scraper.ScrapeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    job.description = description
    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


class JobPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: int, body: JobPatch, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if body.status:
        job.status = JobStatus(body.status)
        if job.status == JobStatus.applied and not job.applied_at:
            job.applied_at = datetime.utcnow()
    if body.notes is not None:
        job.notes = body.notes
    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@app.delete("/api/jobs")
def delete_all_jobs(session: Session = Depends(get_session)):
    """Wipes every job and its drafted documents. Irreversible - the
    frontend should confirm before calling this."""
    jobs = session.exec(select(Job)).all()
    count = len(jobs)
    for doc in session.exec(select(Document)).all():
        session.delete(doc)
    for job in jobs:
        session.delete(job)
    session.commit()
    return {"ok": True, "deleted": count}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    for doc in session.exec(select(Document).where(Document.job_id == job_id)).all():
        session.delete(doc)
    session.delete(job)
    session.commit()
    return {"ok": True}


class ManualJobIn(BaseModel):
    title: str
    company: str
    description: str
    url: Optional[str] = None
    location: Optional[str] = None


@app.post("/api/jobs")
def create_job(body: ManualJobIn, session: Session = Depends(get_session)):
    job = Job(title=body.title, company=body.company, description=body.description, url=body.url, location=body.location)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# -------------------------------------------------------------- evaluate --

class EvaluateIn(BaseModel):
    experience_note: Optional[str] = None


@app.post("/api/jobs/{job_id}/evaluate")
def evaluate_job(job_id: int, body: EvaluateIn = EvaluateIn(), session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    profile = current_profile(session)
    if body.experience_note:
        profile = (
            f"{profile}\n\n--- ADDITIONAL EXPERIENCE FOR THIS EVALUATION ---\n{body.experience_note}"
        )
    try:
        result = gemini_client.evaluate_fit(profile, job.title, job.company, job.description)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    job.fit_score = result.score
    job.fit_reasoning = result.reasoning
    job.fit_flags = json.dumps(result.flags)
    job.status = JobStatus.evaluated
    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# ----------------------------------------------------------------- draft --

@app.post("/api/jobs/{job_id}/draft")
def draft_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    profile = current_profile(session)
    try:
        result = gemini_client.draft_and_review(profile, job.title, job.company, job.description)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    cv_doc = Document(job_id=job.id, doc_type=DocType.cv, content_markdown=result["cv_markdown"], reviewer_notes=result["reviewer_notes"])
    cl_doc = Document(job_id=job.id, doc_type=DocType.cover_letter, content_markdown=result["cover_letter_markdown"], reviewer_notes=result["reviewer_notes"])
    session.add(cv_doc)
    session.add(cl_doc)

    job.status = JobStatus.drafted
    job.updated_at = datetime.utcnow()
    session.add(job)
    session.commit()
    return {"cv": cv_doc, "cover_letter": cl_doc}


class DocPatch(BaseModel):
    content_markdown: str


@app.put("/api/documents/{doc_id}")
def update_document(doc_id: int, body: DocPatch, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.content_markdown = body.content_markdown
    doc.version += 1
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


# ------------------------------------------------------------- analytics --

@app.get("/api/analytics")
def analytics(session: Session = Depends(get_session)):
    jobs = session.exec(select(Job)).all()
    by_status = {s.value: 0 for s in JobStatus}
    for j in jobs:
        by_status[j.status.value] += 1

    applied = [j for j in jobs if j.applied_at]
    responded = [j for j in jobs if j.status in (JobStatus.interview, JobStatus.offer, JobStatus.rejected)]
    scored = [j for j in jobs if j.fit_score is not None]

    return {
        "total_jobs": len(jobs),
        "by_status": by_status,
        "applications_sent": len(applied),
        "response_rate": round(len(responded) / len(applied), 3) if applied else None,
        "avg_fit_score": round(sum(j.fit_score for j in scored) / len(scored), 1) if scored else None,
        "interviews": by_status["interview"],
        "offers": by_status["offer"],
    }


@app.get("/api/health")
def health():
    return {"ok": True, "repo_mounted": repo_mounted()}


# ------------------------------------------------------------------ export --

@app.get("/api/export")
def export_all(session: Session = Depends(get_session)):
    """Full JSON dump of everything in the DB — jobs, documents, settings.
    Meant as a human-readable backup/portability escape hatch alongside the
    raw sqlite file backup (see backup.sh), not a replacement for it."""
    jobs = session.exec(select(Job)).all()
    documents = session.exec(select(Document)).all()
    settings_rows = session.exec(select(Settings)).all()

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "jobs": [j.model_dump(mode="json") for j in jobs],
        "documents": [d.model_dump(mode="json") for d in documents],
        "settings": {s.key: s.value for s in settings_rows},
    }
    filename = f"ai-job-search-export-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------------------ backup --

class BackupIn(BaseModel):
    label: Optional[str] = None


@app.post("/api/backups")
def create_backup(body: BackupIn = BackupIn()):
    try:
        return backup_mod.create_backup(body.label)
    except backup_mod.BackupError as e:
        raise HTTPException(400, str(e))


@app.get("/api/backups")
def list_backups():
    return backup_mod.list_backups()


@app.get("/api/backups/{name}/download")
def download_backup(name: str):
    try:
        path = backup_mod.get_backup_path(name)
    except backup_mod.BackupError as e:
        raise HTTPException(404, str(e))
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.post("/api/backups/{name}/restore")
def restore_backup(name: str):
    try:
        backup_mod.restore_from_backup(name)
    except backup_mod.BackupError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "restored_from": name}


@app.delete("/api/backups/{name}")
def delete_backup(name: str):
    try:
        backup_mod.delete_backup(name)
    except backup_mod.BackupError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/api/backups/upload")
async def upload_backup(file: UploadFile = File(...)):
    """Uploads a .db file as a new stored backup (does NOT restore it —
    call /api/backups/{name}/restore afterwards to apply it)."""
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    name = f"jobsearch-{stamp}-upload.db"
    dest = backup_mod.BACKUP_DIR / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return backup_mod.describe_backup(dest)


@app.post("/api/restore-upload")
async def restore_upload(file: UploadFile = File(...)):
    """One-step restore: upload a .db file and apply it directly to the
    live database, without keeping it in the backups list."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        backup_mod.restore_from_path(tmp_path)
    except backup_mod.BackupError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"ok": True, "restored_from": file.filename}
