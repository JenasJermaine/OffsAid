"""Step 2: document claim checks.

DocumentFields is the fixed schema the vision model fills from an uploaded offer letter,
contract or screenshot. Every field carries the exact quote it came from, so each check
can show the player the line it is talking about. The check runs on the document alone;
the agent check (Step 1) is a separate endpoint.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel, Field, create_model

CheckStatus = Literal["consistent", "not_confirmed", "contradicted"]
Strength = Literal["strong", "medium", "weak"]
ExtractionMethod = Literal["vision_model", "patterns_only"]

DOCUMENT_DISCLAIMER = (
    "We check what a document claims against public sources and flag inconsistencies. "
    "This is not proof that the offer is real or fake. 'Not confirmed' does not mean fake. "
    "The most reliable check is to contact the club through the contact details on its "
    "official website."
)


# --- What the vision model extracts ------------------------------------------------


class Quoted(BaseModel):
    value: str = Field(description="The value, normalised (ISO dates as YYYY-MM-DD).")
    quote: str = Field(description="The exact line or phrase from the document it came from.")


class FeeRequest(BaseModel):
    amount: Optional[str] = Field(default=None, description="Number only, e.g. '85000'.")
    currency: Optional[str] = Field(default=None, description="ISO code if clear: KES, USD, EUR...")
    purpose: Optional[str] = Field(
        default=None, description="What the money is said to be for: visa, registration, kit..."
    )
    payment_method: Literal[
        "mobile_money", "bank_transfer", "money_transfer_service", "crypto", "cash", "card_or_link", "unknown"
    ] = "unknown"
    payee_type: Literal["club", "individual", "company", "unknown"] = Field(
        default="unknown",
        description="'individual' when the money goes to a named person or a personal phone number.",
    )
    payee_name: Optional[str] = None
    account_or_number: Optional[str] = Field(
        default=None, description="Mobile-money number, paybill/till, bank account or wallet."
    )
    deadline: Optional[str] = Field(default=None, description="ISO date, or the words used.")
    refundable_claim: bool = False
    quote: str = Field(description="The exact sentence asking for the money.")


class Inconsistency(BaseModel):
    description: str
    quote: str


class DocumentFields(BaseModel):
    transcribed_text: str = Field(
        default="", description="All readable text of the document, verbatim, line by line."
    )
    document_type: Literal[
        "offer_letter", "trial_invitation", "contract", "pre_contract", "agency_letter",
        "chat_screenshot", "visa_or_travel", "other",
    ] = "other"
    offer_type: Literal["trial", "contract", "registration", "academy", "unknown"] = "unknown"
    letter_date: Optional[Quoted] = None

    club_name: Optional[Quoted] = None
    club_country: Optional[Quoted] = None
    league: Optional[Quoted] = None

    sender_name: Optional[Quoted] = None
    sender_email: Optional[Quoted] = Field(
        default=None, description="The address the letter says it is from or asks replies to."
    )
    contact_phones: List[Quoted] = Field(
        default_factory=list, description="Phone and WhatsApp numbers given as the club's contact."
    )
    websites: List[Quoted] = Field(default_factory=list)
    signatory_name: Optional[Quoted] = None
    signatory_role: Optional[Quoted] = None

    # Extracted for the record only. The agent check is a separate step.
    agent_name: Optional[Quoted] = None
    agent_phone: Optional[Quoted] = None
    agent_licence_number: Optional[Quoted] = None

    registration_or_start_date: Optional[Quoted] = Field(
        default=None,
        description="Date the player is to be registered, start the contract or start playing.",
    )
    trial_start_date: Optional[Quoted] = None
    trial_end_date: Optional[Quoted] = None
    arrival_date: Optional[Quoted] = None
    departure_date: Optional[Quoted] = None
    player_contract_status: Literal[
        "under_contract", "pre_contract", "contract_ended", "never_professional", "unknown"
    ] = Field(default="unknown", description="The player's current contract status as the document states it.")
    previous_contract_end_date: Optional[Quoted] = None

    fee_requests: List[FeeRequest] = Field(default_factory=list)
    promises: List[Quoted] = Field(
        default_factory=list,
        description="Visa, flights, accommodation, salary or guaranteed-place promises.",
    )
    inconsistencies: List[Inconsistency] = Field(
        default_factory=list,
        description="Dates, visa, travel or contract claims that conflict or look too good, quoted.",
    )


def _is_optional(annotation) -> bool:
    return get_origin(annotation) is Union and type(None) in get_args(annotation)


def _required_form(model: type, swap: dict) -> type:
    """A copy of `model` for the vision model: every field required and nothing nullable.

    Structured outputs limit a schema to 24 optional and 16 nullable parameters, and
    DocumentFields has more. In the form, a value the document does not state is an empty
    string (or a Quoted with an empty value); from_form() turns those back into None.
    """
    def convert(annotation):
        if _is_optional(annotation):
            annotation = next(arg for arg in get_args(annotation) if arg is not type(None))
        if annotation in swap:
            return swap[annotation]
        origin, args = get_origin(annotation), get_args(annotation)
        if origin in (list, List) and args:
            return List[convert(args[0])]
        return annotation

    fields = {
        name: (convert(info.annotation), Field(..., description=info.description))
        for name, info in model.model_fields.items()
    }
    return create_model(model.__name__ + "Form", **fields)


def _blank(value) -> bool:
    return value == "" or (isinstance(value, dict) and set(value) == {"value", "quote"} and not str(value["value"]).strip())


def _restore(model: type, data: dict) -> dict:
    out = {}
    for name, info in model.model_fields.items():
        value = data.get(name)
        if _is_optional(info.annotation) and _blank(value):
            value = None
        elif isinstance(value, list):
            value = [item for item in value if not _blank(item)]
            if name == "fee_requests":
                value = [_restore(FeeRequest, item) for item in value]
        out[name] = value
    return out


def from_form(form: BaseModel) -> DocumentFields:
    return DocumentFields.model_validate(_restore(DocumentFields, form.model_dump()))


def to_form(fields: DocumentFields) -> dict:
    """The reverse of from_form: DocumentFields as the vision model returns it."""
    def fill(model: type, data: dict) -> dict:
        out = {}
        for name, info in model.model_fields.items():
            value = data.get(name)
            if value is None:
                inner = next((a for a in get_args(info.annotation) if a is not type(None)), str)
                value = {"value": "", "quote": ""} if inner is Quoted else ""
            elif name == "fee_requests":
                value = [fill(FeeRequest, item) for item in value]
            out[name] = value
        return out

    return fill(DocumentFields, fields.model_dump())


FeeRequestForm = _required_form(FeeRequest, {})
DocumentFieldsForm = _required_form(DocumentFields, {FeeRequest: FeeRequestForm})


# --- What the checks return ----------------------------------------------------------


class Check(BaseModel):
    check: str
    status: CheckStatus
    strength: Strength
    red_flag: bool = False
    note: str
    quotes: List[str] = Field(default_factory=list, description="Exact lines from the document.")
    sources: List[str] = Field(default_factory=list, description="Links to the evidence used.")


class PageInfo(BaseModel):
    pages: int
    has_text_layer: bool
    metadata: dict = Field(default_factory=dict)


class LinkingKeys(BaseModel):
    """Values the cross-case linking step will store and compare. Hashed, never raw."""

    file_sha256: str
    page_phashes: List[str] = Field(default_factory=list)
    email_hashes: List[str] = Field(default_factory=list)
    phone_hashes: List[str] = Field(default_factory=list)
    payment_account_hashes: List[str] = Field(default_factory=list)


class DocumentCheckResponse(BaseModel):
    extraction_method: ExtractionMethod
    extraction_notes: List[str] = Field(default_factory=list)
    file: PageInfo
    fields: DocumentFields
    checks: List[Check]
    summary: dict
    needs_review: bool
    league_window_missing: bool = Field(
        default=False, description="The club's league has no row in the window table; the web agent looks it up."
    )
    pending_checks: List[str] = Field(
        default_factory=list, description="Checks that wait on the web-checking agent (Step 3)."
    )
    linking_keys: LinkingKeys
    disclaimer: str = DOCUMENT_DISCLAIMER
