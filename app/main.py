from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.document_checks import check_document
from app.document_extraction import MAX_UPLOAD_BYTES, UnsafeDocument, UnsupportedDocument
from app.document_schemas import DocumentCheckResponse, DocumentFields
from app.hashing import hash_lookup_value
from app.matching import AgentQuery, verify_agent
from app.models import LicensedAgent, LookupLog
from app.normalize import normalize_licence, normalize_name, normalize_phone
from app.schemas import (
    DISCLAIMER,
    AgentCheckRequest,
    AgentCheckResponse,
    MatchedAgent,
)
from app.seed import seed_if_empty
from app.web_check_jobs import create_job, get_job, run_job
from app.whatsapp_webhook import router as whatsapp_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed_if_empty(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="OffsAid",
    description=(
        "Step 1: licensed-agent lookup. Licensed never means the offer is real. "
        "Step 2: document claim checks. Not confirmed never means fake. "
        "Step 3: web-checking agent, with a source link for every confirmation."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(whatsapp_router)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


def _matched_payload(outcome) -> Optional[MatchedAgent]:
    if outcome.agent is None:
        return None
    agent = outcome.agent
    return MatchedAgent(
        name=agent.name,
        country=agent.country,
        licence_number=agent.licence_number,
        licence_status=agent.licence_status,
        source=agent.source,
        score=outcome.score,
    )


def _log_lookup(db: Session, request: AgentCheckRequest, outcome) -> None:
    if request.phone and request.phone.strip():
        kind, value = "phone", normalize_phone(request.phone)
    elif request.licence_number and request.licence_number.strip():
        kind, value = "licence", normalize_licence(request.licence_number)
    else:
        kind, value = "name", normalize_name(request.name)
    db.add(
        LookupLog(
            query_hash=hash_lookup_value(value),
            query_kind=kind,
            result_status=outcome.status,
            score=outcome.score,
            agent_id=outcome.agent.id if outcome.agent is not None else None,
        )
    )
    db.commit()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agent-check", response_model=AgentCheckResponse)
def agent_check(
    payload: AgentCheckRequest, db: Session = Depends(get_db)
) -> AgentCheckResponse:
    agents = db.query(LicensedAgent).all()
    query = AgentQuery(
        name=payload.name,
        phone=payload.phone,
        licence_number=payload.licence_number,
        claimed_country=payload.claimed_country,
    )
    outcome = verify_agent(query, agents)
    _log_lookup(db, payload, outcome)
    return AgentCheckResponse(
        status=outcome.status,
        message=outcome.reason,
        match=_matched_payload(outcome),
        disclaimer=DISCLAIMER,
    )


@app.post("/v1/document-check", response_model=DocumentCheckResponse)
async def document_check(document: UploadFile = File(...)) -> DocumentCheckResponse:
    """Check what an uploaded offer letter, contract or screenshot claims.

    Takes the document only. The agent check is a separate endpoint (/v1/agent-check).
    """
    content = await document.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 20 MiB upload limit.")
    try:
        return await run_in_threadpool(check_document, content, document.content_type or "")
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except UnsafeDocument as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


class WebCheckRequest(BaseModel):
    fields: DocumentFields
    window_missing: bool = False


@app.post("/v1/web-check", status_code=202)
def start_web_check(body: WebCheckRequest, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    """Start the web-checking agent on the fields from /v1/document-check. Poll GET /v1/web-checks/{id}."""
    job_id = create_job(db)
    background.add_task(run_job, job_id, body.fields, body.window_missing)
    return {"id": job_id, "status": "running"}


@app.get("/v1/web-checks/{job_id}")
def web_check_result(job_id: str, db: Session = Depends(get_db)) -> dict:
    """The agent's result and every search, page and lookup it made."""
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such web check.")
    return job
