"""Public-source lookups used by the document checks: Wikidata for clubs, RDAP for domain age.

Both return None when the source has no answer or the network is off
(NETWORK_CHECKS_ENABLED=false), and the check then reports "not confirmed".
The web-checking agent (Step 3) can reuse these as its wikidata_club and domain_age tools.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from typing import List, Optional

import httpx
from rapidfuzz import fuzz

from app.config import settings

log = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
FOOTBALL_CLUB = "Q476028"  # instance of: association football club (excludes women's/youth teams)
TIMEOUT = 10.0
NAME_MATCH_MIN = 85
CLUB_NOISE = re.compile(r"\b(f\.?c\.?|football club|club|s\.?c\.?|a\.?f\.?c\.?|the)\b", re.I)


@dataclass
class ClubFacts:
    wikidata_id: str
    name: str
    country: Optional[str] = None
    country_iso2: Optional[str] = None
    leagues: List[str] = field(default_factory=list)
    official_website: Optional[str] = None

    @property
    def source(self) -> str:
        return f"https://www.wikidata.org/wiki/{self.wikidata_id}"


@dataclass
class DomainRegistration:
    domain: str
    registered: date
    source: str


class Lookups:
    """Live lookups. Tests substitute a fake with the same two methods."""

    def club(self, name: str) -> Optional[ClubFacts]:
        if not settings.network_checks_enabled or not name.strip():
            return None
        for query in search_variants(name):
            try:
                club = _wikidata_club(query, name.strip())
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("Wikidata lookup failed: %s", exc)
                return None
            if club is not None:
                return club
        return None

    def domain_registration(self, domain: str) -> Optional[DomainRegistration]:
        if not settings.network_checks_enabled or not re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
            return None
        try:
            return _rdap(domain)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("RDAP lookup failed for %s: %s", domain, exc)
            return None


def _get(params: dict) -> dict:
    for attempt in range(2):  # Wikidata is sometimes slow; one retry on a network error
        try:
            response = httpx.get(
                WIKIDATA_API,
                params={**params, "format": "json"},
                headers={"User-Agent": settings.lookup_user_agent},
                timeout=TIMEOUT,
            )
            break
        except httpx.TransportError:
            if attempt:
                raise
    response.raise_for_status()
    return response.json()


def _claim_values(entity: dict, prop: str) -> List[str]:
    values = []
    for statement in entity.get("claims", {}).get(prop, []):
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and "id" in value:
            values.append(value["id"])
        elif isinstance(value, str):
            values.append(value)
    return values


def search_variants(name: str) -> List[str]:
    """Letters write club names many ways; Wikidata search matches only some of them.

    "Kuopion Palloseura (KuPS)" -> also "Kuopion Palloseura" and "KuPS";
    "Selangor Football Club" -> also "Selangor FC".
    """
    name = " ".join(name.split())
    variants = [name]
    outside = " ".join(re.sub(r"\([^)]*\)", " ", name).split())
    inside = re.findall(r"\(([^)]+)\)", name)
    variants += [outside, *inside]
    for base in (outside or name,):
        variants.append(re.sub(r"\bfootball club\b", "FC", base, flags=re.I))
        variants.append(" ".join(CLUB_NOISE.sub(" ", base).split()))
    seen, out = set(), []
    for variant in variants:
        if variant and len(variant) >= 3 and variant.casefold() not in seen:
            seen.add(variant.casefold())
            out.append(variant)
    return out[:4]


def _names(entity: dict) -> List[str]:
    names = [entity.get("labels", {}).get("en", {}).get("value", "")]
    names += [alias.get("value", "") for alias in entity.get("aliases", {}).get("en", [])]
    return [name for name in names if name]


def _clean_club_name(name: str) -> str:
    return " ".join(CLUB_NOISE.sub(" ", name.casefold()).replace(".", " ").split())


def _names_match(query: str, names: List[str]) -> bool:
    cleaned = _clean_club_name(query)
    return any(fuzz.token_sort_ratio(cleaned, _clean_club_name(name)) >= NAME_MATCH_MIN for name in names)


@lru_cache(maxsize=256)
def _wikidata_club(query: str, name: str) -> Optional[ClubFacts]:
    hits = _get({
        "action": "wbsearchentities", "search": query, "language": "en", "type": "item", "limit": 7,
    }).get("search", [])
    if not hits:
        return None
    entities = _get({
        "action": "wbgetentities", "ids": "|".join(hit["id"] for hit in hits),
        "props": "claims|labels|aliases", "languages": "en",
    })["entities"]
    # Keep search order (best match first) and take the first senior football club whose
    # label or alias really matches, so an unknown club does not latch onto another one.
    club_id = next(
        (
            hit["id"] for hit in hits
            if FOOTBALL_CLUB in _claim_values(entities[hit["id"]], "P31")
            and (_names_match(name, _names(entities[hit["id"]])) or _names_match(query, _names(entities[hit["id"]])))
        ),
        None,
    )
    if club_id is None:
        return None
    club = entities[club_id]
    country_ids = _claim_values(club, "P17")[:1]
    league_ids = _claim_values(club, "P118")
    labels = {}
    if country_ids or league_ids:
        related = _get({
            "action": "wbgetentities", "ids": "|".join(country_ids + league_ids),
            "props": "claims|labels", "languages": "en",
        })["entities"]
        labels = {qid: related[qid].get("labels", {}).get("en", {}).get("value") for qid in related}
        iso2 = _claim_values(related[country_ids[0]], "P297")[:1] if country_ids else []
    else:
        iso2 = []
    websites = _claim_values(club, "P856")
    return ClubFacts(
        wikidata_id=club_id,
        name=club.get("labels", {}).get("en", {}).get("value", name),
        country=labels.get(country_ids[0]) if country_ids else None,
        country_iso2=iso2[0] if iso2 else None,
        leagues=[labels[qid] for qid in league_ids if labels.get(qid)],
        official_website=websites[0] if websites else None,
    )


@lru_cache(maxsize=256)
def _rdap(domain: str) -> Optional[DomainRegistration]:
    url = f"{settings.rdap_base_url.rstrip('/')}/{domain}"
    response = httpx.get(url, headers={"User-Agent": settings.lookup_user_agent}, timeout=TIMEOUT, follow_redirects=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    for event in response.json().get("events", []):
        if event.get("eventAction") == "registration" and event.get("eventDate"):
            registered = datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00")).date()
            return DomainRegistration(domain=domain, registered=registered, source=url)
    return None
