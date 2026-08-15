import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Date, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.criteria import CriteriaNode


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

    criteria_nodes: Mapped[list["CriteriaNode"]] = relationship(
        back_populates="trial", cascade="all, delete-orphan"
    )

    # One composite btree in (created_at, id) order. Two single-column indexes
    # will NOT serve a row-value comparison as a range scan -- Postgres walks one
    # index per scan, so it would bitmap-or them and filter. No DESC variant
    # needed: btrees are walked backwards at identical cost.
    __table_args__ = (
        Index("ix_trials_status_phase", "status", "phase"),
        Index("ix_trials_created_at_id", "created_at", "id"),
    )
