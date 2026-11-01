from __future__ import annotations
from typing import Optional, Literal

from pydantic import BaseModel, Field, model_validator

MatchStatus = Literal["found", "possible_match", "not_found"]

DISCLAIMER = (
    "A licensed agent can still make a false claim. "
    "'Licensed' never means the offer is real."
)


class AgentCheckRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    licence_number: Optional[str] = None
    claimed_country: Optional[str] = None

    @model_validator(mode="after")
    def require_identifier(self) -> "AgentCheckRequest":
        if not any(
            value and value.strip()
            for value in (self.name, self.phone, self.licence_number)
        ):
            raise ValueError("Provide at least one of name, phone, or licence_number.")
        return self


class MatchedAgent(BaseModel):
    name: str
    country: str
    licence_number: str
    licence_status: str
    source: str
    score: Optional[int] = None


class AgentCheckResponse(BaseModel):
    status: MatchStatus
    message: str
    match: Optional[MatchedAgent] = None
    disclaimer: str = Field(default=DISCLAIMER)
