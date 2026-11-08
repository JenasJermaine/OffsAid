from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.document_checks import check_document
from app.document_extraction import MAX_UPLOAD_BYTES, UnsafeDocument, UnsupportedDocument
from app.document_schemas import DocumentCheckResponse, DocumentFields
from app.agent_check import _log_lookup, run_agent_check  # noqa: F401  _log_lookup: still importable from here
from app.schemas import AgentCheckRequest, AgentCheckResponse
from app.report import wording_report
from app.scam_runtime import check_screenshot, get_scam_checker
from app.seed import seed_if_empty
from app.web_check_jobs import create_job, get_job, run_job
from app.admin import router as admin_router
from app.demo_chat import router as demo_router
from app.twilio_webhook import router as twilio_router
from app.whatsapp_webhook import router as whatsapp_router

log = logging.getLogger(__name__)

IMAGE_TYPES = {"image/png", "image/jpeg"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        seed_if_empty(db)
    finally:
        db.close()
    _app.state.scam_checker = get_scam_checker()
    yield


app = FastAPI(
    title="OffsAid",
    description=(
        "Step 1: licensed-agent lookup. Licensed never means the offer is real. "
        "Step 2: document claim checks. Not confirmed never means fake. "
        "Step 3: web-checking agent, with a source link for every confirmation. "
        "Wording check: patterns in the other party's messages, with the quoted line. "
        "Not a conclusion about the person."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(whatsapp_router)
app.include_router(twilio_router)
app.include_router(admin_router)
app.include_router(demo_router)


STATIC = Path(__file__).parent / "static"
# Only the landing page's images are served as plain files; the other pages have their own routes.
app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    """The public landing page: what OffsAid does, and how to reach it (WhatsApp, or the web demo)."""
    return FileResponse(STATIC / "landing.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC / "assets" / "favicon.png", media_type="image/png")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/agent-check", response_model=AgentCheckResponse)
def agent_check(
    payload: AgentCheckRequest, db: Session = Depends(get_db)
) -> AgentCheckResponse:
    return run_agent_check(db, payload)


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


class WordingMessage(BaseModel):
    sender: str = "agent"
    text: str = ""
    type: Optional[str] = None
    quoted: bool = False


class WordingMessagesIn(BaseModel):
    messages: list[WordingMessage]


class WordingFlag(BaseModel):
    label: str
    quote: str
    score: float = Field(
        description=(
            "Uncalibrated model score. When source is rule, 0.5 is a placeholder, "
            "not a probability, and must not be shown as a confidence percentage."
        )
    )
    source: str
    meaning: str


class WordingResult(BaseModel):
    flags: list[WordingFlag]
    messages_checked: int
    mode: str


class ScreenshotWordingResult(WordingResult):
    agent_messages: list[WordingMessage]
    app: str
    unavailable: bool = False
    note: Optional[str] = None
    report_text: str


def _log_wording(result: dict) -> None:
    log.info(
        "scam-language messages mode=%s messages_checked=%s labels=%s",
        result.get("mode"),
        result.get("messages_checked"),
        [flag["label"] for flag in result.get("flags", [])],
    )


@app.post("/v1/scam-language/messages", response_model=WordingResult, tags=["scam-language"])
async def scam_language_messages(body: WordingMessagesIn) -> dict:
    """Already-extracted messages in, wording flags out.

    The player's own messages are dropped. Used by tests and by teammates' code.
    """
    checker = app.state.scam_checker
    messages = [message.model_dump() for message in body.messages]
    result = await run_in_threadpool(checker.check_chat, messages)
    _log_wording(result)
    return result


@app.post("/v1/scam-language/screenshot", response_model=ScreenshotWordingResult, tags=["scam-language"])
async def scam_language_screenshot(screenshot: UploadFile = File(...)) -> dict:
    """A chat screenshot in. Flags, plus the other party's messages that were read.

    Same 20 MiB limit as the document upload. PNG and JPEG only.
    """
    mime = (screenshot.content_type or "").split(";")[0].strip().lower()
    if mime not in IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only PNG and JPEG chat screenshots are supported.")
    content = await screenshot.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 20 MiB upload limit.")
    try:
        result = await run_in_threadpool(check_screenshot, content, mime)
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except UnsafeDocument as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    result["report_text"] = wording_report(result)
    return result
