from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class LicensedAgent(Base):
    __tablename__ = "licensed_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(128), nullable=False)
    licence_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
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
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("licensed_agents.id"), nullable=True
    )

    agent: Mapped[LicensedAgent | None] = relationship(back_populates="lookups")
