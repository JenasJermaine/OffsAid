from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
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


def _matched_payload(outcome) -> MatchedAgent | None:
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
