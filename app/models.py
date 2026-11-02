from __future__ import annotations
from typing import Optional
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class LicensedAgent(Base):
    __tablename__ = "licensed_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    licence_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    licence_status: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)

    lookups: Mapped[list["LookupLog"]] = relationship(back_populates="agent")


class LookupLog(Base):
    __tablename__ = "lookup_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    query_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("licensed_agents.id"), nullable=True
    )

    agent: Mapped[Optional[LicensedAgent]] = relationship(back_populates="lookups")


class InboundMessage(Base):
    """One row per WhatsApp message id, so Meta's webhook retries are processed once."""

    __tablename__ = "inbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wamid: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WebCheck(Base):
    """One run of the web-checking agent (Step 3) over the fields of one document."""

    __tablename__ = "web_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # Salted hash of the player's WhatsApp number, never the number itself.
    player_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    logs: Mapped[list["WebCheckLog"]] = relationship(
        back_populates="web_check", cascade="all, delete-orphan", order_by="WebCheckLog.id"
    )


class WebCheckLog(Base):
    """Every search, page and lookup the web-checking agent made, for KEFWA to audit."""

    __tablename__ = "web_check_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    web_check_id: Mapped[str] = mapped_column(ForeignKey("web_checks.id"), nullable=False, index=True)
    # Links to a case once the case history (cross-case linking) exists.
    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    web_check: Mapped[WebCheck] = relationship(back_populates="logs")
