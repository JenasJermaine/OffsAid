import hashlib

from app.config import settings


def hash_lookup_value(value: str) -> str:
    payload = f"{settings.lookup_hash_salt}|{value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
