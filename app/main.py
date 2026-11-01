from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.document_schemas import VerificationPipelineResult
from app.document_verification import (
    MAX_UPLOAD_BYTES,
    UnsupportedDocument,
    UnsafeDocument,
    verify_document,
)
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
    title="OffsAid agent verification",
    description="Part 1: licensed-agent lookup. Licensed never means the offer is real.",
    version="0.1.0",
    lifespan=lifespan,
)


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


@app.post("/v1/verify-document", response_model=VerificationPipelineResult)
async def verify_document_route(
    document: UploadFile = File(...),
    name: Optional[str] = Form(default=None),
    phone: Optional[str] = Form(default=None),
    licence_number: Optional[str] = Form(default=None),
    claimed_country: Optional[str] = Form(default=None),
    case_id: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
) -> VerificationPipelineResult:
    """Run the existing agent check, then check claims in the uploaded document."""
    content = await document.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 20 MiB upload limit.")
    try:
        request = AgentCheckRequest(
            name=name,
            phone=phone,
            licence_number=licence_number,
            claimed_country=claimed_country,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    agents = db.query(LicensedAgent).all()
    outcome = verify_agent(
        AgentQuery(
            name=request.name,
            phone=request.phone,
            licence_number=request.licence_number,
            claimed_country=request.claimed_country,
        ),
        agents,
    )
    _log_lookup(db, request, outcome)
    agent_result = AgentCheckResponse(
        status=outcome.status,
        message=outcome.reason,
        match=_matched_payload(outcome),
        disclaimer=DISCLAIMER,
    )
    try:
        document_result = verify_document(
            content,
            document.content_type or "application/octet-stream",
            filename=document.filename or "upload",
            case_id=case_id,
            agent_check=agent_result,
        )
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except UnsafeDocument as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return VerificationPipelineResult(
        agent_check=agent_result,
        document_verification=document_result,
    )
