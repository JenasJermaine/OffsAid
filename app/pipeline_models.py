"""Tables for the later stages that are not live yet.

Parked: nothing imports this module yet. InboundMessage and WebCheckLog moved to
app/models.py when the WhatsApp webhook and the web-checking agent (Step 3) went live.

The case history, cross-case linking and KEFWA review queue tables (cases, identifiers,
documents, review_queue) are NOT SQLAlchemy models: they are plain-SQL Postgres migrations
in migrations/ (see docs/CROSS_CASE_LINKING_GUIDE.md), used by app/case_linking.py.
"""

from datetime import date

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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
