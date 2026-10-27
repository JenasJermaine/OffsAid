from typing import Literal

from pydantic import BaseModel, Field, model_validator

MatchStatus = Literal["found", "possible_match", "not_found"]

DISCLAIMER = (
    "A licensed agent can still make a false claim. "
    "'Licensed' never means the offer is real."
)


class AgentCheckRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    licence_number: str | None = None
    claimed_country: str | None = None

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
    score: int | None = None


class AgentCheckResponse(BaseModel):
    status: MatchStatus
    message: str
    match: MatchedAgent | None = None
    disclaimer: str = Field(default=DISCLAIMER)
