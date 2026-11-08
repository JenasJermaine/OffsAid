"""Step 2, part 1: read an uploaded document and extract its claims into DocumentFields.

The main path is one call to a vision-capable model with the fixed DocumentFields schema.
Every quote it returns is checked against the document text, and fields whose quote is
not in the document are dropped. Without an API key, a pattern fallback extracts only
what can be found reliably in free text (emails, phone numbers, websites, requests for
money) and leaves the rest empty; the checks then report "not confirmed".
"""

from __future__ import annotations

import base64
import io
import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import fitz
import imagehash
import phonenumbers
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.document_schemas import DocumentFields, DocumentFieldsForm, ExtractionMethod, FeeRequest, Quoted, from_form

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 20
MAX_IMAGE_PIXELS = 40_000_000
SUPPORTED_TYPES = {"application/pdf", "image/png", "image/jpeg"}
METADATA_KEYS = {"author", "creator", "producer", "creationDate", "modDate", "title"}


class UnsupportedDocument(ValueError):
    pass


class UnsafeDocument(ValueError):
    pass


@dataclass
class LoadedDocument:
    data: bytes
    mime_type: str
    page_texts: List[str]
    has_text_layer: bool
    metadata: dict = field(default_factory=dict)
    page_phashes: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.page_texts)


# --- Reading the file -----------------------------------------------------------------


def load_document(data: bytes, mime_type: str) -> LoadedDocument:
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsafeDocument("Document exceeds the 20 MiB upload limit.")
    if mime_type not in SUPPORTED_TYPES:
        raise UnsupportedDocument("Only PDF, PNG and JPEG documents are supported.")
    if mime_type == "application/pdf":
        return _load_pdf(data)
    return _load_image(data, mime_type)


def _load_pdf(data: bytes) -> LoadedDocument:
    if not data.startswith(b"%PDF-"):
        raise UnsupportedDocument("File content does not match a PDF document.")
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnsupportedDocument("PDF could not be read.") from exc
    with pdf:
        if pdf.is_encrypted:
            raise UnsafeDocument("Password-protected PDFs are not supported.")
        if pdf.page_count > MAX_PAGES:
            raise UnsafeDocument(f"Documents may contain at most {MAX_PAGES} pages.")
        texts, phashes = [], []
        for page in pdf:
            # NFKC turns typographic ligatures back into letters: "academy@kups.\ufb01" -> "academy@kups.fi".
            texts.append(unicodedata.normalize("NFKC", page.get_text("text"))[:100_000])
            phashes.append(str(imagehash.phash(_render(page, 0.5))))
        has_text = any(text.strip() for text in texts)
        if not has_text:
            # A scanned letter: no text layer. OCR it if Tesseract is installed.
            texts = [_ocr(_render(page, 2.0)) for page in pdf]
        metadata = {
            key: value[:300] for key, value in (pdf.metadata or {}).items()
            if key in METADATA_KEYS and isinstance(value, str) and value
        }
    return LoadedDocument(data, "application/pdf", texts, has_text, metadata, phashes)


def _load_image(data: bytes, mime_type: str) -> LoadedDocument:
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in {"PNG", "JPEG"}:
            raise UnsupportedDocument("Image content does not match PNG or JPEG.")
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise UnsafeDocument("Image dimensions exceed the 40-megapixel limit.")
        image.load()
    except Image.DecompressionBombError as exc:
        raise UnsafeDocument("Image dimensions exceed the safe decoding limit.") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedDocument("Image could not be read.") from exc
    metadata = {
        str(key)[:50]: str(value)[:300] for key, value in image.info.items()
        if isinstance(value, (str, int)) and key in {"Software", "Creation Time", "Author", "software"}
    }
    return LoadedDocument(data, mime_type, [_ocr(image)], False, metadata, [str(imagehash.phash(image))])


def first_page_image(data: bytes, mime_type: str) -> Image.Image:
    """Page 1 as an image, in memory only (the letter fingerprint for cross-case linking).
    Call it after load_document has accepted the file; nothing is written to disk."""
    if mime_type == "application/pdf":
        with fitz.open(stream=data, filetype="pdf") as pdf:
            return _render(pdf[0], 1.0)
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _render(page: fitz.Page, zoom: float) -> Image.Image:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _ocr(image: Image.Image) -> str:
    if shutil.which("tesseract") is None:
        return ""
    try:
        import pytesseract

        return pytesseract.image_to_string(image)[:100_000]
    except Exception:
        log.warning("OCR failed", exc_info=True)
        return ""


# --- Extraction -----------------------------------------------------------------------


EXTRACTION_SYSTEM = """You read football recruitment documents that a player has forwarded \
(offer letters, trial invitations, contracts, agency letters, chat screenshots) and fill \
in a fixed form with the claims the document makes.

Rules:
- Extract only what the document says. Leave a field empty when the document does not \
state it. Do not guess the club's country or league from your own knowledge.
- Every field must be present. When the document does not state something, use an empty \
string, or {"value": "", "quote": ""} for fields with a quote.
- For every filled field, copy into `quote` the exact words from the document, \
character for character, and put the normalised value in `value` (dates as YYYY-MM-DD).
- `transcribed_text` is all readable text in the document, verbatim, line by line.
- `fee_requests`: every request for the player (or family) to pay money, for any reason \
(registration, visa, ticket, insurance, kit, medical, clearance, "refundable deposit"). \
Statements that no fee is charged are not fee requests.
- `player_contract_status`: what the document says about the player's current contract. \
Use "unknown" if it does not say.
- `inconsistencies`: dates, visa, travel, accommodation or contract claims that \
contradict each other or other parts of the letter, each with its quote. Do not list \
general suspicion.
- Do not judge whether the document is real or fake.
"""


def extract_fields(doc: LoadedDocument) -> Tuple[DocumentFields, ExtractionMethod, List[str]]:
    notes: List[str] = []
    if settings.llm_enabled:
        fields = _extract_with_model(doc)
        if fields is not None:
            notes.extend(_ground(fields, doc))
            notes.extend(_add_missed_fee_requests(fields, doc))
            return fields, "vision_model", notes
        notes.append("The vision model was unavailable, so only pattern extraction ran.")
    else:
        notes.append(
            "No ANTHROPIC_API_KEY is set, so only pattern extraction ran. Club, signatory and "
            "dates are not extracted, and the checks that need them report 'not confirmed'."
        )
    if not doc.text.strip():
        notes.append("No readable text was found in the document (scanned or image, and no OCR).")
    return extract_with_patterns(doc.text), "patterns_only", notes


def _extract_with_model(doc: LoadedDocument) -> Optional[DocumentFields]:
    from app.llm import DATA_NOT_INSTRUCTIONS, parse_structured

    encoded = base64.b64encode(doc.data).decode("ascii")
    if doc.mime_type == "application/pdf":
        block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}}
    else:
        block = {"type": "image", "source": {"type": "base64", "media_type": doc.mime_type, "data": encoded}}
    form = parse_structured(
        DocumentFieldsForm,
        system=EXTRACTION_SYSTEM + "\n" + DATA_NOT_INSTRUCTIONS,
        content=[block, {"type": "text", "text": "Fill in the form from this document."}],
        max_tokens=16000,
    )
    return None if form is None else from_form(form)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[‘’‚′]", "'", text)
    text = re.sub(r"[“”„″]", '"', text)
    text = re.sub(r"[‐-―]", "-", text)
    return " ".join(text.split())


def _ground(fields: DocumentFields, doc: LoadedDocument) -> List[str]:
    """Drop every extracted value whose quote is not in the document.

    PDFs with a text layer are checked against that text. Scans and screenshots have no
    text layer, so quotes are checked against the model's own transcription, which is weaker.
    """
    notes = []
    if doc.has_text_layer:
        reference = _normalise(doc.text)
    else:
        reference = _normalise(fields.transcribed_text)
        notes.append("Quotes were checked against the model's transcription (no text layer in the file).")
    dropped = []

    def grounded(quote: Optional[str]) -> bool:
        return bool(quote) and _normalise(quote) in reference

    for name, value in fields:
        if isinstance(value, Quoted) and not grounded(value.quote):
            setattr(fields, name, None)
            dropped.append(name)
        elif isinstance(value, list) and value and hasattr(value[0], "quote"):
            kept = [item for item in value if grounded(item.quote)]
            if len(kept) < len(value):
                dropped.append(name)
            setattr(fields, name, kept)
    if dropped:
        notes.append("Dropped values whose quote is not in the document: " + ", ".join(sorted(set(dropped))) + ".")
    return notes


def _add_missed_fee_requests(fields: DocumentFields, doc: LoadedDocument) -> List[str]:
    """A second, rule-based pass for money requests, so a model miss is not silent."""
    if fields.fee_requests:
        return []
    found = _fee_requests(doc.text or fields.transcribed_text)
    if not found:
        return []
    fields.fee_requests = found
    return ["The model listed no payment request, but the payment rules found one; it is included."]


# --- Pattern fallback -----------------------------------------------------------------

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WEBSITE = re.compile(r"\b(?:https?://|www\.)[^\s,;)]+", re.I)
AMOUNT = re.compile(
    r"(?P<cur>KES|KSh|Ksh|Kshs|USD|US\$|EUR|GBP|MYR|RM|IDR|Rp|AED|\$|€|£)\s?(?P<num>\d[\d,. ]*\d|\d)"
    r"|(?P<num2>\d[\d,.]*\d)\s?(?P<cur2>KES|KSh|Kshs|shillings|USD|dollars|EUR|euros|AED|dirhams)",
    re.I,
)
PAYMENT_WORDS = re.compile(
    r"\b(pay|paid|payment|send|deposit|fee|fees|charge|transfer|m-?pesa|paybill|till|"
    r"western union|moneygram|world ?remit|bank account|account (?:no|number)|wallet|bitcoin|usdt|crypto)\b",
    re.I,
)
CHANNEL_WORDS = re.compile(r"\b(m-?pesa|paybill|till|western union|moneygram|world ?remit|account|wallet|bitcoin|usdt)\b", re.I)
NEGATION = re.compile(
    r"\b(no|not|never|without|free of)\b[^.]{0,40}\b(fee|fees|charge|charges|payment|pay|cost|costs)\b"
    r"|\bfree of charge\b|\bdoes not charge\b|\bnever (?:ask|request)",
    re.I,
)
PROMISE_WORDS = re.compile(r"\b(visa|flight|ticket|accommodation|guarantee[ds]?|work permit)\b", re.I)
# Sentence ends, but not after "Mr." or "licence no.".
SENTENCE_END = re.compile(r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\b[Nn]o\.)(?<!\bSt\.)(?<=[.!?])\s+")
CURRENCY_CODES = {
    "ksh": "KES", "kshs": "KES", "kes": "KES", "shillings": "KES", "usd": "USD", "us$": "USD", "$": "USD",
    "dollars": "USD", "eur": "EUR", "€": "EUR", "euros": "EUR", "gbp": "GBP", "£": "GBP", "myr": "MYR",
    "rm": "MYR", "idr": "IDR", "rp": "IDR", "aed": "AED", "dirhams": "AED",
}


def _sentences(text: str) -> List[str]:
    # Blank lines end a paragraph; single line breaks inside a paragraph are wrapping.
    paragraphs = re.split(r"\n\s*\n", text)
    out = []
    for paragraph in paragraphs:
        flat = " ".join(paragraph.split())
        out.extend(part.strip() for part in SENTENCE_END.split(flat) if part.strip())
    return out


def _fee_requests(text: str) -> List[FeeRequest]:
    requests = []
    for sentence in _sentences(text):
        if not PAYMENT_WORDS.search(sentence) or NEGATION.search(sentence):
            continue
        amount = AMOUNT.search(sentence)
        if not amount and not CHANNEL_WORDS.search(sentence):
            continue
        lower = sentence.lower()
        if re.search(r"m-?pesa|mobile money|airtel money", lower):
            method = "mobile_money"
        elif re.search(r"western union|moneygram|world ?remit", lower):
            method = "money_transfer_service"
        elif re.search(r"bitcoin|usdt|crypto|wallet", lower):
            method = "crypto"
        elif re.search(r"bank|account", lower):
            method = "bank_transfer"
        else:
            method = "unknown"
        account = None
        number = next(iter(phonenumbers.PhoneNumberMatcher(sentence, "KE")), None)
        if number:
            account = phonenumbers.format_number(number.number, phonenumbers.PhoneNumberFormat.E164)
        else:
            digits = re.search(r"(?:paybill|till|account|acc)\D{0,20}(\d[\d -]{4,}\d)", sentence, re.I)
            account = digits.group(1) if digits else None
        personal = bool(number) or bool(re.search(r"\b(mr|mrs|ms|receiver name|my personal|account (?:name|holder))\b", lower))
        currency = None
        if amount:
            raw = (amount.group("cur") or amount.group("cur2") or "").lower()
            currency = CURRENCY_CODES.get(raw, raw.upper() or None)
        requests.append(FeeRequest(
            amount=re.sub(r"[^\d.]", "", amount.group("num") or amount.group("num2")) if amount else None,
            currency=currency,
            payment_method=method,
            payee_type="individual" if personal else "unknown",
            account_or_number=account,
            refundable_claim=bool(re.search(r"refund", lower)),
            quote=sentence,
        ))
    return requests


def extract_with_patterns(text: str) -> DocumentFields:
    """Format-independent extraction: only values that can be found anywhere in prose."""
    fields = DocumentFields(transcribed_text=text)
    if not text.strip():
        return fields
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    emails = []
    for line in lines:
        for match in EMAIL.finditer(line):
            if match.group(0).lower() not in [e.value for e in emails]:
                emails.append(Quoted(value=match.group(0).lower(), quote=line))
    if emails:
        # The first address is usually the letterhead or "reply to" address. Marked as a guess in notes.
        fields.sender_email = emails[0]
    seen = set()
    for line in lines:
        for match in phonenumbers.PhoneNumberMatcher(line, "KE"):
            e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
            if e164 not in seen:
                seen.add(e164)
                fields.contact_phones.append(Quoted(value=e164, quote=line))
        for match in WEBSITE.finditer(line):
            fields.websites.append(Quoted(value=match.group(0).rstrip("."), quote=line))
    fields.fee_requests = _fee_requests(text)
    payment_numbers = {fee.account_or_number for fee in fields.fee_requests}
    fields.contact_phones = [phone for phone in fields.contact_phones if phone.value not in payment_numbers]
    fields.promises = [Quoted(value=s, quote=s) for s in _sentences(text) if PROMISE_WORDS.search(s)]
    return fields
