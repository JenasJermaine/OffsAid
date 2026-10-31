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
    club_name: str | None = None
    club_country: str | None = None
    league: str | None = None
    agent_name: str | None = None
    agent_phone: str | None = None
    agent_licence_number: str | None = None
    agent_claimed_country: str | None = None
    signatory_name: str | None = None
    signatory_role: str | None = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    websites: list[str] = Field(default_factory=list)
    fee_requested: bool = False
    fee_amount: str | None = None
    fee_purpose: str | None = None
    payment_accounts: list[str] = Field(
        default_factory=list,
        description="Mobile-money numbers, till/paybill numbers or bank accounts money should go to.",
    )
    offer_type: OfferType = "unknown"
    registration_or_start_date: str | None = Field(
        default=None, description="ISO date (YYYY-MM-DD) the player is to be registered or start."
    )
    letter_date: str | None = Field(default=None, description="ISO date (YYYY-MM-DD).")
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
    source: str | None = None


class RedFlag(BaseModel):
    label: str
    description: str
    quote: str | None = None
    strength: Strength = "strong"
    origin: str = "rules"


class AgentFinding(BaseModel):
    queried_name: str | None = None
    status: MatchStatus
    message: str
    matched_name: str | None = None
    matched_country: str | None = None
    licence_status: str | None = None


class CaseLink(BaseModel):
    case_id: int
    reason: str
    simulated: bool = False


class CaseFindings(BaseModel):
    agent: AgentFinding | None = None
    claims: list[Finding] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    links: list[CaseLink] = Field(default_factory=list)
    club_contact: str | None = None
    notes: list[str] = Field(default_factory=list)
