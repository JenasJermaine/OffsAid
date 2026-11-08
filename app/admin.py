"""KEFWA reviewer routes for cross-case linking (HTTP Basic: ADMIN_USERNAME / ADMIN_PASSWORD).

GET    /admin/review                  the KEFWA review page (app/static/kefwa.html)
GET    /admin/review-queue            open items: linking escalations and chat referrals, with the reasons,
                                      the findings the player was sent, their phone number, matching case ids
POST   /admin/cases/{case_id}/status  review outcome: set_status (a confirmed_scam status makes later
                                      cases sharing an identifier escalate); resolves the queue row
POST   /admin/review-queue/{id}/resolve  close a queue item (e.g. a referral with no stored case)
DELETE /admin/review-queue/{id}       erase a queue item, including the player's phone number
DELETE /admin/cases/{case_id}         erasure request: the case, its hashes, fingerprints and queue row

An empty ADMIN_PASSWORD disables these routes (404). They need Postgres (503 otherwise).
Case ids appear only here, never in a player's report.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from app import case_linking as cl
from app.config import settings
from app.db import is_postgres
from app.linking_step import linking_connection

router = APIRouter(prefix="/admin", tags=["kefwa-review"])
_basic = HTTPBasic(auto_error=False)


def require_reviewer(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    if not settings.admin_password:
        raise HTTPException(status_code=404, detail="Not found.")
    ok = credentials is not None and secrets.compare_digest(
        credentials.username.encode(), settings.admin_username.encode()
    ) and secrets.compare_digest(credentials.password.encode(), settings.admin_password.encode())
    if not ok:
        raise HTTPException(status_code=401, detail="Login required.", headers={"WWW-Authenticate": "Basic"})


def _connection():
    if not is_postgres():
        raise HTTPException(status_code=503, detail="Cross-case linking needs Postgres; DATABASE_URL is not Postgres.")
    return linking_connection()


class StatusUpdate(BaseModel):
    status: Literal["unreviewed", "reported", "confirmed_scam", "verified_genuine"]
    note: str = ""


class Resolve(BaseModel):
    note: str = ""


@router.get("/review", dependencies=[Depends(require_reviewer)], include_in_schema=False)
def review_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "kefwa.html")


@router.get("/review-queue", dependencies=[Depends(require_reviewer)])
def review_queue() -> list[dict]:
    with _connection() as conn:
        rows = conn.execute(
            """select q.id, q.case_id, q.created_at, q.reasons, q.linked_case_ids, q.origin,
                      q.player_contact, q.findings_text,
                      c.source, c.status, c.claimed_club, c.claimed_agent, c.summary
               from review_queue q left join cases c on c.id = q.case_id
               where q.status = 'open' order by q.created_at desc"""
        ).fetchall()
    keys = ("id", "case_id", "created_at", "reasons", "linked_case_ids", "origin", "player_contact",
            "findings_text", "source", "case_status", "claimed_club", "claimed_agent", "summary")
    return [dict(zip(keys, row)) for row in rows]


@router.post("/cases/{case_id}/status", dependencies=[Depends(require_reviewer)])
def set_case_status(case_id: int, body: StatusUpdate) -> dict:
    with _connection() as conn:
        if conn.execute("select 1 from cases where id = %s", (case_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="No such case.")
        cl.set_status(conn, case_id, body.status)
        if body.status != "unreviewed":
            conn.execute(
                "update review_queue set status = 'resolved', reviewer_note = %s, resolved_at = now() where case_id = %s",
                (body.note, case_id),
            )
    return {"case_id": case_id, "status": body.status}


@router.delete("/cases/{case_id}", dependencies=[Depends(require_reviewer)])
def erase_case(case_id: int) -> dict:
    with _connection() as conn:
        if conn.execute("select 1 from cases where id = %s", (case_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="No such case.")
        cl.delete_case(conn, case_id)
    return {"case_id": case_id, "deleted": True}


@router.post("/review-queue/{item_id}/resolve", dependencies=[Depends(require_reviewer)])
def resolve_item(item_id: int, body: Resolve) -> dict:
    with _connection() as conn:
        row = conn.execute(
            "update review_queue set status = 'resolved', reviewer_note = %s, resolved_at = now() where id = %s returning id",
            (body.note, item_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such item.")
    return {"id": item_id, "resolved": True}


@router.delete("/review-queue/{item_id}", dependencies=[Depends(require_reviewer)])
def erase_item(item_id: int) -> dict:
    with _connection() as conn:
        row = conn.execute("delete from review_queue where id = %s returning id", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such item.")
    return {"id": item_id, "deleted": True}
