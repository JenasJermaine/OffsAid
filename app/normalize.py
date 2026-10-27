from __future__ import annotations

import re
import unicodedata

TITLES = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
        "prof",
        "professor",
        "hon",
        "sir",
        "madam",
        "md",
    }
)


def _ascii_fold(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    text = _ascii_fold(name).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in TITLES]
    return " ".join(sorted(tokens))


def normalize_licence(licence_number: str | None) -> str:
    if not licence_number:
        return ""
    return re.sub(r"[\s\-]", "", licence_number).upper()


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    return digits


def normalize_country(country: str | None) -> str:
    if not country:
        return ""
    return re.sub(r"\s+", " ", country.strip().lower())
