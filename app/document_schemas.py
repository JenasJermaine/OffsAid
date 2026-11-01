from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.schemas import AgentCheckResponse

DocumentCheckStatus = Literal["consistent", "not_confirmed", "contradicted"]
EvidenceStrength = Literal["strong", "medium", "weak"]
DocumentStatus = Literal[
    "no_major_inconsistency_found",
    "inconsistencies_found",
    "insufficient_evidence",
]


class ClaimValue(BaseModel):
    claim: str
    value: Optional[str] = None
    source_page: Optional[int] = None
    source_text: str
    confidence: float = Field(ge=0, le=1)


class ClubClaims(BaseModel):
    name: Optional[ClaimValue] = None
    country: Optional[ClaimValue] = None
    league: Optional[ClaimValue] = None


class SenderClaims(BaseModel):
    name: Optional[ClaimValue] = None
    email: Optional[ClaimValue] = None
    email_domain: Optional[str] = None
    phone: Optional[ClaimValue] = None
    whatsapp_number: Optional[ClaimValue] = None


class SignatoryClaims(BaseModel):
    name: Optional[ClaimValue] = None
    role: Optional[ClaimValue] = None


class AgentClaims(BaseModel):
    name: Optional[ClaimValue] = None
    phone: Optional[ClaimValue] = None
    email: Optional[ClaimValue] = None
    licence_number: Optional[ClaimValue] = None


class PaymentClaims(BaseModel):
    amount: Optional[ClaimValue] = None
    currency: Optional[ClaimValue] = None
    reason: Optional[ClaimValue] = None
    deadline: Optional[ClaimValue] = None
    recipient: Optional[ClaimValue] = None
    bank_account: Optional[ClaimValue] = None
    mobile_money_number: Optional[ClaimValue] = None
    payment_method: Optional[ClaimValue] = None
    source_page: int
    source_text: str


class TrialClaims(BaseModel):
    trial_date: Optional[ClaimValue] = None
    location: Optional[ClaimValue] = None
    arrival_date: Optional[ClaimValue] = None
    departure_date: Optional[ClaimValue] = None
    accommodation: Optional[ClaimValue] = None
    travel_arrangements: Optional[ClaimValue] = None


class ContractClaims(BaseModel):
    contract_date: Optional[ClaimValue] = None
    proposed_start_date: Optional[ClaimValue] = None
    registration_date: Optional[ClaimValue] = None
    duration: Optional[ClaimValue] = None
    salary: Optional[ClaimValue] = None
    benefits: Optional[ClaimValue] = None
    player_status: Optional[ClaimValue] = None
    previous_contract_status: Optional[ClaimValue] = None
    previous_contract_end: Optional[ClaimValue] = None


class DocumentExtraction(BaseModel):
    club: ClubClaims = Field(default_factory=ClubClaims)
    sender: SenderClaims = Field(default_factory=SenderClaims)
    signatory: SignatoryClaims = Field(default_factory=SignatoryClaims)
    agent: AgentClaims = Field(default_factory=AgentClaims)
    payments: list[PaymentClaims] = Field(default_factory=list)
    trial: TrialClaims = Field(default_factory=TrialClaims)
    contract: ContractClaims = Field(default_factory=ContractClaims)
    visa_claims: list[ClaimValue] = Field(default_factory=list)
    other_claims: list[ClaimValue] = Field(default_factory=list)
    page_count: int = 0
    text_available: bool = False
    metadata: dict[str, Optional[Union[str, int]]] = Field(default_factory=dict)
    synthetic: bool = False
    source: Optional[str] = None


class CheckEvidence(BaseModel):
    page: Optional[int] = None
    text: str
    source: Optional[str] = None


class DocumentCheck(BaseModel):
    check_name: str
    status: DocumentCheckStatus
    strength: EvidenceStrength
    claim: str
    evidence: list[CheckEvidence] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    explanation: str
    checked_at: datetime


class SimilarDocument(BaseModel):
    document_id: str
    similarity: float
    synthetic: bool = False


class SimilarityResult(BaseModel):
    match_found: bool
    similar_documents: list[SimilarDocument] = Field(default_factory=list)
    similarity_score: Optional[float] = None
    evidence: str


class DocumentVerificationResult(BaseModel):
    document_id: str
    case_id: Optional[str] = None
    agent_context: Optional[AgentCheckResponse] = None
    extraction: DocumentExtraction
    checks: list[DocumentCheck]
    summary: dict[DocumentCheckStatus, int]
    document_status: DocumentStatus
    evidence_summary: list[CheckEvidence] = Field(default_factory=list)
    similarity: SimilarityResult
    synthetic: bool = False
    source: Optional[str] = None


class VerificationPipelineResult(BaseModel):
    agent_check: AgentCheckResponse
    document_verification: DocumentVerificationResult