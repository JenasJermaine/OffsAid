"""Tables for the later stages (cases, cross-case linking, KEFWA review queue).

Parked: nothing imports this module yet, so Part 1 creates only the agent-check tables.
Import it from app.db.init_db() when the WhatsApp pipeline is switched on; on Postgres
that also needs the pgvector extension (CREATE EXTENSION vector).
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base

TEXT_VECTOR_DIM = 512


class TextVector(TypeDecorator):
    """pgvector column on Postgres, JSON list elsewhere (SQLite in tests)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(TEXT_VECTOR_DIM))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        return None if value is None else [float(v) for v in value]

    def process_result_value(self, value, dialect):
        return None if value is None else [float(v) for v in value]


class InboundMessage(Base):
    """One row per WhatsApp message id, so Meta's webhook retries are processed once."""

    __tablename__ = "inbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wamid: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    player_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    offer_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extracted: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    findings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    report_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text_vector: Mapped[list[float] | None] = mapped_column(TextVector(), nullable=True)
    image_phash: Mapped[str | None] = mapped_column(String(16), nullable=True)

    signals: Mapped[list["CaseSignal"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class CaseSignal(Base):
    """Hashed values (phone, email, payment account...) used for exact cross-case matches."""

    __tablename__ = "case_signals"
    __table_args__ = (Index("ix_case_signals_kind_hash", "kind", "value_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    case: Mapped[Case] = relationship(back_populates="signals")


class ReviewItem(Base):
    """KEFWA review queue: unclear and high-risk cases for a person to look at."""

    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Raw number kept only here, so KEFWA staff can follow up with the player.
    player_wa_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    reviewer_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped[Case | None] = relationship()


class WebCheckLog(Base):
    """Every search, page and lookup the web-checking agent made, for KEFWA to audit."""

    __tablename__ = "web_check_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)


class RegistrationWindow(Base):
    """League registration periods, each row with its source and the date we checked it."""

    __tablename__ = "registration_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    season: Mapped[str] = mapped_column(String(16), nullable=False)
    opens: Mapped[date] = mapped_column(Date, nullable=False)
    closes: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    checked_on: Mapped[date] = mapped_column(Date, nullable=False)
