from __future__ import annotations

import hmac
import os
from typing import Any

import modal
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

app = modal.App("offsaid-scam-model")
model_volume = modal.Volume.from_name("offsaid-model")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.7.1+cpu",
        "transformers>=4.40",
        "sentencepiece",
        "fastapi",
        extra_index_url="https://download.pytorch.org/whl/cpu",
    )
    .add_local_file("app/scam_language.py", "/root/offsaid_scam_language.py")
)

_checker = None


class ClassificationRequest(BaseModel):
    messages: list[dict[str, Any]]


def _get_checker():
    global _checker
    if _checker is None:
        from offsaid_scam_language import ScamLanguageChecker

        model_dir = "/model/model/offsaid_classifier_v4"
        _checker = ScamLanguageChecker.load(model_dir)
        _checker.classify([{"sender": "agent", "text": "warmup"}])
    return _checker


def _authorize(request: Request) -> None:
    expected = os.environ.get("SCAM_MODEL_API_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "SCAM_MODEL_API_TOKEN is missing from the Modal Secret "
                "'offsaid-scam-api'."
            ),
        )
    scheme, separator, token = supplied.partition(" ")
    if not supplied:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is missing; expected 'Bearer <token>'.",
        )
    if scheme.lower() != "bearer" or not separator or not token:
        raise HTTPException(
            status_code=401,
            detail="Malformed Authorization header; expected 'Bearer <token>'.",
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=401,
            detail=(
                "Bearer token did not match. Check that the caller uses the same "
                "SCAM_MODEL_API_TOKEN as the Modal Secret. After changing that "
                "Secret, redeploy the Modal app and retry."
            ),
        )


web = FastAPI(title="OffsAid scam-language classifier")


@web.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@web.post("/classify")
def classify(body: ClassificationRequest, request: Request) -> dict:
    _authorize(request)
    if len(body.messages) > 100:
        raise HTTPException(status_code=413, detail="At most 100 messages can be checked at once.")
    for message in body.messages:
        text = message.get("text", "")
        if not isinstance(text, str) or len(text) > 20_000:
            raise HTTPException(status_code=413, detail="Each message must be text of at most 20,000 characters.")
    return _get_checker().check_chat(body.messages)


@app.function(
    image=image,
    volumes={"/model": model_volume},
    secrets=[modal.Secret.from_name("offsaid-scam-api")],
    cpu=2.0,
    memory=4096,
    timeout=180,
)
@modal.asgi_app()
def serve():
    return web
