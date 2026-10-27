from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from rapidfuzz import fuzz

from app.config import settings
from app.models import LicensedAgent
from app.normalize import normalize_country, normalize_licence, normalize_name, normalize_phone

MatchStatus = Literal["found", "possible_match", "not_found"]


@dataclass
class AgentQuery:
    name: str | None = None
    phone: str | None = None
    licence_number: str | None = None
    claimed_country: str | None = None


@dataclass
class MatchOutcome:
    status: MatchStatus
    agent: LicensedAgent | None
    score: int | None
    reason: str


def countries_differ(claimed: str | None, listed: str) -> bool:
    claimed_n = normalize_country(claimed)
    if not claimed_n:
        return False
    return claimed_n != normalize_country(listed)


def name_score(query_name: str, agent: LicensedAgent) -> int:
    left = normalize_name(query_name)
    right = agent.normalized_name or normalize_name(agent.name)
    if not left or not right:
        return 0
    return int(fuzz.token_sort_ratio(left, right))


def _name_conflicts(query: AgentQuery, agent: LicensedAgent) -> bool:
    if not query.name:
        return False
    return name_score(query.name, agent) < settings.possible_min_score


def _from_licence(query: AgentQuery, agents: Sequence[LicensedAgent]) -> MatchOutcome | None:
    key = normalize_licence(query.licence_number)
    if not key:
        return None
    hit = next((a for a in agents if normalize_licence(a.licence_number) == key), None)
    if hit is None:
        return None
    score = name_score(query.name, hit) if query.name else 100
    if countries_differ(query.claimed_country, hit.country) or _name_conflicts(query, hit):
        return MatchOutcome(
            status="possible_match",
            agent=hit,
            score=score,
            reason="Licence number matches a row, but another detail differs. Check the details.",
        )
    return MatchOutcome(
        status="found",
        agent=hit,
        score=score,
        reason="Licence number found on the licensed list.",
    )


def _from_phone(query: AgentQuery, agents: Sequence[LicensedAgent]) -> MatchOutcome | None:
    key = normalize_phone(query.phone)
    if not key:
        return None
    hit = next((a for a in agents if a.phone and normalize_phone(a.phone) == key), None)
    if hit is None:
        return None
    score = name_score(query.name, hit) if query.name else 100
    if countries_differ(query.claimed_country, hit.country) or _name_conflicts(query, hit):
        return MatchOutcome(
            status="possible_match",
            agent=hit,
            score=score,
            reason="Phone number matches a row, but another detail differs. Check the details.",
        )
    if query.name and score < settings.found_min_score:
        return MatchOutcome(
            status="possible_match",
            agent=hit,
            score=score,
            reason="Phone number matches a row. Name is close but not confirmed. Check the details.",
        )
    return MatchOutcome(
        status="found",
        agent=hit,
        score=score,
        reason="Phone number found on the licensed list.",
    )


def _from_name(query: AgentQuery, agents: Sequence[LicensedAgent]) -> MatchOutcome:
    if not query.name:
        return MatchOutcome(
            status="not_found",
            agent=None,
            score=None,
            reason="No matching licensed agent.",
        )
    best: LicensedAgent | None = None
    best_score = -1
    for agent in agents:
        score = name_score(query.name, agent)
        better_score = score > best_score
        same_score_country_fit = (
            score == best_score
            and best is not None
            and countries_differ(query.claimed_country, best.country)
            and not countries_differ(query.claimed_country, agent.country)
        )
        if better_score or same_score_country_fit:
            best_score = score
            best = agent
    if best is None or best_score < settings.possible_min_score:
        return MatchOutcome(
            status="not_found",
            agent=None,
            score=best_score if best_score >= 0 else None,
            reason="No matching licensed agent.",
        )
    if best_score >= settings.found_min_score and not countries_differ(
        query.claimed_country, best.country
    ):
        return MatchOutcome(
            status="found",
            agent=best,
            score=best_score,
            reason="Name found on the licensed list.",
        )
    reason = (
        "Name matches a licensed agent in a different country. Check the details."
        if countries_differ(query.claimed_country, best.country)
        else "Possible match, check the details."
    )
    return MatchOutcome(
        status="possible_match",
        agent=best,
        score=best_score,
        reason=reason,
    )


def verify_agent(query: AgentQuery, agents: Sequence[LicensedAgent]) -> MatchOutcome:
    licence_result = _from_licence(query, agents)
    if licence_result is not None:
        return licence_result
    phone_result = _from_phone(query, agents)
    if phone_result is not None:
        return phone_result
    if query.name:
        return _from_name(query, agents)
    return MatchOutcome(
        status="not_found",
        agent=None,
        score=None,
        reason="No matching licensed agent.",
    )
