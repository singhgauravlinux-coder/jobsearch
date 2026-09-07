import os
from pathlib import Path

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobsearch.db"

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def _migrate_add_missing_columns() -> None:
    """SQLModel.metadata.create_all only creates missing tables, not missing
    columns on tables that already exist - so a schema change like adding
    Job.external_id needs an explicit, additive ALTER TABLE here. Safe to
    run on every startup: checks what's already there first."""
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(job)"))}
        if existing and "external_id" not in existing:
            conn.execute(text("ALTER TABLE job ADD COLUMN external_id VARCHAR"))
            conn.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate_add_missing_columns()


def get_session():
    with Session(engine) as session:
        yield session
