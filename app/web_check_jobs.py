"""Run the web-checking agent as a background job and keep its result and audit log.

The WhatsApp webhook and /v1/web-check both answer at once and start the agent here, so a
check that takes a minute never holds up Meta's webhook call.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.document_schemas import DocumentFields
from app.models import WebCheck, WebCheckLog
from app.web_agent import WebCheckResult, run_web_check

log = logging.getLogger(__name__)


def create_job(db: Session, player_hash: Optional[str] = None) -> str:
    job = WebCheck(id=str(uuid.uuid4()), status="running", player_hash=player_hash)
    db.add(job)
    db.commit()
    return job.id


def run_job(job_id: str, fields: DocumentFields, window_missing: bool = False, **agent_options) -> WebCheckResult:
    db = SessionLocal()
    try:
        def on_log(tool: str, detail: str) -> None:
            db.add(WebCheckLog(web_check_id=job_id, tool=tool, detail=detail))
            db.commit()

        try:
            result = run_web_check(fields, window_missing=window_missing, on_log=on_log, **agent_options)
        except Exception:
            log.exception("Web check %s crashed", job_id)
            result = WebCheckResult(status="failed", notes=["The web check failed unexpectedly."])
        job = db.get(WebCheck, job_id)
        if job is not None:
            job.status = result.status
            job.result = result.model_dump(exclude={"log"}, mode="json")
            db.commit()
        return result
    finally:
        db.close()


def get_job(db: Session, job_id: str) -> Optional[dict]:
    job = db.get(WebCheck, job_id)
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "result": job.result,
        "log": [{"tool": entry.tool, "detail": entry.detail, "at": entry.created_at.isoformat() if entry.created_at else None}
                for entry in job.logs],
    }
