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


# --- Extraction (fixed schema the vision LLM must fill) ---------------------

OfferType = Literal["trial", "contract", "registration", "academy", "unknown"]
ContractStatus = Literal["under_contract", "contract_ended", "never_professional", "unknown"]


class Inconsistency(BaseModel):
    description: str
    quote: str


class ExtractedOffer(BaseModel):
    transcribed_text: str = Field(
        default="", description="All readable text of the offer, verbatim, line by line."
    )
    club_name: Optional[str] = None
    club_country: Optional[str] = None
    league: Optional[str] = None
    agent_name: Optional[str] = None
    agent_phone: Optional[str] = None
    agent_licence_number: Optional[str] = None
    agent_claimed_country: Optional[str] = None
    signatory_name: Optional[str] = None
    signatory_role: Optional[str] = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    websites: list[str] = Field(default_factory=list)
    fee_requested: bool = False
    fee_amount: Optional[str] = None
    fee_purpose: Optional[str] = None
    payment_accounts: list[str] = Field(
        default_factory=list,
        description="Mobile-money numbers, till/paybill numbers or bank accounts money should go to.",
    )
    offer_type: OfferType = "unknown"
    registration_or_start_date: Optional[str] = Field(
        default=None, description="ISO date (YYYY-MM-DD) the player is to be registered or start."
    )
    letter_date: Optional[str] = Field(default=None, description="ISO date (YYYY-MM-DD).")
    player_contract_status: ContractStatus = "unknown"
    promises: list[str] = Field(
        default_factory=list, description="Visa, travel, accommodation or salary promises."
    )
    inconsistencies: list[Inconsistency] = Field(
        default_factory=list,
        description="Dates, visa or travel claims that conflict or look too good, with the quoted line.",
    )


# --- Findings (what the checks return; the report is built only from these) ---

FindingStatus = Literal["consistent", "not_confirmed", "contradicted"]
Strength = Literal["strong", "medium", "weak"]


class Finding(BaseModel):
    check: str
    status: FindingStatus
    evidence: str
    source: Optional[str] = None


class RedFlag(BaseModel):
    label: str
    description: str
    quote: Optional[str] = None
    strength: Strength = "strong"
    origin: str = "rules"


class AgentFinding(BaseModel):
    queried_name: Optional[str] = None
    status: MatchStatus
    message: str
    matched_name: Optional[str] = None
    matched_country: Optional[str] = None
    licence_status: Optional[str] = None


class CaseLink(BaseModel):
    case_id: int
    reason: str
    simulated: bool = False


class CaseFindings(BaseModel):
    agent: Optional[AgentFinding] = None
    claims: list[Finding] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    links: list[CaseLink] = Field(default_factory=list)
    club_contact: Optional[str] = None
    notes: list[str] = Field(default_factory=list)
