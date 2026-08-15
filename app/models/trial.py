import enum
from datetime import date

from sqlalchemy import JSON, Date, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin


class TrialStatus(str, enum.Enum):
    RECRUITING = "recruiting"
    ACTIVE_NOT_RECRUITING = "active_not_recruiting"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class Trial(Base, UUIDMixin, TimestampMixin):
    """Mirrors a ClinicalTrials.gov study. Synced in week 2."""

    __tablename__ = "trials"

    nct_id: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    brief_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TrialStatus] = mapped_column(
        Enum(TrialStatus, name="trial_status"), nullable=False, index=True
    )
    phase: Mapped[str | None] = mapped_column(String(32))
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    locations: Mapped[list] = mapped_column(JSON, default=list)
    start_date: Mapped[date | None] = mapped_column(Date)
    last_synced_at: Mapped[date | None] = mapped_column(Date)

    criteria_nodes: Mapped[list["CriteriaNode"]] = relationship(  # noqa: F821
        back_populates="trial", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_trials_status_phase", "status", "phase"),)
